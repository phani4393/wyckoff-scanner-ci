"""
Trade journal -- the honest, personalized replacement for the mechanical
backtest that failed. Log every options trade with its DECISION CONTEXT
(setup, thesis, IV, strike/expiry) at entry and its OUTCOME + exit reason at
close. trade_analytics.py then measures YOUR realized edge from this -- which
is answerable and survivorship-free, because it's your own trades.

PRIVACY: this writes to trades.csv in this folder and nothing leaves your
machine unless you choose to push the analytics digest. Your P&L is NOT
committed to GitHub (trades.csv is gitignored in the CI repo).

Two ways to log:
  1. This CLI (avoids CSV-formatting mistakes) -- recommended.
  2. Edit trades.csv directly in Excel (schema documented below).

CLI usage:
  python trade_journal.py open --ticker NVDA --dir long_call --setup spring \\
      --thesis "spring at 180 support, RS rising" --underlying 182.40 \\
      --opt-price 4.20 --strike 190 --expiry 2026-08-15 --contracts 3 \\
      --iv-rank 55 [--earnings 2026-08-27]

EARNINGS: if you omit --earnings, this best-effort auto-fetches the next
earnings date from Yahoo Finance (earnings_calendar.py) and uses that
instead -- an explicit --earnings always overrides the auto-fetch. That
lookup is an unofficial endpoint that can fail or go away with no notice;
on any failure this falls back silently to no earnings date, same as before
the feature existed. Auto-fetched dates print whether Yahoo marked them
"confirmed" or "estimated."
  python trade_journal.py list
  python trade_journal.py close --id 3 --opt-price 7.10 --reason target \\
      [--notes "hit first target, closed half early last time -- held full this time"]

Exit reasons (kept to a small vocabulary so analytics can find your leaks):
  target | stop | thesis_invalid | time_stop | earnings_exit | discretionary

DRAFTS -- closing the loop with the scanner: when wyckoff_watchlist_scanner.py
fires a signal during a LOCAL run, it auto-appends a "draft" row here (ticker,
setup, direction, thesis, the underlying price at signal time) instead of
making you transcribe it by hand. Drafts are inert until you decide to actually
trade one and fill in the option-specific fields (strike/expiry/opt price) that
only you know at that point:
  python trade_journal.py drafts                      # see pending signal drafts
  python trade_journal.py promote --id 7 --opt-price 4.20 --strike 190 \\
      --expiry 2026-08-15 --contracts 3 [--iv-rank 55] [--earnings 2026-08-27]
  python trade_journal.py discard --id 7 --notes "skipped, IV too rich"

Drafts only accumulate from LOCAL runs -- the GitHub Actions cron run's
filesystem is thrown away when the job ends, so a draft written there would
vanish immediately. Not a bug, just means this feature only helps when you
run the watchlist scanner yourself.
"""

import argparse
import csv
import datetime as _dt
from pathlib import Path

JOURNAL = Path(__file__).resolve().parent.parent / "trades.csv"

FIELDS = [
    "id", "status", "signal_date", "open_date", "ticker", "direction", "setup", "thesis",
    "entry_underlying", "entry_opt_price", "strike", "expiry", "contracts",
    "iv_rank_entry", "earnings_date",
    "close_date", "exit_opt_price", "exit_reason", "pnl", "pnl_pct", "notes",
]

VALID_DIRECTIONS = {"long_call", "long_put", "call_debit_spread", "put_debit_spread", "other"}
VALID_REASONS = {"target", "stop", "thesis_invalid", "time_stop", "earnings_exit", "discretionary"}


def _today():
    # Date.now() is unavailable in some sandboxes; use date.today via a guarded import.
    return _dt.date.today().isoformat()


