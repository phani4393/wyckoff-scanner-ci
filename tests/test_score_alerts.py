"""
Tests for score_alerts.py against synthetic bars/alerts -- no network calls.
Monkeypatches wyckoff_common.fetch_bars (the only network boundary) and the
module's ALERTS_LOG/SCORED_LOG paths (the only filesystem boundary) so the
scoring math and idempotency can be checked deterministically.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import wyckoff_common as c
import score_alerts as sa


def make_bars(n, start="2026-01-02", prices=None):
    """Sequential trading-day bars (weekends skipped isn't modeled -- date
    values just need to be distinct and ordered, which is all the entry-index
    lookup and forward_return math actually depend on)."""
    import datetime as dt
    d = dt.date.fromisoformat(start)
    out = []
    for i in range(n):
        price = prices[i] if prices else 100.0
        out.append({"date": d.isoformat(), "open": price, "high": price + 1,
                     "low": price - 1, "close": price, "volume": 1000})
        d += dt.timedelta(days=1)
    return out


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


ALERT_FIELDS = ["logged_at", "source", "sym", "setup", "direction", "thesis", "underlying"]


def _patch_paths(monkeypatch, tmp_path):
    alerts_log = tmp_path / "alerts_log.csv"
    scored_log = tmp_path / "alerts_scored.csv"
    monkeypatch.setattr(sa, "ALERTS_LOG", alerts_log)
    monkeypatch.setattr(sa, "SCORED_LOG", scored_log)
    return alerts_log, scored_log


def test_find_entry_idx_exact_match_and_miss():
    bars = make_bars(5, start="2026-01-02")
    assert sa._find_entry_idx(bars, "2026-01-04") == 2
    assert sa._find_entry_idx(bars, "2099-01-01") is None


def test_score_pending_bullish_hit(monkeypatch, tmp_path):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    # Flat 100 for the first 10 bars, then a step up to 110 -- a long_call
    # entered on day 9 (close=100) held 5 bars lands on day 14 (close=110),
    # a clean +10% signed return.
    prices = [100.0] * 10 + [110.0] * 25
    bars = make_bars(35, start="2026-01-02", prices=prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)

    entry_date = bars[9]["date"]
    write_csv(alerts_log, [{
        "logged_at": f"{entry_date}T20:35:00Z", "source": "watchlist", "sym": "TEST",
        "setup": "spring", "direction": "long_call", "thesis": "test", "underlying": 100.0,
    }], ALERT_FIELDS)

    new_rows = sa.score_pending(api_key="fake")
    by_h = {int(r["horizon_days"]): r for r in new_rows}
    assert set(by_h) == {5, 10, 20}
    assert by_h[5]["hit"] == 1
    assert abs(by_h[5]["stock_return_pct"] - 10.0) < 1e-6
    assert by_h[5]["exit_date"] == bars[14]["date"]


def test_score_pending_bearish_flips_sign(monkeypatch, tmp_path):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    # Same underlying move (100 -> 110, i.e. UP), but a long_put alert should
    # score this as a LOSS (signed return negative) since a put wants the
    # underlying to fall.
    prices = [100.0] * 10 + [110.0] * 15
    bars = make_bars(25, start="2026-01-02", prices=prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)

    entry_date = bars[9]["date"]
    write_csv(alerts_log, [{
        "logged_at": f"{entry_date}T20:35:00Z", "source": "watchlist", "sym": "TEST",
        "setup": "upthrust", "direction": "long_put", "thesis": "test", "underlying": 100.0,
    }], ALERT_FIELDS)

    new_rows = sa.score_pending(api_key="fake")
    by_h = {int(r["horizon_days"]): r for r in new_rows}
    assert by_h[5]["hit"] == 0
    assert abs(by_h[5]["stock_return_pct"] - (-10.0)) < 1e-6


def test_score_pending_horizon_not_yet_elapsed(monkeypatch, tmp_path):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    # Only 3 bars exist after entry -- not enough for even the 5d horizon.
    bars = make_bars(13, start="2026-01-02")
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)

    entry_date = bars[9]["date"]
    write_csv(alerts_log, [{
        "logged_at": f"{entry_date}T20:35:00Z", "source": "watchlist", "sym": "TEST",
        "setup": "spring", "direction": "long_call", "thesis": "test", "underlying": 100.0,
    }], ALERT_FIELDS)

    new_rows = sa.score_pending(api_key="fake")
    assert new_rows == []


def test_score_pending_is_idempotent(monkeypatch, tmp_path):
    alerts_log, scored_log = _patch_paths(monkeypatch, tmp_path)
    prices = [100.0] * 10 + [110.0] * 25
    bars = make_bars(35, start="2026-01-02", prices=prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)

    entry_date = bars[9]["date"]
    write_csv(alerts_log, [{
        "logged_at": f"{entry_date}T20:35:00Z", "source": "watchlist", "sym": "TEST",
        "setup": "spring", "direction": "long_call", "thesis": "test", "underlying": 100.0,
    }], ALERT_FIELDS)

    first = sa.score_pending(api_key="fake")
    assert len(first) == 3  # one row per horizon
    second = sa.score_pending(api_key="fake")
    assert second == []  # nothing new -- already scored

    with open(scored_log, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3  # no duplicates written
