"""Static HTML reports (no server or JS dependencies). Every page carries a
banner stating the data mode: SYNTHETIC, HISTORICAL, PAPER/DEMO or LIVE."""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

MODE_STYLE = {
    "SYNTHETIC": ("#7a1f1f", "SYNTHETIC DATA - results carry no information about real markets"),
    "HISTORICAL": ("#1f4f7a", "HISTORICAL BACKTEST - simulated fills on past data"),
    "DEMO": ("#5a4a00", "DEMO / PAPER ACCOUNT - no real money"),
    "LIVE": ("#000000", "LIVE MONEY"),
}

CSS = """
:root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--card:#f7f7f7;--accent:#2563eb}
@media (prefers-color-scheme: dark){:root{--bg:#141414;--fg:#e8e8e8;--muted:#9a9a9a;--line:#333;--card:#1e1e1e;--accent:#60a5fa}}
body{font:14px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:0 16px 40px}
main{max-width:1100px;margin:auto}.banner{color:#fff;padding:10px 16px;margin:0 -16px 16px;font-weight:600}
table{border-collapse:collapse;width:100%;margin:8px 0 20px;font-variant-numeric:tabular-nums}
th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}
.card{background:var(--card);padding:10px;border-radius:6px}.card b{display:block;font-size:18px}
.pass{color:#15803d}.fail{color:#b91c1c}.muted{color:var(--muted)}svg{width:100%;height:auto}
.wrap{overflow-x:auto}
"""


def _fmt(v) -> str:
    if isinstance(v, float):
        if 0 < abs(v) < 1e-4:
            return f"{v:.2e}"
        if abs(v) < 10 and v != 0 and abs(v) >= 1e-4:
            return f"{v:.4f}"
        return f"{v:,.2f}"
    return html.escape(str(v))


def svg_line(series: dict[str, pd.Series], w: int = 1000, h: int = 260) -> str:
    vals = [s.dropna() for s in series.values() if len(s.dropna())]
    if not vals:
        return "<p class='muted'>no data</p>"
    lo, hi = min(float(v.min()) for v in vals), max(float(v.max()) for v in vals)
    t0 = min(v.index[0] for v in vals).value
    t1 = max(v.index[-1] for v in vals).value
    span, rng = max(t1 - t0, 1), max(hi - lo, 1e-9)
    colors = ["var(--accent)", "#9ca3af", "#d97706", "#059669"]
    paths, legend = [], []
    for (name, s), col in zip(series.items(), colors):
        s = s.dropna()
        if s.empty:
            continue
        step = max(1, len(s) // 800)
        pts = " ".join(f"{(t.value - t0) / span * (w - 60) + 50:.1f},{h - 25 - (v - lo) / rng * (h - 40):.1f}"
                       for t, v in list(s.items())[::step])
        paths.append(f"<polyline fill='none' stroke='{col}' stroke-width='1.6' points='{pts}'/>")
        legend.append(f"<tspan fill='{col}'>&#9632; {html.escape(name)}</tspan> ")
    axis = (f"<text x='0' y='15' font-size='11' fill='currentColor'>{hi:,.0f}</text>"
            f"<text x='0' y='{h - 25}' font-size='11' fill='currentColor'>{lo:,.0f}</text>"
            f"<text x='50' y='{h - 5}' font-size='11' fill='currentColor'>{vals[0].index[0].date()}</text>"
            f"<text x='{w - 120}' y='{h - 5}' font-size='11' fill='currentColor'>{vals[0].index[-1].date()}</text>"
            f"<text x='50' y='15' font-size='12'>{''.join(legend)}</text>")
    return f"<svg viewBox='0 0 {w} {h}' role='img' aria-label='equity curve'>{axis}{''.join(paths)}</svg>"


def page(title: str, mode: str, body: str) -> str:
    color, text = MODE_STYLE[mode]
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><div class='banner' style='background:{color}'>"
            f"{text}</div><main><h1>{html.escape(title)}</h1><p class='muted'>Generated {gen}</p>{body}</main></body></html>")


def table(rows: list[dict], cols: list[str] | None = None) -> str:
    if not rows:
        return "<p class='muted'>none</p>"
    cols = cols or list(rows[0])
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_fmt(r.get(c, ''))}</td>" for c in cols) + "</tr>" for r in rows)
    return f"<div class='wrap'><table><tr>{head}</tr>{body}</table></div>"


def research_report(rep: dict, out: Path) -> Path:
    mode = "SYNTHETIC" if rep["synthetic"] else "HISTORICAL"
    o = rep["oos"]
    cards = "".join(f"<div class='card'>{k}<b>{_fmt(v)}</b></div>" for k, v in [
        ("OOS net return", o["net_return"]), ("Sharpe", o["sharpe"]), ("Sharpe 90% CI", f"{o['sharpe_ci90'][0]:.2f} .. {o['sharpe_ci90'][1]:.2f}"),
        ("Max drawdown", o["max_drawdown"]), ("Trades", o["n_trades"]), ("Profit factor", o["profit_factor"]),
        ("Win rate", o["win_rate"]), ("Expectancy (R)", o["expectancy_r"]), ("Trials run", rep["n_trials"])])
    gate = rep["gate"]
    gate_html = (f"<p class='{'pass' if gate['passed'] else 'fail'}'><b>Promotion gate: {'PASSED' if gate['passed'] else 'REJECTED'}</b></p>"
                 + "<ul>" + "".join(f"<li>{html.escape(r)}</li>" for r in gate["reasons"]) + "</ul>")
    keys = ["net_return", "sharpe", "sortino", "max_drawdown", "n_trades", "profit_factor", "win_rate", "expectancy", "exposure", "total_costs"]
    comp = [{"run": n, **{k: m.get(k) for k in keys}} for n, m in
            [(rep["strategy"], o), (f"{rep['strategy']} @ {rep['cost_stress']['multiplier']}x costs", rep["cost_stress"])] + list(rep["baselines"].items())]
    body = (f"<p>{html.escape(rep['instrument'])} {rep['timeframe']} &middot; source <b>{html.escape(rep['data_source'])}</b> &middot; "
            f"research {rep['research_period'][0][:10]} to {rep['research_period'][1][:10]} &middot; "
            f"holdout {'untouched' if rep['holdout_period'] else 'none'} &middot; {rep['n_folds']} walk-forward folds</p>"
            f"<div class='grid'>{cards}</div>{gate_html}<h2>Out-of-sample equity</h2>{svg_line({'OOS equity': rep['oos_equity']})}"
            f"<h2>Comparison</h2>{table(comp)}<h2>By regime</h2>{table([{'regime': k, **v} for k, v in rep['by_regime'].items()])}"
            f"<h2>Folds</h2>{table([{**f, 'params': json.dumps(f['params'])} for f in rep['folds']])}"
            f"<p class='muted'>Metrics after spread, slippage, commission and financing. Past simulated performance does not "
            f"predict future results. Number of trials is reported because testing many variants inflates the best result.</p>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page(f"Research report: {rep['strategy']}", mode, body))
    return out
