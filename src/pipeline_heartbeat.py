"""
Weekly pipeline health check: are the other four scheduled workflows
(sp500-scan, watchlist-scan, score-alerts, alert-followup) actually still
succeeding, not just scheduled? The rest of this pipeline already has
degraded-scan detection (more than half the tickers failed to fetch) but
nothing notices if a workflow starts silently failing outright -- a code
bug, not a data issue -- you'd only find out by checking GitHub Actions
yourself.

Checks the workflow's most recent SCHEDULED run specifically (not a manual
workflow_dispatch), via the GitHub REST API directly rather than the `gh`
CLI -- keeps this testable with a mocked HTTP response, same pattern as
earnings_calendar.py's Yahoo lookup. Only pushes to Telegram when something
actually looks wrong; a clean run is just printed, not sent as noise.

Usage:
  python src/pipeline_heartbeat.py
Needs GITHUB_TOKEN (auto-provided by Actions with `permissions: actions:
read`) and GITHUB_REPOSITORY (auto-set by Actions; falls back to the known
repo slug for local runs).
"""

import datetime as _dt
import os

import requests

REPO = os.environ.get("GITHUB_REPOSITORY", "phani4393/wyckoff-scanner-ci")
API_BASE = f"https://api.github.com/repos/{REPO}/actions/workflows"
WATCHED_WORKFLOWS = ["sp500-scan.yml", "watchlist-scan.yml", "score-alerts.yml", "alert-followup.yml"]
STALE_AFTER_DAYS = 4  # weekday-only cadence: weekend + one day of slack before calling it stale


def _headers():
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _latest_scheduled_run(workflow_file):
    """Returns (conclusion, created_at) for the most recent run triggered by
    the actual cron (event=schedule) -- scoped that way so a recent manual
    workflow_dispatch can't mask a genuinely broken schedule. Returns None
    if the API call fails or there's no scheduled run yet."""
    try:
        r = requests.get(
            f"{API_BASE}/{workflow_file}/runs",
            params={"event": "schedule", "per_page": 1},
            headers=_headers(), timeout=20,
        )
        if not r.ok:
            return None
        runs = r.json().get("workflow_runs") or []
        if not runs:
            return None
        run = runs[0]
        return run.get("conclusion"), run.get("created_at")
    except requests.RequestException:
        return None


def check_all(now=None):
    """Returns a list of human-readable problem strings; empty if every
    watched workflow's most recent scheduled run succeeded within
    STALE_AFTER_DAYS."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    problems = []
    for wf in WATCHED_WORKFLOWS:
        result = _latest_scheduled_run(wf)
        if result is None:
            problems.append(f"{wf}: could not check (API call failed, or no scheduled run yet)")
            continue
        conclusion, created_at = result
        if conclusion != "success":
            problems.append(f"{wf}: last scheduled run did NOT succeed (conclusion={conclusion})")
            continue
        run_time = _dt.datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        age_days = (now - run_time).total_seconds() / 86400
        if age_days > STALE_AFTER_DAYS:
            problems.append(f"{wf}: last successful scheduled run was {age_days:.1f} days ago (>{STALE_AFTER_DAYS}d)")
    return problems


def main():
    problems = check_all()
    if not problems:
        print(f"Pipeline heartbeat: all {len(WATCHED_WORKFLOWS)} scheduled workflows look healthy.")
        return
    message = "PIPELINE HEARTBEAT WARNING:\n" + "\n".join(f"  - {p}" for p in problems)
    print(message)
    try:
        import wyckoff_notify as notify
        notify.send_message(message[:4000])
        print("\n[pushed to Telegram]")
    except Exception as e:
        print(f"\n[Telegram push failed: {e}]")


if __name__ == "__main__":
    main()
