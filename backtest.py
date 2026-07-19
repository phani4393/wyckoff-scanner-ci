"""
Backtest: for every historical instance of each Wyckoff signal, what was the
stock's forward return over the next 5/10/20 trading days -- and how often
did it move in the direction a long call/put would need it to?

This does NOT model actual option P&L (no historical options chain data
available for free) -- it answers the prerequisite question: does the
signal have real directional edge at all? If the underlying doesn't move
enough in the right direction often enough, no amount of clever strike/DTE
selection saves a long option trade built on that signal.

Reuses the exact same detectors as the live scanners (wyckoff_common,
wyckoff_patterns) so the backtest tests the SAME logic that's actually
running in production, not a reimplementation that could drift out of sync.
"""

import csv
import json
import statistics
from pathlib import Path

import wyckoff_common as c
from wyckoff_patterns import climax_events, sos_sow_events, lps_lpsy_events

TICKER_FILE = Path(__file__).with_name("Core Watchlist.csv")
HORIZONS = (5, 10, 20)
OUTPUT_SIZE = 5000  # ~20yr of daily bars, same 1 credit as any other size


def load_tickers():
    with open(TICKER_FILE, newline="", encoding="utf-8") as f:
        return [row["Symbol"] for row in csv.DictReader(f)]


def fetch_full_history(sym, api_key):
    import requests

    c._throttle()
    r = requests.get(
        c.TIME_SERIES_URL,
        params={"symbol": sym, "interval": "1day", "outputsize": OUTPUT_SIZE, "order": "ASC", "apikey": api_key},
        timeout=30,
    )
    data = r.json()
    if data.get("status") == "error" or not data.get("values"):
        return None
    bars = []
    for v in data["values"]:
        try:
            bars.append({
                "date": v["datetime"][:10],
                "open": float(v["open"]), "high": float(v["high"]),
                "low": float(v["low"]), "close": float(v["close"]),
                "volume": float(v["volume"]) if v.get("volume") else 0,
            })
        except (KeyError, ValueError, TypeError):
            continue
    bars.sort(key=lambda b: b["date"])
    return bars


def spring_upthrust_events(bars, spy_by_date):
    """All historical edge-triggered spring/upthrust instances (same logic as
    wheel_signals, but walked across the whole series instead of just the
    last 2 bars)."""
    n = len(bars)
    res, sup = c.pivots(bars)
    atr = c.wilder_atr(bars)
    rs = [None] * n
    for i, b in enumerate(bars):
        spy_close = spy_by_date.get(b["date"])
        rs[i] = (b["close"] / spy_close) if spy_close else None

    def sell_put(j):
        if sup[j] is None:
            return False
        b = bars[j]
        spring = b["low"] < sup[j] and b["close"] > sup[j]
        near = atr[j] and abs(b["close"] - sup[j]) <= 0.5 * atr[j]
        rs_rising = j >= 5 and rs[j] is not None and rs[j - 5] is not None and rs[j] > rs[j - 5]
        return (spring or near) and rs_rising

    def sell_call(j):
        if res[j] is None:
            return False
        b = bars[j]
        up = b["high"] > res[j] and b["close"] < res[j]
        near = atr[j] and abs(b["close"] - res[j]) <= 0.5 * atr[j]
        return up or near

    events = []
    prev_put = prev_call = False
    for j in range(n):
        put, call = sell_put(j), sell_call(j)
        if put and not prev_put:
            events.append({"idx": j, "date": bars[j]["date"], "type": "spring", "direction": "bullish"})
        if call and not prev_call:
            events.append({"idx": j, "date": bars[j]["date"], "type": "upthrust", "direction": "bearish"})
        prev_put, prev_call = put, call
    return events


