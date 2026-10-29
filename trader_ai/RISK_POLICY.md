# Risk policy

The risk engine (`src/tradeintel/risk/engine.py`) is deterministic and independent of every AI component. It evaluates every order intent. A rejection can't be overridden by any strategy, model, agent, news item or retrieved document. Configuration changes require editing `config/` (or env vars) and restarting. Nothing at runtime writes risk configuration.

## Default limits (starting assumptions, `config/default.yaml`)

| Control | Default | Effect when breached |
|---|---|---|
| Max risk per trade (entry→stop, account ccy) | 0.5% equity | reject |
| Max aggregate open risk | 1.5% equity | reject. A position without a stop counts as infinite risk |
| Max daily loss (vs day-start equity) | 2% | reject **and engage kill switch** |
| Max drawdown (vs high-water mark) | 10% | reject **and engage kill switch** |
| Max leverage (total notional / equity) | 5x | reject |
| Max units per position | 500,000 | reject |
| Positions per instrument | 1 | reject |
| Correlated exposure (same direction on one currency) | 2 positions | reject |
| Max spread | 3 pips | reject |
| Quote staleness | 30 s | reject |
| Price consistency (order vs quote) | 1% | reject |
| Consecutive losses | 5 | reject new entries (pause and review). Counted per closed round trip, judged by the market price when a cycle notices the close (approximate until fills are reconciled). `tradeintel risk reset` clears it |
| Event blackout | ±30 min around high-impact events for either currency (for a stock/ETF: the account currency) | reject |
| Economic calendar unknown | this week's calendar not fetched successfully | reject new entries (fail closed). Cycles try to refresh it first |
| Stop-loss required | yes, on the correct side | reject |
| Duplicate order key | reject (keys persisted in the audit log, survive restart) | reject |
| Account environment | paper or practice only | reject. The pipeline raises on a live account |
| Missing / NaN / non-positive inputs | | reject |

## Kill switch
- File: `var/KILL_SWITCH` (JSON with reason and time). It survives restarts.
- It engages automatically on a daily-loss or drawdown breach, or manually with `tradeintel risk kill --reason ...`. Every decision cycle checks daily loss and drawdown, whether or not it proposes an order. Day-start equity and the high-water mark are recorded on every cycle. It never closes positions by itself.
- `ExecutionPipeline.emergency_stop()` engages it and closes all positions.
- Only a human can clear it: `tradeintel risk reset`, or the app's "Turn off emergency stop" button. Both require typing `RESET` and also clear the losing-streak pause. The app writes the reset to the audit table. The assistant can only propose it; the same typed confirmation still applies. Review the cause first.

## Promotion criteria (placeholders until the target market and risk budget are confirmed)
A strategy may send demo orders only when, for the **exact** instrument, timeframe and parameter set it trades (enforced in `service.strategy_promoted`):
1. a walk-forward on non-synthetic data has a `promotable` verdict **and traded those parameters in every fold** (run it with `--no-optimize` on the live settings), and
2. the single-use holdout (`tradeintel holdout`) with those parameters has verdict `passed`: net return > 0, Sharpe > 0, max drawdown ≤ the OOS limit below.

The walk-forward verdict requires all of:
- at least 60 out-of-sample trades
- OOS Sharpe ≥ 0.5, and a bootstrap 90% CI lower bound > 0
- OOS max drawdown ≤ 15%
- profit factor ≥ 1.1
- beats both the no-trade and buy-and-hold baselines on Sharpe and net return
- still profitable at 2x costs

Unfinished candles (today's bar while its session is still open) are dropped at ingest and again before every decision, so live signals use the same finished bars as backtests.

These thresholds are deliberately modest placeholders, not evidence of profitability. They must be set from your risk budget before demo trading.

## Live trading
Not supported. `execution.allow_live: true` fails config validation. The OANDA client is hard-wired to the practice host and refuses `OANDA_ENVIRONMENT != practice`. The Alpaca client is hard-wired to `paper-api.alpaca.markets` and refuses any account whose number lacks the paper `PA` prefix. For SPY the default `max_leverage` is 1.0 (no margin), and USD high-impact events trigger the blackout. The pipeline raises `BrokerError` if a broker reports a live account. Enabling live trading would need code changes, review and a separate decision. It must never happen silently.
