"""
Additional Wyckoff-adjacent pattern detectors, beyond the spring/upthrust
Wheel Zones logic in wyckoff_scanner.py / Wyckoff Wheel Zones.pine.

Every function here is a HEURISTIC approximation of a textbook concept, not
an authoritative definition -- these patterns are inherently a matter of
analyst judgment. Thresholds are documented inline so they can be tuned
against real chart reads, the same way the Weis Wave flag ratio was tuned.

All functions take `bars` (list of {date, open, high, low, close, volume},
ascending by date) and reuse ATR/pivot arrays from wyckoff_common where
relevant, rather than recomputing them.
"""

import statistics

from wyckoff_common import zigzag, LEFT_BARS, RIGHT_BARS


# ---------------------------------------------------------------------------
# 1. Trading range (accumulation/distribution) detector
# ---------------------------------------------------------------------------
def trading_range(bars, window=40, max_range_pct=20.0):
    """Context filter: is price currently confined to a sideways band?
    Looks at the last `window` bars; flags a range if the high-low spread
    over that window is under `max_range_pct` of the range low. This is a
    simple compression check, not phase-aware (doesn't try to tell
    accumulation apart from distribution -- that's what the other
    detectors below are for, conditioned on being inside a range)."""
    n = len(bars)
    if n < window:
        return None
    recent = bars[-window:]
    range_high = max(b["high"] for b in recent)
    range_low = min(b["low"] for b in recent)
    if range_low <= 0:
        return None
    range_pct = (range_high - range_low) / range_low * 100
    return {
        "inRange": range_pct <= max_range_pct,
        "rangeHigh": range_high,
        "rangeLow": range_low,
        "rangePct": range_pct,
    }


# ---------------------------------------------------------------------------
# 2. Buying/Selling Climax + Automatic Reaction/Rally
# ---------------------------------------------------------------------------
def climax_events(bars, atr, trend_lookback=10, trend_move_pct=5.0,
                   vol_mult=2.0, range_mult=1.8, close_pos_thresh=0.35,
                   ar_lookforward=15):
    """Selling Climax (SC): sharp decline, volume + range spike, closes near
    the bar's low, after a real prior decline -- classic panic-selling bottom.
    Buying Climax (BC): mirror image at a top.

    For each SC/BC found, scans forward up to `ar_lookforward` bars for the
    first opposing pivot (the Automatic Rally after an SC, or the Automatic
    Reaction after a BC) which sets the other side of the resulting range.
    Returns a list of dicts; 'arIdx' is None if no AR has confirmed yet."""
    n = len(bars)
    events = []
    if n < trend_lookback + 21:
        return events

    for i in range(20, n):
        vol_avg20 = statistics.mean(b["volume"] for b in bars[i - 20:i])
        bar = bars[i]
        bar_range = bar["high"] - bar["low"]
        if bar_range <= 0 or vol_avg20 <= 0 or atr[i] is None:
            continue
        vol_spike = bar["volume"] > vol_mult * vol_avg20
        range_spike = bar_range > range_mult * atr[i]
        if not (vol_spike and range_spike):
            continue

        prior_close = bars[i - trend_lookback]["close"]
        if prior_close <= 0:
            continue
        move_pct = (bar["close"] - prior_close) / prior_close * 100
        close_pos = (bar["close"] - bar["low"]) / bar_range  # 0 = closed at low, 1 = closed at high

        kind = None
        if move_pct <= -trend_move_pct and close_pos <= close_pos_thresh:
            kind = "SC"
        elif move_pct >= trend_move_pct and close_pos >= 1 - close_pos_thresh:
            kind = "BC"
        if kind is None:
            continue

        ar_idx, ar_price = None, None
        want_high = kind == "SC"  # AR after SC is a rally (pivot high); after BC is a reaction (pivot low)
        for j in range(i + 1, min(i + 1 + ar_lookforward, n - RIGHT_BARS)):
            k = j
            if k < LEFT_BARS or k + RIGHT_BARS >= n:
                continue
            window = bars[k - LEFT_BARS:k + RIGHT_BARS + 1]
            if want_high and bar["high"] <= 0:
                continue
            if want_high:
                if bars[k]["high"] == max(b["high"] for b in window):
                    ar_idx, ar_price = k, bars[k]["high"]
                    break
            else:
                if bars[k]["low"] == min(b["low"] for b in window):
                    ar_idx, ar_price = k, bars[k]["low"]
                    break

        events.append({
            "idx": i, "date": bar["date"], "type": kind, "price": bar["close"],
            "arIdx": ar_idx, "arPrice": ar_price,
        })
    return events


