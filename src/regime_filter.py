"""
Market regime filter for live signal gating.

The backtests in regime_analysis.py showed that springs/upthrusts have
negative edge in BOTH bull and bear regimes — but the magnitude differs,
and more importantly, trading WITH the broad trend (bullish signals in bull
regimes, bearish signals in bear regimes) is less bad than fighting it.

This module provides a simple API for the live scanners:
  1. get_regime(spy_bars) -> dict with regime info and signal gates
  2. should_take_signal(regime, direction) -> bool

The regime definition (SPY close > 200d SMA = bull) is locked to match
regime_analysis.py — changing it here without re-running the backtest
would invalidate the statistical basis for the filter.

Usage in scanners:
    from regime_filter import get_regime, should_take_signal

    spy_bars = fetch_bars("SPY", api_key)
    regime = get_regime(spy_bars)

    # Before firing a bullish signal (spring, SC, SOS, LPS, bullish ABC):
    if should_take_signal(regime, "bullish"):
        # fire the alert
    else:
        # skip or downgrade to "regime-filtered" note

    # Before firing a bearish signal (upthrust, BC, SOW, LPSY, bearish ABC):
    if should_take_signal(regime, "bearish"):
        # fire the alert

The filter is OPTIONAL — scanners can still fire alerts with a regime warning
instead of suppressing them entirely, letting the trader make the final call.
"""

import statistics

SMA_LEN = 200

# Regime mode controls how strict the filter is:
#   "strict"   = only take signals aligned with regime (bull->bullish, bear->bearish)
#   "permissive" = take all signals but add regime context to the alert
#   "adaptive" = in strong trends, strict; in choppy regimes, permissive
DEFAULT_MODE = "strict"


def _sma(bars, length):
    """Simple moving average, returns list aligned with bars (None for first length-1 bars)."""
    if len(bars) < length:
        return [None] * len(bars)
    out = [None] * len(bars)
    running = sum(b["close"] for b in bars[:length])
    out[length - 1] = running / length
    for i in range(length, len(bars)):
        running += bars[i]["close"] - bars[i - length]["close"]
        out[i] = running / length
    return out


def get_regime(spy_bars, mode=None):
    """
    Determine current market regime from SPY bars.

    Returns a dict with:
        regime: "bull" | "bear" | "unknown"
        spy_close: current SPY close
        sma_200: current 200-day SMA value
        distance_pct: how far price is from the SMA (positive = above)
        trend_strength: "strong" | "weak" | "neutral" based on distance
        take_bullish: whether to take bullish signals
        take_bearish: whether to take bearish signals
        mode: the filtering mode used
        message: human-readable regime summary
    """
    mode = mode or DEFAULT_MODE

    if not spy_bars or len(spy_bars) < SMA_LEN:
        return {
            "regime": "unknown",
            "spy_close": None,
            "sma_200": None,
            "distance_pct": None,
            "trend_strength": "unknown",
            "take_bullish": True,  # fail open — don't block signals if we can't determine regime
            "take_bearish": True,
            "mode": mode,
            "message": "Insufficient SPY data for regime detection — all signals allowed",
        }

    sma = _sma(spy_bars, SMA_LEN)
    spy_close = spy_bars[-1]["close"]
    sma_200 = sma[-1]

    if sma_200 is None or sma_200 <= 0:
        return {
            "regime": "unknown",
            "spy_close": spy_close,
            "sma_200": None,
            "distance_pct": None,
            "trend_strength": "unknown",
            "take_bullish": True,
            "take_bearish": True,
            "mode": mode,
            "message": "Could not compute SPY 200d SMA — all signals allowed",
        }

    regime = "bull" if spy_close > sma_200 else "bear"
    distance_pct = ((spy_close - sma_200) / sma_200) * 100

    # Trend strength: how decisively is SPY above/below the SMA?
    # >3% above = strong bull, <-3% below = strong bear, else weak/neutral
    if distance_pct > 3.0:
        trend_strength = "strong"
    elif distance_pct < -3.0:
        trend_strength = "strong"
    elif abs(distance_pct) < 1.0:
        trend_strength = "neutral"
    else:
        trend_strength = "weak"

    # Determine signal gates based on mode
    if mode == "strict":
        take_bullish = regime == "bull"
        take_bearish = regime == "bear"
    elif mode == "permissive":
        take_bullish = True
        take_bearish = True
    elif mode == "adaptive":
        # In strong trends, be strict; in weak/neutral, be permissive
        if trend_strength == "strong":
            take_bullish = regime == "bull"
            take_bearish = regime == "bear"
        else:
            take_bullish = True
            take_bearish = True
    else:
        # Unknown mode — fail open
        take_bullish = True
        take_bearish = True

    # Build message
    regime_word = "BULL" if regime == "bull" else "BEAR"
    strength_word = f"({trend_strength})" if trend_strength != "neutral" else "(near SMA)"
    gate_msg = []
    if not take_bullish:
        gate_msg.append("bullish signals filtered")
    if not take_bearish:
        gate_msg.append("bearish signals filtered")
    gate_str = "; ".join(gate_msg) if gate_msg else "all signals allowed"

    message = (f"SPY {regime_word} regime {strength_word}: "
               f"close {spy_close:.2f} vs 200d SMA {sma_200:.2f} ({distance_pct:+.1f}%) — {gate_str}")

    return {
        "regime": regime,
        "spy_close": spy_close,
        "sma_200": sma_200,
        "distance_pct": distance_pct,
        "trend_strength": trend_strength,
        "take_bullish": take_bullish,
        "take_bearish": take_bearish,
        "mode": mode,
        "message": message,
    }


