---
evidence_level: hypothesis
source_date: 2026-10-30
---
# Algorithm Reconstruction (Evidence-Bounded)

Each algorithm states **what it reconstructs** and **how strong the evidence is**.
- "Mechanism" = the documented behaviour that is being matched.
- Constants and thresholds marked `# chosen` are mine, not the vendor's. Vendor values are UNKNOWN unless cited.
- Nothing here is vendor source code.

---

## 1. Holly-style nightly strategy selector
**Reconstructs:** Trade Ideas Holly [TI-1, TI-2, TI-3].
- *Mechanism:* VERIFIED. Nightly backtest of each strategy, one-setting-at-a-time optimization, drop non-performing filters, keep those passing a "high-probability cut", signal with entry/stop/target/time stop.
- *Thresholds:* UNKNOWN.

```python
# Strategy = rule template with tunable filters, e.g. Breakout:
#   price in [lo, hi], rel_vol >= rv, cross_above(resistance), shares_traded_today >= q
def nightly_select(library, history, as_of, window_days=60,          # chosen
                   min_trades=30, min_pf=1.3, min_win=0.55, top_k=15):  # chosen
    active = []
    for strat in library:
        params = strat.defaults.copy()
        for f in strat.filters:                         # coordinate descent ("one setting at a time")
            best = score(strat, params, history, as_of, window_days)
            for v in f.grid:
                trial = {**params, f.name: v}
                s = score(strat, trial, history, as_of, window_days)
                if s.objective > best.objective and s.n >= min_trades:
                    params, best = trial, s
            if f.optional and best.objective <= score(strat, params | {f.name: None}, ...).objective:
                params[f.name] = None                   # "filters that still underperform are removed"
        if best.n >= min_trades and best.pf >= min_pf and best.win >= min_win:
            active.append((strat, params, best))
    # added safeguard (not documented by TI): penalize for number of trials
    active.sort(key=lambda x: deflated_score(x[2], n_trials=total_trials()), reverse=True)
    return active[:top_k]

def score(strat, params, history, as_of, window_days):
    trades = simulate(strat, params, history.slice(as_of - window_days, as_of - 1),
                      entry='next_tick_after_alert', stop=0.03,  # ~3% default stop [TI-1]
                      exits=['stop', 'target', 'trailing', 'time', 'eod'],  # [TI-3] conservative mode
                      commission=..., slippage_per_share=...)  # OddsMaker-style inputs [TI-5]
    return stats(trades)   # n, win, pf = sum(wins)/abs(sum(losses)), avg_w, avg_l, dd
```
**Honest evaluation** (missing from public TI material): run `nightly_select` for every past day and record only the next day's P&L of the picks ([training_pipeline.md](training_pipeline.md) §10).

---

## 2. Pivot detection and automated trendlines
**Reconstructs:** TrendSpider auto-trendlines.
- *VERIFIED:* the patent says "filter all possible trend lines by relevance, proximity to current price, user criteria" [TS-5]; lines are computed within the user's analysis window [TS-6].
- *HYPOTHESIS:* the pivot method and the scoring formula.

