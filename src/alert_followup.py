"""
3-day forward follow-up on each fired alert: for day+1/day+2/day+3 (trading
days) after an alert, checks whether the underlying has moved further in the
predicted direction since the alert (getting STRONGER) or given some of it
back / reversed (getting WEAKER) than the day before -- a short, fast read
distinct from score_alerts.py's longer 5/10/20-trading-day statistical
scoring, meant to answer "is this one still building or already fading" in
the first few days after it fired.

Signal used is price movement only (direction-adjusted return since the
alert), not a structural re-check of the Wyckoff pattern itself -- deliberate
choice, keeps this simple and fast to read.

Reuses backtest.py's forward_return and score_alerts.py's entry-date lookup
and direction mapping directly, so the "did price move the right way" math
never drifts out of sync with what those already do.

Stops after day 3 -- score_alerts.py's own 5/10/20-trading-day scoring picks
up the longer-term view from there; this tool never looks past day+3 for a
given alert. Idempotent: each (alert, day_offset) pair is only ever reported
once, appended to data/alerts_followup.csv.

Usage:
  python src/alert_followup.py              # check in, print today's follow-ups
  python src/alert_followup.py --telegram   # also push the consolidated message to Telegram
"""

import argparse
import csv
from pathlib import Path

import wyckoff_common as c
from backtest import forward_return
from score_alerts import ALERTS_LOG, DIRECTION_TO_BULLISH, _find_entry_idx, _load_rows

FOLLOWUP_LOG = Path(__file__).resolve().parent.parent / "data" / "alerts_followup.csv"
DAY_OFFSETS = (1, 2, 3)

FOLLOWUP_FIELDS = ["logged_at", "sym", "setup", "direction", "day_offset",
                   "check_date", "cumulative_return_pct", "trend"]


def _followup_keys(rows):
    return {(r["logged_at"], r["sym"], r["setup"], r["day_offset"]) for r in rows}


def check_pending(api_key):
    """For every logged alert that has just reached day+1/2/3 (trading days)
    and hasn't been reported at that day_offset yet, computes the
    direction-adjusted cumulative return and whether it's stronger, weaker,
    or flat vs. the previous day_offset's cumulative return (day+1 compares
    against the alert day itself, i.e. 0%). Appends new rows to
    data/alerts_followup.csv. Returns the newly-computed rows."""
    alerts = _load_rows(ALERTS_LOG)
    if not alerts:
        return []
    done = _followup_keys(_load_rows(FOLLOWUP_LOG))

    by_sym = {}
    for a in alerts:
        by_sym.setdefault(a["sym"], []).append(a)

    new_rows = []
    for sym, sym_alerts in by_sym.items():
        pending = [a for a in sym_alerts
                   if any((a["logged_at"], a["sym"], a["setup"], str(d)) not in done for d in DAY_OFFSETS)]
        if not pending:
            continue
        bars = c.fetch_bars(sym, api_key)
        if not bars:
            print(f"  {sym}: could not fetch bars this run, will retry next time")
            continue
        for a in pending:
            entry_date = a["logged_at"][:10]
            idx = _find_entry_idx(bars, entry_date)
            if idx is None:
                continue  # outside the fetched window, or a date mismatch -- skip, don't guess
            bullish = DIRECTION_TO_BULLISH.get(a["direction"])
            if bullish is None:
                continue  # no directional bias (e.g. weis_wave context flags) -- nothing to track
            for d in DAY_OFFSETS:
                key = (a["logged_at"], a["sym"], a["setup"], str(d))
                if key in done:
                    continue
                check_idx = idx + d
                if check_idx >= len(bars):
                    continue  # that day hasn't happened yet -- still pending
                ret = forward_return(bars, idx, d)
                prev_ret = forward_return(bars, idx, d - 1)  # d-1=0 -> always 0.0, always in bounds
                if ret is None or prev_ret is None:
                    continue
                signed, signed_prev = (ret, prev_ret) if bullish else (-ret, -prev_ret)
                if signed > signed_prev:
                    trend = "STRONGER"
                elif signed < signed_prev:
                    trend = "WEAKER"
                else:
                    trend = "FLAT"
                new_rows.append({
                    "logged_at": a["logged_at"], "sym": sym, "setup": a["setup"],
                    "direction": a["direction"], "day_offset": d,
                    "check_date": bars[check_idx]["date"],
                    "cumulative_return_pct": round(signed * 100, 3),
                    "trend": trend,
                })
                done.add(key)

    if new_rows:
        is_new = not FOLLOWUP_LOG.exists()
        FOLLOWUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(FOLLOWUP_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FOLLOWUP_FIELDS)
            if is_new:
                w.writeheader()
            w.writerows(new_rows)
    return new_rows


def format_followups(new_rows):
    """One consolidated message for everything that checked in today -- every
    alert currently inside its 3-day window gets one line."""
    if not new_rows:
        return ""
    lines = [f"3-DAY FOLLOW-UP -- {len(new_rows)} alert(s) checked in today:"]
    for r in sorted(new_rows, key=lambda r: (r["sym"], r["day_offset"])):
        lines.append(f"  {r['sym']} {r['setup']} ({r['direction']}), day+{r['day_offset']} "
                      f"({r['check_date']}): {r['cumulative_return_pct']:+.1f}% since alert -- {r['trend']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true", help="also push today's follow-ups to Telegram")
    a = ap.parse_args()

    api_key = c.load_api_key()
    new_rows = check_pending(api_key)
    message = format_followups(new_rows)
    print(message or "No alerts checked in today (none currently inside their 1-3 trading day window).")

    if a.telegram and new_rows:
        try:
            import wyckoff_notify as notify
            notify.send_message(message[:4000])
            print("\n[pushed to Telegram]")
        except Exception as e:
            print(f"\n[Telegram push failed: {e}]")
    elif a.telegram:
        print("\n[nothing to report -- skipping Telegram push]")


if __name__ == "__main__":
    main()
