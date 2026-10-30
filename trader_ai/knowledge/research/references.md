---
evidence_level: unverified_claim
source_date: 2026-10-30
---
# References

Research date: 2026-10-30. All sources are public. No login, paid tier, private API, or decompiled client was used.

Reliability grades: **P** = primary (vendor docs, source code, patent). **V** = vendor marketing or press release (a primary source, but promotional). **S** = secondary (third-party review or aggregator). Do not build on S alone.

Tags like `[TI-1]` are the citation keys used in the other documents.


## Trade Ideas (TI)

| Key | Source | Grade | Used for |
|---|---|---|---|
| TI-1 | https://www.trade-ideas.com/features/ai-signals/ | P/V | Nightly backtest and optimization loop, "high-probability cut", ~3% default stop |
| TI-2 | https://www.trade-ideas.com/hollyguide/Holly_AI_Strategies.html | P | "Over 60 strategies", per-strategy entry rules, parameters proprietary |
| TI-3 | https://www.trade-ideas.com/hollyguide/AI_Risk_Mode.html | P | Exit reasons by risk mode (stop, target, trailing, timed, "reduce risk") |
| TI-4 | https://www.trade-ideas.com/compare | V | "Tested every night, only the ones that pass send signals" |
| TI-5 | https://www.trade-ideas.com/OddsMaker/Help.html | P | OddsMaker inputs (equity, sizing, commission, slippage) and metric definitions |
| TI-6 | https://www.trade-ideas.com/holly/ (2016 "Morning Huddle" post) | V | Historical example: 12 strategies chosen overnight, per-strategy Win % 51.7–70.0, Risk-On/Off modes |
| TI-7 | https://www.trade-ideas.com/hollyguide/Trade_Holly_Manually.html | P | Holly closes tracked trades same day |
| TI-S1 | https://finmasters.com/trade-ideas-review/ | S | "~60 strategies re-optimized nightly" (consistent with TI-2) |
| TI-S2 | https://daytradingtoolkit.com/trading-tools-tutorials/trade-ideas-holly-ai-explained | S | Variant descriptions (conflicting across sources) |
| TI-S3 | https://daytradingtoolkit.com/trading-tools-tutorials/trade-ideas-oddsmaker-review | S | Claims OddsMaker history window is up to 64 days (unverified) |

## TrendSpider (TS)

| Key | Source | Grade | Used for |
|---|---|---|---|
| TS-1 | https://help.trendspider.com/kb/ai-strategies/what-is-an-ai-strategy | P | ML strategy concept, TP-before-SL label over N candles, OOS backtest framing, "black box" caveat |
| TS-2 | https://trendspider.com/product/machine-learning-quant-trading-strategy-lab/ | P/V | Model types (RF, KNN, Naive Bayes, Logistic Regression), inputs, deployment |
| TS-3 | https://trendspider.com/blog/building-a-strategy-with-ai-strategy-lab/ | P | Worked example: SPY 1h, 2018-12-01..2020-02-01 train, OOS after; RSI 14/20/28/56; 40-candle, +1.5%/-0.5% |
| TS-4 | https://trendspider.com/intellectual-property/ | P | Lists US 11,935,118 |
| TS-5 | https://patents.google.com/patent/US11935118B2/en | P | Patent: OHLCV → charts → candidate trend lines filtered by relevance/proximity → alert triggers (touch/bounce/break), sensitivity buffer, multi-factor logical trees, multi-timeframe; volume-at-price split-VWAP chart construction |
| TS-6 | https://help.trendspider.com/kb/automated-technical-analysis/automated-trendline-detection | P | Trendlines computed from price inside the user's "Truth in Analysis" window |
| TS-7 | https://help.trendspider.com/kb/charting/multi-timeframe-analysis | P | MTFA overlays higher-timeframe trendlines, indicators, and patterns |
| TS-8 | https://trendspider.com/blog/introducing-trendspiders-ai-strategy-lab/ | V | Launch post |

## Tickeron (TK)