```python
def zigzag_pivots(h, l, atr, k=2.0):                  # chosen: ATR-scaled reversal threshold
    piv, trend, hi_i, lo_i = [], 0, 0, 0              # trend: +1 seeking a top, -1 seeking a bottom
    for i in range(1, len(h)):
        if trend >= 0:
            if h[i] >= h[hi_i]: hi_i = i
            elif h[hi_i] - l[i] > k * atr[i]:         # reversal from the running high confirms a top
                piv.append(('H', hi_i, h[hi_i])); trend, lo_i = -1, i; continue
        if trend <= 0:
            if l[i] <= l[lo_i]: lo_i = i
            elif h[i] - l[lo_i] > k * atr[i]:         # reversal from the running low confirms a bottom
                piv.append(('L', lo_i, l[lo_i])); trend, hi_i = 1, i
    return piv                                        # pivots are only known k*ATR after the extreme (causal)

def candidate_lines(pivots, kind):                    # kind 'L' → support, 'H' → resistance
    P = [p for p in pivots if p[0] == kind]
    for a, b in combinations(P, 2):                   # "all possible trend lines"
        yield Line(a, b, slope=(b[2]-a[2])/(b[1]-a[1]))

def line_score(line, h, l, c, atr, now, tol=0.25):    # chosen weights
    touches = violations = 0
    for i in range(line.a_idx, now + 1):
        y = line.at(i)
        if abs((l[i] if line.kind=='L' else h[i]) - y) <= tol * atr[i]: touches += 1
        if (c[i] < y - tol*atr[i]) if line.kind=='L' else (c[i] > y + tol*atr[i]): violations += 1
    proximity = abs(c[now] - line.at(now)) / atr[now]           # "proximity to current price"
    recency   = (now - line.b_idx) / (now - line.a_idx + 1)
    return 1.0*touches - 2.0*violations - 0.5*proximity - 1.0*recency

def auto_trendlines(bars, window, max_lines=3):
    seg = bars[window.start:window.end]               # "Truth in Analysis" window [TS-6]
    piv = zigzag_pivots(seg.h, seg.l, seg.atr)
    lines = [ln for kind in 'LH' for ln in candidate_lines(piv, kind)]
    lines = [ln for ln in lines if ln.violations(seg) <= 1]
    return dedupe_similar(sorted(lines, key=lambda ln: line_score(ln, ...), reverse=True))[:max_lines]
```

### Line events with sensitivity buffer
**Reconstructs:** TS-5 patent trigger semantics. VERIFIED: touch / bounce / break-through, buffer ±tolerance, confirmation timeframe.
```python
def line_event(line, bar, prev_bar, buf):             # buf = absolute tolerance, e.g. 0.25*ATR
    y = line.at(bar.idx)
    touched = bar.l <= y + buf and bar.h >= y - buf
    if touched and prev_bar_touched(line, prev_bar, buf) and moved_away(bar, y, line.kind): return 'bounce'
    if line.kind == 'H' and bar.c > y + buf and prev_bar.c > line.at(prev_bar.idx) + buf: return 'break'  # passes and holds
    if line.kind == 'L' and bar.c < y - buf and prev_bar.c < line.at(prev_bar.idx) - buf: return 'break'
    if touched: return 'touch'
```
**Multi-factor alerts** [TS-5] = boolean expression trees whose leaves are `(indicator, timeframe, offset_bars, comparator, operand)`. Evaluate each leaf on the bar series of its own timeframe, using only **closed** bars of the higher timeframe (otherwise you get look-ahead).

---

## 3. Chart-pattern detection (example: double bottom)
**Reconstructs:** the Tickeron/TrendSpider pattern engines at the concept level. VERIFIED: patterns "by key geometrical elements", Emerging/Confirmed states, breakout and target price [TK-1]. Rules: standard TA definitions; method per Lo-Mamaysky-Wang [ACAD-4].
```python
def double_bottom(piv, c, atr, now, tol=0.5, min_sep=10):   # chosen
    # need L1, H, L2 consecutive pivots
    for L1, H, L2 in triples(piv, pattern=('L','H','L')):
        if L2.idx - L1.idx < min_sep: continue
        if abs(L1.px - L2.px) > tol * atr[L2.idx]: continue
        if H.px - max(L1.px, L2.px) < 2 * atr[H.idx]: continue
        neckline = H.px
        state = 'confirmed' if c[now] > neckline else 'emerging'
        target = neckline + (neckline - min(L1.px, L2.px))   # measured move
        return Pattern('double_bottom', state, breakout_px=neckline, target_px=target)
```
Other patterns (H&S, triangles, flags, wedges, channels) follow the same pivot-sequence + geometric-constraint template. Kernel-smoothed extrema (ACAD-4) reduce noise-driven pivots.

---

