"""
Pre-trade context an options buyer actually needs, computed from the daily
bars we already fetch (free -- no options/IV feed required).

What this DOES give you (free):
  - Expected move: a realized-volatility estimate of how far the underlying
    is likely to travel over your holding horizon (1 sigma). This is the
    single most useful free number for STRIKE SELECTION -- a strike well
    beyond ~1 sigma is a low-probability lotto; a target inside ~1 sigma is
    reachable on normal movement.

What it does NOT give you (needs paid data, ~$229/mo Twelve Data Pro):
  - IV RANK / implied vol -- so this can't tell you if the option is rich or
    cheap. Check IV rank in your broker before buying. (Rule of thumb: high
    IV rank favors debit spreads over naked longs to cut vega/theta bleed.)
  - Automatic earnings dates -- enter the earnings date when you log the
    trade (trade_journal.py --earnings) and the journal warns you.

Realized vol is a rough proxy for the market's implied move; when your
option's breakeven needs a move much larger than the expected move below,
you're likely overpaying for time/vol.

Usage (standalone):
  python pretrade.py NVDA --horizon 30
"""

import argparse
import math
import statistics

import wyckoff_common as c

VOL_WINDOW = 20  # trading days of returns for the realized-vol estimate


def realized_daily_vol(bars, window=VOL_WINDOW):
    if len(bars) < window + 1:
        return None
    closes = [b["close"] for b in bars[-(window + 1):]]
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 2:
        return None
    return statistics.stdev(rets)


def expected_move(bars, horizon_days):
    """1-sigma expected move over `horizon_days` trading days, from realized
    vol. Returns dict or None. Linear approximation (fine for the ~1 sigma,
    few-week horizons an options buyer cares about)."""
    dv = realized_daily_vol(bars)
    if dv is None or not bars:
        return None
    close = bars[-1]["close"]
    move_pct = dv * math.sqrt(max(horizon_days, 1))
    move_abs = close * move_pct
    return {
        "close": close,
        "daily_vol_pct": dv * 100,
        "horizon_days": horizon_days,
        "move_pct": move_pct * 100,
        "move_abs": move_abs,
        "low": close - move_abs,
        "high": close + move_abs,
    }


def format_line(em, direction=None):
    """One-line summary for a notification. direction in {'bullish','bearish'}
    highlights the relevant side of the range."""
    if not em:
        return "expected move: n/a (insufficient history)"
    base = (f"~{em['horizon_days']}d expected move +/-{em['move_pct']:.1f}% "
            f"(+/-${em['move_abs']:.2f}); 1-sigma range ${em['low']:.2f}-${em['high']:.2f}")
    if direction == "bullish":
        base += f"; realistic upside target ~${em['high']:.2f}"
    elif direction == "bearish":
        base += f"; realistic downside target ~${em['low']:.2f}"
    return base


def main():
    ap = argparse.ArgumentParser(description="Free pre-trade context (expected move) for an options buyer")
    ap.add_argument("ticker")
    ap.add_argument("--horizon", type=int, default=30, help="holding horizon in trading days (default 30)")
    a = ap.parse_args()
    key = c.load_api_key()
    bars = c.fetch_bars(a.ticker.upper(), key)
    if not bars:
        raise SystemExit(f"Could not fetch {a.ticker}")
    em = expected_move(bars, a.horizon)
    print(f"{a.ticker.upper()}: {format_line(em)}")
    print("  (IV rank NOT included -- needs a paid options feed; check it in your broker before buying.)")


if __name__ == "__main__":
    main()
