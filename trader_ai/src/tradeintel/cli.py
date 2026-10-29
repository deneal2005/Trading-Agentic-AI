"""Command-line interface: `tradeintel <command>`."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import PROJECT_ROOT, load_settings
from .logging_setup import setup_logging

log = logging.getLogger("tradeintel")


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _bars(s, synthetic: bool):
    from .data.providers import SyntheticProvider
    from .data.store import BarStore
    from .data.validate import validate_bars

    m = s.market
    if synthetic:
        bars, _ = validate_bars(SyntheticProvider().fetch(m.instrument, m.timeframe, s.data.start), m.timeframe)
        bars.attrs["source"] = "SYNTHETIC"
        return bars
    bars = BarStore(s.paths.db_path).load(m.instrument, m.timeframe)
    if bars.empty:
        sys.exit("no stored bars; run `tradeintel data ingest` first (or pass --synthetic)")
    return bars


def cmd_data_ingest(s, a):
    from .data.providers import DataUnavailable, make_provider
    from .data.store import BarStore
    from .data.validate import validate_bars

    m = s.market
    prov = make_provider(a.provider or s.data.provider, s.data.csv_path)
    try:
        raw = prov.fetch(m.instrument, m.timeframe, s.data.start)
    except DataUnavailable as e:
        sys.exit(f"data unavailable: {e}")
    bars, rep = validate_bars(raw, m.timeframe)
    if not rep.ok:
        sys.exit(f"validation failed: {rep.issues}")
    h = BarStore(s.paths.db_path).upsert(bars, m.instrument, m.timeframe, prov.source, rep.issues)
    _print({"source": prov.source, "rows": rep.rows_out, "dropped": rep.dropped, "issues": rep.issues, "hash": h,
            "first": str(bars.index[0]), "last": str(bars.index[-1])})


def cmd_backtest(s, a):
    from .backtest.engine import run_backtest
    from .backtest.metrics import compute_metrics
    from .backtest.research import engine_params
    from .strategies.library import build

    bars = _bars(s, a.synthetic)
    strat = build(a.strategy, **json.loads(a.params or "{}"))
    ep = engine_params(s)
    ep.require_stop = strat.requires_stop
    res = run_backtest(bars, strat.signals(bars), s.costs, ep)
    m = compute_metrics(res, s.market.timeframe)
    _print({"warning": "single full-sample backtest: in-sample, for inspection only; use `research` for evidence",
            "source": bars.attrs.get("source"), "strategy": strat.describe(), **m})


def cmd_research(s, a):
    from .backtest.research import research
    from .experiments.registry import Registry
    from .reporting.html import research_report

    bars = _bars(s, a.synthetic)
    reg = Registry(s.paths.db_path)
    out = {}
    for name in a.strategies:
        rep = research(s, bars, name, registry=reg, optimize=not a.no_optimize)
        path = research_report(rep, s.paths.reports_dir / f"research_{name}.html")
        out[name] = {"experiment_id": rep.get("experiment_id"), "source": rep["data_source"], "oos_sharpe": rep["oos"]["sharpe"],
                     "oos_return": rep["oos"]["net_return"], "max_dd": rep["oos"]["max_drawdown"], "trades": rep["oos"]["n_trades"],
                     "gate": "PASSED" if rep["gate"]["passed"] else "REJECTED", "reasons": rep["gate"]["reasons"], "report": str(path)}
    _print(out)


def cmd_holdout(s, a):
    from .backtest.research import holdout_test
    from .experiments.registry import Registry

    bars = _bars(s, False)
    _print(holdout_test(s, bars, a.strategy, json.loads(a.params), Registry(s.paths.db_path)))


def cmd_model_eval(s, a):
    from dataclasses import asdict

    from .data.store import frame_hash
    from .experiments.registry import Registry
    from .models.forecast import ModelRegistry, walk_forward_eval

    bars = _bars(s, a.synthetic)
    ev, model = walk_forward_eval(bars, s.backtest.walkforward.train_bars, s.backtest.walkforward.test_bars)
    meta = {**asdict(ev), "data_source": bars.attrs.get("source"), "data_hash": frame_hash(bars)}
    if bars.attrs.get("source") == "SYNTHETIC":
        meta["verdict"] = "rejected"
        meta["reasons"] = meta["reasons"] + ["trained on SYNTHETIC data"]
    version = ModelRegistry(s.paths.models_dir).save("direction_logit", model, meta)
    Registry(s.paths.db_path).record("model", strategy="direction_logit", params={"C": 0.1}, instrument=s.market.instrument,
                                     timeframe=s.market.timeframe, data_source=meta["data_source"], data_hash=meta["data_hash"],
                                     config=s.model_dump(mode="json"), n_trials=1, metrics=meta, verdict=meta["verdict"])
    _print({"version": version, **meta})


def cmd_news_ingest(s, a):
    from .news.sources import ForexFactoryCalendar, RSSSource
    from .news.store import NewsStore

    ns = NewsStore(s.paths.db_path)
    out = {}
    t = s.news.request_timeout_seconds
    for f in s.news.rss_feeds:
        items, h = RSSSource(f.name, f.url, f.reliability, t).fetch()
        ns.record_health(h)
        out[f.name] = {"ok": h.ok, "fetched": h.items, "added": ns.add_news(items), "error": h.error}
    ev, h = ForexFactoryCalendar(s.news.forexfactory_calendar_url, t).fetch()
    ns.record_health(h)
    new, rev = ns.upsert_events(ev)
    out["forexfactory"] = {"ok": h.ok, "fetched": h.items, "new": new, "revised": rev, "error": h.error}
    _print(out)


def cmd_news_analyze(s, a):
    from .models.llm import make_llm
    from .news.analyzer import analyze_event, analyze_news, to_json
    from .news.store import NewsStore

    llm = make_llm(s.llm.provider, s.llm.ollama_model, s.llm.anthropic_model, s.llm.timeout_seconds)
    ns = NewsStore(s.paths.db_path)
    done = []
    for e in ns.events(impact="high")[-a.limit:]:
        an = analyze_event(e, llm, s.llm.max_input_chars)
        ns.save_analysis("events", "event_key", e["event_key"], to_json(an))
        done.append({"event": e["title"], "ccy": e["currency"], "released": an.facts.released,
                     "direction": an.interpretation.direction_for_currency, "confidence": an.interpretation.confidence,
                     "method": an.interpretation.method, "flags": an.flags})
    for n in ns.recent_news(a.limit):
        r = analyze_news(n, llm)
        ns.save_analysis("news", "id", n["id"], json.dumps(r))
    _print({"llm": llm.name, "events": done})


def cmd_news_ablation(s, a):
    from .backtest.research import engine_params
    from .news.features import events_frame, news_ablation
    from .news.store import NewsStore
    from .strategies.library import build

    bars = _bars(s, a.synthetic)
    if a.events_csv:
        import pandas as pd
        events = events_frame(pd.read_csv(a.events_csv).to_dict("records"))
    else:
        events = events_frame(NewsStore(s.paths.db_path).events())
    ccys = {s.market.base_ccy, s.market.quote_ccy}
    w = s.backtest.walkforward
    _print(news_ablation(build(a.strategy), bars, events, ccys, s.costs, engine_params(s), w.train_bars, w.test_bars,
                         s.market.timeframe, a.mode))


def cmd_kb(s, a):
    from .kb.ingest import clone_or_update, ingest_markdown_dir, ingest_repo, ingest_strategy_cards, load_sources
    from .kb.store import KnowledgeBase

    kb = KnowledgeBase(s.paths.db_path)
    if a.kb_cmd == "build":
        out = {"strategy_cards": ingest_strategy_cards(kb, PROJECT_ROOT / "knowledge/strategies"),
               "concepts": ingest_markdown_dir(kb, PROJECT_ROOT / "knowledge/concepts")}
        if a.with_repos:
            for src in load_sources(PROJECT_ROOT / "knowledge/sources.yaml"):
                dest = s.paths.data_dir.parent / "refs" / src["name"]
                try:
                    ver = clone_or_update(src["url"], dest)
                    out[src["name"]] = ingest_repo(kb, dest, src, ver)
                except Exception as e:
                    out[src["name"]] = f"failed: {e}"
        out["stats"] = kb.stats()
        _print(out)
    elif a.kb_cmd == "search":
        levels = tuple(a.levels.split(",")) if a.levels else None
        _print([h.__dict__ for h in kb.search(" ".join(a.query), a.k, levels)])


def cmd_risk(s, a):
    from .risk.engine import KillSwitch

    ks = KillSwitch(s.paths.kill_switch_file)
    if a.risk_cmd == "status":
        _print({"kill_switch": ks.engaged(), "reason": ks.reason() if ks.engaged() else None, "limits": s.risk.model_dump()})
    elif a.risk_cmd == "kill":
        ks.engage(a.reason or "manual")
        _print({"kill_switch": True})
    elif a.risk_cmd == "reset":
        if input("Type RESET to clear the kill switch after reviewing the cause: ").strip() != "RESET":
            sys.exit("not reset")
        ks.reset()
        _print({"kill_switch": False})


def cmd_run_once(s, a):
    from .service import run_cycle
    _print(run_cycle(s))


def cmd_service(s, a):
    from .service import run_service
    run_service(s, a.interval)


def cmd_dashboard(s, a):
    from .reporting.dashboard import build_dashboard
    print(build_dashboard(s))


def cmd_experiments(s, a):
    from .experiments.registry import Registry
    rows = Registry(s.paths.db_path).list(a.limit)
    _print([{k: r[k] for k in ("id", "created_at", "kind", "strategy", "data_source", "n_trials", "verdict")} for r in rows])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tradeintel", description="Local-first AI trading research and demo-trading system")
    p.add_argument("--config-dir", type=Path, default=None)
    p.add_argument("--log-level", default="WARNING")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("data", help="market data").add_subparsers(dest="data_cmd", required=True)
    di = d.add_parser("ingest", help="fetch, validate and store bars")
    di.add_argument("--provider", choices=["yahoo", "stooq", "csv", "synthetic"])
    di.set_defaults(fn=cmd_data_ingest)

    def synth(sp):
        sp.add_argument("--synthetic", action="store_true", help="use labelled synthetic data (offline/testing)")

    b = sub.add_parser("backtest", help="single in-sample backtest (inspection only)")
    b.add_argument("strategy"); b.add_argument("--params"); synth(b); b.set_defaults(fn=cmd_backtest)

    r = sub.add_parser("research", help="walk-forward + baselines + cost stress + gate + report")
    r.add_argument("strategies", nargs="+"); r.add_argument("--no-optimize", action="store_true"); synth(r)
    r.set_defaults(fn=cmd_research)

    h = sub.add_parser("holdout", help="one-time evaluation of fixed params on the untouched holdout")
    h.add_argument("strategy"); h.add_argument("--params", required=True); h.set_defaults(fn=cmd_holdout)

    me = sub.add_parser("model-eval", help="walk-forward evaluation of the direction model vs baselines")
    synth(me); me.set_defaults(fn=cmd_model_eval)

    n = sub.add_parser("news", help="news and economic calendar").add_subparsers(dest="news_cmd", required=True)
    n.add_parser("ingest").set_defaults(fn=cmd_news_ingest)
    na = n.add_parser("analyze"); na.add_argument("--limit", type=int, default=20); na.set_defaults(fn=cmd_news_analyze)
    nb = n.add_parser("ablation", help="measure strategy with vs without the news filter")
    nb.add_argument("strategy"); nb.add_argument("--mode", choices=["no_entry", "flat"], default="no_entry")
    nb.add_argument("--events-csv", help="historical events CSV (event_time,currency,impact,title)"); synth(nb)
    nb.set_defaults(fn=cmd_news_ablation)

    k = sub.add_parser("kb", help="knowledge base").add_subparsers(dest="kb_cmd", required=True)
    kbb = k.add_parser("build"); kbb.add_argument("--with-repos", action="store_true"); kbb.set_defaults(fn=cmd_kb)
    ks = k.add_parser("search"); ks.add_argument("query", nargs="+"); ks.add_argument("-k", type=int, default=5)
    ks.add_argument("--levels", help="comma-separated evidence levels"); ks.set_defaults(fn=cmd_kb)

    rk = sub.add_parser("risk", help="risk status and kill switch").add_subparsers(dest="risk_cmd", required=True)
    rk.add_parser("status").set_defaults(fn=cmd_risk)
    kk = rk.add_parser("kill"); kk.add_argument("--reason"); kk.set_defaults(fn=cmd_risk)
    rk.add_parser("reset").set_defaults(fn=cmd_risk)

    sub.add_parser("run-once", help="one decision cycle (dry run unless execution.enabled)").set_defaults(fn=cmd_run_once)
    sv = sub.add_parser("service", help="repeat decision cycles"); sv.add_argument("--interval", type=int, default=900)
    sv.set_defaults(fn=cmd_service)
    sub.add_parser("dashboard", help="write the status dashboard HTML").set_defaults(fn=cmd_dashboard)
    ex = sub.add_parser("experiments", help="list recorded experiments"); ex.add_argument("--limit", type=int, default=20)
    ex.set_defaults(fn=cmd_experiments)
    return p


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    s = load_settings(a.config_dir)
    setup_logging(s.paths.data_dir.parent / "logs", a.log_level)
    a.fn(s, a)


if __name__ == "__main__":
    main()
