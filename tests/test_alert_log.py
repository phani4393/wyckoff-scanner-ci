"""
Tests for alert_log.py's same-day dedup guard -- no network calls, just CSV
read/write against a monkeypatched LOG_FILE path.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import alert_log as al


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_first_log_of_the_day_writes(monkeypatch, tmp_path):
    log_file = tmp_path / "alerts_log.csv"
    monkeypatch.setattr(al, "LOG_FILE", log_file)
    result = al.log_alert("watchlist", "NVDA", "spring", "long_call", "thesis", 182.4)
    assert result is True
    assert len(_rows(log_file)) == 1


def test_same_ticker_setup_direction_same_day_is_skipped(monkeypatch, tmp_path):
    log_file = tmp_path / "alerts_log.csv"
    monkeypatch.setattr(al, "LOG_FILE", log_file)
    al.log_alert("watchlist", "NVDA", "spring", "long_call", "thesis one", 182.4)
    result = al.log_alert("sp500_sweep", "NVDA", "spring", "long_call", "thesis two", 182.5)
    rows = _rows(log_file)
    assert result is False
    assert len(rows) == 1
    assert rows[0]["thesis"] == "thesis one"  # first one wins, second is dropped


def test_different_setup_same_ticker_same_day_both_logged(monkeypatch, tmp_path):
    log_file = tmp_path / "alerts_log.csv"
    monkeypatch.setattr(al, "LOG_FILE", log_file)
    al.log_alert("watchlist", "NVDA", "spring", "long_call", "t1", 182.4)
    result = al.log_alert("watchlist", "NVDA", "sos", "long_call", "t2", 185.0)
    assert result is True
    assert len(_rows(log_file)) == 2


def test_different_direction_same_setup_same_day_both_logged(monkeypatch, tmp_path):
    # abc can legitimately be bullish or bearish -- direction, not just
    # setup, has to be part of the dedup key.
    log_file = tmp_path / "alerts_log.csv"
    monkeypatch.setattr(al, "LOG_FILE", log_file)
    al.log_alert("watchlist", "NVDA", "abc", "long_call", "t1", 182.4)
    result = al.log_alert("watchlist", "NVDA", "abc", "long_put", "t2", 182.4)
    assert result is True
    assert len(_rows(log_file)) == 2


def test_same_setup_next_day_is_logged(monkeypatch, tmp_path):
    log_file = tmp_path / "alerts_log.csv"
    monkeypatch.setattr(al, "LOG_FILE", log_file)
    monkeypatch.setattr(al, "_now", lambda: "2026-07-21T21:24:00Z")
    al.log_alert("watchlist", "NVDA", "spring", "long_call", "t1", 182.4)
    monkeypatch.setattr(al, "_now", lambda: "2026-07-22T21:24:00Z")
    result = al.log_alert("watchlist", "NVDA", "spring", "long_call", "t2", 190.0)
    assert result is True
    assert len(_rows(log_file)) == 2  # a genuinely new day's test of the level
