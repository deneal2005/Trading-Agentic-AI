# Imported: "AI Day Trading Bot" research framework

Source: `ai_day_trading_bot_complete.zip` supplied by the user on 2026-10-30. It is a paper-only
research framework built on 5-minute bars (default timezone Asia/Kolkata). Its own README says no
strategy has been verified as profitable.

## What was taken into this platform
- **Candle and indicator features** in `models/forecast.py` (`CANDLE_FEATURES`): MACD histogram as a
  fraction of price, Bollinger width, a squeeze percentile (width rank over 100 bars), where the
  close sits in the bar's range, candle body % and range %. They are scale-free, so they work on
  daily bars and across instruments. Like every feature, the model's walk-forward gate decides
  whether they help.
- **Purge gap in walk-forward**: the bot skips the label horizon between train and test windows.
  Our audit (M3) had flagged the missing purge, so we now skip one row.

## What was not taken, and why
- `data/sample_ohlcv.csv`: a synthetic geometric random walk (symbol `SYNTH`, seed 42). It has no
  signal, and the bot's own README says not to use it for performance claims. Training on it
  would teach the model noise.
- Random-forest model, paper trader, risk and execution code: we already have gated logit/MLP
  models, a risk policy, a kill switch and broker adapters with more controls.
- Intraday-session rules (opening range, VWAP, previous-day levels, gap and go) need intraday
  bars. This platform runs on `1d` bars, so they stay listed below as candidates only.

## Strategy catalogue (untested candidates)
Signals are +1 long, -1 short, 0 none. They are evaluated on the completed bar and filled at the
next open. "vol" means volume ≥ 1.5 × its 20-bar mean.

| ID | Name | Rule (long side; short is the mirror image) | Needs intraday? |
|---|---|---|---|
| STR001 | Opening range breakout | After the first 15 min, close > opening-range high, vol, close > VWAP | yes |
| STR002 | VWAP pullback | Above rising VWAP, EMA9 > EMA20, close within 0.35 ATR of VWAP/EMA20, bullish rejection candle | yes |
| STR003 | EMA crossover + trend | EMA9 crosses above EMA21 and close > EMA200 | no |
| STR004 | Previous-day high break | Close > prior-day high, low within 0.25 ATR of it (retest), vol | yes |
| STR005 | Support/resistance reversal | Low within 0.2 ATR of the 50-bar low, bullish rejection, RSI < 45 | no |
| STR006 | RSI + Bollinger mean reversion | Low < lower band, close back inside, RSI < 30, bullish rejection | no |
| STR007 | Bollinger squeeze breakout | Prior width in the bottom 20% of 100 bars, close > upper band, vol | no |
| STR008 | MACD momentum | MACD crosses above its signal line, close > EMA20 | no |
| STR009 | First pullback after breakout | Close > 10-bar high, low retests it within 0.25 ATR, bullish | no |
| STR010 | Gap and go | Gap ≥ 1% vs prior close, then opening-range breakout with vol | yes |
| STR011 | VWAP reclaim | Close crosses above VWAP, low within 0.2 ATR of it, bullish | yes |
| STR012 | Inside bar breakout | Previous bar was an inside bar; close > its mother bar's high, close > EMA20 | no |
| STR013 | Flag/pennant | 6-bar impulse > 1.5%, then a shallow 3-bar pullback (0 to -1%), break of the 3-bar high, vol | no |
| STR014 | ATR range breakout | Close > 10-bar high + 0.5 ATR, vol | no |
| STR015 | Trendline break + retest | 30-bar regression of highs slopes down; close breaks above it and retests within 0.25 ATR | no |

"Bullish rejection" means close > open and the close in the top 35% of the bar's range.

## Methods worth keeping in mind
- **Triple-barrier labels** (horizon 6 bars, target 1.5 ATR, stop 1.0 ATR; if both are touched in
  one bar, count the stop first). This is a better training target than next-bar direction for a
  stop/target strategy. We have not adopted it because it changes what the model predicts.
- If labels look more than one bar ahead, the purge gap must grow to match the horizon.
- Cost placeholders (Indian equities): ₹20 per order, 5 bps slippage, 0.6 bps exchange and
  regulatory fees, 2.5 bps sell-side tax. Replace them with the real broker's fee schedule.
- Risk defaults: 0.5% risk per trade, 1.5% daily loss stop, halt after 2 consecutive losses,
  at most 3 trades a day, flat at session end.
