"""Audit probes. Each prints PASS/FAIL with evidence. Uses temp dirs; reads the real DB only for data-quality checks."""
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from tradeintel.config import load_settings
from tradeintel.data.providers import SyntheticProvider
from tradeintel.data.store import BarStore
from tradeintel.data.validate import validate_bars

R = []


def rec(tid, status, title, evidence):
    R.append({"id": tid, "status": status, "title": title, "evidence": evidence})
    print(f"[{status}] {tid} {title}\n    {evidence}")


def tmp_settings():
    d = Path(tempfile.mkdtemp(prefix="audit_"))
    return load_settings(env={}, root=d, overrides={"market": {"asset_class": "forex", "instrument": "EURUSD", "pip_size": 0.0001},
                                                    "costs": {"spread_pips": 1.2, "slippage_pips": 0.5, "financing_rate_annual": 0.01},
                                                    "risk": {"max_leverage": 5.0},
                                                    "backtest": {"walkforward": {"train_bars": 300, "test_bars": 100}}})


synth = SyntheticProvider(seed=3).fetch("EURUSD", "1d", "2015-01-01", n=1200)
synth.attrs["source"] = "SYNTHETIC"

# ---------------- MODEL
from tradeintel.models.forecast import FEATURES, make_features
full = make_features(synth)
ok = True
for cut in (400, 800, 1100):
    part = make_features(synth.iloc[:cut])
    a = full[FEATURES].iloc[:cut].dropna(); b = part[FEATURES].dropna()
    common = a.index.intersection(b.index)
    if not np.allclose(a.loc[common].to_numpy(), b.loc[common].to_numpy(), equal_nan=True):
        ok = False
rec("M1", "PASS" if ok else "FAIL", "Features at t use only bars <= t (truncation test, 12 features)",
    f"features recomputed on truncated history (cuts 400/800/1100) identical on overlapping rows: {ok}")
y_ok = bool((full["y"].iloc[:-1] == (synth["close"].shift(-1) > synth["close"]).astype(float).iloc[:-1]).all()) and pd.isna(full["y"].iloc[-1])
rec("M2", "PASS" if y_ok else "FAIL", "Label y_t = 1 if close[t+1] > close[t]; last row unlabeled", f"matches definition: {y_ok}")
X = make_features(synth).dropna()
rec("M3", "FAIL", "Walk-forward has no purge gap between train and test",
    f"last training row {X.index[299].date()} has a label built from the close of {X.index[300].date()}, the first test row "
    "(1-bar label overlap; small but real leakage).")

# ---------------- DATA (real DB, read-only)
s_real = load_settings()
for inst in ("SPY", "EURUSD"):
    try:
        b = BarStore(s_real.paths.db_path).load(inst, "1d")
    except Exception as e:
        rec(f"D-{inst}", "BLOCKED", f"{inst} stored bars", str(e)); continue
    _, rep = validate_bars(b, "1d")
    dup = int(b.index.duplicated().sum())
    rec(f"D1-{inst}", "PASS" if rep.ok and dup == 0 and not rep.dropped else "FAIL",
        f"{inst} stored bars re-validate cleanly", f"rows={len(b)} dropped_on_revalidation={rep.dropped} duplicates={dup} issues={rep.issues}")
    rec(f"D2-{inst}", "INFO", f"{inst} last stored bar", f"{b.index[-1].isoformat()} (now {datetime.now(timezone.utc).isoformat(timespec='minutes')})")
with __import__("sqlite3").connect(s_real.paths.db_path) as con:
    rows = con.execute("SELECT instrument, last_ts, ingested_at FROM ingestions ORDER BY id").fetchall()
partial = [r for r in rows if r[1][:10] == r[2][:10]]
rec("D3", "FAIL" if partial else "PASS", "Ingestion can store today's unfinished daily bar",
    f"ingestions whose last bar is dated the same UTC day it was ingested (possible partial candle): {partial}")

# ---------------- PROMOTION GATE SCOPE
from tradeintel.experiments.registry import Registry
from tradeintel.service import strategy_promoted
s = tmp_settings()
Registry(s.paths.db_path).record("walkforward", strategy="sma_cross", params={"fast": 50, "slow": 200}, instrument="GBPUSD",
                                 timeframe="1h", data_source="yahoo", data_hash="x", config={}, n_trials=1, metrics={}, verdict="promotable")