def pure_spring_upthrust_events(bars):
    """Textbook spring / upthrust ONLY -- no 'near the level' dilution, no RS
    gate. These are what the patterns actually are:
      spring   = today's LOW undercuts the last confirmed support, but the
                 CLOSE recovers back above it (a failed breakdown -> bullish).
      upthrust = today's HIGH pokes above the last confirmed resistance, but
                 the CLOSE falls back below it (a failed breakout -> bearish).
    Entry is this bar's close (you can see the reversal by the close), which
    is a realistic, tradeable entry. Edge-triggered so a multi-bar stay near
    the level isn't counted repeatedly."""
    n = len(bars)
    res, sup = c.pivots(bars)
    events = []
    prev_sp = prev_ut = False
    for j in range(n):
        b = bars[j]
        sp = sup[j] is not None and b["low"] < sup[j] and b["close"] > sup[j]
        ut = res[j] is not None and b["high"] > res[j] and b["close"] < res[j]
        if sp and not prev_sp:
            events.append({"idx": j, "date": b["date"], "type": "pure_spring", "direction": "bullish"})
        if ut and not prev_ut:
            events.append({"idx": j, "date": b["date"], "type": "pure_upthrust", "direction": "bearish"})
        prev_sp, prev_ut = sp, ut
    return events


def abc_events_all(bars):
    """All historical ABC completions (abc_pattern() in wyckoff_patterns only
    returns the latest one; this slides the same 4-point check across every
    swing in the zigzag).

    BUG FIX: point C (p3) isn't actually confirmed until RIGHT_BARS after its
    own bar -- that's what "confirmed pivot" means, and it's why the live
    abc_pattern() checks p3["idx"] >= n-1-RIGHT_BARS for "isNew" rather than
    just using p3["idx"] directly. The event date/idx used for measuring
    forward returns must be the CONFIRMATION day (p3["idx"] + RIGHT_BARS),
    not the pivot day itself -- using the raw pivot day is lookahead bias:
    it measures returns starting from a point you couldn't have known was
    significant yet, and that point is by definition a local extreme, which
    biases the "forward return" toward mean-reversion that was never
    actually tradeable."""
    swings = c.zigzag(bars)
    n = len(bars)
    events = []
    for k in range(3, len(swings)):
        p0, p1, p2, p3 = swings[k - 3:k + 1]
        a_len = abs(p1["price"] - p0["price"])
        if a_len <= 0:
            continue
        b_len = abs(p2["price"] - p1["price"])
        c_len = abs(p3["price"] - p2["price"])
        b_retrace = b_len / a_len
        c_ext = c_len / a_len
        a_dir = p1["price"] - p0["price"]
        c_dir = p3["price"] - p2["price"]
        same_direction = a_dir * c_dir > 0
        if 0.30 <= b_retrace <= 0.79 and 0.618 <= c_ext <= 1.618 and same_direction:
            confirm_idx = p3["idx"] + c.RIGHT_BARS
            if confirm_idx >= n:
                continue  # not actually confirmable within the data we have
            direction = "bearish" if c_dir > 0 else "bullish"
            events.append({"idx": confirm_idx, "date": bars[confirm_idx]["date"], "type": "abc", "direction": direction})
    return events


DIRECTION_MAP = {"SC": "bullish", "BC": "bearish", "SOS": "bullish", "SOW": "bearish",
                  "LPS": "bullish", "LPSY": "bearish"}


def all_signal_events(bars, spy_by_date):
    atr = c.wilder_atr(bars)
    res, sup = c.pivots(bars)
    events = list(spring_upthrust_events(bars, spy_by_date))
    events += pure_spring_upthrust_events(bars)
    events += abc_events_all(bars)
    for e in climax_events(bars, atr):
        events.append({"idx": e["idx"], "date": e["date"], "type": e["type"], "direction": DIRECTION_MAP[e["type"]]})
    sos_sow = sos_sow_events(bars, res, sup, atr)
    for e in sos_sow:
        events.append({"idx": e["idx"], "date": e["date"], "type": e["type"], "direction": DIRECTION_MAP[e["type"]]})
    for e in lps_lpsy_events(bars, sos_sow, atr):
        events.append({"idx": e["idx"], "date": e["date"], "type": e["type"], "direction": DIRECTION_MAP[e["type"]]})
    return events


def forward_return(bars, idx, horizon):
    j = idx + horizon
    if j >= len(bars):
        return None
    entry = bars[idx]["close"]
    if entry <= 0:
        return None
    return (bars[j]["close"] - entry) / entry


