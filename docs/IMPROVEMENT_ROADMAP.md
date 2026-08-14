# Wyckoff Scanner — Improvement Roadmap

A comprehensive list of potential improvements organized by intent. The goal: become a better trader and consistently make money using this system.

**Current State**: 10 of 11 implemented signals have a statistically significant negative edge vs. naive swing-trading. The system works as a "chart-event radar" for discretionary review, not as a mechanical trading system.

---

## Category 1: Find Signals That Actually Have Edge

The backtest showed Wyckoff patterns alone underperform naive swing-trading. The fix isn't abandoning Wyckoff — it's adding filters that isolate WHEN these patterns work.

### 1. Market Regime Filter ✅ IMPLEMENTED

Gate all signals based on broad market conditions:

- **SPY above/below 200-day SMA** — only take bullish signals when SPY > 200d, bearish when SPY < 200d
- **VIX regime** — springs might work better in high-VIX (fear) environments, upthrusts in low-VIX (complacency)
- **Breadth** — % of S&P above their own 50d SMA; don't fight broad weakness

The `regime_filter.py` module provides `get_regime()` which returns the current market regime and whether bullish/bearish signals should be taken.

### 2. Relative Strength Gate Enhancement

The old `spring_upthrust_events` had an RS gate (`rs[j] > rs[j-5]`), but `is_pure_spring` doesn't. Backtest both versions separately:

- Pure spring WITHOUT RS filter: what's the edge?
- Pure spring WITH "stock outperforming SPY over last 5 days": what's the edge?

This tells you whether RS actually helps or just reduces sample size without improving hit rate.

### 3. Volume Confirmation

Springs/upthrusts are more meaningful when the reversal bar has above-average volume. Add:

```python
vol_confirm = bars[idx]["volume"] > 1.3 * avg_volume_20d
```

Then backtest the filtered version. If it helps, add it to the live detectors.

### 4. Sector/Industry Rotation

The watchlist is heavy tech/AI. These names move together. Add:

- **Sector ETF regime** (XLK, SMH) — only take tech longs when XLK > 20d SMA
- **Rotation signal** — when money flows OUT of tech (XLK underperforming SPY), tech springs are fighting the tide

---

## Category 2: Better Entry Timing / Trade Management

The signals might be directionally right but entered too early or held wrong.

### 5. Multi-Timeframe Confirmation

Currently daily-only. Add:

- **Weekly chart context** — is the daily spring happening at a weekly support level too?
- **Intraday entry** — fire the daily alert, but wait for an intraday spring (hourly/15m) to actually enter

This requires premium data (intraday costs money on Twelve Data), but dramatically improves entries.

### 6. Dynamic Position Sizing Based on Conviction

Not all springs are equal. Create a "signal strength" score:

- Spring at a level tested 3+ times = stronger
- Spring with volume spike = stronger
- Spring in a name showing RS = stronger
- Spring in an uptrending sector = stronger

Then size positions: strong signals get full size, weak signals get half or skip.

### 7. Trailing Stop / Target System

Add automated exit logic to the journal:

- **Initial stop**: below the spring low (the whole thesis fails if that breaks)
- **First target**: recent swing high or 1R (risk unit)
- **Trail**: once +1R, move stop to breakeven

Track these in `trades.csv` and measure whether discretionary exits beat the mechanical rules.

---

## Category 3: Learn Faster From Your Trades

### 8. Trade Replay / Post-Mortem Generator

Build a script that, for each closed trade, generates:

- Chart at entry (what you saw)
- Chart at exit (what actually happened)
- What the signal was, what the thesis was
- Whether similar signals in the past worked

This forces deliberate review. Most learning happens post-trade, not during.

### 9. Pattern Recognition on YOUR Trades

Once you have 50+ closed trades, run analytics on YOUR data:

- Which setups do YOU trade well? (Not which setups backtest well — which ones YOU execute well)
- Do you have a "Tuesday problem"? (Certain days worse)
- Do you hold winners long enough? (Compare avg bars held for wins vs losses)
- Do you overtrade after a loss?

