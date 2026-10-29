# Experiments

The authoritative record is the `experiments` table in `var/tradeintel.sqlite` (`tradeintel experiments`). It holds every run, including failures, with the data hash, config hash, code version and trial count. This file summarises the notable ones.

**No experiment has been run on real market data yet.** The build environment's network policy blocked Yahoo, Stooq, Forex Factory and central-bank feeds. All results below use **SYNTHETIC** data: a regime-switching random walk with fat tails. They validate the pipeline, not any strategy.

## E1–E3: walk-forward baselines on synthetic EURUSD-like daily data (2026-10-29)
Setup: 2010-01-01 to 2025-12-31 (4,174 business days), 20% holdout untouched, 750/250 walk-forward, 10 folds, parameters re-selected per fold from the grid, costs 1.2 pip spread + 0.5 pip slippage per side + 1% p.a. financing.

| Strategy | Trials | OOS return | OOS Sharpe (90% CI) | Max DD | Trades | PF | 2x-cost return | Gate |
|---|---|---|---|---|---|---|---|---|
| sma_cross | 320 | +6.6% | 0.28 (−0.15 … 0.73) | 4.8% | 31 | 1.82 | +4.2% | rejected |
| donchian_breakout | 200 | −8.0% | −0.46 (−0.87 … 0.01) | 9.5% | 53 | 0.52 | −6.0% | rejected |
| rsi_mean_reversion | 320 | +8.8% | 0.44 (−0.09 … 0.96) | 5.0% | 301 | 1.23 | −2.0% | rejected |
| buy_and_hold (baseline) | — | −29.9% | −0.27 | 44.3% | 10* | | | |
| no_trade (baseline) | — | 0 | 0 | 0 | 0 | | | |

\*Each walk-forward fold starts flat, so buy-and-hold re-enters once per fold.

**Interpretation:** these numbers are consistent with no edge, which is correct for a random walk. Every Sharpe CI includes zero. Two of the strategies "beat" the baselines only because the synthetic path happened to fall over the period. The gate rejected all three: synthetic data, too few trades, a CI that includes zero, and, for RSI, losses at 2x costs. The pipeline behaved as intended.

## E4: direction model (logistic regression) on synthetic data
Walk-forward (750/250): OOS accuracy 0.503, log loss 0.6983 vs a base-rate baseline of 0.6936. **Rejected**: it doesn't beat the naive baseline, and accuracy is within 2 s.e. of 0.5. This is the expected result on a random walk. The model registry kept the version, marked rejected.

## E5: news-filter ablation (sma_cross, synthetic schedule)
A synthetic monthly USD "NFP-like" schedule (first Friday, 12:30 UTC) on synthetic prices, `no_entry` mode. 191 event bars. With the filter: Sharpe 0.19 vs 0.22 without, return 5.4% vs 6.2%, drawdown roughly unchanged. **No conclusion possible.** The events have no causal link to synthetic prices. This run only exercises the harness.

## Next experiments (blocked on running locally with internet access)
1. `tradeintel data ingest` on EURUSD daily (Yahoo, 2010–present), then E1–E3 on real data.
2. Collect a historical high-impact event calendar for USD and EUR (official release calendars, or a licensed source), then rerun the ablation (E5) on real data.
3. Rerun E4 on real data. Expect rejection unless an edge is present.
4. Multi-agent vs single-strategy comparison on the same OOS windows, once historical news exists.
