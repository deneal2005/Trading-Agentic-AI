---
evidence_level: unverified_claim
source_date: 2026-10-30
---
# Technical Investigation: Intelligence Engines of Trade Ideas, TrendSpider, Tickeron, QuantConnect

Date: 2026-10-30 · Scope: public, legally accessible evidence only · Citation keys → [references.md](references.md)

Companion documents: [platform_comparison.md](platform_comparison.md) · [ai_architecture.md](ai_architecture.md) · [training_pipeline.md](training_pipeline.md) · [data_pipeline.md](data_pipeline.md) · [algorithm_reconstruction.md](algorithm_reconstruction.md) · [implementation_roadmap.md](implementation_roadmap.md)

## Evidence labels

| Label | Meaning |
|---|---|
| **VERIFIED** | Stated in the vendor's technical documentation, source code, or patent. Vendor *marketing* that only describes the vendor's own process is marked VERIFIED (vendor claim), meaning the vendor says it and nobody has audited it. |
| **STRONGLY INFERRED** | Follows from several independent technical indicators. |
| **HYPOTHESIS** | Technically plausible, but not supported by evidence. |
| **UNKNOWN** | Not enough public information. |

**Performance caveat.** This report does not treat any vendor win rate, confidence score, or backtest return as verified profitability. None of the three closed platforms publishes audited, out-of-sample, cost-inclusive live results.

---

## 0. Executive summary

1. **Only one of the four platforms has a fully inspectable engine.** QuantConnect's LEAN is open source (Apache-2.0, ~22k stars) [QC-1]. Its architecture is VERIFIED down to the source code. The other three are closed SaaS products, and their "AI" can only be described through documentation, a patent, and marketing.
2. **Trade Ideas "Holly" is a strategy-selection system, not a predictive neural model.** Its documentation describes a library of 60+ hand-written intraday scan rules. Each night it backtests each rule, adjusts filter parameters one at a time, keeps the rules that clear an undisclosed "high-probability cut", and emits entry, stop, and target signals the next day [TI-1, TI-2]. That is nightly grid or coordinate-search optimization plus a survival filter: a statistical/quant technique. No ML model is documented.
3. **TrendSpider has two separate engines.**
   - (a) A **deterministic geometric engine** for trendlines, patterns, multi-timeframe overlays, and alerts, covered by US patent 11,935,118. It generates every candidate line, filters by relevance and proximity, and uses touch, bounce, and break triggers with tolerance buffers [TS-5].
   - (b) A user-driven **classical supervised ML lab**: Random Forest, KNN, Naive Bayes, and Logistic Regression on indicator features. The label is a "take-profit-before-stop-loss within N candles" outcome [TS-1, TS-2, TS-3]. This is the most transparent genuine ML among the closed platforms.
4. **Tickeron discloses the least.** Its documented outputs are historical frequency statistics: "k of n past occurrences" odds of success, plus percentage confidence levels derived from history [TK-1, TK-2]. It says it uses "neural networks", and calls its "Financial Learning Models" regime-conditional activation of known models [TK-2, TK-5]. Architecture, features, training, and validation are UNKNOWN. One of its own published examples has an arithmetic inconsistency (50/62 cases reported as 76%) [TK-1].
5. **All three closed platforms share one core pattern:** *rules or models → historical simulation → keep what scored well recently → emit signals with a probability-like number*. Without strict out-of-sample discipline, that loop is exactly what produces selection bias and backtest overfitting [ACAD-2, ACAD-3]. None of the three documents purging, embargoing, deflated Sharpe, or multiple-testing corrections.
6. **An independent replica is feasible with open-source tools** for every architectural layer. What you cannot replicate cheaply is **data**: survivorship-free tick/quote history, corporate actions, and fundamentals. The platforms' edge is mostly data plumbing plus curated rule libraries, not unreproducible AI.

---


## 2. Trade Ideas: Holly AI

### 2.1 What is documented