def swing_baseline_events(bars):
    """The NAIVE version of every signal here: just trade confirmed swing
    points, with none of the volume/RS/ratio filters the real signals add.
    A confirmed swing low -> a LONG (bullish null); a confirmed swing high ->
    a SHORT (bearish null). Measured at pivot_idx + RIGHT_BARS, the same
    confirmation-day entry the ABC backtest uses, so the comparison is
    apples-to-apples. If a real signal can't beat this, its extra machinery
    (Elliott ratios, volume spikes, RS) is adding nothing over 'trade the
    swing' -- which on drifting stocks is mostly 'buy the dip'."""
    swings = c.zigzag(bars)
    n = len(bars)
    sma200 = _sma(bars, 200)
    out = []  # (entry_idx, direction, trend_aligned)
    for s in swings:
        entry = s["idx"] + c.RIGHT_BARS
        if entry >= n:
            continue
        direction = "bullish" if s["type"] == "L" else "bearish"
        ma = sma200[entry]
        close = bars[entry]["close"]
        # Trend-aligned = long only above the 200d average, short only below it.
        if ma is None:
            aligned = False
        else:
            aligned = (close > ma) if direction == "bullish" else (close < ma)
        out.append((entry, direction, aligned))
    return out


def _sma(bars, length):
    out = [None] * len(bars)
    run = 0.0
    for i, b in enumerate(bars):
        run += b["close"]
        if i >= length:
            run -= bars[i - length]["close"]
        if i >= length - 1:
            out[i] = run / length
    return out


def run_backtest(tickers, api_key, progress=True):
    import time
    start = time.monotonic()

    print("Fetching SPY benchmark history...", flush=True)
    spy_bars = fetch_full_history("SPY", api_key)
    spy_by_date = c.build_close_by_date(spy_bars)
    print(f"SPY: {len(spy_bars)} bars, {spy_bars[0]['date']} to {spy_bars[-1]['date']}\n", flush=True)

    records = []  # {sym, date, type, direction, returns: {5: r, 10: r, 20: r}}
    skipped = []
    ticker_meta = []  # what this actually ran against, per ticker
    # Unconditional baseline: forward return from EVERY day (a random long entry).
    baseline_returns = {h: [] for h in HORIZONS}
    # Matched baselines: the naive "just trade the swing" control, split by
    # direction. Bullish = long from confirmed swing lows; bearish = short
    # from confirmed swing highs (stored as signed -ret so >0 means the
    # bearish trade won).
    swing_bull = {h: [] for h in HORIZONS}
    swing_bear = {h: [] for h in HORIZONS}
    # Trend-aligned variants: dip-buy only above 200d SMA, short only below it.
    trend_bull = {h: [] for h in HORIZONS}
    trend_bear = {h: [] for h in HORIZONS}
    running_totals = {}

    for i, sym in enumerate(tickers, 1):
        # SPY is fetched only as the RS benchmark; it is not a single-name
        # options candidate and (as an index) distorts the aggregates, so it
        # is never scanned as a tradeable name.
        if sym == "SPY":
            if progress:
                print(f"[{i}/{len(tickers)}] SPY: skipped as tradeable (benchmark only)", flush=True)
            continue
        bars = fetch_full_history(sym, api_key)
        if not bars or len(bars) < 60:
            skipped.append(sym)
            if progress:
                print(f"[{i}/{len(tickers)}] {sym}: SKIPPED (insufficient/no data)", flush=True)
            continue

        events = all_signal_events(bars, spy_by_date)
        per_type = {}
        for e in events:
            returns = {h: forward_return(bars, e["idx"], h) for h in HORIZONS}
            records.append({"sym": sym, "date": e["date"], "type": e["type"],
                             "direction": e["direction"], "returns": returns})
            per_type[e["type"]] = per_type.get(e["type"], 0) + 1
            running_totals[e["type"]] = running_totals.get(e["type"], 0) + 1

        for j in range(len(bars)):
            for h in HORIZONS:
                r = forward_return(bars, j, h)
                if r is not None:
                    baseline_returns[h].append(r)

        for entry_idx, direction, aligned in swing_baseline_events(bars):
            for h in HORIZONS:
                r = forward_return(bars, entry_idx, h)
                if r is None:
                    continue
                signed = r if direction == "bullish" else -r
                if direction == "bullish":
                    swing_bull[h].append(signed)
                    if aligned:
                        trend_bull[h].append(signed)
                else:
                    swing_bear[h].append(signed)
                    if aligned:
                        trend_bear[h].append(signed)

        ticker_meta.append({"sym": sym, "bars": len(bars), "from": bars[0]["date"], "to": bars[-1]["date"],
                             "events": len(events), "events_by_type": per_type})

        if progress:
            elapsed = time.monotonic() - start
            per_ticker_secs = elapsed / i
            remaining = per_ticker_secs * (len(tickers) - i)
            breakdown = ", ".join(f"{k}={v}" for k, v in sorted(per_type.items())) or "no signals"
            print(f"[{i}/{len(tickers)}] {sym}: {len(bars)} bars ({bars[0]['date']} to {bars[-1]['date']}) "
                  f"-- {len(events)} events ({breakdown}) | "
                  f"running totals: {running_totals} | "
                  f"~{remaining/60:.1f} min left", flush=True)

    matched = {"bullish": _dist_stats(swing_bull), "bearish": _dist_stats(swing_bear),
                "trend_bullish": _dist_stats(trend_bull), "trend_bearish": _dist_stats(trend_bear)}
    return records, skipped, ticker_meta, baseline_returns, matched


