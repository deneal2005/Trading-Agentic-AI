"""Bounded multi-agent decision layer.

Each agent is a pure function of structured inputs, returning a structured
report with evidence. The coordinator runs each agent once (no debate
loop), applies explicit decision criteria, and abstains when data is
missing, stale or the agents materially disagree. It produces a *proposal*
only; the deterministic risk engine decides whether any order is allowed.

Baseline for comparison: the strategy signal alone ("single" mode). The
multi-agent layer is kept only if it measurably improves on that baseline.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from ..data.store import connect

Stance = Literal["long", "short", "neutral", "abstain"]


@dataclass
class AgentReport:
    agent: str
    stance: Stance
    confidence: Literal["low", "medium", "high"]
    evidence: list[str]
    missing: list[str] = field(default_factory=list)
    objections: list[str] = field(default_factory=list)


@dataclass
class Decision:
    action: Literal["enter_long", "enter_short", "exit", "hold", "abstain"]
    reasons: list[str]
    reports: list[AgentReport]
    mode: str
    decided_at: str


@dataclass
class DecisionContext:
    instrument: str
    bars: pd.DataFrame                  # history up to the decision bar
    signal: pd.DataFrame                # strategy signal frame for bars
    strategy: str
    now: datetime
    max_bar_age: timedelta
    current_position: int = 0
    events: list[dict] = field(default_factory=list)          # scheduled/released events with analysis
    model_prob_up: float | None = None
    model_status: str = "no accepted model"
    regime: str | None = None


def _dir(x: int) -> Stance:
    return "long" if x > 0 else "short" if x < 0 else "neutral"


def market_analyst(ctx: DecisionContext) -> AgentReport:
    missing = []
    if ctx.bars.empty:
        return AgentReport("market", "abstain", "low", [], ["price history"])
    last_t = ctx.bars.index[-1]
    if ctx.now - last_t > ctx.max_bar_age:
        missing.append(f"latest bar {last_t} is stale")
    tgt = int(ctx.signal["target"].iloc[-1])
    ev = [f"{ctx.strategy} target={tgt} at {last_t}", f"regime={ctx.regime or 'unknown'}"]
    return AgentReport("market", "abstain" if missing else _dir(tgt), "medium", ev, missing)


def news_analyst(ctx: DecisionContext, horizon: timedelta = timedelta(hours=24)) -> AgentReport:
    base, quote = ctx.instrument[:3], ctx.instrument[3:6]
    upcoming = [e for e in ctx.events if e.get("impact") == "high" and e.get("currency") in (base, quote)
                and timedelta(0) <= pd.Timestamp(e["event_time"]).to_pydatetime() - ctx.now <= horizon]
    score, ev = 0, []
    for e in ctx.events:
        a = e.get("analysis") or {}
        interp = a.get("interpretation", {}) if isinstance(a, dict) else {}
        d = interp.get("direction_for_currency")
        if d not in ("bullish", "bearish") or interp.get("confidence") == "low":
            continue
        s = 1 if d == "bullish" else -1
        if e.get("currency") == base:
            score += s
        elif e.get("currency") == quote:
            score -= s
        ev.append(f"{e.get('currency')} {e.get('title')}: {d} ({interp.get('confidence')})")
    objections = [f"high-impact event within {horizon}: {e['title']} ({e['currency']})" for e in upcoming]
    stance: Stance = "long" if score > 0 else "short" if score < 0 else "neutral"
    return AgentReport("news", stance, "medium" if abs(score) >= 2 else "low", ev or ["no directional event analysis"],
                       objections=objections)


def quant_analyst(ctx: DecisionContext) -> AgentReport:
    if ctx.model_prob_up is None:
        return AgentReport("quant", "abstain", "low", [], [ctx.model_status])
    p = ctx.model_prob_up
    stance: Stance = "long" if p > 0.55 else "short" if p < 0.45 else "neutral"
    return AgentReport("quant", stance, "medium" if abs(p - 0.5) > 0.1 else "low", [f"P(up next bar)={p:.3f}"])


def adversarial_reviewer(ctx: DecisionContext, reports: list[AgentReport]) -> AgentReport:
    """Tries to find reasons NOT to act on the primary signal."""
    primary = next(r for r in reports if r.agent == "market")
    obj = []
    for r in reports:
        if r.agent == "market":
            continue
        if r.stance in ("long", "short") and primary.stance in ("long", "short") and r.stance != primary.stance \
                and r.confidence != "low":
            obj.append(f"{r.agent} disagrees ({r.stance}, {r.confidence})")
        obj += r.objections
    if ctx.regime and primary.stance in ("long", "short"):
        if ctx.regime.startswith("flat") and ctx.strategy in ("sma_cross", "donchian_breakout"):
            obj.append(f"trend strategy in {ctx.regime} regime (historically weaker; verify in regime report)")
        if not ctx.regime.startswith("flat") and ctx.strategy == "rsi_mean_reversion":
            obj.append(f"mean-reversion strategy in trending regime {ctx.regime}")
    return AgentReport("adversarial", "neutral", "medium", [f"{len(obj)} objection(s)"], objections=obj)


def coordinate(ctx: DecisionContext, mode: Literal["single", "multi"] = "multi", max_objections: int = 0) -> Decision:
    now = datetime.now(timezone.utc).isoformat()
    m = market_analyst(ctx)
    reports = [m]
    if m.stance == "abstain":
        return Decision("abstain", ["market data missing or stale: " + "; ".join(m.missing)], reports, mode, now)
    want = {"long": 1, "short": -1, "neutral": 0}[m.stance]
    if mode == "multi":
        reports += [news_analyst(ctx), quant_analyst(ctx)]
        adv = adversarial_reviewer(ctx, reports)
        reports.append(adv)
        entering = want != 0 and want != ctx.current_position
        if entering and len(adv.objections) > max_objections:
            return Decision("abstain", ["objections to new entry: " + "; ".join(adv.objections)], reports, mode, now)
    if want == ctx.current_position:
        return Decision("hold", ["signal unchanged"], reports, mode, now)
    if want == 0:
        return Decision("exit", ["signal flat"], reports, mode, now)
    return Decision("enter_long" if want > 0 else "enter_short", [f"{ctx.strategy} signal {m.stance}"], reports, mode, now)


class DecisionLog:
    SCHEMA = """CREATE TABLE IF NOT EXISTS decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, decided_at TEXT, instrument TEXT, strategy TEXT, mode TEXT,
      action TEXT, reasons TEXT, reports TEXT, inputs TEXT)"""

    def __init__(self, db_path: Path):
        self.db = db_path
        with connect(db_path) as con:
            con.execute(self.SCHEMA)

    def record(self, ctx: DecisionContext, d: Decision) -> int:
        inputs = {"last_bar": str(ctx.bars.index[-1]) if len(ctx.bars) else None,
                  "last_close": float(ctx.bars["close"].iloc[-1]) if len(ctx.bars) else None,
                  "signal": ctx.signal.iloc[-1].to_dict() if len(ctx.signal) else None,
                  "events": len(ctx.events), "model_prob_up": ctx.model_prob_up, "regime": ctx.regime,
                  "position": ctx.current_position}
        with connect(self.db) as con:
            cur = con.execute("INSERT INTO decisions (decided_at,instrument,strategy,mode,action,reasons,reports,inputs) VALUES (?,?,?,?,?,?,?,?)",
                              (d.decided_at, ctx.instrument, ctx.strategy, d.mode, d.action, json.dumps(d.reasons),
                               json.dumps([asdict(r) for r in d.reports]), json.dumps(inputs, default=str)))
            return int(cur.lastrowid)

    def recent(self, limit: int = 20) -> list[dict]:
        with connect(self.db) as con:
            con.row_factory = __import__("sqlite3").Row
            return [dict(r) for r in con.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))]
