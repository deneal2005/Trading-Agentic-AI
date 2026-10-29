---
evidence_level: research_finding
---
# Backtesting pitfalls

## Look-ahead bias
Using information at time t that was only available later: same-bar fills on the signal bar's close, revised economic data, centered indicators. This system fills at the next bar's open and uses causal indicators only.

## Multiple testing and overfitting
Testing many parameter combinations inflates the best Sharpe ratio by chance. Report the number of trials; prefer robust parameter regions over peaks. References: Bailey & Lopez de Prado (2014), The deflated Sharpe ratio, Journal of Portfolio Management; Harvey, Liu & Zhu (2016), ...and the cross-section of expected returns, Review of Financial Studies.

## Holdout contamination
A holdout used repeatedly becomes a training set. The registry counts holdout uses and refuses reuse on the same data.

## Unrealistic costs and fills
Ignoring spread, slippage, financing and event-time spread widening. Always stress costs (2x) and check whether the edge survives.

## Small samples
Sharpe ratio estimates have wide confidence intervals; Lo (2002), The statistics of Sharpe ratios, Financial Analysts Journal. Fewer than ~100 trades rarely supports strong conclusions.

## Survivorship bias
Relevant for equity universes (delisted stocks). Less relevant for a single major FX pair.
