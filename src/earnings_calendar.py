"""
Best-effort next-earnings-date lookup for trade_journal.py's earnings
guardrail, so you don't have to remember and type --earnings by hand for
every trade.

Uses Yahoo Finance's undocumented quoteSummary API. This is NOT an
official, supported endpoint -- Yahoo can change or block it at any time
with no notice, and as of this writing it requires a session cookie + crumb
handshake it didn't used to. Every call is wrapped to fail soft (return
None) rather than raise, so a broken lookup never blocks logging a trade --
it just falls back to typing --earnings yourself, exactly like before this
existed.

Twelve Data (this project's primary data vendor) has no earnings-calendar
endpoint on the free tier (see README "Data & limits") -- confirmed, so this
doesn't duplicate or compete with anything already working there.
"""

import requests

_session = None
_crumb = None

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_QUOTE_SUMMARY_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"


def _get_crumb():
    """One cookie+crumb handshake per process, reused across every ticker
    looked up in the same run -- not re-done per call."""
    global _session, _crumb
    if _crumb is not None:
        return _session, _crumb
    s = requests.Session()
    s.headers.update(_HEADERS)
    try:
        s.get("https://fc.yahoo.com", timeout=10)  # primes a session cookie
        r = s.get("https://query2.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        r.raise_for_status()
        crumb = r.text.strip()
        if not crumb or "<html" in crumb.lower():
            return None, None
        _session, _crumb = s, crumb
        return s, crumb
    except requests.RequestException:
        return None, None


def _parse_calendar_response(data):
    """Pure parsing logic, split out from the network call so it can be
    tested directly against a captured real response shape without mocking
    HTTP. Returns (date_str 'YYYY-MM-DD', is_estimate: bool), or (None, None)
    if the shape doesn't have what's expected."""
    try:
        result = data.get("quoteSummary", {}).get("result")
        if not result:
            return None, None
        earnings = result[0].get("calendarEvents", {}).get("earnings", {})
        dates = earnings.get("earningsDate") or []
        if not dates:
            return None, None
        date_str = dates[0].get("fmt")
        if not date_str:
            return None, None
        is_estimate = bool(earnings.get("isEarningsDateEstimate", True))
        return date_str, is_estimate
    except (AttributeError, TypeError, KeyError, IndexError):
        return None, None


def get_next_earnings_date(ticker):
    """Returns (date_str, is_estimate) for the next upcoming earnings date,
    or (None, None) if the lookup fails for any reason (network, ticker not
    found, Yahoo changed the endpoint, no crumb, etc.) -- never raises."""
    session, crumb = _get_crumb()
    if session is None:
        return None, None
    try:
        r = session.get(
            _QUOTE_SUMMARY_URL.format(ticker=ticker.upper()),
            params={"modules": "calendarEvents", "crumb": crumb},
            timeout=10,
        )
        r.raise_for_status()
        return _parse_calendar_response(r.json())
    except (requests.RequestException, ValueError):
        return None, None