This is already partially in `trade_analytics.py` — expand it.

### 10. Win/Loss Journaling with Tags

Add free-form tags to trades: `rushed_entry`, `chased`, `perfect_plan`, `news_driven`, `FOMO`, `revenge_trade`. Then analyze:

- What % of `rushed_entry` trades win?
- What's your edge on `perfect_plan` trades?

Your leaks are behavioral, not technical. The signals are just prompts — your execution is where money is made or lost.

---

## Category 4: Expand the Universe

### 11. Futures / Forex Scanner

Wyckoff applies to anything with volume. Add ES (S&P futures), NQ (Nasdaq), GC (gold), CL (oil). These have 24-hour data and different character than equities.

### 12. Crypto Scanner

BTC, ETH have strong trending behavior and retail-driven volume patterns. Wyckoff climaxes (SC/BC) might actually work better here because retail capitulation is more pronounced.

### 13. Small-Cap / Momentum Screen

The current watchlist is large-cap. Add a momentum screen (e.g., stocks up 50%+ in 3 months, pulling back) — these are where springs into support have higher probability because there's a real trend to resume.

---

## Category 5: Reduce Manual Work

### 14. Auto-Fetch IV Rank

Currently IV rank is manually input. Add:

- Fetch IV rank from a free source (or scrape it)
- Auto-reject signals where IV rank > 50 (you're overpaying for options)
- Or: switch to credit spreads when IV is high (sell premium instead of buy)

### 15. Earnings Auto-Gate

The `earnings_calendar.py` exists. Wire it into the scanner:

- If earnings < 10 days away, DON'T fire the signal (or add a warning)
- Post-earnings springs (the day after a gap) might have better edge — test separately

### 16. Broker Integration (Paper First)

Add paper-trading execution via Alpaca or IBKR API:

- When a high-conviction signal fires, auto-place a limit order at the close
- Track fill rates, slippage
- This removes "I saw it but didn't act" bias from the journal

---

## Category 6: Validate Faster

### 17. Expand the Backtest Universe

The `--extra-tickers` flag exists for `backtest.py`. Run it on:

- Russell 2000 (small-caps)
- Different time periods (2008 crisis, 2020 COVID, 2022 bear)
- Sectors separately (do springs work better in energy than tech?)

### 18. Walk-Forward Optimization

Current backtest is in-sample. Add:

- Train parameters on 2005-2020
- Test on 2021-2025
- Does the edge hold out-of-sample?

### 19. Monte Carlo Equity Curves

Given signal hit rates and R-multiples, simulate 10,000 possible equity curves. This tells you:

- What's the probability of a 30% drawdown?
- How long might a losing streak last?
- Is position sizing survivable?

---

## Priority Ranking (Recommended Order)

Based on impact vs. effort:

| Priority | Improvement | Why |
|----------|-------------|-----|
| 1 | Regime Filter | Highest potential to flip negative edge to positive. Gate signals by SPY > 200d SMA. |
| 2 | Trade YOUR Trades | Get to 50 closed trades, then analyze YOUR execution. Signal is 20%; execution is 80%. |
| 3 | Post-Mortem Habit | For every closed trade, write: what I expected, what happened, what I learned. |
| 4 | Kill the Losers | Disable the worst 8 signals. Run ONLY the best 2-3. Fewer, higher-quality alerts. |
| 5 | Volume Confirmation | Simple filter that may improve hit rate with minimal code change. |
| 6 | Expand Backtest | Test regime filter across different universes and time periods. |
| 7 | Earnings Auto-Gate | Already have the code; just wire it in as a filter. |

---

## Measuring Success

After implementing improvements, success is measured by:

1. **Live scored alerts** — does the hit rate improve after filtering?
2. **Your trade journal** — does YOUR P&L improve?
3. **Backtest delta** — does the filtered signal beat the unfiltered one historically?

The ultimate metric: **your account balance over 6-12 months**, not backtest results.

---

*Document created: August 2026*
*Last updated: August 2026*
