"""
Regression tests for the detector logic in wyckoff_common.py / wyckoff_patterns.py,
run against synthetic OHLC fixtures instead of live data. Before this file existed,
detector correctness was verified by hand-eyeballing one ticker (AAPL) once -- fine
for catching an obvious miss, but nothing would have caught a regression the next
time someone touched pivots()/zigzag()/abc_pattern(). These fixtures pin down the
exact textbook behavior that was hand-verified, so it can't silently drift.

Run with: python -m pytest tests/ -v   (from the repo root)

Fixture construction note: baseline bars use a negligible monotonic price drift
(1e-5/bar) rather than a truly flat price. A truly flat baseline ties for both
max() and min() in every pivot window, which makes wyckoff_common.pivots() flag
spurious pivots almost everywhere -- the drift breaks those ties without being
large enough to interfere with the real (much larger) injected excursions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import wyckoff_common as c
from wyckoff_patterns import abc_pattern

LEFT, RIGHT = c.LEFT_BARS, c.RIGHT_BARS


def make_flat_bars(n):
    out = []
    for i in range(n):
        price = 100.0 - i * 0.00001
        out.append({"date": f"2024-{i:05d}", "open": price, "high": price + 0.5,
                     "low": price - 0.5, "close": price, "volume": 1000})
    return out


def set_bar(bars, idx, **kwargs):
    bars[idx] = {**bars[idx], **kwargs}


def is_pure_spring(bars, sup, idx):
    b = bars[idx]
    return sup[idx] is not None and b["low"] < sup[idx] and b["close"] > sup[idx]


def is_pure_upthrust(bars, res, idx):
    b = bars[idx]
    return res[idx] is not None and b["high"] > res[idx] and b["close"] < res[idx]


# ---------------------------------------------------------------------------
# Spring: undercut-and-recover
# ---------------------------------------------------------------------------
def test_pure_spring_fires_on_undercut_and_recover():
    n = LEFT + RIGHT + 20
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, low=90.0, close=91.0)
    _, sup = c.pivots(bars)
    assert sup[pivot_idx + RIGHT] == 90.0, "support should confirm RIGHT_BARS after the pivot bar"

    spring_idx = pivot_idx + RIGHT + 5
    set_bar(bars, spring_idx, low=88.0, high=92.0, close=91.0, open=90.0)  # undercuts 90, closes back above it
    _, sup = c.pivots(bars)
    assert is_pure_spring(bars, sup, spring_idx), \
        "textbook spring (low undercuts support, close recovers above it) must fire"


def test_pure_spring_does_not_fire_on_genuine_breakdown():
    n = LEFT + RIGHT + 20
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, low=90.0, close=91.0)
    breakdown_idx = pivot_idx + RIGHT + 5
    set_bar(bars, breakdown_idx, low=87.0, high=89.0, close=87.5, open=88.0)  # undercuts AND stays below
    _, sup = c.pivots(bars)
    assert not is_pure_spring(bars, sup, breakdown_idx), \
        "a close that stays below support is a real breakdown, not a spring -- must not fire"


# ---------------------------------------------------------------------------
# Upthrust: poke-and-fail
# ---------------------------------------------------------------------------
def test_pure_upthrust_fires_on_poke_and_fail():
    n = LEFT + RIGHT + 20
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, high=110.0, close=109.0)
    res, _ = c.pivots(bars)
    assert res[pivot_idx + RIGHT] == 110.0, "resistance should confirm RIGHT_BARS after the pivot bar"

    ut_idx = pivot_idx + RIGHT + 5
    set_bar(bars, ut_idx, high=112.0, low=108.0, close=109.0, open=110.0)  # pokes above 110, closes back below it
    res, _ = c.pivots(bars)
    assert is_pure_upthrust(bars, res, ut_idx), \
        "textbook upthrust (high pokes above resistance, close fails back below it) must fire"


def test_pure_upthrust_does_not_fire_on_genuine_breakout():
    n = LEFT + RIGHT + 20
    bars = make_flat_bars(n)
    pivot_idx = LEFT
    set_bar(bars, pivot_idx, high=110.0, close=109.0)
    breakout_idx = pivot_idx + RIGHT + 5
    set_bar(bars, breakout_idx, high=112.0, low=110.5, close=111.0, open=110.0)  # pokes AND closes above
    res, _ = c.pivots(bars)
    assert not is_pure_upthrust(bars, res, breakout_idx), \
        "a close above resistance is a real breakout (SOS candidate), not an upthrust -- must not fire"


def test_no_spurious_signals_on_pure_noise_free_bars():
    """Flat (drift-only) bars with no injected excursion should never trip
    either detector -- there's no level for price to fail at."""
    bars = make_flat_bars(LEFT + RIGHT + 10)
    res, sup = c.pivots(bars)
    for j in range(len(bars)):
        assert not is_pure_spring(bars, sup, j)
        assert not is_pure_upthrust(bars, res, j)


# ---------------------------------------------------------------------------
# ABC: confirmation-day timing (regression for the lookahead bug that was
# found and fixed -- firing on the raw pivot day instead of pivot_idx +
# RIGHT_BARS measures a "forward return" starting from a point that wasn't
# actually knowable yet, and that point is by definition a local extreme,
# which manufactures a fake edge).
# ---------------------------------------------------------------------------
def _abc_fixture():
    """P0(low)->P1(high) leg A=60, P1->P2(low) leg B=36 (retrace 0.60),
    P2->P3(high) leg C=40 (extension 0.667) -- inside both Elliott tolerance
    bands, same direction as leg A, so abc_pattern() should accept it once
    P3's pivot is confirmable."""
    i0, i1, i2, i3 = 20, 50, 80, 110
    n = i3 + RIGHT + 15
    bars = make_flat_bars(n)
    set_bar(bars, i0, low=70.0, close=71.0)
    set_bar(bars, i1, high=130.0, close=129.0)
    set_bar(bars, i2, low=94.0, close=95.0)
    set_bar(bars, i3, high=134.0, close=133.5)
    return bars, i3


def test_abc_does_not_fire_before_point_c_is_confirmable():
    bars, i3 = _abc_fixture()
    # Right up through the pivot bar itself and each day before RIGHT_BARS
    # more have elapsed, point C isn't a confirmed pivot yet -- must be None.
    for extra in range(0, RIGHT):
        truncated = bars[: i3 + extra + 1]
        assert abc_pattern(truncated) is None, \
            f"point C not yet confirmable at +{extra} days past its pivot bar -- must not report a pattern"


def test_abc_fires_exactly_on_the_confirmation_day_only():
    bars, i3 = _abc_fixture()
    confirm_len = i3 + RIGHT + 1
    r = abc_pattern(bars[:confirm_len])
    assert r is not None and r["isNew"] is True, "must fire on the exact confirmation day"
    assert r["pointC"]["idx"] == i3

    # One bar later, the same completed pattern is still detectable (it's the
    # most recent 4 swings) but must NOT re-fire as "new" -- that would let a
    # live scanner send the same alert twice, and would let a backtest double
    # count the same event's forward return from two different start points.
    r_next = abc_pattern(bars[: confirm_len + 1])
    assert r_next is not None and r_next["isNew"] is False, \
        "must not re-fire as new on the day after confirmation"