def _read():
    if not JOURNAL.exists():
        return []
    with open(JOURNAL, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(rows):
    with open(JOURNAL, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def _next_id(rows):
    ids = [int(r["id"]) for r in rows if r.get("id", "").isdigit()]
    return str(max(ids) + 1) if ids else "1"


def _resolve_earnings(ticker, explicit):
    """Manual --earnings always wins. If omitted, best-effort auto-fetch
    from Yahoo (earnings_calendar.py) -- silently falls back to no date on
    any failure, exactly like the behavior before that feature existed."""
    if explicit:
        return explicit
    try:
        from earnings_calendar import get_next_earnings_date
        date_str, is_estimate = get_next_earnings_date(ticker)
    except Exception:
        return None
    if date_str:
        tag = "estimated" if is_estimate else "confirmed"
        print(f"  Auto-fetched next earnings date: {date_str} ({tag})")
    return date_str


def cmd_open(a):
    if a.dir not in VALID_DIRECTIONS:
        raise SystemExit(f"--dir must be one of {sorted(VALID_DIRECTIONS)}")
    rows = _read()
    tid = _next_id(rows)
    earnings = _resolve_earnings(a.ticker, a.earnings)
    rows.append({
        "id": tid, "status": "open", "open_date": a.date or _today(),
        "ticker": a.ticker.upper(), "direction": a.dir, "setup": a.setup,
        "thesis": a.thesis, "entry_underlying": a.underlying, "entry_opt_price": a.opt_price,
        "strike": a.strike, "expiry": a.expiry, "contracts": a.contracts,
        "iv_rank_entry": a.iv_rank if a.iv_rank is not None else "",
        "earnings_date": earnings or "", "close_date": "", "exit_opt_price": "",
        "exit_reason": "", "pnl": "", "pnl_pct": "", "notes": a.notes or "",
    })
    _write(rows)
    print(f"Opened trade #{tid}: {a.ticker.upper()} {a.dir} ({a.setup}) @ opt {a.opt_price}")
    if earnings:
        _earnings_warn(a.date or _today(), earnings, a.expiry)


def _earnings_warn(open_date, earnings, expiry):
    try:
        o = _dt.date.fromisoformat(open_date)
        e = _dt.date.fromisoformat(earnings)
        days = (e - o).days
        if 0 <= days <= 10:
            print(f"  ** EARNINGS WARNING: {days} days to earnings ({earnings}). "
                  "Long options into earnings = IV-crush + gap risk. Deliberate?")
        if expiry:
            x = _dt.date.fromisoformat(expiry)
            if o < e <= x:
                print(f"  ** Your expiry ({expiry}) is AFTER earnings ({earnings}) -- "
                      "you're holding through the event. Deliberate?")
    except ValueError:
        pass


def add_draft(ticker, setup, direction, thesis, underlying, signal_date=None):
    """Called by wyckoff_watchlist_scanner.py when a signal fires during a
    local run -- appends an inert 'draft' row so the decision context (what
    fired, at what price, with what thesis) doesn't have to be transcribed by
    hand later if you decide to trade it. Returns the new row's id."""
    rows = _read()
    tid = _next_id(rows)
    rows.append({
        "id": tid, "status": "draft", "signal_date": signal_date or _today(),
        "open_date": "", "ticker": ticker.upper(), "direction": direction or "",
        "setup": setup, "thesis": thesis, "entry_underlying": underlying,
        "entry_opt_price": "", "strike": "", "expiry": "", "contracts": "",
        "iv_rank_entry": "", "earnings_date": "", "close_date": "",
        "exit_opt_price": "", "exit_reason": "", "pnl": "", "pnl_pct": "", "notes": "",
    })
    _write(rows)
    return tid


def cmd_draft(a):
    tid = add_draft(a.ticker, a.setup, a.dir, a.thesis, a.underlying, a.date)
    print(f"Drafted #{tid}: {a.ticker.upper()} ({a.setup}) @ {a.underlying} -- not a real position until promoted")


def cmd_drafts(a):
    rows = _read()
    drafts = [r for r in rows if r["status"] == "draft"]
    if not drafts:
        print("No pending signal drafts.")
        return
    print(f"{'id':>3}  {'signal_date':11} {'ticker':6} {'dir':10} {'setup':10} {'underlying':>10}  thesis")
    for r in drafts:
        print(f"{r['id']:>3}  {r['signal_date']:11} {r['ticker']:6} {r['direction']:10} {r['setup']:10} "
              f"{r['entry_underlying']:>10}  {r['thesis'][:60]}")


def cmd_promote(a):
    """Turn a draft into a real tracked position -- fills in the option-
    specific fields only you know once you decide to actually take the trade.
    open_date is set to TODAY (not the signal date), so hold-time analytics
    measure your actual holding period, not your decision lag."""
    rows = _read()
    for r in rows:
        if r["id"] == str(a.id):
            if r["status"] != "draft":
                raise SystemExit(f"#{a.id} is not a pending draft (status={r['status']}).")
            earnings = _resolve_earnings(r["ticker"], a.earnings)
            r.update({
                "status": "open", "open_date": a.date or _today(),
                "entry_opt_price": a.opt_price, "strike": a.strike, "expiry": a.expiry,
                "contracts": a.contracts, "iv_rank_entry": a.iv_rank if a.iv_rank is not None else "",
                "earnings_date": earnings or "",
            })
            _write(rows)
            print(f"Promoted #{a.id} to an open position: {r['ticker']} {r['direction']} @ opt {a.opt_price}")
            if earnings:
                _earnings_warn(r["open_date"], earnings, a.expiry)
            return
    raise SystemExit(f"No draft with id {a.id}")


def cmd_discard(a):
    rows = _read()
    for r in rows:
        if r["id"] == str(a.id):
            if r["status"] != "draft":
                raise SystemExit(f"#{a.id} is not a pending draft (status={r['status']}).")
            r.update({"status": "discarded", "notes": a.notes or ""})
            _write(rows)
            print(f"Discarded draft #{a.id}: {r['ticker']} ({r['setup']})")
            return
    raise SystemExit(f"No draft with id {a.id}")


def cmd_close(a):
    if a.reason not in VALID_REASONS:
        raise SystemExit(f"--reason must be one of {sorted(VALID_REASONS)}")
    rows = _read()
    for r in rows:
        if r["id"] == str(a.id):
            if r["status"] == "closed":
                raise SystemExit(f"Trade #{a.id} is already closed.")
            entry = float(r["entry_opt_price"])
            contracts = float(r["contracts"] or 1)
            exit_price = a.opt_price
            # Long option P&L; per-contract prices, x100 multiplier, x contracts.
            pnl = (exit_price - entry) * 100 * contracts
            pnl_pct = (exit_price - entry) / entry * 100 if entry else 0
            r.update({
                "status": "closed", "close_date": a.date or _today(),
                "exit_opt_price": exit_price, "exit_reason": a.reason,
                "pnl": f"{pnl:.2f}", "pnl_pct": f"{pnl_pct:.1f}",
                "notes": (r.get("notes", "") + (" | " + a.notes if a.notes else "")).strip(" |"),
            })
            _write(rows)
            print(f"Closed #{a.id}: {r['ticker']} {r['direction']} -- "
                  f"P&L ${pnl:,.2f} ({pnl_pct:+.1f}%), reason={a.reason}")
            return
    raise SystemExit(f"No trade with id {a.id}")


def cmd_list(a):
    rows = _read()
    opens = [r for r in rows if r["status"] == "open"]
    if not opens:
        print("No open trades.")
        return
    print(f"{'id':>3}  {'ticker':6} {'dir':16} {'setup':10} {'strike':>7} {'expiry':10} {'earnings':10}  thesis")
    for r in opens:
        print(f"{r['id']:>3}  {r['ticker']:6} {r['direction']:16} {r['setup']:10} "
              f"{r['strike']:>7} {r['expiry']:10} {r['earnings_date'] or '-':10}  {r['thesis'][:50]}")


def main():
    p = argparse.ArgumentParser(description="Options trade journal")
    sub = p.add_subparsers(dest="cmd", required=True)

    po = sub.add_parser("open", help="log a new trade")
    po.add_argument("--ticker", required=True)
    po.add_argument("--dir", required=True, help=f"one of {sorted(VALID_DIRECTIONS)}")
    po.add_argument("--setup", required=True, help="spring/upthrust/abc/SOS/SOW/LPS/discretionary/...")
    po.add_argument("--thesis", required=True)
    po.add_argument("--underlying", required=True)
    po.add_argument("--opt-price", dest="opt_price", required=True)
    po.add_argument("--strike", required=True)
    po.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    po.add_argument("--contracts", default="1")
    po.add_argument("--iv-rank", dest="iv_rank", type=float, default=None)
    po.add_argument("--earnings", default=None, help="next earnings date YYYY-MM-DD (for the guardrail)")
    po.add_argument("--date", default=None, help="open date, default today")
    po.add_argument("--notes", default=None)
    po.set_defaults(func=cmd_open)

    pc = sub.add_parser("close", help="close an open trade")
    pc.add_argument("--id", required=True)
    pc.add_argument("--opt-price", dest="opt_price", type=float, required=True)
    pc.add_argument("--reason", required=True, help=f"one of {sorted(VALID_REASONS)}")
    pc.add_argument("--date", default=None)
    pc.add_argument("--notes", default=None)
    pc.set_defaults(func=cmd_close)

    pl = sub.add_parser("list", help="list open trades")
    pl.set_defaults(func=cmd_list)

    pd = sub.add_parser("draft", help="manually add a signal draft (normally done by the scanner)")
    pd.add_argument("--ticker", required=True)
    pd.add_argument("--setup", required=True)
    pd.add_argument("--dir", default=None, help=f"one of {sorted(VALID_DIRECTIONS)}, if known yet")
    pd.add_argument("--thesis", required=True)
    pd.add_argument("--underlying", required=True)
    pd.add_argument("--date", default=None, help="signal date, default today")
    pd.set_defaults(func=cmd_draft)

    pds = sub.add_parser("drafts", help="list pending signal drafts")
    pds.set_defaults(func=cmd_drafts)

    pp = sub.add_parser("promote", help="turn a draft into a real open position")
    pp.add_argument("--id", required=True)
    pp.add_argument("--opt-price", dest="opt_price", required=True)
    pp.add_argument("--strike", required=True)
    pp.add_argument("--expiry", required=True, help="YYYY-MM-DD")
    pp.add_argument("--contracts", default="1")
    pp.add_argument("--iv-rank", dest="iv_rank", type=float, default=None)
    pp.add_argument("--earnings", default=None)
    pp.add_argument("--date", default=None, help="actual entry date, default today")
    pp.set_defaults(func=cmd_promote)

    pdis = sub.add_parser("discard", help="drop a draft you decided not to trade")
    pdis.add_argument("--id", required=True)
    pdis.add_argument("--notes", default=None)
    pdis.set_defaults(func=cmd_discard)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