## 4. Analogue "odds of success" with honest uncertainty
**Reconstructs:** Tickeron odds. STRONGLY INFERRED: the odds are k/n of past occurrences [TK-1]. Added here: a Wilson interval and Beta-Binomial shrinkage (not documented by Tickeron).
```python
def analogue_odds(events_hist, outcome_fn, prior_a=None, prior_b=None, z=1.96):
    # events_hist: past occurrences of (pattern, ticker[, regime]) strictly before now
    k = sum(outcome_fn(e) for e in events_hist); n = len(events_hist)
    p = k / n if n else float('nan')
    # Wilson interval
    d = 1 + z*z/n; centre = (p + z*z/(2*n)) / d
    half = z * sqrt(p*(1-p)/n + z*z/(4*n*n)) / d
    # Beta-Binomial shrinkage toward the pattern's pooled rate across all tickers
    p_shrunk = (k + prior_a) / (n + prior_a + prior_b) if prior_a else p
    return dict(k=k, n=n, p=p, ci=(centre-half, centre+half), p_shrunk=p_shrunk)
```
`outcome_fn` must define the trade path explicitly: target hit before stop within H bars, **after costs**. Example check from TK-1's own figures: 43/52 → p=0.83, Wilson 95% CI ≈ [0.70, 0.91].

---

## 5. Barrier-label classifier with a confidence threshold
**Reconstructs:** TrendSpider AI Strategy Lab. VERIFIED: label = TP before SL within N candles; RF/KNN/NB/LR; confidence-vs-win-rate threshold; OOS backtest [TS-1, TS-2, TS-3].
```python
def barrier_label(o, h, l, c, t0, tp, sl, H, side=+1):
    entry = o[t0 + 1]                                  # enter next bar open (no same-bar look-ahead)
    up, dn = entry*(1+tp), entry*(1-sl)
    for t in range(t0 + 1, min(t0 + 1 + H, len(c))):
        hit_up, hit_dn = h[t] >= up, l[t] <= dn
        if hit_up and hit_dn: return -1                # ambiguous bar → assume stop (conservative)
        if hit_up: return +1
        if hit_dn: return -1
    return 0                                           # time barrier

X, y, t_idx, t_end = build_events_features_labels(...)  # event-sampled rows
for train, test in purged_walk_forward(t_idx, t_end, n_folds=6, embargo=H):
    models = {m: fit(m, X[train], y[train] == +1, sample_weight=uniq_w[train])
              for m in ['logreg', 'rf', 'knn', 'nb', 'lgbm']}
    oos[test] = {m: md.predict_proba(X[test])[:, 1] for m, md in models.items()}
calibrated = isotonic_fit(oos_pred, y_oos == +1)        # fit on OOS predictions only
threshold = pick_threshold_on_dev(calibrated, expectancy_after_costs)  # never on holdout
```

---

## 6. Regime-gated model activation ("FLM"-style)
**Reconstructs:** the Tickeron FLM concept. VERIFIED vendor description: back-test known models to find the conditions where each works, then activate them dynamically [TK-5]. The implementation here is my own design.
```python
regime_model = GaussianHMM(n_components=3).fit(features_market[:t_train])   # refit monthly, past only
def regime_probs(t):  return regime_model.filtered_proba(features_market[:t+1])[-1]  # filtered, not smoothed

# Offline: performance table perf[strategy][regime] from purged OOS backtests only
def activation_weights(t, perf, min_trades=50, shrink=20):
    pr = regime_probs(t)
    w = {}
    for s in strategies:
        er = sum(pr[r] * shrink_mean(perf[s][r], shrink) for r in regimes if perf[s][r].n >= min_trades)
        w[s] = max(er, 0)
    return normalize(w)
```

---

