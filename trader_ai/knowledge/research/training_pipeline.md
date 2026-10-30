---
evidence_level: research_finding
source_date: 2026-10-30
---
# Training and Validation Pipeline

**Goal.** Train models whose out-of-sample, after-cost performance is measured honestly.

**Why this matters here.** The selection loops that the closed platforms describe re-pick the best of many rules or models on recent data ([research_report.md](research_report.md) §6). Without the controls below, that loop overfits.

## Pipeline diagram

```mermaid
flowchart LR
  A[Point-in-time data<br/>snapshot vN] --> B[Event sampling<br/>CUSUM / rule triggers / pattern events]
  B --> C[Labelling<br/>triple barrier: TP, SL, time]
  C --> D[Sample weights<br/>label uniqueness, time decay]
  D --> E[Outer split<br/>dev  |  final holdout (untouched)]
  E --> F[Purged + embargoed<br/>walk-forward CV on dev]
  F --> G[Inner HPO<br/>Optuna, nested]
  G --> H[OOS predictions<br/>stitched across folds]
  H --> I[Calibration<br/>isotonic on OOS]
  I --> J[Strategy backtest on OOS preds<br/>event-driven, full costs]
  J --> K{Validation gate<br/>DSR, PBO, regime split,<br/>cost stress, baseline beat}
  K -- pass --> L[Final holdout run ONCE]
  L -- pass --> M[Registry: staged → paper → live]
  K -- fail --> X[Reject / log trial count]
  L -- fail --> X
```

## 1. Datasets and data requirements
| Item | Minimum | Preferred |
|---|---|---|
| Universe | Liquid US equities/ETFs, price > $5, ADV > $10M | Same plus delisted names (survivorship-free) |
| History | 5 years daily / 2 years 1-min | 15+ years daily, 5+ years 1-min, covering 2008, 2020, 2022 |
| Fields | OHLCV, splits/dividends | + NBBO quotes, trade conditions, PIT fundamentals, short interest |
| Versioning | Snapshot ID per training run | DVC/LakeFS snapshot + hash stored in MLflow |

## 2. Sampling (which bars become training rows)
Do not train on every bar. Sample **events**, because events are what the strategy can act on:
- rule triggers from the strategy library (Holly-style setups),
- pattern events (trendline break, pattern confirmation),
- CUSUM-filter events on returns (ACAD-1).

This also keeps the train distribution equal to the live decision distribution. TrendSpider's lab trains on every bar instead, which mismatches the live decision distribution.

