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
"""

import csv
import datetime as _dt
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent / "data" / "alerts_log.csv"
FIELDS = ["logged_at", "source", "sym", "setup", "direction", "thesis", "underlying"]


def _now():
    return _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def log_alert(source, sym, setup, direction, thesis, underlying):
    """source: 'watchlist' or 'sp500_sweep' -- which scanner fired this."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_FILE.exists()
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if is_new:
            w.writeheader()
        w.writerow({
            "logged_at": _now(), "source": source, "sym": sym.upper(),
            "setup": setup, "direction": direction or "", "thesis": thesis,
            "underlying": underlying,
        })
