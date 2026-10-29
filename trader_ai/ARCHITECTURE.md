# Architecture

## Design principles
1. **Evidence before execution.** A strategy runs against a demo account only after it passes a documented promotion gate on real data, out of sample.
2. **Deterministic risk is outside the AI.** The risk engine is plain code with hard limits. Agents and LLMs produce *proposals*. They can't see or change risk configuration.
3. **Fail closed.** Missing data, stale quotes, broker errors, unparseable LLM output or disagreement all lead to *no order*.
4. **Everything is recorded.** Experiments (including failures and trial counts), decisions with their inputs, risk verdicts and order results all go to SQLite.
5. **Local-first, portable.** Python, SQLite and static HTML, with no required services. An LLM is optional, either local (Ollama) or hosted.

## Components and data flow

```
                 ┌──────────────┐   validate    ┌───────────┐
 Yahoo/Stooq/CSV │ data.providers├─────────────►│ BarStore  │ (provenance, hash)
                 └──────────────┘               └─────┬─────┘
 RSS (Fed/ECB/BoE)┌────────────┐ sanitize/dedup ┌──────┴─────┐
 FF calendar ────►│news.sources├───────────────►│ NewsStore  │ (revisions, source health)
                  └────────────┘                └──────┬─────┘
                                                       │ analyzer: facts (code) + interpretation (rules ± LLM)
 knowledge/ + refs ─► kb.ingest ─► KnowledgeBase (FTS5, source, license, evidence level)
                                                       │
 RESEARCH PATH                                         ▼
   strategies.library ─► backtest.walkforward ─► metrics/regimes ─► promotion gate ─► Registry + HTML report
   models.forecast (walk-forward vs baselines) ─► ModelRegistry (versioned, accept/reject)
   news.features.news_ablation (with vs without news filter)

 DECISION PATH (run-once / service)
   bars ─► strategy signal ─► agents.coordinator (market, news, quant, adversarial; one pass)
        ─► Decision (enter/exit/hold/abstain) ─► DecisionLog
        ─► OrderIntent ─► risk.RiskEngine (hard limits, kill switch) ─► broker (paper | OANDA practice)
        ─► audit table ─► dashboard
```

## Key decisions

| Decision | Choice | Why |
|---|---|---|
| Initial market | EURUSD daily (configurable) | Most liquid FX pair, low costs, ties directly to the Forex Factory calendar. Daily bars are available free and are less sensitive to execution latency |
| Language / storage | Python 3.11+, SQLite (WAL), static HTML | Runs anywhere, with no services to operate. Easy to move to a VPS |
| Backtester | Own bar-by-bar engine | Explicit, testable fill model (next-bar open, spread, slippage, gap-through stops, financing). Libraries such as vectorbt and backtrader hide fill assumptions or add heavy dependencies |
| Validation | Rolling walk-forward plus an untouched 20% holdout, used once | Out-of-sample by construction. Holdout reuse is refused by the registry |
| Knowledge retrieval | SQLite FTS5 / BM25 | No embedding model or vector DB needed. Results are deterministic and inspectable. Embeddings can be added if retrieval quality proves insufficient |
| LLM role | Interpretation and explanation only | Fine-tuning is not justified until a measurable task and labelled dataset exist (see PROJECT_PLAN) |
| Quant model | Regularised logistic regression vs naive baselines | Simple, fast and honest. More complex models must beat it first |
| Multi-agent | Single-pass deterministic agents, abstain on objections | Avoids endless debates and invented consensus. Compared against the single-strategy baseline (`mode="single"`) |
| Demo broker | OANDA v20 practice API | Free practice accounts, a REST API, FX-native. The live host is not reachable from the client class |

## Leakage controls
- Indicators are causal. Tests assert that signals don't change when future bars are removed.
- Signals at bar *t* fill at the open of *t+1*.
- Walk-forward parameters are selected on train only. Test windows start flat.
- Regime labels use expanding (not full-sample) thresholds.
- Event features use scheduled times, which are known in advance, never actual values before release.
- Model features at *t* use bars ≤ *t*. The label is the next bar's direction, and train and test are split chronologically.

## Security
- External text (news, calendar, repo Markdown) is sanitized, truncated, marked as possible injection when suspicious, and passed to LLMs only inside `<data>` tags, with instructions to treat it as data.
- LLM output is parsed into a fixed schema. Unknown keys are dropped.
- Reference repositories are cloned and read as Markdown only. Their code is never imported or executed.
- Secrets are read only from the environment. `.env` is git-ignored.
- Model artifacts are Python pickles created locally. Never load model files from untrusted sources.

## Deployment
Local and VPS use the same code. To deploy on a VPS:
1. `git clone`, then `python3 -m venv .venv && .venv/bin/pip install -e .`
2. Copy `.env` (permissions 600) and `config/local.yaml` to the server.
3. Run `tradeintel data ingest` and `tradeintel news ingest` on a schedule (cron or systemd timers).
4. Run the decision loop as a service. For example, with systemd:
   ```ini
   [Service]
   WorkingDirectory=/opt/trader_ai
   EnvironmentFile=/opt/trader_ai/.env
   ExecStart=/opt/trader_ai/.venv/bin/tradeintel service --interval 900
   Restart=on-failure
   ```
5. Monitor `var/logs/tradeintel.log` (JSON lines) and `tradeintel dashboard`. Add an external heartbeat check.

A `Dockerfile` is included but has not been built or tested yet. Alerting (Telegram or e-mail) is planned; see PROJECT_PLAN.
