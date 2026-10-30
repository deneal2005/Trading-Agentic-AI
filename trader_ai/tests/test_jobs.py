"""The job table behind the live activity bar: states, steps, failures, duplicates, lost jobs,
the scheduler heartbeat, and following up sent orders."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from tradeintel.data.store import BarStore, connect
from tradeintel.execution.pipeline import ExecutionPipeline
from tradeintel.jobs import JobStore, mark_abandoned, run_job, start_job
from tradeintel.service import StateStore, run_service
from tradeintel.ui import api


def wait_done(store, job_id, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = store.get(job_id)
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("still running")


def test_job_records_steps_and_result(tmp_path):
    js = JobStore(tmp_path / "db.sqlite")

    def body(progress):
        progress("A")
        progress("B")
        return {"ok": True, "value": 7}

    out = run_job(js, "check", "Test", ["A", "B", "C"], body, "cli")
    j = js.recent(1)[0]
    assert out["value"] == 7 and j["state"] == "succeeded" and j["done_steps"] == ["A", "B"]  # C was never needed
    assert j["result"]["value"] == 7 and j["source"] == "cli" and j["finished_at"]


def test_failures_are_recorded_both_ways(tmp_path):
    js = JobStore(tmp_path / "db.sqlite")
    run_job(js, "ingest", "Returns not ok", ["x"], lambda p: {"ok": False, "error": "no data"}, "app")
    run_job(js, "ingest", "Raises", ["x"], lambda p: 1 / 0, "app")
    raised, returned = js.recent(2)
    assert returned["state"] == "failed" and returned["error"] == "no data"
    assert raised["state"] == "failed" and "ZeroDivisionError" in raised["error"] and raised["step"] == "x"


def test_background_job_is_visible_while_running_and_duplicates_are_refused(tmp_path):
    js = JobStore(tmp_path / "db.sqlite")
    gate = threading.Event()

    def body(progress):
        progress("waiting")
        gate.wait(5)
        return {"ok": True}

    first, started = start_job(js, "check", "Slow", ["waiting"], body)
    assert started and first["state"] == "running"
    again, started2 = start_job(js, "check", "Slow again", ["waiting"], body)
    assert not started2 and again["id"] == first["id"]  # followed, not started twice
    _, started3 = start_job(js, "ingest", "Different kind", ["x"], lambda p: {"ok": True})
    assert started3  # a different kind may run alongside
    assert [j["id"] for j in js.running("check")] == [first["id"]]
    gate.set()
    assert wait_done(js, first["id"])["state"] == "succeeded"
    assert js.running("check") == []


def test_a_job_with_no_sign_of_life_is_reported_lost_and_slow_ones_are_flagged(tmp_path):
    js = JobStore(tmp_path / "db.sqlite")
    jid = js.start("check", "Crashed", ["a"], "service", 10)
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with connect(js.db) as con:
        con.execute("UPDATE jobs SET updated_at=?, started_at=? WHERE id=?", (old, old, jid))
    j = js.get(jid)
    assert j["state"] == "lost" and "no sign of life" in j["error"] and js.running() == []
    slow = js.start("check", "Slow", ["a"], "app", 1)
    with connect(js.db) as con:  # alive (fresh heartbeat) but running far longer than expected
        con.execute("UPDATE jobs SET started_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(), slow))
    assert js.get(slow)["slow"] is True


def test_app_restart_closes_jobs_it_left_running(tmp_path):
    js = JobStore(tmp_path / "db.sqlite")
    jid = js.start("check", "Left over", ["a"], "app", 10)
    with connect(js.db) as con:
        con.execute("UPDATE jobs SET pid=-1 WHERE id=?", (jid,))  # started by an earlier app process
    mark_abandoned(js)
    assert js.get(jid)["state"] == "failed" and "closed" in js.get(jid)["error"]


def test_scheduler_records_each_check_as_a_job_and_a_heartbeat(settings, synth_bars, monkeypatch):
    import tradeintel.service as svc
    monkeypatch.setattr(svc, "ensure_calendar", lambda s, now=None: False)  # no network in tests
    monkeypatch.setattr(svc.time, "sleep", lambda s: None)
    BarStore(settings.paths.db_path).upsert(synth_bars, "EURUSD", "1d", "SYNTHETIC")
    run_service(settings, interval_s=900, max_cycles=1)
    j = JobStore(settings.paths.db_path).recent(1)[0]
    assert j["kind"] == "check" and j["source"] == "service" and j["state"] == "succeeded"
    assert "AI agents reviewing" in j["done_steps"]
    hb = StateStore(settings.paths.db_path).get("service_heartbeat")
    assert hb["interval_s"] == 900
    act = api.activity(settings)
    assert act["service"]["alive"] and act["recent"][0]["id"] == j["id"]


class StatusBroker:
    def __init__(self, answers):
        self.answers = answers

    def order_status(self, key):
        return self.answers.get(key)


def test_reconcile_updates_pending_orders_from_the_broker(tmp_path):
    db = tmp_path / "db.sqlite"
    pipe = ExecutionPipeline(db, StatusBroker({"k-fill": {"status": "filled", "fill_price": 501.2, "filled_units": 10},
                                               "k-wait": {"status": "pending"}}), None, True)
    for key in ("k-fill", "k-wait", "k-unknown"):
        pipe.audit("practice", key, {}, None, None, {"status": "pending", "broker_order_id": "o", "fill_price": None,
                                                     "filled_units": 0, "message": "sent"})
    pipe.audit("practice", "k-done", {}, None, None, {"status": "filled", "fill_price": 1.0})
    assert pipe.reconcile() == 1
    with connect(db) as con:
        res = {k: json.loads(r) for k, r in con.execute("SELECT idempotency_key, order_result FROM audit")}
    assert res["k-fill"]["status"] == "filled" and res["k-fill"]["fill_price"] == 501.2 and res["k-fill"]["checked_at"]
    assert res["k-wait"]["status"] == "pending" and res["k-unknown"]["status"] == "pending"
    assert "k-fill" in pipe.submitted_keys()  # still protected against re-sending
    assert ExecutionPipeline(db, object(), None, True).reconcile() == 0  # brokers without a lookup are skipped


def test_failed_requests_are_followed_up_and_only_resent_once_the_broker_denies_them(tmp_path):
    db = tmp_path / "db.sqlite"
    pipe = ExecutionPipeline(db, StatusBroker({"k-arrived": {"status": "filled", "fill_price": 500.0, "filled_units": 10}}), None, True)
    for key in ("k-arrived", "k-lost"):
        pipe.audit("practice", key, {}, None, None, {"status": "error", "message": "network error"})
    assert pipe.submitted_keys() == {"k-arrived", "k-lost"}  # may have reached the broker: not sent again yet
    assert pipe.reconcile() == 2
    with connect(db) as con:
        res = {k: json.loads(r) for k, r in con.execute("SELECT idempotency_key, order_result FROM audit")}
    assert res["k-arrived"]["status"] == "filled" and res["k-lost"]["status"] == "not_received"
    assert pipe.submitted_keys() == {"k-arrived"}  # the broker confirmed the lost one never arrived


def test_a_check_that_collides_with_a_running_one_is_skipped_not_failed(settings, monkeypatch):
    import tradeintel.service as svc
    monkeypatch.setattr(svc, "ensure_calendar", lambda s: True)
    with svc.single_instance(settings.paths.db_path.parent / "cycle.lock"):
        out = svc.check(settings)
    assert out["ok"] and out["result"]["status"] == "skipped"


def test_releasing_the_stop_is_refused_while_a_limit_is_breached_and_audited_when_done(settings):
    from tradeintel.risk.engine import KillSwitch
    from tradeintel.service import make_broker, release_kill_switch
    kill, state = KillSwitch(settings.paths.kill_switch_file), StateStore(settings.paths.db_path)
    equity = make_broker(settings).account().equity
    kill.engage("max drawdown reached")
    state.set("hwm", equity * 2)  # 50% below the peak
    assert "max drawdown" in release_kill_switch(settings, "test") and kill.engaged()
    state.set("hwm", equity)
    assert release_kill_switch(settings, "test") is None and not kill.engaged()
    with connect(settings.paths.db_path) as con:
        row = con.execute("SELECT decision FROM audit WHERE mode='manual'").fetchone()
    assert "turned off from test" in row[0]
