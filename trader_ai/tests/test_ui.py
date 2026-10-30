import http.client
import json
import re
import threading
from datetime import timedelta

import pytest

from tradeintel.agents.coordinator import DecisionContext, DecisionLog, coordinate
from tradeintel.data.store import BarStore
from tradeintel.risk.engine import KillSwitch
from tradeintel.strategies.library import build
from tradeintel.ui import api
from tradeintel.ui.companion import Companion
from tradeintel.ui.server import App, finite, make_handler

BROKER = {"broker": "paper", "available": True, "environment": "paper", "account": "...er-1", "currency": "USD",
          "cash": 10000.0, "equity": 10000.0, "positions": [], "fetched_at": "2026-10-29T00:00:00+00:00"}


@pytest.fixture
def app(settings, synth_bars):
    BarStore(settings.paths.db_path).upsert(synth_bars, "EURUSD", "1d", "SYNTHETIC")
    cache = api.BrokerCache(settings, fetch=lambda s: dict(BROKER))  # no network in tests
    return App(settings, 0, token="t0ken", cache=cache)


@pytest.fixture
def server(app):
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app))
    port = httpd.server_port
    app.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()
    httpd.server_close()


def call(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=hdrs)
    r = c.getresponse()
    raw = r.read()
    return r.status, raw, dict(r.getheaders())


def post(port, path, body=None, **hdr):
    return call(port, "POST", path, body or {}, {"X-TI-Token": "t0ken", **hdr})


def record_decision(settings, synth_bars):
    strat = build("sma_cross")
    ctx = DecisionContext("EURUSD", synth_bars, strat.signals(synth_bars), "sma_cross", synth_bars.index[-1].to_pydatetime(),
                          timedelta(days=4), 0, [])
    return DecisionLog(settings.paths.db_path).record(ctx, coordinate(ctx, "multi"))


# ------------------------------------------------------------ read models

def test_state_and_pages_are_valid_json_even_with_nan_signal_fields(app, server, settings, synth_bars):
    record_decision(settings, synth_bars)  # stored signal row has NaN tp_dist/size_frac
    for path in ["/api/state", "/api/watchlist", "/api/bars?limit=50", "/api/decisions?limit=5", "/api/audit", "/api/agents",
                 "/api/risk", "/api/research", "/api/news", "/api/logs", "/api/config"]:
        code, raw, _ = call(server, "GET", path)
        assert code == 200, (path, raw[:200])
        json.loads(raw)  # strict JSON: NaN would raise
    st = json.loads(call(server, "GET", "/api/state")[1])
    assert st["mode"]["execution_enabled"] is False and st["mode"]["live_trading"] is False
    assert st["agent"]["last_decision"]["instrument"] == "EURUSD"
    assert st["data"]["source"] == "SYNTHETIC"


def test_finite_replaces_nan_and_inf():
    assert finite({"a": [1.0, float("nan")], "b": float("inf"), "c": "x"}) == {"a": [1.0, None], "b": None, "c": "x"}


def test_config_view_reports_secret_presence_never_values(settings, monkeypatch):
    monkeypatch.setenv("APCA_API_SECRET_KEY", "super-secret-value")
    out = json.dumps(api.config_view(settings))
    assert "super-secret-value" not in out and '"APCA_API_SECRET_KEY": true' in out


def test_bars_view_markers_are_signal_changes(app, settings):
    d = api.bars_view(settings, "EURUSD", "1d", 300, app.cache)
    assert d["available"] and len(d["candles"]) == 300
    targets = [m["target"] for m in d["markers"]]
    assert all(a != b for a, b in zip(targets, targets[1:]))  # consecutive markers always differ
    assert api.bars_view(settings, "NOPE", "1d", 10, app.cache)["available"] is False


# ------------------------------------------------------------ server safety

def test_index_gets_token_and_security_headers(server):
    code, raw, hdr = call(server, "GET", "/")
    assert code == 200 and b"t0ken" in raw and b"__TI_TOKEN__" not in raw
    assert "script-src 'self'" in hdr["Content-Security-Policy"] and hdr["X-Content-Type-Options"] == "nosniff"


def test_foreign_host_is_refused(server):
    assert call(server, "GET", "/api/state", headers={"Host": "evil.example:80"})[0] == 403


