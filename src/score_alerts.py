"""
Scores logged alerts (data/alerts_log.csv) against what actually happened,
once enough trading days have elapsed -- the live, out-of-sample complement
to backtest.py's historical replay. Answers a question the backtest
structurally can't: were the negative historical edges in
docs/BACKTEST_FINDINGS.md an artifact of hindsight (detectors that can only
be checked against history everyone can already see), or do they hold up on
signals fired blind, in real time, before anyone knew the outcome?

Reuses the exact same forward-return and options-P&L math as backtest.py
(forward_return, options_pricing.option_pnl_pct) and the same fair-baseline
concept (swing_baseline_events) -- so a live signal is compared against a
live, time-matched "just trade the swing" control from the same tickers and
period, not against the 20-year historical baseline pool (mixing the two
would blend different market regimes into what should be an apples-to-apples
check).

Idempotent and incremental: appends newly-scoreable (alert, horizon) rows to
data/alerts_scored.csv and never rescores a row once written, so this is
cheap to run on every scheduled pass -- it only fetches bars for tickers that
actually have a pending, unscored alert.

Usage:
  python src/score_alerts.py              # score pending alerts, print digest
  python src/score_alerts.py --telegram   # also push the digest to Telegram
"""

import argparse
import csv
import statistics
from pathlib import Path

import options_pricing as opt
import stats_utils
import wyckoff_common as c
from backtest import forward_return, swing_baseline_events

ALERTS_LOG = Path(__file__).resolve().parent.parent / "data" / "alerts_log.csv"
SCORED_LOG = Path(__file__).resolve().parent.parent / "data" / "alerts_scored.csv"
HORIZONS = (5, 10, 20)

# The day alert_log.py started shipping with the live scanners -- swing
# baseline instances before this date belong to the pre-existing historical
# period, not the live/forward one, and would contaminate an apples-to-apples
# live comparison if mixed in.
LIVE_START_DATE = "2026-07-21"

SCORED_FIELDS = ["logged_at", "sym", "setup", "direction", "horizon_days",
                 "entry_date", "exit_date", "stock_return_pct", "hit",
                 "options_pnl_pct"]

# alerts_log.csv's `direction` field is "long_call" / "long_put" (what the
# Telegram alert told you to consider), not the "bullish"/"bearish" backtest.py
# and options_pricing.py use internally -- map it once, here.
DIRECTION_TO_BULLISH = {"long_call": True, "long_put": False}


def _load_rows(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _scored_keys(scored_rows):
    return {(r["logged_at"], r["sym"], r["setup"], r["horizon_days"]) for r in scored_rows}


def _find_entry_idx(bars, date):
    """Exact-date match against the fetched bar series. Returns None if the
    alert's date isn't in the fetched window (too old for the ~300-bar fetch,
    or -- shouldn't happen in practice since scans only run on trading days --
    a non-trading-day timestamp)."""
    for i, b in enumerate(bars):
        if b["date"] == date:
            return i
    return None


def score_pending(api_key):
    """Fetch bars for tickers with unscored, elapsed-horizon alerts and
    append newly-scoreable rows to alerts_scored.csv. Returns the newly-scored
    rows (empty if nothing was ready yet)."""
    alerts = _load_rows(ALERTS_LOG)
    if not alerts:
        return []
    scored = _load_rows(SCORED_LOG)
    done = _scored_keys(scored)

    by_sym = {}
    for a in alerts:
        by_sym.setdefault(a["sym"], []).append(a)

    new_rows = []
    for sym, sym_alerts in by_sym.items():
        pending = [a for a in sym_alerts
                   if any((a["logged_at"], a["sym"], a["setup"], str(h)) not in done for h in HORIZONS)]
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
                continue  # no directional bias recorded -- nothing to score against
            for h in HORIZONS:
                key = (a["logged_at"], a["sym"], a["setup"], str(h))
                if key in done:
                    continue
                ret = forward_return(bars, idx, h)
                if ret is None:
                    continue  # not enough trading days have elapsed yet -- still pending
                signed_ret = ret if bullish else -ret
                pnl = opt.option_pnl_pct(bars, idx, h, "bullish" if bullish else "bearish")
                new_rows.append({
                    "logged_at": a["logged_at"], "sym": sym, "setup": a["setup"],
                    "direction": a["direction"], "horizon_days": h,
                    "entry_date": entry_date, "exit_date": bars[idx + h]["date"],
                    "stock_return_pct": round(signed_ret * 100, 3),
                    "hit": int(signed_ret > 0),
                    "options_pnl_pct": round(pnl * 100, 3) if pnl is not None else "",
                })
                done.add(key)

    if new_rows:
        is_new = not SCORED_LOG.exists()
        SCORED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SCORED_LOG, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SCORED_FIELDS)
            if is_new:
                w.writeheader()
            w.writerows(new_rows)
    return new_rows