## 3. Labels
**Triple barrier** (a generalization of TrendSpider's documented "TP before SL within N candles" label [TS-1]):
```
upper = entry * (1 + k_tp * ATR%)      # or fixed %
lower = entry * (1 - k_sl * ATR%)
t_max = entry_time + H bars
label = +1 if upper touched first, -1 if lower first, 0 (or sign of return) if t_max first
```
- **Same-bar ambiguity:** if one bar's high ≥ upper and low ≤ lower, resolve with intrabar (1-min) data. If that isn't available, assume the **stop hit first** (conservative).
- **Costs in labels:** subtract the round-trip cost estimate from TP/SL distances, so "win" means a win after costs.
- **Meta-labels:** when a rule supplies the side, label = 1 if the rule's trade hit TP (after costs), else 0.

## 4. Features
See [data_pipeline.md](data_pipeline.md) §4. Rules:
- Every feature computed as-of `t` uses data with timestamp ≤ `t` and *publication* time ≤ `t` (fundamentals).
- Cross-sectional normalization uses only that timestamp's cross-section.
- Feature selection happens **inside** each training fold: MDA permutation importance or clustered feature importance. Never on the full dataset.

## 5. Splits
| Split | Purpose | Rule |
|---|---|---|
| Final holdout | Last 12–18 months | Touched **once**, at the end. Logged. Re-using it makes it part of dev. |
| Walk-forward dev folds | Model selection and HPO | Expanding or rolling train window; test window 3–6 months; step = test length |
| Purge | Remove train samples whose label interval overlaps the test window | Overlap = [t_entry, t_exit] intersection |
| Embargo | Drop train samples starting within `h` bars *after* the test window | `h` ≈ max label horizon (e.g. 1–2% of samples) |
| CPCV (optional) | Many backtest paths for PBO | Combinatorial purged CV (ACAD-1, ACAD-3) |

## 6. Training methodology
- **Model ladder** (stop when the next rung fails to beat the previous one OOS): logistic regression → random forest → LightGBM → sequence model (TCN/LSTM/Transformer on raw bars).
- **Class imbalance:** class weights, not oversampling (oversampling leaks across overlapping labels).
- **Sample weights:** label uniqueness × optional time decay.
- **HPO:** Optuna with ≤50–100 trials per model inside inner folds. **Record the total trial count**; it feeds the Deflated Sharpe.
- **Early stopping** on an inner validation fold (purged), never on the test fold.
- **Seeds:** at least 5 seeds for the final configuration; report the dispersion.

## 7. Evaluation metrics
| Layer | Metric | Gate (example) |
|---|---|---|
| Classifier | Log-loss and Brier vs base-rate; AUC; ECE after calibration | Beats base-rate log-loss in ≥70% of folds |
| Ranking | Precision@threshold; rank IC of score vs realized R | IC > 0 with t-stat > 2 across folds |
| Economic (after costs) | Expectancy (R/trade), PF, Sharpe, Sortino, max DD, Calmar, turnover | Expectancy > 0 at 2× cost assumptions |
| Overfitting | **Deflated Sharpe** with the true trial count (ACAD-2); **PBO** < 0.2 (ACAD-3); White/SPA test across the strategy set (ACAD-6) | DSR p < 0.05 |
| Robustness | Regime split (from component 7), parameter-neighbourhood stability (±20%), sub-universe split | No single regime contributes > 60% of P&L |
| Baselines | Same rule without the ML filter; random entry with the same exits; buy-and-hold beta | Must beat all of them |

> **Accuracy is not profitability.** A 60% hit rate with a 0.5R average win and a 1R average loss loses money. Always report expectancy after costs.

## 8. Leakage prevention checklist
- [ ] As-of joins for every non-price field (fundamentals by publication date, index membership by historical constituent lists).
- [ ] Indicators computed causally (no centred windows, no `shift(-1)`).
- [ ] Scalers and encoders fit inside each training fold.
- [ ] Regime model uses filtered probabilities, refit only on past data.
- [ ] Purge and embargo applied; verified by a unit test that no train label interval overlaps a test interval.
- [ ] Corporate-action adjustments applied as of the time of use (back-adjust the training history only with factors known at `t`, or use returns).
- [ ] Universe at time `t` = the tradable set at `t` (includes later-delisted names).
- [ ] Holdout used once; every run is logged.

## 9. Retraining, online vs offline, drift
| Topic | Policy |
|---|---|
| Mode | Offline batch retrain. Online learning is not recommended at the start (hard to validate, easy to chase noise). |
| Frequency | Scheduled monthly retrain on an expanding/rolling window. The walk-forward CV already simulates this cadence, so validated performance matches deployment. |
| Promotion | Shadow (score live, no orders) ≥ 4 weeks → paper → small capital → full. |
| Drift triggers | Feature PSI > 0.25 on key features; rolling 60-trade Brier worse than the OOS 95th percentile; CUSUM alarm on R-multiples; live slippage > 1.5× model. |
| Response | Suspend the strategy → retrain → **full validation gate again**. Never hot-swap. |

## 10. Rolling strategy re-selection (Holly-style, made safe)
The Trade Ideas pattern (re-optimize nightly, keep the winners) is useful only if its own selection is out-of-sample:
```
for each day d:
    candidates = strategies × parameter grid
    score each on window [d-W, d-1] with purge     # selection window
    pick top K with min trades, DSR-adjusted
    record OOS P&L of picks on day d               # evaluation is ONLY these day-d results
report: P&L of the *selection process* across all d, with the total trial count
```
This is walk-forward evaluation of the selector itself. Its stitched P&L is the only valid estimate of what the selector delivers.
