# Project plan and progress

Legend: ✅ done and tested · 🟡 partial · ⏳ planned · ⛔ blocked

## Starting assumptions (all configurable, reversible)
- **Market:** **SPY** (US ETF), **daily** bars, account currency USD. Switched from EURUSD on 2026-10-29 because the user's demo account is Alpaca, which has no forex. EURUSD still works via config (see the comment in `config/default.yaml`).
- **Demo broker:** Alpaca *paper* (`execution.broker: alpaca`). OANDA practice is still available for FX. The internal paper broker is the default.
- **Hardware assumed:** CPU only (no GPU detected in the build environment: 4 cores, 15 GB RAM). Everything runs on CPU. A 7B local LLM via Ollama is feasible on a typical desktop but optional.
- **Risk budget:** 0.5% per trade, 2% daily loss, 10% drawdown. Placeholders until you confirm your budget.

## Phase status

| Phase | Status | Notes |
|---|---|---|
| 1. Foundation | ✅ | Package, config validation (YAML + env), JSON logging, CLI, tests |
| 2. Market data | ✅ / ⛔ | Yahoo, Stooq, CSV and synthetic providers, validation, SQLite with provenance. ⛔ Real downloads blocked in the build container (network policy). They need to run locally |
| 3. News intelligence | 🟡 | RSS and Forex Factory ingestion, sanitizing, dedup, revisions, source health, facts-vs-interpretation analyzer, optional LLM with schema validation, point-in-time features, ablation harness. ⏳ Historical event archive, official-release cross-checks, latency monitoring of the release-to-ingest delay |
| 4. Knowledge base | ✅ | Strategy cards (11 required fields), concept notes, evidence levels, FTS5 search, ingestion of the 6 reference repos with license and source attribution. ⏳ Rank boost for verified local knowledge over repo text |
| 5. Baseline strategies | ✅ | SMA cross, Donchian breakout, RSI mean reversion, buy-and-hold, no-trade. Causality tests |
| 6. Backtesting and validation | ✅ | Next-bar fills, spread, slippage, gaps, financing, risk sizing. Walk-forward, single-use holdout, baselines, 2x cost stress, regime breakdown, bootstrap Sharpe CI, experiment registry, promotion gate, HTML report. ⏳ Deflated Sharpe, parameter-stability maps, event-time spread widening |
| 7. AI decision engine | 🟡 | Bounded agents (market, news, quant, adversarial) plus coordinator with abstention and a decision log. Direction model with walk-forward vs baselines and a versioned registry. ⏳ LLM narrative in decisions, RAG context into agents, multi- vs single-agent evaluation (needs historical news) |
| 8. Risk engine | ✅ | All controls in RISK_POLICY.md, persistent kill switch, duplicate protection across restarts |
| 9. Demo execution | 🟡 | Paper broker (persisted), audited pipeline, dry-run default, promotion requirement, live refusal. OANDA practice and Alpaca paper clients written, plus a read-only `broker-check`. ✅ Alpaca paper verified read-only on 2026-10-29 (`broker-check`: paper account recognised, $1M paper equity, live SPY quote). The free IEX quote is noisy: 3c, 73c and 8c SPY spreads within 4 s at 09:35 ET, and live SIP isn't permitted on the free plan. The Alpaca broker therefore takes 3 IEX snapshots and keeps the tightest valid one. That is still an upper bound on the market-wide spread, so the 3-cent limit is unchanged. Wide spreads in the first minutes after the open will still (correctly) be rejected. ⛔ Order submission is still untested, and OANDA is untested. Alpaca fills asynchronously, so orders come back `pending` and stops are sent as GTC OTO legs. ⏳ Partial-fill handling beyond FOK, reconciliation job, broker-side stop verification |
| 10. Monitoring and improvement | 🟡 | Trading OS UI (`tradeintel ui`): local web dashboard over the real backend, an agent trace inspector, and an in-app companion that answers from records and only proposes actions. Static dashboard, audit trail, experiment registry, model registry. ⏳ Drift and calibration monitoring on live-demo results, alerts (Telegram/e-mail), heartbeat. A Dockerfile exists but is unbuilt and untested |

## Audit
The full platform audit of 2026-10-29 is in [audit/AUDIT_2026-10-29.md](audit/AUDIT_2026-10-29.md). Verdict: **not ready for live trading**. The six P1 risk-control defects (F-01 to F-06) are fixed with regression tests (`tests/test_p1_fixes.py`). Before paper orders: a supervised paper-order lifecycle test; P2 items F-07 to F-12 remain.

## Known limitations
- First real-data runs (E6–E8 EURUSD daily, E11 SPY daily): all three baseline strategies rejected on both. No edge after costs on EURUSD, and none beats buy-and-hold on SPY (see EXPERIMENTS.md).
- Research strategies (E13, E15, E16): the engine sizes a position only at entry. So volatility targeting (Moreira & Muir) can't be tested as a strategy until rebalancing is added; it's used only as a model feature. Overnight-drift strategies need close fills, which the engine doesn't model.
- Agents (found in the UI audit): the news analyst matches events by currency-pair letters, so for SPY it sees no events (the risk engine's USD blackout still applies). The quant analyst always abstains because the live cycle passes no model probability. Neither records durations or tool calls.
- UI: no human-approval order queue, no order cancel or position close, no runtime risk editing (by policy), no voice, no desktop always-on-top window (it's a web app), no custom layouts. No scheduler heartbeat, so a running `service` can't be detected.
- Equity costs assume no financing, so short positions don't pay a borrow fee in backtests. Positions aren't reconciled against Alpaca fills yet (orders return `pending`).
- Cross-currency P&L conversion isn't implemented. The account currency must be one side of the pair (fine for EURUSD/USD).
- Daily bars are too coarse to model announcement-time slippage. The backtest doesn't widen spreads around events.
- The paper broker opens from flat only (no netting/partial closes) and assumes a USD-quoted pair for unrealized P&L.
- The consecutive-loss counter judges closed trades by the market price at the next cycle, not by actual fills (needs fill reconciliation, audit F-07).
- Each walk-forward fold starts flat, which slightly understates long-hold strategies (visible in buy-and-hold's trade count).
- Forex Factory's export only covers the current week. Historical calendar data must come from elsewhere.

## Next concrete tasks
1. ✅ Real EURUSD research run (E6–E8). Next: the Stooq cross-check (E9) and `news ingest` on the live feeds.
2. Build a historical event calendar (official BLS/Fed/ECB/Eurostat release schedules) and run the news ablation on real data.
3. Wire the consecutive-loss counter and P&L tracking from closed trades into the service state.
4. Add deflated Sharpe and parameter-neighbourhood robustness to the promotion gate.
5. Run `tradeintel broker-check --broker alpaca` against the real Alpaca paper account (read-only), then add a fill-reconciliation step for `pending` orders.
6. Add alerts and a heartbeat, then build and test the Dockerfile, ahead of the VPS move.