def format_new_scores(new_rows):
    """Simple, direct per-alert lines for whatever became scoreable THIS run
    -- "did the view pan out, and by how much" -- ahead of the fuller
    statistical breakdown below. This is the part meant to be read on a
    phone; the scorecard is for whoever wants to dig into whether it's
    actually significant."""
    if not new_rows:
        return []
    lines = [f"New scores this run ({len(new_rows)}):"]
    for r in new_rows:
        verdict = "CORRECT" if r["hit"] else "INCORRECT"
        opt_str = f", option P&L {r['options_pnl_pct']:+.1f}%" if r["options_pnl_pct"] != "" else ""
        lines.append(f"  {r['sym']} {r['setup']} ({r['direction']}), {r['horizon_days']}d: "
                      f"{r['entry_date']} -> {r['exit_date']}  {r['stock_return_pct']:+.1f}%{opt_str} -- {verdict}")
    return lines


def _pool_stat(pool):
    wins = [v for v in pool if v > 0]
    return len(wins) / len(pool), statistics.mean(pool) * 100


def live_swing_baseline(tickers, api_key):
    """Time-matched control: naive swing-trade entries on/after LIVE_START_DATE,
    on the given tickers -- so the comparison isn't polluted by mixing in the
    20-year historical baseline. Scoped to the tickers passed in (currently:
    just the ones with a scored alert) rather than the full watchlist, to keep
    this cheap while alert volume is still low; pass backtest.load_tickers()
    here instead once you want faster statistical power at the cost of ~44
    extra API calls every run.

    Returns (bull, bear, bull_opt, bear_opt) pooled-by-horizon dicts and
    (bull_by_ticker, bear_by_ticker, bull_opt_by_ticker, bear_opt_by_ticker)
    grouped-by-ticker dicts, same shapes backtest.py uses for its point
    estimates and cluster bootstrap respectively."""
    bull = {h: [] for h in HORIZONS}
    bear = {h: [] for h in HORIZONS}
    bull_opt = {h: [] for h in HORIZONS}
    bear_opt = {h: [] for h in HORIZONS}
    bull_bt = {h: {} for h in HORIZONS}
    bear_bt = {h: {} for h in HORIZONS}
    bull_obt = {h: {} for h in HORIZONS}
    bear_obt = {h: {} for h in HORIZONS}

    for sym in tickers:
        bars = c.fetch_bars(sym, api_key)
        if not bars:
            continue
        for entry_idx, direction, _aligned in swing_baseline_events(bars):
            if bars[entry_idx]["date"] < LIVE_START_DATE:
                continue
            for h in HORIZONS:
                r = forward_return(bars, entry_idx, h)
                if r is None:
                    continue
                signed = r if direction == "bullish" else -r
                pnl = opt.option_pnl_pct(bars, entry_idx, h, direction)
                pool, pool_bt = (bull, bull_bt) if direction == "bullish" else (bear, bear_bt)
                pool[h].append(signed)
                pool_bt[h].setdefault(sym, []).append(signed)
                if pnl is not None:
                    opool, opool_bt = (bull_opt, bull_obt) if direction == "bullish" else (bear_opt, bear_obt)
                    opool[h].append(pnl)
                    opool_bt[h].setdefault(sym, []).append(pnl)

    return bull, bear, bull_opt, bear_opt, bull_bt, bear_bt, bull_obt, bear_obt


