"""
Daily summary: consolidates all alerts and updates from today into one
clean, plain-English Telegram message. Runs as the last job of the day,
after all scans, scoring, and follow-ups are complete.

This is the "TL;DR" message -- everything else can be muted/archived if
you just want the one end-of-day summary.

Usage:
  python src/daily_summary.py              # print summary
  python src/daily_summary.py --telegram   # also push to Telegram
  python src/daily_summary.py --dry-run    # preview without sending
"""

import argparse
import csv
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import wyckoff_notify as notify
from wyckoff_common import get_confidence_tier

ALERTS_LOG = Path(__file__).resolve().parent.parent / "data" / "alerts_log.csv"
ALERTS_SCORED = Path(__file__).resolve().parent.parent / "data" / "alerts_scored.csv"
ALERTS_FOLLOWUP = Path(__file__).resolve().parent.parent / "data" / "alerts_followup.csv"


def _load_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _today_str():
    """Today's date in UTC as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get_todays_alerts(today=None):
    """Get all alerts logged today, grouped by actionable vs watchlist."""
    today = today or _today_str()
    rows = _load_csv(ALERTS_LOG)
    
    actionable = []  # (sym, setup, direction, thesis)
    watchlist = []   # (sym, setup, thesis)
    
    for r in rows:
        if not r.get("logged_at", "").startswith(today):
            continue
        
        thesis = r.get("thesis", "")
        is_filtered = "[REGIME-FILTERED]" in thesis
        clean_thesis = thesis.replace("[REGIME-FILTERED] ", "")
        
        if is_filtered:
            watchlist.append((r["sym"], r["setup"], clean_thesis))
        else:
            actionable.append((r["sym"], r["setup"], r.get("direction", ""), clean_thesis))
    
    return actionable, watchlist


def get_todays_scores(today=None):
    """Get alerts that were scored today."""
    today = today or _today_str()
    rows = _load_csv(ALERTS_SCORED)
    
    # The exit_date tells us when the horizon elapsed, not when it was scored
    # But for daily summary, we care about rows where exit_date is today
    # (meaning we got the result today)
    results = []
    for r in rows:
        if r.get("exit_date") == today:
            hit = r.get("hit") == "1"
            stock_ret = float(r.get("stock_return_pct", 0))
            opt_pnl = r.get("options_pnl_pct", "")
            opt_pnl = float(opt_pnl) if opt_pnl else None
            results.append({
                "sym": r["sym"],
                "setup": r["setup"],
                "direction": r["direction"],
                "horizon": int(r["horizon_days"]),
                "hit": hit,
                "stock_ret": stock_ret,
                "opt_pnl": opt_pnl,
            })
    
    return results


def get_todays_followups(today=None):
    """Get follow-up updates from today."""
    today = today or _today_str()
    rows = _load_csv(ALERTS_FOLLOWUP)
    
    followups = []
    for r in rows:
        if r.get("followup_date") == today:
            followups.append({
                "sym": r["sym"],
                "setup": r["setup"],
                "day": int(r.get("day_number", 0)),
                "status": r.get("status", ""),
                "ret": float(r.get("return_pct", 0)) if r.get("return_pct") else 0,
            })
    
    return followups


def build_summary(today=None):
    """Build a plain-English daily summary."""
    today = today or _today_str()
    
    actionable, watchlist = get_todays_alerts(today)
    scores = get_todays_scores(today)
    followups = get_todays_followups(today)
    
    lines = [f"📊 DAILY SUMMARY — {today}", ""]
    
    # --- New Alerts Section ---
    if actionable or watchlist:
        lines.append("🔔 NEW SIGNALS TODAY:")
        
        if actionable:
            # Group by tier
            by_tier = defaultdict(list)
            for sym, setup, direction, thesis in actionable:
                tier_info = get_confidence_tier(setup, regime_aligned=True)
                by_tier[tier_info["tier"]].append((sym, setup, direction, tier_info["label"]))
            
            # HIGH tier first
            for tier in ["HIGH", "MEDIUM", "REVIEW"]:
                if tier not in by_tier:
                    continue
                items = by_tier[tier]
                if tier == "HIGH":
                    lines.append(f"  ⭐⭐⭐ HIGH confidence ({len(items)}):")
                elif tier == "MEDIUM":
                    lines.append(f"  ⭐⭐ MEDIUM confidence ({len(items)}):")
                else:
                    lines.append(f"  ⭐ Review only ({len(items)}):")
                
                for sym, setup, direction, label in items:
                    dir_str = "bullish" if direction == "long_call" else "bearish" if direction == "long_put" else ""
                    lines.append(f"    • {sym} — {setup} ({dir_str})")
        
        if watchlist:
            lines.append(f"  📋 Watchlist only ({len(watchlist)} regime-filtered):")
            # Just list tickers, no details for filtered ones
            syms = sorted(set(s[0] for s in watchlist))
            lines.append(f"    {', '.join(syms)}")
        
        lines.append("")
    else:
        lines.append("🔔 No new signals today.")
        lines.append("")
    
    # --- Results Section (scored alerts) ---
    if scores:
        wins = [s for s in scores if s["hit"]]
        losses = [s for s in scores if not s["hit"]]
        
        lines.append(f"📈 RESULTS TODAY: {len(wins)}W / {len(losses)}L")
        
        if wins:
            lines.append("  ✅ Winners:")
            for s in wins:
                opt_str = f", option {s['opt_pnl']:+.0f}%" if s["opt_pnl"] is not None else ""
                lines.append(f"    • {s['sym']} {s['setup']} {s['horizon']}d: stock {s['stock_ret']:+.1f}%{opt_str}")
        
        if losses:
            lines.append("  ❌ Losers:")
            for s in losses:
                opt_str = f", option {s['opt_pnl']:+.0f}%" if s["opt_pnl"] is not None else ""
                lines.append(f"    • {s['sym']} {s['setup']} {s['horizon']}d: stock {s['stock_ret']:+.1f}%{opt_str}")
        
        # Quick stats
        if scores:
            total_opt = [s["opt_pnl"] for s in scores if s["opt_pnl"] is not None]
            if total_opt:
                avg_opt = sum(total_opt) / len(total_opt)
                lines.append(f"  → Avg option P&L today: {avg_opt:+.1f}%")
        
        lines.append("")
    
    # --- Follow-ups Section ---
    if followups:
        stronger = [f for f in followups if f["status"] == "stronger"]
        weaker = [f for f in followups if f["status"] == "weaker"]
        
        lines.append("📍 OPEN POSITION UPDATES:")
        if stronger:
            lines.append(f"  ✅ Getting stronger ({len(stronger)}):")
            for f in stronger:
                lines.append(f"    • {f['sym']} day {f['day']}: {f['ret']:+.1f}%")
        if weaker:
            lines.append(f"  ⚠️ Giving back ({len(weaker)}):")
            for f in weaker:
                lines.append(f"    • {f['sym']} day {f['day']}: {f['ret']:+.1f}%")
        lines.append("")
    
    # --- Bottom line ---
    if not actionable and not watchlist and not scores and not followups:
        lines.append("Quiet day — no alerts, no results, no updates.")
    else:
        # One-liner summary
        parts = []
        if actionable:
            parts.append(f"{len(actionable)} actionable signal{'s' if len(actionable) != 1 else ''}")
        if watchlist:
            parts.append(f"{len(watchlist)} on watchlist")
        if scores:
            win_pct = len([s for s in scores if s["hit"]]) / len(scores) * 100
            parts.append(f"{len(scores)} scored ({win_pct:.0f}% hit)")
        
        if parts:
            lines.append("—")
            lines.append("TL;DR: " + ", ".join(parts) + ".")
    
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true", help="push summary to Telegram")
    ap.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="preview without sending (implies --telegram)")
    args = ap.parse_args()
    
    summary = build_summary()
    print(summary)
    
    if args.telegram or args.dry_run:
        if args.dry_run:
            print("\n[DRY RUN: would push this to Telegram]")
        else:
            try:
                notify.send_message(summary[:4000])
                print("\n[pushed to Telegram]")
            except Exception as e:
                print(f"\n[Telegram push failed: {e}]")


if __name__ == "__main__":
    main()
