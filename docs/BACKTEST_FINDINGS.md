# Backtest findings — read before trusting any signal

**Bottom line: none of the implemented Wyckoff signals show a tradeable mechanical
edge.** They are shipped as *discretionary review triggers* — "come look at this
chart" — not as validated buy/sell signals. Apply your own judgment (market
context, IV, news, broader structure) before entering any option.

## What was tested

- Universe: the 44-name Core Watchlist (large-cap / growth / momentum names).
- History: up to ~20 years of daily bars per name (Twelve Data, `outputsize=5000`).
- For every historical instance of each signal, forward return at 5 / 10 / 20
  trading days, direction-adjusted for a **long option** (call for bullish, put
  for bearish).

## The fair baseline

The honest control is not "a random entry" — it's **the naive version of the same
idea**: just trade every confirmed swing point (long the swing lows, short the
swing highs), with none of the volume / RS / Elliott-ratio machinery.

- Buy every confirmed swing low: **73% hit rate, +4.7% avg over 10 days.**
- This is itself mostly "buy the dip on names that went up for 20 years" —
  survivorship + a secular bull market, **not** a proven forward edge.

## Results vs. that baseline (10-day)

| Signal | Hit rate | Edge vs. naive swing |
|---|---|---|
| ABC correction | 72% | **+0.7pp ≈ zero** (its earlier "+14pp" was the dip-buy artifact) |
| pure spring (textbook) | 54% | **−19pp** |
| pure upthrust (textbook, short) | 44% | **−26pp** (loses money outright) |
| SC / LPS / SOS (bullish) | 53–60% | −13 to −21pp |
| BC / SOW / LPSY / upthrust (bearish) | 40–46% | −17 to −28pp |

Notes:
- Accurate, hand-verified "textbook" spring/upthrust detectors were used — the
  failure is **not** a detection artifact.
- A 200-day-SMA trend filter did **not** help (72.6% vs 73.2%).
- ABC live timing was fixed to fire only on the confirmation bar, matching what
  the backtest measured (no repaint gap).

## What this does NOT prove

- It does not prove Wyckoff "doesn't work" — Wyckoff is discretionary and
  context-dependent; a mechanical proxy losing edge is consistent with that.
- It is in-sample on survivor names in a bull market. A real validated edge
  needs: a survivorship-free universe (incl. delisted names), out-of-sample /
  walk-forward testing, and transaction + options-cost modeling. That is the
  separate research track.