def build_digest(api_key):
    scored = _load_rows(SCORED_LOG)
    alerts = _load_rows(ALERTS_LOG)
    if not scored:
        return (f"{len(alerts)} alerts logged since {LIVE_START_DATE}, none have reached the "
                 f"shortest ({HORIZONS[0]}-trading-day) scoring horizon yet. Check back soon.")

    for r in scored:
        r["_ret"] = float(r["stock_return_pct"]) / 100
        r["_hit"] = int(r["hit"])
        r["_opt"] = float(r["options_pnl_pct"]) / 100 if r["options_pnl_pct"] not in ("", None) else None

    tickers = sorted({r["sym"] for r in scored})
    bull, bear, bull_opt, bear_opt, bull_bt, bear_bt, bull_obt, bear_obt = live_swing_baseline(tickers, api_key)

    total_possible = len(alerts) * len(HORIZONS)
    lines = [f"LIVE ALERT SCORECARD -- {len(scored)}/{total_possible} (alert, horizon) instances "
             f"scored across {len(tickers)} tickers since {LIVE_START_DATE}",
             "(Out-of-sample complement to docs/BACKTEST_FINDINGS.md -- these signals fired "
             "blind, in real time, before today's outcome was known. Small samples are noisy; "
             "don't trust a breakdown until the CI below actually excludes zero.)"]

    by_h = {h: [r for r in scored if int(r["horizon_days"]) == h] for h in HORIZONS}
    for h in HORIZONS:
        recs = by_h[h]
        if not recs:
            continue
        n = len(recs)
        hit_rate = sum(r["_hit"] for r in recs) / n
        avg_ret = statistics.mean(r["_ret"] for r in recs) * 100
        opt_vals = [r["_opt"] for r in recs if r["_opt"] is not None]
        frac_bull = sum(1 for r in recs if r["direction"] == "long_call") / n

        opt_str = f"  avg_option_pnl={statistics.mean(opt_vals) * 100:+.1f}%" if opt_vals else ""
        lines.append(f"\n{h}d horizon: n={n}  hit_rate={hit_rate * 100:.1f}%  avg_stock_ret={avg_ret:+.2f}%{opt_str}")

        mb, mbear = bull.get(h), bear.get(h)
        if mb and mbear:
            bh, br = _pool_stat(mb)
            eh, er = _pool_stat(mbear)
            base_hit = frac_bull * bh + (1 - frac_bull) * eh
            base_ret = frac_bull * br + (1 - frac_bull) * er
            lines.append(f"  live-swing baseline (same tickers/period): hit_rate={base_hit * 100:.1f}%  "
                         f"avg_ret={base_ret:+.2f}%  -> edge {(hit_rate - base_hit) * 100:+.1f}pp hit / "
                         f"{avg_ret - base_ret:+.2f}pp ret")

            vals = [r["_ret"] for r in recs]  # already direction-signed at scoring time
            groups = [r["sym"] for r in recs]
            boot = stats_utils.bootstrap_edge_ci(vals, groups, bull_bt.get(h, {}), bear_bt.get(h, {}), frac_bull)
            if boot:
                hci = tuple(x * 100 for x in boot["hit_edge_ci"])
                sig = "significant" if boot["hit_edge_p"] < 0.05 else "NOT significant"
                lines.append(f"  95% CI (cluster boot, {boot['n_signal_clusters']} tickers): "
                             f"hit edge [{hci[0]:+.1f}, {hci[1]:+.1f}]pp (p={boot['hit_edge_p']:.3f}) -- {sig}")
            else:
                lines.append(f"  Not enough independent tickers yet for a bootstrap CI "
                             f"(have {len(set(groups))}, need 3+) -- read the numbers above as descriptive only.")
        else:
            lines.append("  No live-swing baseline instances yet on these tickers/period to compare against.")

    setups = {}
    for r in by_h.get(10, []):
        setups.setdefault(r["setup"], []).append(r)
    printable = {name: recs for name, recs in setups.items() if len(recs) >= 5}
    if printable:
        lines.append("\nBy setup (10d horizon, n>=5 only -- smaller groups are too anecdotal to read):")
        for name, recs in sorted(printable.items(), key=lambda kv: -len(kv[1])):
            hr = sum(r["_hit"] for r in recs) / len(recs)
            ar = statistics.mean(r["_ret"] for r in recs) * 100
            lines.append(f"  {name:14} n={len(recs):3d}  hit={hr * 100:5.1f}%  avg={ar:+6.2f}%")
    else:
        lines.append("\nBy setup: no setup has 5+ scored instances yet.")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--telegram", action="store_true", help="also push the digest to Telegram")
    a = ap.parse_args()

    api_key = c.load_api_key()
    new_rows = score_pending(api_key)
    if new_rows:
        print(f"Scored {len(new_rows)} newly-eligible (alert, horizon) instance(s).")
    else:
        print("No newly-eligible (alert, horizon) instances this run.")

    new_lines = format_new_scores(new_rows)
    scorecard = build_digest(api_key)
    digest = ("\n".join(new_lines) + "\n\n" + scorecard) if new_lines else scorecard
    print("\n" + digest)

    # Only push to Telegram when there's actually something new -- the
    # scorecard alone doesn't change between runs with nothing newly scored,
    # so sending it daily regardless would just be noise.
    if a.telegram and new_rows:
        try:
            import wyckoff_notify as notify
            notify.send_message(digest[:4000])
            print("\n[pushed to Telegram]")
        except Exception as e:
            print(f"\n[Telegram push failed: {e}]")
    elif a.telegram:
        print("\n[nothing newly scored -- skipping Telegram push]")


if __name__ == "__main__":
    main()