# ---------------------------------------------------------------------------
# 3. Sign of Strength/Weakness + Last Point of Support/Supply
# ---------------------------------------------------------------------------
def sos_sow_events(bars, res, sup, atr, vol_mult=1.5, range_mult=1.3):
    """SOS: a wide-range, high-volume close ABOVE the last confirmed
    resistance -- demand has genuinely overwhelmed supply (as opposed to an
    upthrust, which fails and reverses). SOW is the mirror at support."""
    n = len(bars)
    events = []
    for i in range(20, n):
        vol_avg20 = statistics.mean(b["volume"] for b in bars[i - 20:i])
        bar = bars[i]
        bar_range = bar["high"] - bar["low"]
        if bar_range <= 0 or vol_avg20 <= 0 or atr[i] is None:
            continue
        strong_vol = bar["volume"] > vol_mult * vol_avg20
        wide_range = bar_range > range_mult * atr[i]
        if not (strong_vol and wide_range):
            continue
        if res[i] is not None and bar["close"] > res[i] and bar["close"] > bar["open"]:
            events.append({"idx": i, "date": bar["date"], "type": "SOS", "level": res[i]})
        elif sup[i] is not None and bar["close"] < sup[i] and bar["close"] < bar["open"]:
            events.append({"idx": i, "date": bar["date"], "type": "SOW", "level": sup[i]})
    return events


def lps_lpsy_events(bars, sos_sow, atr, lookforward=20, zone_mult=0.6):
    """After an SOS, the Last Point of Support (LPS) is the pullback that
    holds at or above the broken resistance (now acting as support) --
    conventionally the actual entry, since the SOS breakout itself is often
    already extended. LPSY mirrors this after an SOW."""
    n = len(bars)
    results = []
    for e in sos_sow:
        level = e["level"]
        for j in range(e["idx"] + 1, min(e["idx"] + 1 + lookforward, n)):
            if atr[j] is None:
                continue
            bar = bars[j]
            if e["type"] == "SOS":
                if abs(bar["low"] - level) <= zone_mult * atr[j] and bar["close"] > level:
                    results.append({"idx": j, "date": bar["date"], "type": "LPS",
                                     "level": level, "sourceIdx": e["idx"]})
                    break
                if bar["close"] < level:  # broke back below -- SOS failed, stop looking
                    break
            else:
                if abs(bar["high"] - level) <= zone_mult * atr[j] and bar["close"] < level:
                    results.append({"idx": j, "date": bar["date"], "type": "LPSY",
                                     "level": level, "sourceIdx": e["idx"]})
                    break
                if bar["close"] > level:
                    break
    return results


# ---------------------------------------------------------------------------
# 4. Elliott-style ABC correction
# ---------------------------------------------------------------------------
def abc_pattern(bars, min_b_retrace=0.30, max_b_retrace=0.79,
                min_c_ext=0.618, max_c_ext=1.618):
    """Looks at the last 4 confirmed zigzag swing points (P0-P1-P2-P3).
    P0->P1 = leg A, P1->P2 = leg B (a partial retracement of A),
    P2->P3 = leg C (resumes A's direction). Flags completion once P3 is
    confirmed. Tolerances are Elliott-style ranges, not strict Fibonacci --
    per user: this is the loose corrective-wave definition, not the
    harmonic ABCD pattern."""
    swings = zigzag(bars)
    if len(swings) < 4:
        return None
    p0, p1, p2, p3 = swings[-4], swings[-3], swings[-2], swings[-1]

    a_len = abs(p1["price"] - p0["price"])
    b_len = abs(p2["price"] - p1["price"])
    c_len = abs(p3["price"] - p2["price"])
    if a_len <= 0:
        return None

    b_retrace = b_len / a_len
    c_ext = c_len / a_len
    a_dir = p1["price"] - p0["price"]
    c_dir = p3["price"] - p2["price"]
    same_direction = a_dir * c_dir > 0

    valid = (min_b_retrace <= b_retrace <= max_b_retrace and
             min_c_ext <= c_ext <= max_c_ext and same_direction)
    if not valid:
        return None

    n = len(bars)
    # Fire ONLY on the exact confirmation day: point C's pivot is RIGHT_BARS
    # bars back, which is the first bar with enough right-side data to confirm
    # it as a pivot under the zigzag rule. This (a) makes the live signal match
    # what the backtest measured (entry at p3.idx + RIGHT_BARS), and (b) means
    # the C pivot cannot repaint -- earlier we fired on an unconfirmed C that
    # could still dissolve, so the live signal didn't match the tested one.
    is_new = p3["idx"] == n - 1 - RIGHT_BARS
    direction = "bullish (C down, expect resumption up)" if c_dir < 0 else "bearish (C up, expect resumption down)"
    return {
        "isNew": is_new,
        "direction": direction,
        # 0-A-B-C labeling: start (0) -> point A (end of leg A) -> point B -> point C
        "start": p0, "pointA": p1, "pointB": p2, "pointC": p3,
        "bRetrace": b_retrace, "cExtension": c_ext,
    }
