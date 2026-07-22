"""
Tests for trade_journal.py's earnings auto-fetch wiring (_resolve_earnings)
and its use in cmd_open. No network calls -- earnings_calendar.get_next_earnings_date
is monkeypatched at the point trade_journal imports it.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import trade_journal as tj
import earnings_calendar as ec


def test_resolve_earnings_explicit_always_wins(monkeypatch):
    # Auto-fetch must not even be consulted when --earnings was given.
    def boom(ticker):
        raise AssertionError("auto-fetch should not be called when --earnings is explicit")
    monkeypatch.setattr(ec, "get_next_earnings_date", boom)
    assert tj._resolve_earnings("NVDA", "2026-08-27") == "2026-08-27"


def test_resolve_earnings_auto_fetch_used_when_omitted(monkeypatch, capsys):
    monkeypatch.setattr(ec, "get_next_earnings_date", lambda ticker: ("2026-08-26", False))
    result = tj._resolve_earnings("NVDA", None)
    assert result == "2026-08-26"
    assert "confirmed" in capsys.readouterr().out


def test_resolve_earnings_marks_estimate(monkeypatch, capsys):
    monkeypatch.setattr(ec, "get_next_earnings_date", lambda ticker: ("2026-09-01", True))
    result = tj._resolve_earnings("XYZ", None)
    assert result == "2026-09-01"
    assert "estimated" in capsys.readouterr().out


def test_resolve_earnings_fails_soft_on_lookup_failure(monkeypatch):
    monkeypatch.setattr(ec, "get_next_earnings_date", lambda ticker: (None, None))
    assert tj._resolve_earnings("NVDA", None) is None


def test_resolve_earnings_fails_soft_on_exception(monkeypatch):
    def boom(ticker):
        raise RuntimeError("network exploded")
    monkeypatch.setattr(ec, "get_next_earnings_date", boom)
    assert tj._resolve_earnings("NVDA", None) is None


def test_cmd_open_auto_fetches_when_earnings_omitted(monkeypatch, tmp_path):
    monkeypatch.setattr(tj, "JOURNAL", tmp_path / "trades.csv")
    monkeypatch.setattr(ec, "get_next_earnings_date", lambda ticker: ("2026-08-26", False))

    args = argparse.Namespace(
        dir="long_call", ticker="NVDA", setup="spring", thesis="test",
        underlying="182.40", opt_price="4.20", strike="190", expiry="2026-08-15",
        contracts="3", iv_rank=55.0, earnings=None, date=None, notes=None,
    )
    tj.cmd_open(args)

    with open(tj.JOURNAL, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["earnings_date"] == "2026-08-26"


def test_cmd_open_explicit_earnings_not_overridden(monkeypatch, tmp_path):
    monkeypatch.setattr(tj, "JOURNAL", tmp_path / "trades.csv")
    monkeypatch.setattr(ec, "get_next_earnings_date", lambda ticker: ("2099-01-01", False))

    args = argparse.Namespace(
        dir="long_call", ticker="NVDA", setup="spring", thesis="test",
        underlying="182.40", opt_price="4.20", strike="190", expiry="2026-08-15",
        contracts="3", iv_rank=55.0, earnings="2026-08-27", date=None, notes=None,
    )
    tj.cmd_open(args)

    with open(tj.JOURNAL, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["earnings_date"] == "2026-08-27"
