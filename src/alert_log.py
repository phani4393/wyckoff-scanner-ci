"""
Durable record of every alert either scanner has sent, for evaluation later
against what actually happened -- a real-time, out-of-sample complement to
the historical backtest (the backtest replays the past; this builds an
actual live track record going forward, which is one of the open gaps the
backtest itself can't close).

Appends to data/alerts_log.csv, which IS committed to git -- unlike
trades.csv or the API key, this is just "ticker X flagged for reason Y at
time Z", the same information already sent to Telegram, not P&L or personal
financial detail. Local runs append directly; GitHub Actions runs commit the
updated file back to the repo as a workflow step (see sp500-scan.yml /
watchlist-scan.yml), since the CI runner's own filesystem is discarded when
the job ends.

SAME-DAY DEDUP: edge-triggering (see wyckoff_watchlist_scanner.py /
wyckoff_scanner.py) only stops a signal from re-firing across *different*
trading days -- it does nothing to stop the *same* signal from being logged
twice if a scan actually runs more than once on the same calendar day (a
manual re-run, or a scheduler double-fire). Confirmed to actually happen:
several tickers got logged 2-4x on the same day during manual testing of an
unrelated workflow fix. log_alert() now skips writing if an identical
(ticker, setup, direction) combo is already logged for today, so a
double-run can't inflate score_alerts.py's or alert_followup.py's sample
counts with a duplicate of the same real-world event.
"""

import csv
import datetime as _dt
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "data" / "alerts_log.csv"
FIELDS = ["logged_at", "source", "sym", "setup", "direction", "thesis", "underlying"]


def _now():
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _already_logged_today(sym, setup, direction, today):
    if not LOG_FILE.exists():
        return False
    with open(LOG_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row["sym"] == sym.upper() and row["setup"] == setup
                    and (row.get("direction") or "") == (direction or "")
                    and row["logged_at"][:10] == today):
                return True
    return False


def log_alert(source, sym, setup, direction, thesis, underlying):
    """source: 'watchlist' or 'sp500_sweep' -- which scanner fired this.
    Returns True if a new row was written, False if skipped as a same-day
    duplicate of an already-logged (ticker, setup, direction) combo."""
    now = _now()
    if _already_logged_today(sym, setup, direction, now[:10]):
        return False
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            w.writeheader()
        w.writerow({
            "logged_at": now, "source": source, "sym": sym.upper(),
            "setup": setup, "direction": direction or "", "thesis": thesis,
            "underlying": underlying,
        })
    return True