def test_post_requires_token_origin_and_json(server, settings):
    assert post(server, "/api/actions/kill", **{"X-TI-Token": "wrong"})[0] == 403
    assert call(server, "POST", "/api/actions/kill", {})[0] == 403  # no token
    assert post(server, "/api/actions/kill", Origin="http://evil.example")[0] == 403
    assert call(server, "POST", "/api/actions/kill", None, {"X-TI-Token": "t0ken", "Content-Type": "text/plain"})[0] == 415
    assert not KillSwitch(settings.paths.kill_switch_file).engaged()


def test_static_path_traversal_is_blocked(server):
    for p in ["/../config/default.yaml", "/%2e%2e/%2e%2e/config/default.yaml", "/js/../../server.py", "/reports/../x.html"]:
        assert call(server, "GET", p)[0] == 404, p


def test_turning_off_the_emergency_stop_needs_typed_reset_and_is_audited(app, server, settings):
    post(server, "/api/actions/kill", {"reason": "t"})
    ks = KillSwitch(settings.paths.kill_switch_file)
    assert json.loads(post(server, "/api/actions/reset-kill", {"confirm": "reset"})[1])["ok"] is False and ks.engaged()
    assert json.loads(post(server, "/api/actions/reset-kill", {})[1])["ok"] is False and ks.engaged()
    assert json.loads(post(server, "/api/actions/reset-kill", {"confirm": "RESET"})[1])["ok"] and not ks.engaged()
    row = api.audit_rows(settings, 1)[0]
    assert row["mode"] == "manual" and row["order_result"]["status"] == "cleared"
    # the assistant only proposes it, as a dangerous action (the app then asks for the typed RESET)
    ks.engage("again")
    p = app.companion.ask("please turn off the emergency stop", {})["proposal"]
    assert p["action"] == "reset-kill" and p["danger"] and ks.engaged()


def test_no_endpoint_can_enable_execution_or_close_positions(server):
    for p in ["/api/actions/enable", "/api/actions/reset", "/api/actions/close-all", "/api/actions/cancel", "/api/config"]:
        assert post(server, p)[0] == 404, p


def test_kill_action_engages_switch_and_says_positions_stay_open(server, settings):
    code, raw, _ = post(server, "/api/actions/kill", {"reason": "test"})
    body = json.loads(raw)
    assert code == 200 and body["ok"] and KillSwitch(settings.paths.kill_switch_file).engaged()
    assert "NOT closed" in body["note"]
    st = json.loads(call(server, "GET", "/api/state")[1])
    assert st["kill_switch"]["engaged"] and st["agent"]["state"] == "halted"
    assert any(a["title"].startswith("Emergency stop is on") for a in st["alerts"])


