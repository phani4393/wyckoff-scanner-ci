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
  python trade_journal.py list
  python trade_journal.py close --id 3 --opt-price 7.10 --reason target \\
      [--notes "hit first target, closed half early last time -- held full this time"]

Exit reasons (kept to a small vocabulary so analytics can find your leaks):
  target | stop | thesis_invalid | time_stop | earnings_exit | discretionary
"""

import argparse
import csv
import datetime as _dt
from pathlib import Path

JOURNAL = Path(__file__).resolve().parent.parent / "trades.csv"

FIELDS = [
    "id", "status", "open_date", "ticker", "direction", "setup", "thesis",
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


def cmd_open(a):
    if a.dir not in VALID_DIRECTIONS:
        raise SystemExit(f"--dir must be one of {sorted(VALID_DIRECTIONS)}")
    rows = _read()
    tid = _next_id(rows)
    rows.append({
        "id": tid, "status": "open", "open_date": a.date or _today(),
        "ticker": a.ticker.upper(), "direction": a.dir, "setup": a.setup,
        "thesis": a.thesis, "entry_underlying": a.underlying, "entry_opt_price": a.opt_price,
        "strike": a.strike, "expiry": a.expiry, "contracts": a.contracts,
        "iv_rank_entry": a.iv_rank if a.iv_rank is not None else "",
        "earnings_date": a.earnings or "", "close_date": "", "exit_opt_price": "",
        "exit_reason": "", "pnl": "", "pnl_pct": "", "notes": a.notes or "",
    })
    _write(rows)
    print(f"Opened trade #{tid}: {a.ticker.upper()} {a.dir} ({a.setup}) @ opt {a.opt_price}")
    if a.earnings:
        _earnings_warn(a.date or _today(), a.earnings, a.expiry)


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

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
