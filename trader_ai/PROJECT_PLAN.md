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
| 9. Demo execution | 🟡 | Paper broker (persisted), audited pipeline, dry-run default, promotion requirement, live refusal. OANDA practice and Alpaca paper clients written, plus a read-only `broker-check`. ⛔ Neither has been tested against a real account yet (Alpaca: add keys to `.env`, then run `tradeintel broker-check --broker alpaca`). Alpaca fills asynchronously, so orders come back `pending` and stops are sent as GTC OTO legs. ⏳ Partial-fill handling beyond FOK, reconciliation job, broker-side stop verification |
| 10. Monitoring and improvement | 🟡 | Static dashboard, audit trail, experiment registry, model registry. ⏳ Drift and calibration monitoring on live-demo results, alerts (Telegram/e-mail), heartbeat. A Dockerfile exists but is unbuilt and untested |

## Known limitations
- First real-data runs (E6–E8 EURUSD daily, E11 SPY daily): all three baseline strategies rejected on both. No edge after costs on EURUSD, and none beats buy-and-hold on SPY (see EXPERIMENTS.md).
- Equity costs assume no financing, so short positions don't pay a borrow fee in backtests. Positions aren't reconciled against Alpaca fills yet (orders return `pending`).
- Cross-currency P&L conversion isn't implemented. The account currency must be one side of the pair (fine for EURUSD/USD).
- Daily bars are too coarse to model announcement-time slippage. The backtest doesn't widen spreads around events.
- The paper broker opens from flat only (no netting/partial closes) and assumes a USD-quoted pair for unrealized P&L.
- Consecutive-loss tracking in the live cycle reads a stored counter that the paper broker doesn't yet update.
- The holdout-before-demo step is documented but not enforced in code.
- Each walk-forward fold starts flat, which slightly understates long-hold strategies (visible in buy-and-hold's trade count).
- Forex Factory's export only covers the current week. Historical calendar data must come from elsewhere.

## Next concrete tasks
1. ✅ Real EURUSD research run (E6–E8). Next: the Stooq cross-check (E9) and `news ingest` on the live feeds.
2. Build a historical event calendar (official BLS/Fed/ECB/Eurostat release schedules) and run the news ablation on real data.
3. Wire the consecutive-loss counter and P&L tracking from closed trades into the service state.
4. Add deflated Sharpe and parameter-neighbourhood robustness to the promotion gate.
5. Run `tradeintel broker-check --broker alpaca` against the real Alpaca paper account (read-only), then add a fill-reconciliation step for `pending` orders.
6. Add alerts and a heartbeat, then build and test the Dockerfile, ahead of the VPS move.