| Key | Source | Grade | Used for |
|---|---|---|---|
| TK-1 | https://tickeron.com/blogs/using-a-search-engine-to-discover-trade-opportunities-7773/ | V | Patterns found "by their key geometrical elements"; 37 pattern types; Emerging/Confirmed; odds shown as k-of-n historical cases |
| TK-2 | https://tickeron.com/trading-investing-101/tickeron-innovates-profitable-trading-with-ai-based-trend-prediction-engine-tpe | V | TPE confidence "expressed as a percentage" from history; "neural networks and deep historical backtests"; default 55% min confidence |
| TK-3 | https://globalfintechseries.com/interviews/globalfintechseries-interview-with-sergey-savastiouk-founder-ceo-at-tickeron/ | V | Founder interview: pattern ID across thousands of stocks, NLG layer, robots |
| TK-4 | https://www.barchart.com/story/news/21027766/tickeron-unlocks-hidden-market-insights-with-ai-stock-pattern-search-engine | V | Pattern Search Engine press release |
| TK-5 | Press releases describing FLMs: "back-test vast amounts of financial data … to identify market conditions under which well-known financial models perform optimally" (e.g. https://business.minstercommunitypost.com/minstercommunitypost/article/gnwcq-2024-6-25-revolutionary-financial-health-based-ai-trading-bots-for-hedge-funds-unveiled-by-tickeron) | V | FLM = regime-conditional model activation |
| TK-6 | https://tickeron.com/trading-investing-101/tickeron-unveils-enhanced-real-time-patterns-rtp-tool-with-14day-free-trial-delivering-75-average-confidence-in-forex-patterns/ | V | Multi-timeframe 5/15/60-minute models; confidence figures |
| TK-S1 | https://yespress.io/tickeron | S | Claims daily neural-network retraining (unverified) |
| TK-S2 | https://daytradingtoolkit.com/reviews/tickeron-review | S | "Odds are historical frequency" caveat |

## QuantConnect / LEAN (QC)

| Key | Source | Grade | Used for |
|---|---|---|---|
| QC-1 | https://github.com/QuantConnect/Lean | P (code) | Apache-2.0, C# engine + Python, modular, `pip install lean` CLI, local Docker backtests |
| QC-2 | https://raw.githubusercontent.com/QuantConnect/Lean/master/Engine/Engine.cs | P (code) | Engine run loop, handlers (DataFeed, Transactions, Results, RealTime, Setup), `LiveSynchronizer` vs `Synchronizer`, Isolator time limit |
| QC-3 | https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/overview | P | Universe → Alpha (Insights) → Portfolio Construction (Targets) → Risk → Execution |
| QC-4 | https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/risk-management/supported-models | P | Built-in risk models and defaults |
| QC-5 | https://www.quantconnect.com/docs/v2/writing-algorithms/algorithm-framework/execution/supported-models | P | Built-in execution models (Immediate, Spread, StdDev, VWAP) |
| QC-6 | https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts | P | Fill, slippage, fee, brokerage, buying power, settlement, short availability, margin call models |
| QC-7 | https://www.quantconnect.com/docs/v2/writing-algorithms/machine-learning/key-concepts | P | Object Store persistence, avoid training on test data, precomputed-prediction workflow |
| QC-8 | LEAN source files (not re-fetched in this session; check before relying on them): `Common/Algorithm/Framework/Alphas/Insight.cs`, `Algorithm.Framework/Portfolio/*PortfolioConstructionModel.*` | P (code) | Insight fields; PCM variants (equal weighting, insight weighting, mean-variance, Black-Litterman, risk parity) |

## Open-source and academic background (for the reconstruction)

These describe standard methods. They do not claim anything about how the vendors build their systems.

| Key | Source | Used for |
|---|---|---|
| ACAD-1 | López de Prado, *Advances in Financial Machine Learning* (Wiley, 2018) | Triple-barrier labelling, meta-labelling, purged/embargoed CV, fractional differencing |
| ACAD-2 | Bailey & López de Prado, "The Deflated Sharpe Ratio" (J. Portfolio Mgmt, 2014) | Multiple-testing-adjusted Sharpe |
| ACAD-3 | Bailey, Borwein, López de Prado, Zhu, "The Probability of Backtest Overfitting" (J. Comp. Finance, 2016) | PBO / CSCV |
| ACAD-4 | Lo, Mamaysky, Wang, "Foundations of Technical Analysis" (J. Finance, 2000) | Kernel-smoothed extrema for chart-pattern detection |
| ACAD-5 | Hamilton, "A New Approach to the Economic Analysis of Nonstationary Time Series" (Econometrica, 1989) | Markov-switching regime models |
| ACAD-6 | White, "A Reality Check for Data Snooping" (Econometrica, 2000); Hansen SPA test (2005) | Data-snooping tests for strategy selection |
| OSS-1 | https://github.com/QuantConnect/Lean | Backtest/live engine |
| OSS-2 | https://github.com/polakowo/vectorbt | Vectorized parameter sweeps |
| OSS-3 | https://github.com/microsoft/qlib | ML research platform for quant |
| OSS-4 | https://github.com/TA-Lib/ta-lib-python | Indicators |
| OSS-5 | https://github.com/mlflow/mlflow | Experiment tracking, model registry |
| OSS-6 | https://github.com/feast-dev/feast | Feature store |