def run_action(port, name, body=None):
    """Start an action (a background job) and follow it to the end, like the page does."""
    import time
    started = json.loads(post(port, f"/api/actions/{name}", body or {})[1])
    assert started["ok"] and started["job"]["state"] in ("running", "succeeded", "failed")
    for _ in range(300):
        job = json.loads(call(port, "GET", f"/api/jobs/{started['job']['id']}")[1])
        if job["state"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("job never finished")


def test_backtest_action_is_labelled_in_sample_and_validates_input(server):
    job = run_action(server, "backtest", {"strategy": "sma_cross"})
    body = job["result"]
    assert job["state"] == "succeeded" and job["done_steps"] == ["Running the backtest", "Comparing with buy-and-hold"]
    assert body["ok"] and "in-sample" in body["warning"] and body["equity"] and body["source"] == "SYNTHETIC"
    bad = run_action(server, "backtest", {"strategy": "nope"})
    assert bad["state"] == "failed" and "unknown strategy" in bad["error"]
    assert run_action(server, "research", {"strategy": "buy_and_hold"})["state"] == "failed"


# ------------------------------------------------------------ companion

@pytest.mark.parametrize("msg,action", [
    ("Pause new trade proposals", "kill"), ("please stop trading now", "kill"), ("Run a decision cycle", "run-once"),
    ("Run a backtest on donchian breakout", "backtest"), ("run walk-forward research on rsi mean reversion", "research"),
    ("refresh data", "ingest"),
])
def test_companion_only_proposes_state_changes(app, settings, msg, action):
    out = app.companion.ask(msg, {})
    assert out["proposal"]["action"] == action
    assert not KillSwitch(settings.paths.kill_switch_file).engaged()  # nothing executed


def test_companion_proposal_picks_named_strategy(app):
    assert app.companion.ask("backtest donchian breakout", {})["proposal"]["args"] == {"strategy": "donchian_breakout"}


@pytest.mark.parametrize("msg,intent", [
    ("Explain current positions", "positions"), ("Show my current exposure", "positions"), ("Why is the agent waiting?", "decision"),
    ("Explain the last decision", "decision"), ("Why was that trade rejected?", "rejected"), ("Show the latest agent errors", "errors"),
    ("Summarize today", "today"), ("Explain why you are monitoring this asset", "monitor"), ("what are my risk limits", "risk"),
])
def test_companion_read_only_answers_come_from_records(app, settings, synth_bars, msg, intent):
    record_decision(settings, synth_bars)
    out = app.companion.ask(msg, {"symbol": "EURUSD"})
    assert out["intent"] == intent and out.get("proposal") is None and out["reply"]


def test_companion_fallback_without_llm_says_so(app):
    out = app.companion.ask("what is the meaning of life", {})
    assert out["source"] == "system" and "no LLM is configured" in out["reply"]


def test_companion_llm_answer_is_labelled_and_given_facts(settings, app):
    seen = {}

    class FakeLLM:
        name = "fake"

        def complete_json(self, system, user):
            seen["user"] = json.loads(user)
            return {"reply": "Equity is 10000."}

    c = Companion(settings, app.cache, llm=FakeLLM())
    out = c.ask("how is the account doing overall", {})
    assert out["source"] == "llm" and out["reply"] == "Equity is 10000."
    assert seen["user"]["facts"]["broker"]["equity"] == 10000.0 and "question" in seen["user"]


def test_brain_unavailable_without_a_trained_network(app, server):
    body = json.loads(call(server, "GET", "/api/brain")[1])
    assert body["available"] is False and "model-eval --model mlp" in body["error"]


def test_brain_exposes_real_weights_and_verified_forward_pass(app, server, settings, synth_bars):
    from tradeintel.models.forecast import FEATURES, ModelRegistry, walk_forward_eval
    _, model = walk_forward_eval(synth_bars, 300, 100, kind="mlp")
    ModelRegistry(settings.paths.models_dir).save("direction_mlp", model, {"verdict": "rejected", "reasons": ["test"]})
    b = json.loads(call(server, "GET", "/api/brain?history=30")[1])
    mlp = model.steps[-1][1]
    assert b["available"] and b["layers"] == [len(FEATURES), 16, 8, 1] and b["used_by_agents"] is False
    assert b["weights"][0][0][0] == round(float(mlp.coefs_[0][0][0]), 4)  # weights[layer][from][to] as trained
    assert b["forward_pass_check"] <= 1e-6 and len(b["history"]) == 30
    last = b["history"][-1]
    assert last["actual_up"] is None and 0 <= last["p_up"] <= 1  # the newest bar's outcome isn't known yet
    assert [len(a) for a in last["acts"]] == b["layers"]


def test_companion_endpoint_round_trip(server):
    body = json.loads(post(server, "/api/companion", {"message": "Explain current positions", "context": {}})[1])
    assert body["source"] == "records" and re.search(r"No open positions", body["reply"])


def test_ui_status_model_reflects_real_state():
    """The browser's status model (controls.js deriveStatus): no 'running' while disconnected, pending vs confirmed, etc."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    here = Path(__file__).parent
    js = here.parent / "src" / "tradeintel" / "ui" / "static" / "js" / "controls.js"
    r = subprocess.run([node, str(here / "ui_status_check.mjs"), str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_snapshot_export_has_no_token_local_paths_or_account_id(settings, tmp_path):
    from tradeintel.ui.server import export_site
    site = tmp_path / "site"
    res = export_site(settings, site)
    assert "__TI_TOKEN__" in (site / "index.html").read_text(encoding="utf-8")  # no token: the page runs in snapshot mode
    st = json.loads((site / "api" / "state.json").read_text(encoding="utf-8"))
    assert st["broker"]["account"] == "paper" and res["written"] > 10
    local = str(settings.paths.data_dir.parent).replace("\\", "\\\\")  # as it would appear inside JSON
    for f in (site / "api").glob("*.json"):
        assert local not in f.read_text(encoding="utf-8"), f.name