s.live.params = {"fast": 10, "slow": 50}
got = strategy_promoted(s, "sma_cross")
rec("G1", "FAIL" if got else "PASS", "Promotion is scoped to instrument, timeframe and parameters",
    f"a 'promotable' verdict for sma_cross on GBPUSD 1h (fast=50/slow=200) makes live sma_cross on EURUSD 1d (fast=10/slow=50) count as promoted: {got}")

# ---------------- SERVICE STATE: day-start equity / hwm only set on non-hold cycles
from tradeintel.execution.broker import Quote
from tradeintel.execution.paper import PaperBroker
from tradeintel.service import StateStore, run_cycle
s = tmp_settings()
BarStore(s.paths.db_path).upsert(synth, "EURUSD", "1d", "SYNTHETIC")
now = synth.index[-1].to_pydatetime() + timedelta(hours=26)
pb = PaperBroker(s.paths.db_path)
last = float(synth["close"].iloc[-1]); pb.set_quote(Quote("EURUSD", last, last + 0.0001, now))
out = run_cycle(s, broker=pb, now=now)
st = StateStore(s.paths.db_path)
rec("R1", "FAIL" if out["decision"] in ("hold", "abstain") and st.get("day_start_equity") is None else "INFO",
    "Day-start equity / high-water mark are recorded on every cycle",
    f"cycle decision={out['decision']}; day_start_equity stored={st.get('day_start_equity')}, hwm={st.get('hwm')} "
    "(only recorded when a cycle gets past hold/abstain, so the daily-loss baseline can be set late in the day)")

# ---------------- RISK
from tradeintel.news.store import NewsStore
from tradeintel.risk.engine import AccountState, KillSwitch, MarketState, OrderIntent, RiskEngine
s = tmp_settings()
T = datetime(2026, 10, 29, 12, 30, tzinfo=timezone.utc)
a = AccountState(equity=10000, day_start_equity=10000, high_water_mark=10000)
m = MarketState(1.1000, 1.1001, T - timedelta(seconds=2), T, 0.0001)
eng = lambda: RiskEngine(s.risk, KillSwitch(Path(tempfile.mkdtemp()) / "K"))  # noqa: E731
d = eng().evaluate(OrderIntent("EURUSD", 1, 10000, 1.1001, 1.0951, None, "t", "k-b", T), a, m, events=[])
n_events = len(NewsStore(s_real.paths.db_path).events())
rec("K1", "FAIL" if d.approved else "PASS", "Event blackout fails closed when the calendar is missing",
    f"order at 12:30 UTC (a typical US data release time) approved with an empty event list: {d.approved}; events stored in the real DB right now: {n_events}")
d2 = eng().evaluate(OrderIntent("EURUSD", 1, 10000, 1.1001, 1.0951, None, "t", "k-s", T), a, MarketState(1.1000, 1.1001, T - timedelta(seconds=90), T, 0.0001))
rec("K2", "PASS" if not d2.approved and not d2.checks["quote_fresh"] else "FAIL", "Stale quote (90 s) is rejected", f"approved={d2.approved}; reasons={d2.reasons}")
d3 = eng().evaluate(OrderIntent("EURUSD", 1, 10000, 1.13, 1.12, None, "t", "k-g", T), a, m)
rec("K3", "PASS" if not d3.approved and not d3.checks["price_consistent"] else "FAIL", "Order price far from live quote is rejected", f"approved={d3.approved}")
writes = subprocess.run(["git", "grep", "-nE", "set\\(\"consecutive_losses\"|consecutive_losses\\s*\\+=", "--", "src"], capture_output=True, text=True).stdout.strip()
rec("K4", "FAIL" if not writes else "PASS", "Consecutive-loss counter is updated from closed trades", f"code that writes the counter: {writes or 'none found'}")
p = Path(tempfile.mkdtemp()) / "KILL"
KillSwitch(p).engage("audit")
rec("K5", "PASS" if KillSwitch(p).engaged() else "FAIL", "Kill switch persists across process restart (file-based)", f"re-opened engaged={KillSwitch(p).engaged()}")
d6 = eng()
d6.kill.engage("audit")
r6 = d6.evaluate(OrderIntent("EURUSD", 1, 1000, 1.1001, 1.0951, None, "t", "k-k", T), a, m)
rec("K6", "PASS" if not r6.approved and r6.halted else "FAIL", "Engaged kill switch rejects every order", f"approved={r6.approved}")

