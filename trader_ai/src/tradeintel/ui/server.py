"""Local HTTP server for the Trading OS UI (standard library only).

Safety model:
- Binds to 127.0.0.1 only; requests whose Host header isn't this server are
  refused (blocks DNS-rebinding pages from reading the API).
- Every POST needs the per-launch token (sent by the page as X-TI-Token),
  a same-origin Origin header if one is sent, and a JSON body. Other
  websites can't read the token, so they can't trigger actions.
- Actions call the same backend code as the CLI: the risk engine, kill
  switch, promotion gate and `execution.enabled` still decide everything.
  There is no endpoint that enables execution, edits risk limits, cancels
  orders or closes positions. Clearing the kill switch needs the typed
  word RESET, like the CLI, and is written to the audit table.
"""

from __future__ import annotations

import hmac
import json
import logging
import math
import mimetypes
import re
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..config import Settings
from . import api
from .companion import Companion
from ..jobs import JobStore, mark_abandoned, start_job
from ..service import CHECK_STEPS

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
mimetypes.add_type("font/woff2", ".woff2")  # not in Windows' registry by default
MAX_BODY = 64 * 1024
# inline styles are needed by the chart library; scripts stay 'self'-only
CSP_APP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
           "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
# research reports are self-contained HTML with inline styles and SVG
CSP_REPORT = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; frame-ancestors 'none'"


