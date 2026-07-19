"""
Edge analytics on your own trade journal (trades.csv). This is the honest,
survivorship-free answer to "does my trading work, and where do I leak?" --
computed from YOUR realized trades, not a mechanical backtest.

Usage:
  python trade_analytics.py              # print the digest
  python trade_analytics.py --telegram   # also push the digest to Telegram
                                          # (needs wyckoff_notify + env vars;
                                          #  only do this if you're OK with the
                                          #  summary leaving your machine)
"""

import argparse
import csv
import datetime as _dt
import statistics
from pathlib import Path

JOURNAL = Path(__file__).resolve().parent.parent / "trades.csv"
EARNINGS_WINDOW = 10  # days; "traded near earnings" if opened within this many days before earnings


def _closed_trades():
    if not JOURNAL.exists():
        return []
    out = []
    with open(JOURNAL, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("status") != "closed" or not r.get("pnl"):
                continue
            try:
                r["_pnl"] = float(r["pnl"])
                r["_pnl_pct"] = float(r["pnl_pct"]) if r.get("pnl_pct") else 0.0
            except ValueError:
                continue
            out.append(r)
    return out


def _hold_days(r):
    try:
        o = _dt.date.fromisoformat(r["open_date"])
        c = _dt.date.fromisoformat(r["close_date"])
        return (c - o).days
    except (ValueError, KeyError):
        return None


def _near_earnings(r):
    try:
        o = _dt.date.fromisoformat(r["open_date"])
        e = _dt.date.fromisoformat(r["earnings_date"])
        return 0 <= (e - o).days <= EARNINGS_WINDOW
    except (ValueError, KeyError):
        return False


def _stats(trades):
    if not trades:
        return None
    pnls = [t["_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    return {
        "n": len(trades),
        "win_rate": len(wins) / len(trades) * 100,
        "total_pnl": sum(pnls),
        "avg_win": statistics.mean(wins) if wins else 0,
        "avg_loss": statistics.mean(losses) if losses else 0,
        "expectancy": statistics.mean(pnls),  # avg P&L per trade
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
    }


def _group(trades, key):
    groups = {}
    for t in trades:
        groups.setdefault(t.get(key, "") or "(blank)", []).append(t)
    return groups


def build_digest():
    trades = _closed_trades()
    lines = []
    if not trades:
        return ("No closed trades logged yet. Log trades with trade_journal.py; "
                "analytics appear once you have closed positions.")

    o = _stats(trades)
    lines.append(f"TRADE EDGE REPORT -- {o['n']} closed trades")
    lines.append(f"  Net P&L: ${o['total_pnl']:,.0f} | Win rate: {o['win_rate']:.0f}% | "
                 f"Expectancy: ${o['expectancy']:,.0f}/trade | Profit factor: {o['profit_factor']:.2f}")
    lines.append(f"  Avg win: ${o['avg_win']:,.0f} | Avg loss: ${o['avg_loss']:,.0f}")

    # --- by setup ---
    lines.append("\nBy setup (where your edge actually is):")
    setups = _group(trades, "setup")
    for name, ts in sorted(setups.items(), key=lambda kv: -_stats(kv[1])["expectancy"]):
        s = _stats(ts)
        lines.append(f"  {name:14} n={s['n']:2d}  win={s['win_rate']:3.0f}%  "
                     f"exp=${s['expectancy']:>7,.0f}/trade  PF={s['profit_factor']:.2f}")

    # --- by direction ---
    lines.append("\nBy direction:")
    for name, ts in _group(trades, "direction").items():
        s = _stats(ts)
        lines.append(f"  {name:18} n={s['n']:2d}  win={s['win_rate']:3.0f}%  exp=${s['expectancy']:>7,.0f}")

    # --- behavioral leaks ---
    lines.append("\nBehavioral leaks:")
    win_holds = [d for t in trades if t["_pnl"] > 0 and (d := _hold_days(t)) is not None]
    loss_holds = [d for t in trades if t["_pnl"] <= 0 and (d := _hold_days(t)) is not None]
    if win_holds and loss_holds:
        wh, lh = statistics.mean(win_holds), statistics.mean(loss_holds)
        lines.append(f"  Avg hold: winners {wh:.1f}d vs losers {lh:.1f}d"
                     + ("  <- holding losers longer than winners (classic leak)" if lh > wh else ""))
    # exit reason mix
    reasons = _group(trades, "exit_reason")
    rsum = ", ".join(f"{r}={len(ts)}" for r, ts in sorted(reasons.items(), key=lambda kv: -len(kv[1])))
    lines.append(f"  Exit reasons: {rsum}")
    # earnings exposure
    near = [t for t in trades if _near_earnings(t)]
    if near:
        s = _stats(near)
        lines.append(f"  Trades opened within {EARNINGS_WINDOW}d of earnings: n={s['n']}  "
                     f"win={s['win_rate']:.0f}%  net=${s['total_pnl']:,.0f}"
                     + ("  <- earnings trades are dragging you" if s["expectancy"] < o["expectancy"] else ""))

    lines.append("\n(Reminder: this is YOUR realized edge on YOUR trades -- the honest metric. "
                 "Small samples are noisy; let it build to 30+ trades before trusting a breakdown.)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true", help="also push the digest to Telegram")
    a = ap.parse_args()
    digest = build_digest()
    print(digest)
    if a.telegram:
        try:
            import wyckoff_notify as notify
            notify.send_message(digest[:4000])
            print("\n[pushed to Telegram]")
        except Exception as e:
            print(f"\n[Telegram push failed: {e}]")


if __name__ == "__main__":
    main()
