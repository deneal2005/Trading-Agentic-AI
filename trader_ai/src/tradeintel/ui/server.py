"""Local HTTP server for the Trading OS UI (standard library only).

Safety model:
- Binds to 127.0.0.1 only; requests whose Host header isn't this server are
  refused (blocks DNS-rebinding pages from reading the API).
- Every POST needs the per-launch token (sent by the page as X-TI-Token),
  a same-origin Origin header if one is sent, and a JSON body. Other
  websites can't read the token, so they can't trigger actions.
- Actions call the same backend code as the CLI: the risk engine, kill
  switch, promotion gate and `execution.enabled` still decide everything.
  There is no endpoint that enables execution, edits risk limits, clears
  the kill switch, cancels orders or closes positions.
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

log = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
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
        raise KeyError(path)

    def post(self, path: str, body: dict):
        s = self.s
        if path == "/api/companion":
            return self.companion.ask(str(body.get("message", "")), body.get("context") if isinstance(body.get("context"), dict) else {})
        if path == "/api/actions/kill":
            return api.act_kill(s, str(body.get("reason", "")))
        if path == "/api/actions/run-once":
            out = api.act_run_once(s)
            self.cache.get(force=True)
            return out
        if path == "/api/actions/ingest":
            return api.act_ingest(s)
        if path == "/api/actions/news-ingest":
            return api.act_news_ingest(s)
        if path == "/api/actions/backtest":
            params = body.get("params") if isinstance(body.get("params"), dict) else None
            return api.act_backtest(s, str(body.get("strategy", "")), params)
        if path == "/api/actions/research":
            return api.act_research(s, str(body.get("strategy", "")))
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