| Finding | Label | Source |
|---|---|---|
| Holly uses "over 60 strategies". Each is a named, rule-based intraday scan with price, float, volume, and time-of-day filters (e.g. *Breakout*: crossing resistance on high relative volume, $10–150 stocks; *Volume Doesn't Lie*: up ≥4% on ≥2× volume; *Power Hour*: last hour only). | VERIFIED | TI-2 |
| Strategy parameters are proprietary, and users cannot customize them. | VERIFIED | TI-2 |
| "Every night she backtests each one on market history and then optimizes it", adjusting filter values one setting at a time and removing filters that still underperform. | VERIFIED (vendor claim) | TI-1 |
| "Only the strategies that make the high-probability cut generate signals in the next market session." The threshold is not disclosed. | VERIFIED (threshold UNKNOWN) | TI-1, TI-4 |
| Each signal = symbol + entry + stop + target. The stop is "usually set about 3% from entry". Gaps can exceed it. Re-entry may be offered after a stop-out. | VERIFIED | TI-1, TI-4 |
| Exit logic by risk mode. *Moderate*: stop or time stop. *Conservative*: stop, target, trailing "profit save", "reduce risk" on unexpected price action, or time stop. *Aggressive*: no stop (presentation only). | VERIFIED | TI-3 |
| Holly's tracked trades close by the end of the day (intraday). | VERIFIED | TI-1, TI-7 |
| A 2016 vendor post lists 12 strategies selected overnight, each with a backtested Win % of 51.7–70.0. | VERIFIED (historical vendor claim) | TI-6 |
| OddsMaker, the user-facing backtester, takes starting equity, $ or share sizing, per-trade commission, and per-share slippage. It reports profit factor (gross win / abs gross loss), win rate, average winner and loser, return, projected annual return, max buying power, drawdown, and an equity curve. | VERIFIED | TI-5 |
| Holly's selection backtest reuses the OddsMaker-style engine over a short recent window. | STRONGLY INFERRED: same vendor, same metrics, and reviewers describe "recent months" of data | TI-1, TI-5, TI-S1 |
| Selection thresholds of ~60% win rate and 2:1 reward-to-risk. | HYPOTHESIS: widely repeated, but no primary source found | — |
| Any neural network, gradient boosting, or learned model inside Holly. | UNKNOWN. Nothing is documented, and the described loop needs none. | — |
| Holly variants (Grail / 2.0 / Neo): their technical differences. | UNKNOWN. Secondary sources contradict each other. | TI-S2 |

### 2.2 Reconstructed mechanism (STRONGLY INFERRED from TI-1/2/3/5)

```
nightly:
  for strategy in library (≈60 rule templates with tunable filters):
      for filter in strategy.filters:                    # "one setting at a time"
          try alternative values → re-run backtest on recent history
          keep value if metric improves; drop filter if it never helps
      if score(strategy) ≥ cut: activate for tomorrow
intraday:
  real-time alert server evaluates active strategies on streaming trades/quotes
  on match → emit {symbol, entry=last, stop≈entry*(1-0.03) or rule-based, target, time-stop}
  manage exits per risk mode (stop / target / trailing / time / "reduce risk")
```

**Classification:** coordinate-descent parameter optimization plus a performance-threshold survival filter. That is **conventional quantitative optimization**, which the vendor markets as "AI". Its weakness is selection bias: picking the best of ~60 strategies × many parameter settings on a short window inflates expected performance. Nothing public shows a correction for this.

### 2.3 Data and infrastructure
- **Real-time US-equity trade and quote stream across a broad universe.** STRONGLY INFERRED: the scans use relative volume, float, gaps, and time-of-day.
- **Exact feed vendor, storage, latency, compute: UNKNOWN.**
- **Auto-trading through a broker (Brokerage Plus / IBKR).** VERIFIED (vendor), per reviews and product pages.

---

## 3. TrendSpider

### 3.1 Engine A: automated technical analysis (deterministic)

| Finding | Label | Source |
|---|---|---|
| Patent US 11,935,118 (TrendSpider LLC; inventors D. Ushman, R. Lagutin; priority 2019-02-21; granted 2024-03-19): retrieve OHLCV → build charts with indicators → **automatically annotate by filtering all possible trend lines by relevance, proximity to current price, and user criteria** → evaluate alert triggers in real time → notify. | VERIFIED | TS-5 |
| Trigger semantics: *touch*, *bounce* (touch, then a retracing candle closes), *break through* (passes and holds). Each has a **sensitivity buffer** (value ± tolerance) and a **confirmation timeframe**. | VERIFIED | TS-5 |
| **Dynamic alerts** follow a trendline's slope or an indicator's value. **Multi-factor alerts** are logical trees (And / Or / If-Then) with "X candles ago" offsets that can mix timeframes (e.g. 60-min SMA50 vs daily SMA50). | VERIFIED | TS-5 |
| The engine downloads extra history to warm up long-lookback indicators. | VERIFIED | TS-5 |
| Volume-at-price candle construction ("Raindrop"-style): split each period into halves, compute VWAP per half, color by the relationship between the two VWAPs, and set width by volume at price. | VERIFIED (patent text; the patent does not use the name "Raindrop") | TS-4, TS-5 |
| Trendlines are computed only from bars inside the user's "Truth in Analysis" window. | VERIFIED | TS-6 |
| MTFA projects higher-timeframe lines, indicators, and patterns onto a lower-timeframe chart. | VERIFIED | TS-7 |
| Pivot detection method, minimum touches, line scoring formula. | UNKNOWN. The patent only says "relevance / proximity". | — |
| Lines are anchored on swing pivots (fractal or zig-zag), scored by touch count within tolerance and by recency, then de-duplicated. | HYPOTHESIS: the standard approach, consistent with the patent wording | — |

**Classification:** computational geometry plus rule evaluation. **Not ML.**

### 3.2 Engine B: AI Strategy Lab (supervised ML)

| Finding | Label | Source |
|---|---|---|
| Model families: **Random Forest, KNN, Naive Bayes, Logistic Regression**. Users can train several models on one target and compare them. "Cross-training/cross-breeding" combines trained models and retrains the result. | VERIFIED | TS-2 |
| Inputs: indicator values, fundamentals, price-action features, custom formulas, custom JS indicators, alternative data. "LLM-based feature engineering" is mentioned. | VERIFIED (vendor) | TS-1, TS-2 |
| **Label:** a signal is positive when price, within the next N candles, is more likely to hit +X% take-profit than −Y% stop-loss. That is a fixed-horizon **two-barrier label with a time limit**, the same family as López de Prado's triple-barrier method. | VERIFIED (exact tie-breaking UNKNOWN) | TS-1, TS-3, ACAD-1 |
| Worked example: SPY 1h, train 2018-12-01 → 2020-02-01, OOS backtest after 2020-02-01; features RSI(14/20/28/56); 40-candle horizon, +1.5% / −0.5%. KNN and RF showed confidence rising with win rate. | VERIFIED | TS-3 |
| Model outputs a confidence score. Users choose an "optimal ML confidence level" threshold from a confidence-vs-win-rate chart. | VERIFIED | TS-2, TS-3 |
| Validation = date-range split, then backtest "on data it has not previously seen", then forward test (bots). | VERIFIED | TS-1 |
| Purging or embargo between overlapping labels, walk-forward retraining, hyperparameter search, calibration. | UNKNOWN. Not documented. Note that 40-bar labels overlap heavily. | — |
| Deployment: model signals used as conditions in scanners, alerts, and backtests, and in "Strategy Bots" that send broker webhooks. | VERIFIED | TS-2 |
| Models are probably implemented with standard libraries (scikit-learn-equivalent) server-side. | HYPOTHESIS | — |

### 3.3 Risk management
Users configure TP, SL, and horizon in the strategy tester. The tester reports win rate, drawdown, an equity curve, and reward/risk [TS-2, TS-3]. **Position sizing and portfolio-level risk: UNKNOWN.**

---

## 4. Tickeron

| Finding | Label | Source |
|---|---|---|
| Pattern engine detects ~37–39 chart-pattern types "by their key geometrical elements", labelled Emerging or Confirmed, each with a breakout price and target price. | VERIFIED (vendor) | TK-1, TK-4 |
| "Odds of success" are shown as historical counts: e.g. "358 of 419 cases, 85%". | VERIFIED (vendor examples) | TK-1 |
| Odds = (past occurrences followed by the expected outcome) / (all past occurrences), per ticker and/or pattern. | STRONGLY INFERRED from the k/n examples | TK-1 |
| One published example is internally inconsistent: "rose further in 50 of 62 cases" (≈81%) is presented as 76% odds of a continued *down*trend. | VERIFIED (observed error) | TK-1 |
| Trend Prediction Engine: confidence "expressed as a percentage" from historical data, using a proprietary "Odds of Success" formula, with a default 55% minimum filter. Uses "neural networks and deep historical backtests". | VERIFIED (vendor claim; no architecture given) | TK-2 |
| "Financial Learning Models": back-test many known financial models to find the market conditions in which each works, then **dynamically activate** models by regime. The vendor says this is "inspired by LLMs". | VERIFIED (vendor description) | TK-5 |
| FLMs = a regime-conditioned model-selection / mixture-of-experts layer over rule-based and statistical sub-models, not a transformer trained on price tokens. | STRONGLY INFERRED from the vendor's own description of the mechanism | TK-5 |
| Models on 5/15/60-minute timeframes. | VERIFIED (vendor) | TK-6 |
| AI Robots (signal bots and paper or live "agents"), plus an NLG layer that writes tens of thousands of commentary articles. | VERIFIED (vendor) | TK-3 |
| Daily neural-network retraining. | HYPOTHESIS: secondary source only | TK-S1 |
| Network architecture, features, labels, training data, validation, calibration, cost modelling. | UNKNOWN | — |

**Classification:**
- The documented, user-visible quantities (odds, confidence) are **frequency statistics over historical analogues**.
- The pattern detector is geometric.
- Any neural component is undisclosed.
- Risk for users: a k-of-n hit rate with small n, and no costs or trade-path definition, says little about expectancy. For example, 43 of 52 has a 95% Wilson interval of about 0.70–0.91, and that is before any multiple-testing correction across 37 patterns × thousands of tickers.

---

## 5. QuantConnect: LEAN

LEAN is open source, so this section is about architecture, not inference.

### 5.1 Engine (VERIFIED from source, QC-2)

```mermaid
flowchart LR
  Job[Job packet] --> Setup[SetupHandler<br/>Initialize()]
  Setup --> DM[DataManager + DataFeed<br/>Synchronizer / LiveSynchronizer]
  DM -->|TimeSlice| AM[AlgorithmManager.Run<br/>inside Isolator time limit]
  AM --> Algo[QCAlgorithm user code<br/>OnData / Framework]
  Algo -->|Orders| TH[TransactionHandler<br/>Brokerage: Backtesting or live]
  TH -->|Fills, OrderEvents| AM
  AM --> RT[RealTimeHandler<br/>Scheduled events]
  AM --> RH[ResultHandler<br/>stats, charts, logs]
```

- One `Engine.Run` handles both backtest and live. They differ only in pluggable handlers: `Synchronizer` vs `LiveSynchronizer`, a backtesting brokerage vs a real `IBrokerage`, and history-request parallelism disabled in live [QC-2].
- **Event-driven, time-sliced simulation.** All subscribed data is merged into chronological `TimeSlice`s before user code sees them. That structurally prevents look-ahead within the engine [QC-2, QC-3].

### 5.2 Algorithm Framework (VERIFIED, QC-3/4/5)

`Universe Selection → Alpha (Insights) → Portfolio Construction (PortfolioTargets) → Risk Management (adjusted targets) → Execution (orders)`

- **Insight**: direction, period, and optional magnitude, confidence, and weight [QC-8: from LEAN source, not re-fetched this session].
- **Portfolio construction:** equal weighting, insight weighting, mean-variance, Black-Litterman, risk parity [QC-3, QC-8].
- **Risk models:** null (default); per-security max drawdown (5%); portfolio max drawdown (5%, optional trailing); per-security max unrealized profit (5%); sector exposure cap (20%, Morningstar); trailing stop (5%) [QC-4].
- **Execution:** Immediate (market, the default); Spread (≤0.5% spread); StdDev (buy dips 2σ below a 60-bar mean); VWAP (≤1% of bar volume per order) [QC-5].
- **Reality modelling:** per-security fill, slippage, fee, brokerage, buying power, settlement, short availability, option, margin-interest, and dividend models; portfolio-level margin-call and risk-free-rate models. The defaults assume liquid assets [QC-6].

### 5.3 ML integration (VERIFIED, QC-7)
- ML runs inside user algorithms using the Python libraries LEAN ships with.
- Models persist in the **Object Store**.
- The docs explicitly warn against training on test data (look-ahead).
- They recommend precomputing predictions in Research and streaming them back as a custom dataset.
- Scheduled retraining uses scheduled events (`Train`), with time limits.

### 5.4 What is proprietary at QuantConnect
- The cloud data library (curated, survivorship-aware equities, options, futures, alternative data), the hosted compute grid, and live co-location are commercial. **Details of their internals: UNKNOWN.**
- The engine, the data *formats*, and the brokerage adapters are open [QC-1].

---

## 6. Cross-cutting findings on quantitative validation

| Issue | TI | TS | TK | QC |
|---|---|---|---|---|
| Out-of-sample testing | Not documented for Holly. The nightly re-fit on recent data is in-sample selection. | User-defined date split [TS-1] | UNKNOWN | User responsibility; tooling supports it [QC-7] |
| Walk-forward | Effectively a rolling re-optimization; no held-out evaluation documented | UNKNOWN | UNKNOWN | Possible via scheduled `Train` + optimizer; not automatic |
| Costs and slippage | Flat user inputs in OddsMaker [TI-5] | UNKNOWN | UNKNOWN | Pluggable fee, slippage, and fill models [QC-6] |
| Survivorship bias | UNKNOWN | UNKNOWN | UNKNOWN | Data library includes delisted securities (vendor claim; not checked here) |
| Multiple-testing correction | None documented (60+ strategies × parameters, re-selected nightly) | None documented (many models × thresholds) | None documented (≈37 patterns × thousands of tickers) | None built in; user responsibility |
| Metrics shown | PF, win rate, avg W/L, DD, return | Win rate, DD, equity, R:R, confidence vs win rate | Odds %, confidence % | Full statistics set (Sharpe, Sortino, PSR, DD, alpha/beta, turnover, capacity) |

**Bottom line:** none of the closed platforms gives enough evidence to judge real profitability. The reconstruction in [ai_architecture.md](ai_architecture.md) puts validation (purged walk-forward, deflated Sharpe, PBO, realistic costs) at the centre, not as an afterthought.