def should_take_signal(regime_info, direction):
    """
    Check if a signal should be taken given the current regime.

    Args:
        regime_info: dict returned by get_regime()
        direction: "bullish" or "bearish"

    Returns:
        True if the signal should be taken, False if it should be filtered.
    """
    if direction == "bullish":
        return regime_info.get("take_bullish", True)
    elif direction == "bearish":
        return regime_info.get("take_bearish", True)
    else:
        # Unknown direction — fail open
        return True


def regime_context_line(regime_info):
    """
    Returns a single-line context string for including in alerts.

    Example: "Regime: SPY BULL (strong), +4.2% above 200d SMA"
    """
    if regime_info["regime"] == "unknown":
        return "Regime: unknown (insufficient data)"

    regime_word = regime_info["regime"].upper()
    strength = regime_info["trend_strength"]
    dist = regime_info["distance_pct"]
    above_below = "above" if dist >= 0 else "below"

    return f"Regime: SPY {regime_word} ({strength}), {abs(dist):.1f}% {above_below} 200d SMA"


def get_signal_direction(setup):
    """
    Map setup names to their directional bias.

    Args:
        setup: signal type name (e.g., "spring", "upthrust", "sc", "bc")

    Returns:
        "bullish", "bearish", or None if unknown
    """
    bullish_setups = {"spring", "pure_spring", "sc", "sos", "lps", "abc_bullish"}
    bearish_setups = {"upthrust", "pure_upthrust", "bc", "sow", "lpsy", "abc_bearish"}

    setup_lower = setup.lower()

    if setup_lower in bullish_setups:
        return "bullish"
    elif setup_lower in bearish_setups:
        return "bearish"
    elif setup_lower == "abc":
        # ABC direction depends on the pattern — caller should specify
        return None
    else:
        return None


# ---------------------------------------------------------------------------
# CLI for manual regime check
# ---------------------------------------------------------------------------
def main():
    import argparse

    import wyckoff_common as c

    parser = argparse.ArgumentParser(description="Check current market regime")
    parser.add_argument("--mode", choices=["strict", "permissive", "adaptive"],
                        default=DEFAULT_MODE, help="Filter mode")
    args = parser.parse_args()

    api_key = c.load_api_key()
    print("Fetching SPY data...", flush=True)
    spy_bars = c.fetch_bars("SPY", api_key)

    if not spy_bars:
        print("ERROR: Could not fetch SPY data")
        return

    regime = get_regime(spy_bars, mode=args.mode)

    print()
    print("=" * 60)
    print("MARKET REGIME CHECK")
    print("=" * 60)
    print(f"SPY Close:     {regime['spy_close']:.2f}")
    print(f"200d SMA:      {regime['sma_200']:.2f}")
    print(f"Distance:      {regime['distance_pct']:+.2f}%")
    print(f"Regime:        {regime['regime'].upper()}")
    print(f"Trend Strength:{regime['trend_strength']}")
    print(f"Mode:          {regime['mode']}")
    print()
    print(f"Take Bullish:  {'YES' if regime['take_bullish'] else 'NO'}")
    print(f"Take Bearish:  {'YES' if regime['take_bearish'] else 'NO'}")
    print()
    print(f"Summary: {regime['message']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
