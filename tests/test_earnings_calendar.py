"""
Tests for earnings_calendar.py's response-parsing logic -- no network calls.
The "real NVDA response" fixture below is a captured, real Yahoo
quoteSummary/calendarEvents payload (verified live before this module was
built), not a guess at the shape.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import earnings_calendar as ec

REAL_NVDA_RESPONSE = {
    "quoteSummary": {
        "result": [{
            "calendarEvents": {
                "maxAge": 1,
                "earnings": {
                    "earningsDate": [{"raw": 1787774400, "fmt": "2026-08-26"}],
                    "earningsCallDate": [{"raw": 1779310800, "fmt": "2026-05-20"}],
                    "isEarningsDateEstimate": False,
                    "earningsAverage": {"raw": 2.08225, "fmt": "2.08"},
                },
                "exDividendDate": {"raw": 1780531200, "fmt": "2026-06-04"},
                "dividendDate": {"raw": 1782432000, "fmt": "2026-06-26"},
            }
        }],
        "error": None,
    }
}


def test_parse_real_response_shape():
    date_str, is_estimate = ec._parse_calendar_response(REAL_NVDA_RESPONSE)
    assert date_str == "2026-08-26"
    assert is_estimate is False


def test_parse_estimate_flag_true():
    data = {"quoteSummary": {"result": [{"calendarEvents": {"earnings": {
        "earningsDate": [{"raw": 0, "fmt": "2026-09-01"}], "isEarningsDateEstimate": True,
    }}}]}}
    date_str, is_estimate = ec._parse_calendar_response(data)
    assert date_str == "2026-09-01"
    assert is_estimate is True


def test_parse_missing_result():
    assert ec._parse_calendar_response({"quoteSummary": {"result": None}}) == (None, None)
    assert ec._parse_calendar_response({"quoteSummary": {"result": []}}) == (None, None)


def test_parse_missing_earnings_date():
    data = {"quoteSummary": {"result": [{"calendarEvents": {"earnings": {}}}]}}
    assert ec._parse_calendar_response(data) == (None, None)


def test_parse_malformed_input_does_not_raise():
    assert ec._parse_calendar_response({}) == (None, None)
    assert ec._parse_calendar_response({"quoteSummary": None}) == (None, None)
    assert ec._parse_calendar_response("not even a dict") == (None, None)


def test_get_next_earnings_date_returns_none_when_no_crumb(monkeypatch):
    # Simulate the handshake itself failing (network down, Yahoo blocked it,
    # etc.) -- must fail soft, not raise.
    monkeypatch.setattr(ec, "_get_crumb", lambda: (None, None))
    assert ec.get_next_earnings_date("NVDA") == (None, None)
