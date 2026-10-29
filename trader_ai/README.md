# TradeIntel: local-first AI trading research and demo-trading system

TradeIntel is a modular research platform. It ingests market data, news and economic events. It tests strategies with walk-forward validation and realistic costs, and gives you an evidence trail for every decision. A deterministic risk engine sits between every decision and any order, and no AI component can override it.

> **Status: research foundation (v0.1).** Nothing here has shown an edge on real data yet. Live-money trading is refused by code. Order submission (paper or OANDA *practice*) is off by default, and it requires a strategy that has passed the promotion gate on real data. See [PROJECT_PLAN.md](PROJECT_PLAN.md) for what is done, partial, planned and blocked.

This is research software, not financial advice. Most retail trading strategies lose money after costs.

## Quick start

```bash
cd trader_ai
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # optional: LLM / broker settings
pytest                          # 98 tests, ~20 s

# Offline: everything works on clearly labelled SYNTHETIC data
tradeintel research sma_cross donchian_breakout rsi_mean_reversion --synthetic
open var/reports/research_sma_cross.html

# Real data (needs internet): SPY daily from Yahoo, or --provider alpaca / stooq / csv
tradeintel data ingest
tradeintel research sma_cross donchian_breakout rsi_mean_reversion
```

## Commands

| Command | What it does |
|---|---|
| `tradeintel data ingest [--provider yahoo\|stooq\|alpaca\|csv\|synthetic]` | Fetch, validate and store bars, with provenance |
| `tradeintel broker-check [--broker paper\|oanda\|alpaca]` | Read-only: shows account, positions and a live quote. Never places, changes or cancels orders |
| `tradeintel ui [--port 8765] [--no-browser]` | Opens the Trading OS: local dashboard and AI companion in your browser (see below) |
| `tradeintel backtest <strategy> [--synthetic]` | One full-sample backtest. In-sample, for inspection only |
| `tradeintel research <strategy...> [--synthetic]` | Walk-forward OOS test, baselines, 2x cost stress, regime breakdown, promotion gate, HTML report and registry entry |
| `tradeintel holdout <strategy> --params '{...}'` | One-time test of fixed parameters on the untouched holdout. A second use is refused |
| `tradeintel model-eval [--synthetic]` | Walk-forward direction model vs naive baselines. Versioned, and rejected unless it beats them |
| `tradeintel news ingest` | Central-bank RSS plus the Forex Factory weekly calendar, with dedup, revision tracking and source health |
| `tradeintel news analyze` | Facts and interpretation for high-impact events (rules, with an optional LLM) |
| `tradeintel news ablation <strategy> [--events-csv f]` | Same strategy and folds, with and without the news filter |
| `tradeintel kb build [--with-repos]` / `kb search <query>` | Knowledge base: strategy cards, concepts and the 6 reference repos, all source-attributed |
| `tradeintel run-once` / `service --interval 900` | Decision cycle: data, signal, agents, risk, (dry-run) order, audit |
| `tradeintel risk status \| kill \| reset` | Kill switch. `reset` requires typing RESET |
| `tradeintel dashboard` | Writes `var/reports/dashboard.html` (status, feeds, events, decisions, audit, positions, experiments) |
| `tradeintel experiments` | Lists every recorded experiment, including failures |

## Configuration

`config/default.yaml` holds every setting and documents the assumptions. You can override it in two ways:
- `config/local.yaml`, which is git-ignored
- environment variables such as `TRADEINTEL_RISK__MAX_DAILY_LOSS=0.01` or `TRADEINTEL_LLM_PROVIDER=ollama`

Invalid configuration fails at startup. That includes `allow_live: true`, risk limits outside sane bounds, and a per-trade risk larger than the aggregate risk.

Secrets come only from the environment (`.env`, never committed). See `.env.example`.

