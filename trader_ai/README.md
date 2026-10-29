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
pytest                          # 88 tests, ~20 s

# Offline: everything works on clearly labelled SYNTHETIC data
tradeintel research sma_cross donchian_breakout rsi_mean_reversion --synthetic
open var/reports/research_sma_cross.html

# Real data (needs internet): EURUSD daily from Yahoo, or --provider stooq / csv
tradeintel data ingest
tradeintel research sma_cross donchian_breakout rsi_mean_reversion
```

## Commands

| Command | What it does |
|---|---|
| `tradeintel data ingest [--provider yahoo\|stooq\|csv\|synthetic]` | Fetch, validate and store bars, with provenance |
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
Use an OANDA practice account: set `OANDA_API_TOKEN`, `OANDA_ACCOUNT_ID` and `OANDA_ENVIRONMENT=practice`, with `execution.broker: oanda`. Orders are sent only when all of these hold:
- `execution.enabled: true`
- the broker reports a non-live account
- the kill switch is clear
- the strategy has a `promotable` verdict on real data
- the risk engine approves the order

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