## 7. Signal ranking and position sizing
```python
def rank(cands):
    for c in cands:
        p = c.p_cal_lower if c.n_eff < 200 else c.p_cal   # use CI lower bound when evidence is thin
        c.ev_R = p * c.avg_win_R - (1 - p) * c.avg_loss_R - c.cost_R
        c.score = c.ev_R * c.regime_weight
    return dedupe_correlated(sorted([c for c in cands if c.ev_R > 0], key=lambda c: c.score, reverse=True))

def size(c, equity, risk_frac=0.005, adv_cap=0.01):     # chosen
    stop_dist = abs(c.entry - c.stop)
    qty = equity * risk_frac / stop_dist
    kelly = max(0, (c.p * c.b - (1 - c.p)) / c.b) * 0.25   # quarter Kelly; b = win/loss ratio
    qty = min(qty, equity * kelly / c.entry, adv_cap * c.adv)
    return floor(qty)
```

---

## 8. LEAN framework wiring
**Mirrors:** QC framework. VERIFIED API concepts [QC-3, QC-4, QC-5]; minor API details may differ by LEAN version.
```python
class EngineAlpha(AlphaModel):
    def update(self, algorithm, data):
        cands = strategy_engine.candidates(data)           # components 4, 6
        ranked = rank(score_with_models(cands))            # components 5, 7, 8
        return [Insight.price(c.symbol, timedelta(minutes=c.max_hold_min),
                              InsightDirection.UP if c.side > 0 else InsightDirection.DOWN,
                              None, c.p_cal, None, c.weight) for c in ranked[:top_k]]

class MyAlgo(QCAlgorithm):
    def initialize(self):
        self.set_universe_selection(LiquidUniverse())
        self.set_alpha(EngineAlpha())
        self.set_portfolio_construction(InsightWeightingPortfolioConstructionModel())
        self.add_risk_management(MaximumDrawdownPercentPerSecurity(0.03))
        self.add_risk_management(MaximumSectorExposureRiskManagementModel(0.25))
        self.set_execution(SpreadExecutionModel(0.005))
        self.schedule.on(self.date_rules.month_start(), self.time_rules.at(2, 0), self.retrain)
```

---

## 9. Purged walk-forward split
**Source:** ACAD-1 method.
```python
def purged_walk_forward(t_start, t_end, n_folds, embargo):
    # t_start[i], t_end[i]: label interval of sample i (sorted by t_start)
    bounds = split_time_into(n_folds + 1)
    for k in range(1, n_folds + 1):
        test = idx_in(bounds[k])
        t0, t1 = t_start[test].min(), t_end[test].max()
        train = [i for i in range(len(t_start))
                 if t_end[i] < t0                     # strictly before the test window (expanding)
                 and not (t_start[i] <= t1 and t_end[i] >= t0)]   # purge overlap
        # embargo: also drop samples starting within `embargo` bars after t1 (relevant for CPCV / rolling variants)
        yield train, test

def test_no_overlap():   # self-check to run in CI
    for tr, te in purged_walk_forward(...):
        assert all(t_end[i] < t_start[te].min() for i in tr)
```

---

## 10. Deflated Sharpe ratio
**Source:** ACAD-2.
```python
from scipy.stats import norm
def deflated_sharpe(sr_hat, T, skew, kurt, sr_trials_var, N):
    # sr_hat: observed per-period Sharpe of the chosen strategy; N: number of trials tried
    g = 0.5772156649
    sr0 = sqrt(sr_trials_var) * ((1-g)*norm.ppf(1-1/N) + g*norm.ppf(1-1/(N*e)))
    z = (sr_hat - sr0) * sqrt(T - 1) / sqrt(1 - skew*sr_hat + (kurt - 1)/4 * sr_hat**2)
    return norm.cdf(z)        # probability the true Sharpe > 0 after selection; require > 0.95
```

---

## What could not be reconstructed (UNKNOWN)
- Holly's actual 60+ rule parameter grids, objective function, window length, and threshold.
- TrendSpider's exact pivot and line scoring, and its handling of overlapping labels.
- Tickeron's neural architecture, the "Odds of Success" formula beyond the k/n examples, and the TPE horizon.
- Any vendor's live, audited, after-cost performance.
