"""
Tests for alert_followup.py against synthetic bars/alerts -- no network calls.
Monkeypatches wyckoff_common.fetch_bars and the module's ALERTS_LOG/FOLLOWUP_LOG
paths, same pattern as test_score_alerts.py.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import wyckoff_common as c
import score_alerts as sa
import alert_followup as af

ALERT_FIELDS = ["logged_at", "source", "sym", "setup", "direction", "thesis", "underlying"]


def make_bars(prices, start="2026-01-02"):
    d = dt.date.fromisoformat(start)
    out = []
    for price in prices:
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


def _patch_paths(monkeypatch, tmp_path):
    alerts_log = tmp_path / "alerts_log.csv"
    followup_log = tmp_path / "alerts_followup.csv"
    monkeypatch.setattr(af, "ALERTS_LOG", alerts_log)
    monkeypatch.setattr(sa, "ALERTS_LOG", alerts_log)  # af.ALERTS_LOG is imported from sa
    monkeypatch.setattr(af, "FOLLOWUP_LOG", followup_log)
    return alerts_log, followup_log


def _write_alert(alerts_log, entry_date, sym="TEST", setup="spring", direction="long_call"):
    write_csv(alerts_log, [{
        "logged_at": f"{entry_date}T20:35:00Z", "source": "watchlist", "sym": sym,
        "setup": setup, "direction": direction, "thesis": "test", "underlying": 100.0,
    }], ALERT_FIELDS)


def test_no_followup_before_day_plus_one(monkeypatch, tmp_path):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    # Entry is the LAST bar -- no day+1 exists yet.
    bars = make_bars([100.0] * 10)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    _write_alert(alerts_log, bars[9]["date"])

    assert af.check_pending(api_key="fake") == []


def test_day1_and_day2_both_stronger(monkeypatch, tmp_path):
    alerts_log, followup_log = _patch_paths(monkeypatch, tmp_path)
    # Entry at bars[9], close=100. day+1=105 (+5%), day+2=110 (+10%) -- each
    # day beats the prior day's cumulative return -> STRONGER both times.
    prices = [100.0] * 10 + [105.0, 110.0]
    bars = make_bars(prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    _write_alert(alerts_log, bars[9]["date"])

    rows = af.check_pending(api_key="fake")
    by_day = {r["day_offset"]: r for r in rows}
    assert set(by_day) == {1, 2}
    assert by_day[1]["trend"] == "STRONGER"
    assert abs(by_day[1]["cumulative_return_pct"] - 5.0) < 1e-6
    assert by_day[2]["trend"] == "STRONGER"
    assert abs(by_day[2]["cumulative_return_pct"] - 10.0) < 1e-6


def test_day3_weaker_after_partial_giveback(monkeypatch, tmp_path):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    # day+1 +5%, day+2 +10%, day+3 +8% -- day+3 is still net positive but
    # WEAKER than day+2's peak, since it gave back some of the move.
    prices = [100.0] * 10 + [105.0, 110.0, 108.0]
    bars = make_bars(prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    _write_alert(alerts_log, bars[9]["date"])

    rows = af.check_pending(api_key="fake")
    by_day = {r["day_offset"]: r for r in rows}
    assert by_day[3]["trend"] == "WEAKER"
    assert abs(by_day[3]["cumulative_return_pct"] - 8.0) < 1e-6


def test_bearish_alert_flips_sign(monkeypatch, tmp_path):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    # Stock RISES 5% but this is a long_put -- that's a loss for the thesis,
    # so day+1 must be negative and WEAKER (below the 0% baseline).
    prices = [100.0] * 10 + [105.0]
    bars = make_bars(prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    _write_alert(alerts_log, bars[9]["date"], setup="upthrust", direction="long_put")

    rows = af.check_pending(api_key="fake")
    assert rows[0]["trend"] == "WEAKER"
    assert abs(rows[0]["cumulative_return_pct"] - (-5.0)) < 1e-6


def test_never_processes_past_day_3(monkeypatch, tmp_path):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    prices = [100.0] * 10 + [101.0, 102.0, 103.0, 104.0, 105.0]  # 5 days of history after entry
    bars = make_bars(prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    _write_alert(alerts_log, bars[9]["date"])

    rows = af.check_pending(api_key="fake")
    assert set(r["day_offset"] for r in rows) == {1, 2, 3}  # never 4 or 5


def test_idempotent(monkeypatch, tmp_path):
    alerts_log, followup_log = _patch_paths(monkeypatch, tmp_path)
    prices = [100.0] * 10 + [105.0]
    bars = make_bars(prices)
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    _write_alert(alerts_log, bars[9]["date"])

    first = af.check_pending(api_key="fake")
    assert len(first) == 1
    second = af.check_pending(api_key="fake")
    assert second == []

    with open(followup_log, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1


def test_format_followups_empty():
    assert af.format_followups([]) == ""


def test_format_followups_lists_each_alert():
    rows = [
        {"sym": "NVDA", "setup": "spring", "direction": "long_call", "day_offset": 1,
         "check_date": "2026-07-22", "cumulative_return_pct": 5.0, "trend": "STRONGER"},
        {"sym": "OKTA", "setup": "upthrust", "direction": "long_put", "day_offset": 3,
         "check_date": "2026-07-24", "cumulative_return_pct": -2.0, "trend": "WEAKER"},
    ]
    msg = af.format_followups(rows)
    assert "2 alert(s)" in msg
    assert "NVDA spring (long_call), day+1" in msg and "STRONGER" in msg
    assert "OKTA upthrust (long_put), day+3" in msg and "WEAKER" in msg


def test_context_only_alerts_are_skipped(monkeypatch, tmp_path):
    # weis_wave alerts have direction=None/"" -- no thesis direction to track.
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    bars = make_bars([100.0] * 10 + [105.0])
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    write_csv(alerts_log, [{
        "logged_at": f"{bars[9]['date']}T20:35:00Z", "source": "sp500_sweep", "sym": "TEST",
        "setup": "weis_wave", "direction": "", "thesis": "context only", "underlying": 100.0,
    }], ALERT_FIELDS)

    assert af.check_pending(api_key="fake") == []


def test_main_dry_run_previews_without_sending(monkeypatch, tmp_path, capsys):
    alerts_log, _ = _patch_paths(monkeypatch, tmp_path)
    bars = make_bars([100.0] * 10 + [105.0])
    monkeypatch.setattr(c, "fetch_bars", lambda sym, key: bars)
    monkeypatch.setattr(c, "load_api_key", lambda: "fake")
    write_csv(alerts_log, [{
        "logged_at": f"{bars[9]['date']}T20:35:00Z", "source": "watchlist", "sym": "TEST",
        "setup": "spring", "direction": "long_call", "thesis": "test", "underlying": 100.0,
    }], ALERT_FIELDS)

    def boom(*a, **k):
        raise AssertionError("wyckoff_notify.send_message must not be called in --dry-run")
    import wyckoff_notify
    monkeypatch.setattr(wyckoff_notify, "send_message", boom)

    monkeypatch.setattr(sys, "argv", ["alert_followup.py", "--dry-run"])
    af.main()
    out = capsys.readouterr().out
    assert "DRY RUN: would push this message to Telegram" in out


def test_main_dry_run_with_nothing_new_skips_cleanly(monkeypatch, tmp_path, capsys):
    _patch_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(c, "load_api_key", lambda: "fake")
    monkeypatch.setattr(sys, "argv", ["alert_followup.py", "--dry-run"])
    af.main()
    out = capsys.readouterr().out
    assert "skipping Telegram push" in out