# ---------------- EXECUTION: paper broker never triggers stops
s = tmp_settings()
pb = PaperBroker(s.paths.db_path)
pb.set_quote(Quote("EURUSD", 1.1000, 1.1001, datetime.now(timezone.utc)))
pb.submit_market("EURUSD", 1000, 1.0950, None, "stop-test")
pb.set_quote(Quote("EURUSD", 1.0500, 1.0501, datetime.now(timezone.utc)))
still = [x.units for x in pb.positions()]
rec("E1", "FAIL" if still else "PASS", "Internal paper broker executes stop-losses", f"price fell 500 pips through a 1.0950 stop; position still open: {still}")

# ---------------- EXECUTION: timeout on submit, then restart
from tradeintel.execution.alpaca import AlpacaPaperBroker
from tradeintel.execution.pipeline import ExecutionPipeline


class Resp:
    def __init__(self, body, code=200):
        self.body, self.status_code, self.content = body, code, json.dumps(body).encode()
    def json(self): return self.body
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))


class FakeAlpaca:
    """Paper-account mock: first POST times out *after* the broker accepted it; later POSTs with the same client id get 422."""
    def __init__(self):
        self.headers, self.accepted, self.posts = {}, set(), 0
    def request(self, method, url, **kw):
        if url.endswith("/v2/account"): return Resp({"account_number": "PA0", "currency": "USD", "cash": "100000", "equity": "100000"})
        if url.endswith("/v2/positions"): return Resp([])
        if url.endswith("/quotes/latest"):
            return Resp({"quote": {"bp": 500.0, "ap": 500.02, "t": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}})
        raise AssertionError(url)
    def post(self, url, json=None, timeout=None):
        self.posts += 1
        cid = json["client_order_id"]
        if cid in self.accepted:
            return Resp({"message": "client_order_id must be unique"}, 422)
        self.accepted.add(cid)
        if self.posts == 1:
            raise requests.Timeout("read timed out")
        return Resp({"id": "o", "status": "accepted"})


s = load_settings(env={}, root=Path(tempfile.mkdtemp()))  # SPY defaults
fake = FakeAlpaca()
br = AlpacaPaperBroker("k", "s", session=fake, quote_pause=0)
it = OrderIntent("SPY", 1, 10, 500.02, 490.0, None, "t", "sma|SPY|bar|1", datetime.now(timezone.utc))
pipe = ExecutionPipeline(s.paths.db_path, br, RiskEngine(s.risk, KillSwitch(s.paths.kill_switch_file)), enabled=True)
r1 = pipe.execute(it, {}, 100000, 100000, 0, 0.01)
pipe2 = ExecutionPipeline(s.paths.db_path, br, RiskEngine(s.risk, KillSwitch(s.paths.kill_switch_file)), enabled=True)
pipe2.risk._seen_keys |= pipe2.submitted_keys()  # what run_cycle does after a restart
r2 = pipe2.execute(it, {}, 100000, 100000, 0, 0.01)
rec("E2", "PASS" if r1["status"] == "error" and r2["status"] in ("rejected", "rejected_by_risk") and len(fake.accepted) == 1 else "FAIL",
    "Timeout with unknown order status does not create a duplicate order (Alpaca mock)",
    f"1st submit: {r1['status']}; after a restart the pipeline re-sent the same order: {r2['status']} ({r2.get('message', '')}); "
    f"orders accepted by the broker: {len(fake.accepted)}. Protection comes only from the broker's unique client_order_id; "
    "the local audit doesn't treat 'error' as possibly-sent.")

# ---------------- concurrency lock
from tradeintel.service import single_instance
lp = Path(tempfile.mkdtemp()) / "cycle.lock"
second = "not raised"
with single_instance(lp):
    try:
        with single_instance(lp):
            pass
    except RuntimeError as e:
        second = str(e)
rec("C1", "PASS" if second == "another cycle is running" else "FAIL", "Two decision cycles cannot run at once", f"second acquire: {second}")

# ---------------- holdout before demo not enforced
src = Path("src/tradeintel/service.py").read_text(encoding="utf-8")
rec("G2", "FAIL" if "holdout" not in src else "PASS", "Single-use holdout must pass before a strategy may trade",
    "service.run_cycle checks only strategy_promoted(); no holdout result is consulted" if "holdout" not in src else "holdout referenced")

json.dump(R, open(sys.argv[1], "w"), indent=1, default=str)