def _dist_stats(by_h):
    out = {}
    for h, vals in by_h.items():
        if not vals:
            continue
        wins = [v for v in vals if v > 0]
        out[h] = {"n": len(vals), "pct_positive": len(wins) / len(vals),
                   "avg_return_pct": statistics.mean(vals) * 100}
    return out


def summarize_baseline(baseline_returns):
    return _dist_stats({h: v for h, v in baseline_returns.items()})


def _is_bullish(direction):
    return direction.startswith("bullish")


def summarize(records, matched):
    by_type = {}
    for r in records:
        by_type.setdefault(r["type"], []).append(r)

    summary = {}
    for sig_type, recs in by_type.items():
        summary[sig_type] = {}
        tickers_contributing = {}
        for r in recs:
            tickers_contributing[r["sym"]] = tickers_contributing.get(r["sym"], 0) + 1
        frac_bull = sum(1 for r in recs if _is_bullish(r["direction"])) / len(recs)
        for h in HORIZONS:
            vals = []
            for r in recs:
                ret = r["returns"].get(h)
                if ret is None:
                    continue
                vals.append(ret if _is_bullish(r["direction"]) else -ret)
            if not vals:
                continue
            wins = [v for v in vals if v > 0]
            losses = [v for v in vals if v <= 0]
            # Matched baseline: blend the naive bullish/bearish swing controls
            # by THIS signal's own direction mix, so the comparison is fair.
            mb = matched.get("bullish", {}).get(h)
            mbear = matched.get("bearish", {}).get(h)
            base_hit = base_ret = None
            if mb and mbear:
                base_hit = frac_bull * mb["pct_positive"] + (1 - frac_bull) * mbear["pct_positive"]
                base_ret = frac_bull * mb["avg_return_pct"] + (1 - frac_bull) * mbear["avg_return_pct"]
            summary[sig_type][h] = {
                "n": len(vals),
                "hit_rate": len(wins) / len(vals),
                "avg_return_pct": statistics.mean(vals) * 100,
                "avg_win_pct": (statistics.mean(wins) * 100) if wins else 0,
                "avg_loss_pct": (statistics.mean(losses) * 100) if losses else 0,
                "matched_hit_rate": base_hit,
                "matched_avg_return_pct": base_ret,
                "edge_hit_pp": (len(wins) / len(vals) - base_hit) * 100 if base_hit is not None else None,
                "edge_ret_pp": (statistics.mean(vals) * 100 - base_ret) if base_ret is not None else None,
            }
        summary[sig_type]["_frac_bull"] = frac_bull
        summary[sig_type]["_tickers_contributing"] = tickers_contributing
    return summary


