"""
Tests for pipeline_heartbeat.py -- no real network calls, requests.get is
monkeypatched to return canned responses shaped like the real GitHub Actions
runs API.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pipeline_heartbeat as hb

NOW = dt.datetime(2026, 7, 29, 12, 0, tzinfo=dt.timezone.utc)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._payload = payload or {}

    def json(self):
        return self._payload


def _runs_response(conclusion, created_at):
    return _FakeResponse(200, {"workflow_runs": [{"conclusion": conclusion, "created_at": created_at}]})


def test_all_healthy_reports_no_problems(monkeypatch):
    recent = "2026-07-27T20:35:00Z"  # 1.6 days before NOW
    monkeypatch.setattr(hb.requests, "get", lambda *a, **k: _runs_response("success", recent))
    assert hb.check_all(now=NOW) == []


def test_failed_run_is_flagged(monkeypatch):
    recent = "2026-07-27T20:35:00Z"

    def fake_get(url, *a, **k):
        if "watchlist-scan" in url:
            return _runs_response("failure", recent)
        return _runs_response("success", recent)

    monkeypatch.setattr(hb.requests, "get", fake_get)
    problems = hb.check_all(now=NOW)
    assert len(problems) == 1
    assert "watchlist-scan.yml" in problems[0]
    assert "failure" in problems[0]


def test_stale_successful_run_is_flagged(monkeypatch):
    stale = "2026-07-20T20:35:00Z"  # 9 days before NOW -- past STALE_AFTER_DAYS

    def fake_get(url, *a, **k):
        if "score-alerts" in url:
            return _runs_response("success", stale)
        return _runs_response("success", "2026-07-27T20:35:00Z")

    monkeypatch.setattr(hb.requests, "get", fake_get)
    problems = hb.check_all(now=NOW)
    assert len(problems) == 1
    assert "score-alerts.yml" in problems[0]
    assert "days ago" in problems[0]


def test_recent_run_within_threshold_is_not_flagged(monkeypatch):
    # 3.6 days old -- inside STALE_AFTER_DAYS (4), must NOT be flagged.
    borderline = "2026-07-25T20:35:00Z"
    monkeypatch.setattr(hb.requests, "get", lambda *a, **k: _runs_response("success", borderline))
    assert hb.check_all(now=NOW) == []


def test_api_failure_is_flagged_not_raised(monkeypatch):
    monkeypatch.setattr(hb.requests, "get", lambda *a, **k: _FakeResponse(500))
    problems = hb.check_all(now=NOW)
    assert len(problems) == len(hb.WATCHED_WORKFLOWS)
    assert all("could not check" in p for p in problems)


def test_no_runs_yet_is_flagged_not_raised(monkeypatch):
    monkeypatch.setattr(hb.requests, "get", lambda *a, **k: _FakeResponse(200, {"workflow_runs": []}))
    problems = hb.check_all(now=NOW)
    assert len(problems) == len(hb.WATCHED_WORKFLOWS)
    assert all("could not check" in p for p in problems)


def test_network_exception_is_caught_not_raised(monkeypatch):
    def boom(*a, **k):
        raise hb.requests.RequestException("connection reset")
    monkeypatch.setattr(hb.requests, "get", boom)
    problems = hb.check_all(now=NOW)
    assert len(problems) == len(hb.WATCHED_WORKFLOWS)
