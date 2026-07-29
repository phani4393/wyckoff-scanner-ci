"""
Tests for wyckoff_watchlist_scanner.py's _draft() helper -- specifically that
it doesn't write a local trade_journal draft for an alert alert_log.py
already decided was a same-day duplicate (see test_alert_log.py). No network
calls; alert_log.log_alert and trade_journal.add_draft are monkeypatched.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import wyckoff_watchlist_scanner as wws


def test_draft_writes_journal_entry_when_newly_logged(monkeypatch):
    monkeypatch.setattr(wws.alert_log, "log_alert", lambda *a, **k: True)
    monkeypatch.setattr(wws, "DRAFT_JOURNAL", True)
    calls = []
    monkeypatch.setattr(wws.trade_journal, "add_draft", lambda *a, **k: calls.append(a))

    wws._draft("NVDA", "spring", "long_call", "thesis", 182.4)
    assert len(calls) == 1


def test_draft_skips_journal_entry_when_duplicate(monkeypatch):
    monkeypatch.setattr(wws.alert_log, "log_alert", lambda *a, **k: False)
    monkeypatch.setattr(wws, "DRAFT_JOURNAL", True)
    calls = []
    monkeypatch.setattr(wws.trade_journal, "add_draft", lambda *a, **k: calls.append(a))

    wws._draft("NVDA", "spring", "long_call", "thesis", 182.4)
    assert calls == []
