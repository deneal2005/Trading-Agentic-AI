"""Promotion gate: a strategy advances only when every documented criterion
passes. Reasons are returned so rejections are explainable."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import PromotionCfg


@dataclass
class GateResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)


def evaluate_promotion(oos: dict, baselines: dict[str, dict], stressed: dict | None, cfg: PromotionCfg,
                       data_is_synthetic: bool, dsr: dict | None = None) -> GateResult:
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    def check(name: str, ok: bool, why: str) -> None:
        checks[name] = ok
        if not ok:
            reasons.append(why)

    check("real_data", not data_is_synthetic, "results are on SYNTHETIC data and cannot be promoted")
    check("min_trades", oos["n_trades"] >= cfg.min_trades, f"only {oos['n_trades']} OOS trades (< {cfg.min_trades})")
    check("oos_sharpe", oos["sharpe"] >= cfg.min_oos_sharpe, f"OOS Sharpe {oos['sharpe']:.2f} < {cfg.min_oos_sharpe}")
    lo = oos.get("sharpe_ci90", [float('nan')])[0]
    check("sharpe_ci_positive", lo == lo and lo > 0, f"Sharpe 90% CI lower bound {lo:.2f} is not > 0")
    check("max_drawdown", oos["max_drawdown"] <= cfg.max_oos_drawdown,
          f"OOS max drawdown {oos['max_drawdown']:.1%} > {cfg.max_oos_drawdown:.0%}")
    check("profit_factor", oos["profit_factor"] >= cfg.min_profit_factor,
          f"profit factor {oos['profit_factor']:.2f} < {cfg.min_profit_factor}")
    for b in cfg.must_beat_baselines:
        if b in baselines:
            ok = oos["sharpe"] > baselines[b]["sharpe"] and oos["net_return"] > baselines[b]["net_return"]
            check(f"beats_{b}", ok, f"does not beat baseline {b} on Sharpe and net return")
        else:
            check(f"beats_{b}", False, f"baseline {b} was not evaluated")
    if stressed is not None:
        check("cost_stress", stressed["net_return"] > 0,
              f"unprofitable at {cfg.max_cost_stress_multiplier_loss}x costs (net {stressed['net_return']:.1%})")
    else:
        check("cost_stress", False, "cost stress test not run")
    if dsr is not None:  # dsr = {"value": probability, "n_trials": variations ever tried}
        p = dsr["value"]
        check("deflated_sharpe", p == p and p >= cfg.min_deflated_sharpe,
              f"deflated Sharpe {p:.2f} < {cfg.min_deflated_sharpe}: after {dsr['n_trials']} variations tried, the result may be luck")
    return GateResult(all(checks.values()), reasons, checks)