def finite(o):
    """NaN/inf -> None so responses are valid JSON (stored signals carry NaN for unused fields)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [finite(v) for v in o]
    return o


class App:
    """Routes and state shared by request threads."""

    def __init__(self, s: Settings, port: int, token: str | None = None, cache: api.BrokerCache | None = None,
                 companion: Companion | None = None):
        self.s, self.port = s, port
        self.token = token or secrets.token_urlsafe(32)
        self.cache = cache or api.BrokerCache(s)
        self.companion = companion or Companion(s, self.cache)
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.jobs = JobStore(s.paths.db_path)
        mark_abandoned(self.jobs)  # anything an earlier app left 'running' can't still be running

    def get(self, path: str, q: dict):
        s, c = self.s, self.cache
        one = lambda k, d=None: (q.get(k) or [d])[0]  # noqa: E731
        if path == "/api/state":
            return api.state(s, c)
        if path == "/api/watchlist":
            return api.watchlist(s, c)
        if path == "/api/bars":
            return api.bars_view(s, one("instrument", s.market.instrument), one("timeframe", s.market.timeframe),
                                 max(0, min(int(one("limit", "500")), 20000)), c)
        if path == "/api/decisions":
            return api.decisions_view(s, max(1, min(int(one("limit", "50")), 500)))
        if path == "/api/audit":
            return api.audit_rows(s, max(1, min(int(one("limit", "100")), 1000)))
        if path == "/api/agents":
            return api.agents_view(s)
        if path == "/api/risk":
            return api.risk_view(s, c)
        if path == "/api/research":
            return api.research_view(s)
        if path == "/api/news":
            return api.news_view(s)
        if path == "/api/logs":
            return api.logs_view(s, max(1, min(int(one("limit", "200")), 2000)), one("level"))
        if path == "/api/config":
            return api.config_view(s)
        if path == "/api/activity":
            return api.activity(s)
        if path == "/api/jobs":
            return [api._slim(j) for j in self.jobs.recent(max(1, min(int(one("limit", "30")), 200)))]
        if path.startswith("/api/jobs/"):
            job = self.jobs.get(int(path.rsplit("/", 1)[1]))
            if not job:
                raise KeyError(path)
            return job
        if path == "/api/outcomes":
            return api.outcomes_view(s)
        if path == "/api/brain":
            return api.brain_view(s, max(1, min(int(one("history", "120")), 1000)))
        raise KeyError(path)

    @property
    def JOBS(self):
        """Long actions run as tracked background jobs: name -> (body) -> (kind, label, steps, expected s, fn(progress))."""
        s, c = self.s, self.cache
        strat = lambda b: str(b.get("strategy", ""))  # noqa: E731

        def check(p):
            out = api.act_run_once(s, p)
            c.get(force=True)  # the account view should reflect the check
            return out
        return {
            "run-once": lambda b: ("check", "Check started from the app", CHECK_STEPS, 12, check),
            "ingest": lambda b: ("ingest", "Refreshing price data", ["Downloading and checking prices"], 8, lambda p: api.act_ingest(s, p)),
            "news-ingest": lambda b: ("news", "Refreshing news & calendar", ["Fetching news feeds and the economic calendar"], 10,
                                      lambda p: api.act_news_ingest(s, p)),
            "backtest": lambda b: ("backtest", f"Quick backtest: {strat(b)}", ["Running the backtest", "Comparing with buy-and-hold"], 8,
                                   lambda p: api.act_backtest(s, strat(b), b.get("params") if isinstance(b.get("params"), dict) else None, p)),
            "research": lambda b: ("research", f"History test: {strat(b)}", ["Testing on 20 years of history", "Writing the report"], 30,
                                   lambda p: api.act_research(s, strat(b), p)),
            "reconcile": lambda b: ("reconcile", "Checking order status with the broker", ["Asking the broker about sent orders"], 5,
                                    lambda p: api.act_reconcile(s, p)),
        }

    def post(self, path: str, body: dict):
        s = self.s
        if path == "/api/companion":
            return self.companion.ask(str(body.get("message", "")), body.get("context") if isinstance(body.get("context"), dict) else {})
        if path == "/api/actions/kill":
            return api.act_kill(s, str(body.get("reason", "")))
        if path == "/api/actions/reset-kill":
            return api.act_reset_kill(s, str(body.get("confirm", "")))
        name = path.removeprefix("/api/actions/")
        if name in self.JOBS:
            kind, label, steps, expected, fn = self.JOBS[name](body)
            job, started = start_job(self.jobs, kind, label, steps, fn, "app", expected)
            return {"ok": True, "job": job, "started": started}
        if path == "/api/actions/refresh-broker":
            return {"ok": True, "broker": self.cache.get(force=True)}
        raise KeyError(path)


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tradeintel-ui"
        sys_version = ""

        def log_message(self, fmt, *args):
            log.debug("http", extra={"data": {"line": fmt % args}})

        def _send(self, code: int, body: bytes, ctype: str, csp: str = CSP_APP):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", csp)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(finite(obj), default=str, allow_nan=False).encode(), "application/json")

        def _host_ok(self) -> bool:
            if self.headers.get("Host", "") in app.hosts:
                return True
            self._json(403, {"error": "forbidden host"})
            return False

        def _api(self, fn):
            try:
                self._json(200, fn())
            except ConnectionError:  # the page cancelled the request (e.g. switched screens): nothing to report
                return
            except KeyError as e:
                self._json(404, {"error": f"not found: {e}"})
            except ValueError as e:  # bad query/body values
                self._json(400, {"error": str(e)})
            except Exception as e:
                log.exception("ui api error")
                self._json(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            if not self._host_ok():
                return
            u = urlparse(self.path)
            if u.path.startswith("/api/"):
                return self._api(lambda: app.get(u.path, parse_qs(u.query)))
            m = re.fullmatch(r"/reports/([a-z0-9_]+\.html)", u.path)
            if m:
                f = app.s.paths.reports_dir / m.group(1)
                if f.is_file():
                    return self._send(200, f.read_bytes(), "text/html; charset=utf-8", CSP_REPORT)
                return self._json(404, {"error": "report not found"})
            rel = "index.html" if u.path in ("/", "") else u.path.lstrip("/")
            f = (STATIC / rel).resolve()
            if not f.is_relative_to(STATIC.resolve()) or not f.is_file():
                return self._json(404, {"error": "not found"})
            data = f.read_bytes()
            if f.name == "index.html":
                data = data.replace(b"__TI_TOKEN__", app.token.encode())
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, data, ctype)

        def do_POST(self):
            if not self._host_ok():
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in app.hosts}:
                return self._json(403, {"error": "forbidden origin"})
            if not hmac.compare_digest(self.headers.get("X-TI-Token", ""), app.token):
                return self._json(403, {"error": "missing or invalid token; reload the page"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(415, {"error": "JSON body required"})
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json(400, {"error": "invalid JSON"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "JSON object required"})
            log.info("ui action", extra={"data": {"path": self.path}})
            self._api(lambda: app.post(urlparse(self.path).path, body))

    return Handler


def export_site(s: Settings, out: Path) -> dict:
    """A read-only snapshot for static hosting (GitHub Pages): the app's files plus each GET view as JSON, read by
    the page's snapshot mode. Local file paths are dropped, the account id is masked and the log file is left out."""
    import shutil

    out = Path(out)
    shutil.copytree(STATIC, out, dirs_exist_ok=True)
    shutil.rmtree(out / "api", ignore_errors=True)  # only our own data folder is replaced
    (out / "api").mkdir()
    (out / ".nojekyll").write_text("", encoding="utf-8")
    cache = api.BrokerCache(s)
    st = api.state(s, cache)
    if st["broker"].get("account"):
        st["broker"]["account"] = "paper"
    cfg = api.config_view(s)
    cfg["settings"].pop("paths", None)
    views = {
        "state": lambda: st, "watchlist": lambda: api.watchlist(s, cache), "decisions": lambda: api.decisions_view(s, 200),
        "audit": lambda: api.audit_rows(s, 300), "agents": lambda: api.agents_view(s), "risk": lambda: api.risk_view(s, cache),
        "research": lambda: api.research_view(s), "news": lambda: api.news_view(s), "activity": lambda: api.activity(s),
        "jobs": lambda: [api._slim(j) for j in JobStore(s.paths.db_path).recent(200)], "brain": lambda: api.brain_view(s, 120),
        "config": lambda: cfg, "outcomes": lambda: api.outcomes_view(s), "logs": lambda: {"available": False, "path": "not included in the published snapshot", "lines": []},
    }
    for n in (21, 63, 126, 252, 1260, 0):  # the chart ranges the pages ask for
        views[f"bars_default_{n}"] = views[f"bars_{s.market.instrument}_{n}"] = \
            lambda n=n: api.bars_view(s, s.market.instrument, s.market.timeframe, n, cache)
    written, skipped = [], {}
    for name, fn in views.items():
        try:
            data = json.dumps(finite(fn()), default=str, allow_nan=False)
        except Exception as e:  # e.g. no trained model: the page shows "not included in this snapshot"
            skipped[name] = f"{type(e).__name__}: {e}"
            continue
        (out / "api" / f"{name}.json").write_text(data, encoding="utf-8")
        written.append(name)
    if s.paths.reports_dir.exists():
        shutil.copytree(s.paths.reports_dir, out / "reports", dirs_exist_ok=True)
    return {"out": str(out), "recorded_at": st["server_time"], "written": len(written), "skipped": skipped}


def serve(s: Settings, port: int = 8765, open_browser: bool = True) -> None:
    app = App(s, port)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    # the first broker read takes seconds; warm it so the first page load isn't blocked
    threading.Thread(target=app.cache.get, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"Trading OS running at {url}  (local only; Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
