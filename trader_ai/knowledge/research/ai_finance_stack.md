---
evidence_level: unverified_claim
source_date: 2026-10-30
---
# AI finance stack: research first, execution last

Shared by the user on 2026-10-30. Advice from a third-party write-up, not tested here.

## Three layers
1. **Analyst layer**: agents that research, debate and form a view (e.g. TradingAgents: market, news, sentiment, fundamentals analysts, bull/bear researchers, risk debaters).
2. **Workflow layer**: the structure that coordinates the agents (e.g. Paperclip). A minimal version has three roles: macro analyst, company analyst, risk reviewer, producing one memo, not a trade.
3. **Data/context layer**: market data, earnings, transcripts, fundamentals, macro, FX, bonds, crypto (e.g. OpenBB). Agents should reason from fetched data, never from memory or guesses.

The common mistake is connecting an agent to a broker before it has a reliable research process.

## Order of work
1. Build a reliable data/context layer.
2. Add agents that read data, debate, summarise and write a memo.
3. Generate research briefs and judge their quality before thinking about trades.
4. Paper trade only, and track whether the reasoning holds up.
5. Add execution last, if ever, after testing, logging, review and risk controls.

## Safety rules
- Not financial advice.
- No agent trades real money without extensive testing.
- Research briefs before execution; paper trading first.
- Log every recommendation **and its outcome**.
- Human review before any real trade.
- Risk rules before broker access.

## One-page research brief template
For a ticker, using available market data:
1. Current market context
2. Recent earnings or news
3. Bull case
4. Bear case
5. Key risks
6. What data would change the thesis
7. A confidence rating

No real-money trade recommendation; research and paper trading only.

## How this project already maps to it
- Analyst layer: market, news and quant agents plus an adversarial reviewer (bull/bear in one pass), coordinator decides.
- Execution last: paper only, live trading impossible in this build, risk engine and kill switch before any order.
- Outcome logging: every decision is recorded and scored against what the price did next ("Track record" on the AI decisions page).
- Not adopted: OpenBB (heavy dependency; the system trades one ETF on daily bars, so earnings and transcripts add little), Paperclip (the coordinator already plays this role).
