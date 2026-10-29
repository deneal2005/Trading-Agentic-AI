# Experiments

The authoritative record is the `experiments` table in `var/tradeintel.sqlite` (`tradeintel experiments`). It holds every run, including failures, with the data hash, config hash, code version and trial count. This file summarises the notable ones.

E6–E8 below are the first runs on **real** market data. E1–E5 use **SYNTHETIC** data (a regime-switching random walk with fat tails): they validate the pipeline, not any strategy.

## E6–E8: walk-forward on REAL EURUSD daily (Yahoo, 2026-10-29)
Data: Yahoo `EURUSD=X`, 2010-01-01 to 2026-10-29, 4,261 bars kept, data hash `aab8ce1a6d373fed`. Validation dropped 10 NaN bars and **119 bars with inconsistent OHLC** (about 2.8%; a known Yahoo FX quirk, still to be cross-checked against Stooq). One gap is larger than expected, starting 2025-04-21 (likely the Easter holiday). Research window 2010-01-01 to 2023-06-07, 10 walk-forward folds (OOS 2012-12 to 2023-06). **The holdout (2023-06 to 2026-10) is still untouched.** It was not used, because nothing passed the gate. Same costs as E1–E3. Run on Windows, commit `62dd0f3`.

| Exp | Strategy | Trials | OOS return | OOS Sharpe (90% CI low) | Max DD | Trades | PF | 2x-cost return | Gate |
|---|---|---|---|---|---|---|---|---|---|
| E6 | sma_cross | 320 | +0.9% | 0.06 (−0.37) | 7.9% | 43 | 1.10 | −2.0% | rejected |
| E7 | donchian_breakout | 200 | +0.8% | 0.06 (−0.45) | 5.6% | 39 | 1.09 | −0.7% | rejected |
| E8 | rsi_mean_reversion | 320 | +0.4% | 0.04 (−0.53) | 3.5% | 107 | 1.03 | −1.7% | rejected |
| — | buy_and_hold (baseline) | — | −30.8% | −0.44 | 37.3% | 10* | 0.36 | | |
| — | no_trade (baseline) | — | 0 | 0 | 0 | 0 | | | |

**Interpretation, in plain words:** on 10 years of real out-of-sample EURUSD daily data, none of the three textbook strategies showed an edge. Each made under +1% in total over ten years (effectively zero). Every Sharpe CI includes zero, and all three **lose money when costs double**: the small profit is thinner than realistic trading costs. They "beat" buy-and-hold only because EURUSD fell about 30% over the period, which says something about the period, not the strategy. Running 200–320 parameter trials and still landing at zero suggests simple daily trend and mean-reversion rules on this pair carry no exploitable edge. The gate worked as designed. **Conclusion: no strategy is promotable, and no demo trading is justified on this evidence.**

SMA regime breakdown (OOS bars): its positive pockets are trending, high-volatility regimes (e.g. "down_highvol", Sharpe 0.91 over 283 bars). It bleeds in "flat_lowvol" (1,525 bars), where the market spends most of its time. That is a hypothesis to test (E10), not a finding.

## E11: walk-forward on REAL SPY daily (Yahoo, split/dividend-adjusted, 2026-10-29)
Market switched to SPY for the Alpaca paper account. Data: Yahoo SPY, 2000-01-03 to 2026-10-28, 6,746 bars, none dropped, hash `b9c67a9afdfe4344`. The one gap (2001-09-17) is the post-9/11 market closure. Research window 2000-01 to 2021-06, 18 walk-forward folds (OOS 2002-12 to 2021-06). **The holdout (2021-06 to 2026-10) is untouched.** Costs: 2 cents spread plus 2 cents slippage per side, no commission, no financing (so shorts are slightly flattered).

| Strategy | Trials | OOS return | OOS Sharpe (90% CI) | Max DD | Trades | PF | 2x-cost return | Gate |
|---|---|---|---|---|---|---|---|---|
| sma_cross | 576 | +6.6% | 0.18 (−0.25 … 0.61) | 7.9% | 62 | 1.34 | +6.0% | rejected |
| donchian_breakout | 360 | −0.6% | −0.01 (−0.37 … 0.39) | 7.3% | 96 | 0.98 | −0.3% | rejected |
| rsi_mean_reversion | 576 | −14.8% | −0.53 (−0.90 … −0.16) | 15.7% | 535 | 0.78 | −20.0% | rejected |
| buy_and_hold (baseline) | — | +443.6% | 0.60 | 55.2% | 18* | 6.19 | | |
| no_trade (baseline) | — | 0 | 0 | 0 | 0 | | | |

**Interpretation, in plain words:** on SPY, simply buying and holding beat all three rules on risk-adjusted return (Sharpe 0.60 vs 0.18 at best), although holding meant sitting through a 55% fall (2008). The raw returns aren't comparable: the strategies risk only 0.5% of equity per trade, so they are mostly small or out of the market, while buy-and-hold is 100% invested. Compare the Sharpe ratios.
- **sma_cross** is the only one that survives doubled costs and passes the trade-count and profit-factor checks. But its Sharpe CI includes zero, and it loses clearly to buy-and-hold. Not an edge.
- **donchian_breakout** is zero.
- **rsi_mean_reversion** is reliably *negative*: its whole Sharpe CI is below zero. On SPY it mostly fights the long-run upward drift (it can short) and pays costs on 535 trades. That is a real, if unhelpful, finding: don't run this rule on SPY in this form.

**Conclusion: no strategy is promotable on SPY, and no paper orders are justified.** The Alpaca paper broker can be connected and checked read-only, but `execution.enabled` must stay false.

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

## Next experiments
Each new idea adds trials. Record every run, and treat the trial count as a cost (deflated Sharpe is still to do).
1. **E9 data quality:** ingest Stooq EURUSD, compare it with Yahoo bar-by-bar, and find out why 119 bars fail OHLC checks. Rerun E6–E8 on Stooq. If the verdicts change, the data was the problem.
2. **E10 regime filter (one pre-registered hypothesis):** SMA cross trades only when realized volatility is above its rolling median. Fix the rule *before* running it, and run it once.
3. ✅ **E11 SPY** (above). Follow-ups: **E12** cross-check SPY Yahoo vs Alpaca bars (2016+). **E13** one pre-registered *long-only* trend filter (hold SPY only above its 200-day SMA, flat otherwise). The question is whether it gets a Sharpe near buy-and-hold with a much smaller drawdown. That is the honest bar on an index with upward drift.
4. Collect a historical high-impact event calendar for USD and EUR (official release calendars, or a licensed source), then rerun the ablation (E5) on real data.
3. Rerun E4 on real data. Expect rejection unless an edge is present.
4. Multi-agent vs single-strategy comparison on the same OOS windows, once historical news exists.
