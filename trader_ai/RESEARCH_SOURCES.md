# Research sources

Every source listed here has its license recorded and how it's used. Reference repositories feed the knowledge base as **Markdown only**, at evidence level `unverified_claim`. Their code is not copied or executed. No secrets, proprietary data or restricted content are included in this project.

Repositories were inspected on 2026-10-29 at their default-branch HEAD.

## The six reference repositories

| Repo | License | What it is | Trainable model? |
|---|---|---|---|
| [Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill) | MIT | 11 Claude skills (SKILL.md prompts plus stdlib Python tools) for day trading US stocks: scanner, news, entry/exit, risk gate, "time machine" Monte Carlo replay, paper broker | No. Agent instructions plus conventional scripts |
| [Jakeschincariol/trader-skill](https://github.com/Jakeschincariol/trader-skill) | MIT | Sibling pack: 13F copycat, news monitor, budget sizing, congressional trades (PTR) tracker, screener, tax lots, rebalance | No |
| [vongchu/TradingAgents_TauricResearch](https://github.com/vongchu/TradingAgents_TauricResearch) | Apache-2.0 | Fork of TauricResearch TradingAgents (paper: arXiv 2412.20138). LangGraph multi-agent LLM framework with analysts (market, news, sentiment, fundamentals), bull/bear researchers, trader, three risk debaters and a portfolio manager | No. Prompted LLM agents, no training pipeline |
| [staskh/trading_skills](https://github.com/staskh/trading_skills) | MIT | 29 skills plus a Python package/MCP server: technicals, fundamentals, Greeks, spreads, correlation, risk, Nasdaq economic calendar, IBKR portfolio tools | No |
| [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) | MIT | ~94 skills for swing trading and long-term investing: backtest-expert, drawdown-circuit-breaker, macro-regime-detector, news-reaction-failure-analyzer, position-sizer, pre-trade-discipline-gate, edge-pipeline, trader-memory | No. Checklists, prompts and screening scripts |
| [himself65/trade-skills](https://github.com/himself65/trade-skills) | MIT (declared in plugin.json; no LICENSE file) | One options-trading skill with an "Open Knowledge Format" library of 38 pitfalls and case studies | No |

### What was useful, and how it's used
- **TradingAgents.** It confirmed the value of role separation (market, news, macro, risk) and *bounded* debate (`max_debate_rounds: 1` by default). Its date-filtering of data and news to prevent look-ahead bias matches our point-in-time rules. *Adopted:* role-specialised agents with structured outputs, one pass. *Not adopted:* free-form LLM debate deciding trades, because it's hard to test, costly, and its published gains are not established out of sample on costs-adjusted live data. We test any agent layer against a single-strategy baseline instead.
- **day-trader-skill / trader-skill.** Three ideas carried over: the risk check is a rules gate and a PASS is "allowed", not "recommended"; paper is the default; nothing executes without a passing gate. The *time machine* (bootstrap replays of recent bars) is a useful sanity check, but resampling 60 days of bars assumes stationarity, so it's treated as a hypothesis. *Adopted in spirit:* the deterministic risk gate and a paper-first design.
- **tradermonty/claude-trading-skills.** The backtest-expert methodology ("find strategies that break the least"), cost stress, parameter robustness and the drawdown-circuit-breaker concept. Its circuit breaker is advisory, while ours is enforced in code. *Adopted:* 2x cost stress in the promotion gate, persistent kill switch on loss/drawdown limits, failed-test recording (experiment registry).
- **staskh/trading_skills.** Its economic calendar hardcodes FOMC dates because the free Nasdaq feed omits them, which shows that free calendars are incomplete. *Adopted:* we treat Forex Factory as unofficial and flag values for verification against official releases.
- **himself65/trade-skills.** A pitfalls library with case studies. These are anecdotal (single-name options cases), stored as `unverified_claim`, and useful as cautionary retrieval context only.

### Conflicts and unreliable approaches found
- Several skills present win rates or "odds" from small samples (for example 60-day replays) without confidence intervals or cost stress. We require walk-forward OOS, bootstrap CIs and trial counts.
- Some skills compute signals and judge them on the same data, which is in-sample. Our `backtest` command is explicitly labelled in-sample; only `research` produces evidence.
- LLM debate frameworks can manufacture consensus. Our coordinator abstains on material disagreement instead of resolving it rhetorically.
- Different repos use different stop/target conventions and position-sizing formulas. We use one documented convention (risk to stop, including round-trip friction).

## Data and news sources

| Source | Use | Access / terms | Limitations |
|---|---|---|---|
| Yahoo Finance chart API | Historical OHLC (default) | Unofficial, free, no key | May lag, change or rate-limit. FX volume is not meaningful |
| Stooq CSV | Alternative daily OHLC | Free | Daily only. Availability varies |
| Local CSV | Your own licensed data | | You must ensure the data is licensed for your use |
| Forex Factory weekly export (`nfs.faireconomy.media/ff_calendar_thisweek.json`) | Economic calendar (time, impact, forecast, previous, actual) | Public export. Poll sparingly and respect the site's terms | Unofficial. Current week only, so no history. Values must be verified against official releases |
| Federal Reserve, ECB, Bank of England RSS | Official announcements | Public feeds | Headlines and summaries only. Publication can lag the event by minutes |
| OANDA v20 practice API | Demo execution, live quotes | Free practice account, token required | Not yet exercised against a real account |

**This build environment could not reach these market-data and news hosts** (the cloud container's network policy). The providers are implemented and unit-tested with mocked responses. Run `tradeintel data ingest` and `news ingest` on your machine to use them.

## Literature

| Reference | Used for |
|---|---|
| Moskowitz, Ooi & Pedersen (2012), *Time series momentum*, JFE 104(2) | Trend hypothesis (sma_cross, donchian) |
| Hurst, Ooi & Pedersen (2017), *A century of evidence on trend-following investing*, JPM | Trend robustness across regimes |
| Menkhoff, Sarno, Schmeling & Schrimpf (2012), *Currency momentum strategies*, JFE 106(3) | FX-specific momentum evidence |
| Lehmann (1990), *Fads, martingales and market efficiency*, QJE | Short-term reversal (rsi_mean_reversion) |
| Andersen, Bollerslev, Diebold & Vega (2003), *Micro effects of macro announcements*, AER 93(1) | Announcement effects on FX, news blackout overlay |
| Bailey & López de Prado (2014), *The deflated Sharpe ratio*, JPM | Multiple-testing awareness, trial counting |
| Harvey, Liu & Zhu (2016), *…and the cross-section of expected returns*, RFS | Multiple-testing thresholds |
| Lo (2002), *The statistics of Sharpe ratios*, FAJ | Sharpe uncertainty, CI reporting |
| Xiao, Sun, Luo & Wang (2024), *TradingAgents: Multi-Agents LLM Financial Trading Framework*, arXiv 2412.20138 | Multi-agent design reference |

Literature findings are mostly for diversified portfolios and longer samples than a single FX pair provides, so they motivate hypotheses rather than guarantee results here.
