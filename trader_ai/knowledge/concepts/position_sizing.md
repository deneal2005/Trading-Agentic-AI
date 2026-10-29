---
evidence_level: established_fact
---
# Position sizing and risk

## Fixed fractional risk
Units = equity x risk fraction / (distance to stop). Losing trades cost about the same fraction of equity regardless of volatility. The arithmetic is exact; the choice of fraction is a judgement.

## Leverage
Notional / equity. Retail FX leverage limits vary by jurisdiction (e.g. 30:1 on majors for retail clients in the EU/UK under ESMA/FCA rules). This system caps leverage independently (max_leverage).

## Drawdown arithmetic
A 50% loss requires a 100% gain to recover. Deep drawdowns are disproportionately expensive, which is why hard drawdown limits halt trading.

## Win rate is not edge
Expectancy = win rate x average win - loss rate x average loss. A 90% win rate with rare large losses can be negative expectancy.