### Local LLM (optional)
The system runs fully without an LLM, using rule-based analysis. To use one:
- Local: install [Ollama](https://ollama.com), `ollama pull qwen2.5:7b-instruct`, then set `TRADEINTEL_LLM_PROVIDER=ollama`.
- Hosted: set `TRADEINTEL_LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`.

LLM output is validated against a schema. It can't raise confidence above what the facts allow, and it never reaches the risk engine.

### Demo broker (later)
**Alpaca paper (US stocks/ETFs, the default market SPY):**
1. In the Alpaca dashboard, switch to **Paper Trading** and generate API keys.
2. Open `trader_ai\.env` in Notepad (copy it from `.env.example` first if it doesn't exist) and fill in `APCA_API_KEY_ID=` and `APCA_API_SECRET_KEY=`. Never paste keys anywhere else.
3. Run `tradeintel broker-check --broker alpaca`. It only reads. You should see `"environment": "practice"` and a SPY quote.

The client talks only to `paper-api.alpaca.markets` and refuses any account whose number lacks the paper `PA` prefix.

**OANDA practice (forex):** set `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` and `OANDA_ENVIRONMENT=practice`, with `execution.broker: oanda`.

For either broker, orders are sent only when all of these hold:
- `execution.enabled: true`
- the broker reports a non-live account
- the kill switch is clear
- the strategy has a `promotable` verdict on real data
- the risk engine approves the order

## Trading OS (dashboard + companion)

```
tradeintel ui
```

This opens `http://127.0.0.1:8765` in your browser. Stop it with Ctrl+C in the terminal. It's a local web app served by the Python package, with no Node or build step. It shows only real data from the database, config and broker; anything unavailable is labelled as such.

| Screen | What it shows (source) |
|---|---|
| Overview | Account strip (broker), SPY chart with strategy signals and recorded decisions, agent status, watchlist, risk utilisation, alerts and events |
| Markets | Candles, volume and SMA 50/200 from stored bars; signal and decision markers; position lines; live quote and data freshness; why the agent watches the symbol |
| Execution | Environment, execution policy, positions and open orders (broker), kill switch, the full order audit with risk checks |
| Agents | The real pipeline as a graph (4 rule-based agents, coordinator, risk engine, execution pipeline), each with its recorded outputs and diagnostics, plus an 11-step trace inspector per recorded decision |
| Risk Center | Kill switch, limit utilisation, configured limits, event blackout, risk rejections |
| Research | Strategies, the experiment registry with gate reasons and warnings, in-sample backtests vs buy-and-hold, walk-forward research runs |
| News & Events, Activity & Logs, Settings | Calendar, headlines and feed health; a filterable timeline of decisions, orders and logs; config, secret presence (never values) and the list of unsupported features |

**Companion:** the orb in the corner (drag, pin, hide; Ctrl+. opens it). It answers from records ("explain current positions", "why is the agent waiting?", "summarize today", "why was that trade rejected?"). Answers are labelled *from records*, *LLM-generated* (only if `llm.provider` is set) or *backend result*. For anything that changes state (kill switch, decision cycle, research, data refresh) it only **proposes**; the action runs after you confirm, and it reports what the backend returned. It lives inside the browser tab; it doesn't float over other Windows apps.

**Safety:** the server listens on 127.0.0.1 only and checks the Host header. Actions need a per-launch token and the same origin. There is no endpoint that enables execution, edits risk limits, clears the kill switch, cancels orders or closes positions. The risk engine and `execution.enabled` decide everything, exactly as from the CLI. Command palette: Ctrl+K.

## Layout

```
src/tradeintel/
  config.py, logging_setup.py, cli.py, service.py
  data/        providers (yahoo, stooq, csv, synthetic), validation, SQLite store
  news/        sources, sanitizer, dedup, store, analyzer, point-in-time features + ablation
  kb/          knowledge base (FTS5/BM25), ingestion of cards/concepts/repos
  strategies/  causal indicators, strategy interface, library (+ baselines)
  backtest/    engine, metrics, walk-forward, research pipeline
  experiments/ registry, promotion gate
  models/      LLM clients, direction model + model registry
  agents/      bounded multi-agent coordinator + decision log
  risk/        deterministic risk engine + persistent kill switch
  execution/   broker interface, paper broker, OANDA practice, audited pipeline
  reporting/   HTML report + dashboard
knowledge/     strategy cards (YAML), concept notes, reference source list
tests/         unit + integration tests
var/           runtime data (git-ignored): SQLite DB, reports, models, logs, kill switch
```

## Moving to a VPS later
Nothing is tied to this machine. All paths come from config, state lives in `var/`, secrets come from the environment, and the service loop is restart-safe. It uses a file lock, idempotent order keys and a kill switch that persists. The steps are in [ARCHITECTURE.md](ARCHITECTURE.md#deployment).