def main():
    api_key = c.load_api_key()
    tickers = load_tickers()
    records, skipped, ticker_meta, baseline_returns, matched = run_backtest(tickers, api_key)

    print("\n" + "=" * 78)
    print("WHAT THIS RAN AGAINST")
    print("=" * 78)
    print(f"{len(tickers)} tickers requested, {len(skipped)} skipped: {skipped or 'none'}")
    print(f"{len(ticker_meta)} tickers actually included (SPY excluded as tradeable), by history:\n")
    for m in sorted(ticker_meta, key=lambda x: -x["bars"]):
        print(f"  {m['sym']:6s} {m['bars']:5d} bars  {m['from']} to {m['to']}  "
              f"({m['events']} signal instances)")
    print(f"\nTotal signal instances: {len(records)}")

    baseline = summarize_baseline(baseline_returns)
    print("\nUNCONDITIONAL baseline (random long entry, any day):")
    for h in HORIZONS:
        b = baseline.get(h)
        if b:
            print(f"  {h}d: n={b['n']:6d}  pct_positive={b['pct_positive']*100:5.1f}%  avg={b['avg_return_pct']:+6.2f}%")
    print("\nMATCHED baseline -- 'just trade the confirmed swing' (the fair control):")
    for label in ("bullish", "bearish", "trend_bullish", "trend_bearish"):
        desc = {"bullish": "long ALL swing lows", "bearish": "short ALL swing highs",
                "trend_bullish": "long swing lows ONLY above 200d SMA",
                "trend_bearish": "short swing highs ONLY below 200d SMA"}[label]
        print(f"  {label} ({desc}):")
        for h in HORIZONS:
            m = matched.get(label, {}).get(h)
            if m:
                print(f"    {h}d: n={m['n']:6d}  hit_rate={m['pct_positive']*100:5.1f}%  avg={m['avg_return_pct']:+6.2f}%")

    summary = summarize(records, matched)
    with open("backtest_results.json", "w") as f:
        json.dump({"records": records, "summary": summary, "ticker_meta": ticker_meta,
                    "baseline": baseline, "matched_baseline": matched, "skipped": skipped}, f, indent=2)

    print("\n" + "=" * 78)
    print("SIGNAL RESULTS vs MATCHED baseline  (edge = does the signal beat naive swing-trading?)")
    print("=" * 78)
    # Rank signals by their best edge_hit_pp at the 10d horizon for a clean read.
    def best_edge(by_h):
        s = by_h.get(10) or next((by_h[h] for h in HORIZONS if h in by_h), None)
        return s["edge_hit_pp"] if s and s.get("edge_hit_pp") is not None else -999
    for sig_type in sorted([k for k in summary], key=lambda k: -best_edge(summary[k])):
        by_h = summary[sig_type]
        contributing = by_h.get("_tickers_contributing", {})
        fb = by_h.get("_frac_bull", 0)
        dirn = "bullish" if fb > 0.6 else "bearish" if fb < 0.4 else f"mixed ({fb*100:.0f}% bull)"
        print(f"\n=== {sig_type} ===  [{dirn}]  from {len(contributing)} tickers")
        for h in HORIZONS:
            if h not in by_h:
                continue
            s = by_h[h]
            eh = s.get("edge_hit_pp")
            er = s.get("edge_ret_pp")
            edge_str = (f"  EDGE vs swing: hit {eh:+5.1f}pp, ret {er:+5.2f}pp"
                        if eh is not None else "")
            print(f"  {h}d: n={s['n']:4d}  hit={s['hit_rate']*100:5.1f}%  avg={s['avg_return_pct']:+6.2f}%  "
                  f"win={s['avg_win_pct']:+6.2f}%  loss={s['avg_loss_pct']:+6.2f}%{edge_str}")
    print("\nFull records saved to backtest_results.json")


if __name__ == "__main__":
    main()
