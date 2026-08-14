"""
Backtest: for every historical instance of each Wyckoff signal, what was the
stock's forward return over the next 5/10/20 trading days -- and how often
did it move in the direction a long call/put would need it to? Also prices
each instance as a modeled long-option round-trip (options_pricing.py:
Black-Scholes with realized vol as an IV proxy, theta decay, an assumed
spread cost), since a signal that looks only mildly behind on stock returns
can be shown for how much worse it is once actually priced as a call/put.

Reuses the exact same detectors as the live scanners (wyckoff_common,
wyckoff_patterns) so the backtest tests the SAME logic that's actually
running in production, not a reimplementation that could drift out of sync.

--extra-tickers CSV unions in another Symbol-column ticker file (e.g.
data/top50_plus_ai.csv) on top of the core watchlist before running -- more
independent tickers tightens the cluster-bootstrap CI, which is specifically
what the ABC signal needs: BACKTEST_FINDINGS.md calls its edge inconclusive
(not distinguishable from zero) rather than negative, and says resolving
that needs a larger sample rather than a different test. Results from an
expanded run are written to backtest_results_expanded.json, never
overwriting the original backtest_results.json that BACKTEST_FINDINGS.md's
published numbers came from.

--regime-filter applies the same regime gating used in the live scanners:
only bullish signals in bull regimes (SPY > 200d SMA), only bearish signals
in bear regimes (SPY < 200d SMA). This lets you compare filtered vs unfiltered
results to see if regime alignment improves edge. Results are written to
backtest_results_regime_filtered.json.
"""

import argparse
import csv
import json
import statistics
from pathlib import Path

import options_pricing as opt
import stats_utils
import wyckoff_common as c
from wyckoff_patterns import climax_events, sos_sow_events, lps_lpsy_events

TICKER_FILE = Path(__file__).resolve().parent.parent / "data" / "core_watchlist.csv"
HORIZONS = (5, 10, 20)
OUTPUT_SIZE = 5000  # ~20yr of daily bars, same 1 credit as any other size
SMA_LEN = 200  # regime filter: bull if SPY close > 200d SMA, else bear


def load_tickers(path=None):
    with open(path or TICKER_FILE, newline="", encoding="utf-8") as f:
        return [row["Symbol"] for row in csv.DictReader(f)]


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


def build_regime_lookup(spy_bars):
    """Returns a dict mapping date -> 'bull' or 'bear' based on SPY's 200d SMA.
    
    This is the same definition used in regime_filter.py and regime_analysis.py:
    bull = SPY close > 200d SMA, bear = SPY close <= 200d SMA.
    """
    sma = _sma(spy_bars, SMA_LEN)
    out = {}
    for i, b in enumerate(spy_bars):
        if sma[i] is None:
            continue
        out[b["date"]] = "bull" if b["close"] > sma[i] else "bear"
    return out


def signal_passes_regime_filter(event, regime_by_date):
    """Returns True if the signal direction aligns with the regime on that date.
    
    - Bullish signals only pass in bull regime (SPY > 200d SMA)
    - Bearish signals only pass in bear regime (SPY < 200d SMA)
    - Signals on dates without regime data pass (fail open)
    """
    regime = regime_by_date.get(event["date"])
    if regime is None:
        return True  # fail open if we don't know the regime
    if event["direction"] == "bullish":
        return regime == "bull"
    elif event["direction"] == "bearish":
        return regime == "bear"
    return True  # unknown direction passes


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
        sp = c.is_pure_spring(bars, sup, j)
        ut = c.is_pure_upthrust(bars, res, j)
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


def run_backtest(tickers, api_key, progress=True, regime_filter=False):
    import time
    start = time.monotonic()

    print("Fetching SPY benchmark history...", flush=True)
    spy_bars = fetch_full_history("SPY", api_key)
    spy_by_date = c.build_close_by_date(spy_bars)
    
    # Build regime lookup for filtering (if enabled)
    regime_by_date = {}
    if regime_filter:
        regime_by_date = build_regime_lookup(spy_bars)
        n_bull = sum(1 for v in regime_by_date.values() if v == "bull")
        n_bear = sum(1 for v in regime_by_date.values() if v == "bear")
        print(f"Regime filter ENABLED: {n_bull} bull days, {n_bear} bear days in history")
        print("  -> Bullish signals only counted in bull regime, bearish in bear regime\n")
    
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
    # Same four controls, but P&L is the modeled long-option round-trip
    # (options_pricing.option_pnl_pct) instead of the raw stock return, so
    # signals can be compared against a fair options-P&L baseline too.
    swing_bull_opt = {h: [] for h in HORIZONS}
    swing_bear_opt = {h: [] for h in HORIZONS}
    trend_bull_opt = {h: [] for h in HORIZONS}
    trend_bear_opt = {h: [] for h in HORIZONS}
    # Same values as swing_bull/swing_bear/*_opt above, but grouped by ticker
    # instead of flattened -- feeds the cluster bootstrap in stats_utils.py.
    # Same-ticker signal instances share trend/vol/beta and overlapping
    # forward-return windows, so they are NOT independent draws; resampling
    # individual instances (as if they were) understates the true CI width.
    swing_bull_by_ticker = {h: {} for h in HORIZONS}
    swing_bear_by_ticker = {h: {} for h in HORIZONS}
    swing_bull_opt_by_ticker = {h: {} for h in HORIZONS}
    swing_bear_opt_by_ticker = {h: {} for h in HORIZONS}
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
        
        # Apply regime filter if enabled: only count signals aligned with regime
        if regime_filter:
            events = [e for e in events if signal_passes_regime_filter(e, regime_by_date)]
        
        per_type = {}
        for e in events:
            returns = {h: forward_return(bars, e["idx"], h) for h in HORIZONS}
            options_pnl = {h: opt.option_pnl_pct(bars, e["idx"], h, e["direction"]) for h in HORIZONS}
            records.append({"sym": sym, "date": e["date"], "type": e["type"],
                             "direction": e["direction"], "returns": returns, "options_pnl": options_pnl})
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
                # option_pnl_pct already prices a call for 'bullish' and a
                # put for 'bearish', so its sign is already the long-option
                # P&L -- no sign flip needed (unlike the raw stock return).
                opt_pnl = opt.option_pnl_pct(bars, entry_idx, h, direction)
                if direction == "bullish":
                    swing_bull[h].append(signed)
                    swing_bull_by_ticker[h].setdefault(sym, []).append(signed)
                    if opt_pnl is not None:
                        swing_bull_opt[h].append(opt_pnl)
                        swing_bull_opt_by_ticker[h].setdefault(sym, []).append(opt_pnl)
                    if aligned:
                        trend_bull[h].append(signed)
                        if opt_pnl is not None:
                            trend_bull_opt[h].append(opt_pnl)
                else:
                    swing_bear[h].append(signed)
                    swing_bear_by_ticker[h].setdefault(sym, []).append(signed)
                    if opt_pnl is not None:
                        swing_bear_opt[h].append(opt_pnl)
                        swing_bear_opt_by_ticker[h].setdefault(sym, []).append(opt_pnl)
                    if aligned:
                        trend_bear[h].append(signed)
                        if opt_pnl is not None:
                            trend_bear_opt[h].append(opt_pnl)

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
                "trend_bullish": _dist_stats(trend_bull), "trend_bearish": _dist_stats(trend_bear),
                "bullish_options": _dist_stats(swing_bull_opt), "bearish_options": _dist_stats(swing_bear_opt),
                "trend_bullish_options": _dist_stats(trend_bull_opt),
                "trend_bearish_options": _dist_stats(trend_bear_opt)}
    # Raw pools grouped BY TICKER, kept alongside the summarized `matched`
    # above so summarize() can run a cluster bootstrap on the edge deltas --
    # _dist_stats() only keeps point estimates, and a flat (non-clustered)
    # resample would treat same-ticker instances as independent when they
    # aren't (shared trend/vol/beta, overlapping forward-return windows).
    raw_matched = {"bullish": swing_bull_by_ticker, "bearish": swing_bear_by_ticker,
                   "bullish_options": swing_bull_opt_by_ticker, "bearish_options": swing_bear_opt_by_ticker}
    return records, skipped, ticker_meta, baseline_returns, matched, raw_matched


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


def summarize(records, matched, raw_matched=None):
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
            val_syms = []
            for r in recs:
                ret = r["returns"].get(h)
                if ret is None:
                    continue
                vals.append(ret if _is_bullish(r["direction"]) else -ret)
                val_syms.append(r["sym"])
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

            # Same comparison, but on MODELED LONG-OPTION P&L (options_pricing.py)
            # instead of the raw stock return -- theta and vol are already
            # priced in, so this is a much closer proxy for whether the
            # signal would actually make money as a call/put trade.
            opt_vals = []
            opt_val_syms = []
            for r in recs:
                ov = r["options_pnl"].get(h)
                if ov is None:
                    continue
                opt_vals.append(ov)
                opt_val_syms.append(r["sym"])
            opt_hit = opt_ret = opt_base_hit = opt_base_ret = None
            if opt_vals:
                opt_wins = [v for v in opt_vals if v > 0]
                opt_hit = len(opt_wins) / len(opt_vals)
                opt_ret = statistics.mean(opt_vals) * 100
            mb_opt = matched.get("bullish_options", {}).get(h)
            mbear_opt = matched.get("bearish_options", {}).get(h)
            if mb_opt and mbear_opt:
                opt_base_hit = frac_bull * mb_opt["pct_positive"] + (1 - frac_bull) * mbear_opt["pct_positive"]
                opt_base_ret = frac_bull * mb_opt["avg_return_pct"] + (1 - frac_bull) * mbear_opt["avg_return_pct"]

            # Bootstrap CI + p-value on the edge itself, so a gap like "-19pp"
            # can be read as "real, CI excludes zero" vs. "within noise given
            # this sample size" rather than taken as a bare point estimate.
            stock_boot = opt_boot = None
            if raw_matched:
                bull_pool = raw_matched.get("bullish", {}).get(h, {})
                bear_pool = raw_matched.get("bearish", {}).get(h, {})
                stock_boot = stats_utils.bootstrap_edge_ci(vals, val_syms, bull_pool, bear_pool, frac_bull)
                if opt_vals:
                    bull_pool_o = raw_matched.get("bullish_options", {}).get(h, {})
                    bear_pool_o = raw_matched.get("bearish_options", {}).get(h, {})
                    opt_boot = stats_utils.bootstrap_edge_ci(opt_vals, opt_val_syms, bull_pool_o, bear_pool_o, frac_bull)

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
                "edge_hit_ci_pp": tuple(x * 100 for x in stock_boot["hit_edge_ci"]) if stock_boot else None,
                "edge_hit_p": stock_boot["hit_edge_p"] if stock_boot else None,
                "edge_ret_ci_pp": tuple(x * 100 for x in stock_boot["mean_edge_ci"]) if stock_boot else None,
                "edge_ret_p": stock_boot["mean_edge_p"] if stock_boot else None,
                "options_n": len(opt_vals),
                "options_hit_rate": opt_hit,
                "options_avg_pnl_pct": opt_ret,
                "options_matched_hit_rate": opt_base_hit,
                "options_matched_avg_pnl_pct": opt_base_ret,
                "options_edge_hit_pp": (opt_hit - opt_base_hit) * 100 if opt_hit is not None and opt_base_hit is not None else None,
                "options_edge_pnl_pp": (opt_ret - opt_base_ret) if opt_ret is not None and opt_base_ret is not None else None,
                "options_edge_hit_ci_pp": tuple(x * 100 for x in opt_boot["hit_edge_ci"]) if opt_boot else None,
                "options_edge_hit_p": opt_boot["hit_edge_p"] if opt_boot else None,
                "options_edge_pnl_ci_pp": tuple(x * 100 for x in opt_boot["mean_edge_ci"]) if opt_boot else None,
                "options_edge_pnl_p": opt_boot["mean_edge_p"] if opt_boot else None,
                "n_signal_clusters": stock_boot["n_signal_clusters"] if stock_boot else None,
            }
        summary[sig_type]["_frac_bull"] = frac_bull
        summary[sig_type]["_tickers_contributing"] = tickers_contributing
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extra-tickers", metavar="CSV",
                     help="union in another Symbol-column ticker CSV (e.g. "
                          "data/top50_plus_ai.csv) on top of the core watchlist before "
                          "running -- more independent tickers tightens the cluster-"
                          "bootstrap CI, which is specifically what ABC needs (see "
                          "docs/BACKTEST_FINDINGS.md). Writes to "
                          "backtest_results_expanded.json instead of overwriting the "
                          "original backtest_results.json.")
    ap.add_argument("--regime-filter", action="store_true",
                     help="only count signals aligned with SPY regime: bullish signals "
                          "in bull regime (SPY > 200d SMA), bearish in bear regime. "
                          "This tests whether regime-aligned signals have better edge "
                          "than unfiltered signals. Writes to "
                          "backtest_results_regime_filtered.json.")
    args = ap.parse_args()

    api_key = c.load_api_key()
    tickers = load_tickers()
    if args.extra_tickers:
        seen = set(tickers)
        tickers += [t for t in load_tickers(args.extra_tickers) if t not in seen and t != "SPY"]
    records, skipped, ticker_meta, baseline_returns, matched, raw_matched = run_backtest(
        tickers, api_key, regime_filter=args.regime_filter
    )

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

    print("\nMATCHED baseline, MODELED LONG-OPTION P&L (same swing control, priced as a "
          "call/put via options_pricing.py -- theta + spread + realized-vol-as-IV already netted in):")
    for label in ("bullish_options", "bearish_options", "trend_bullish_options", "trend_bearish_options"):
        desc = {"bullish_options": "long calls at ALL swing lows", "bearish_options": "long puts at ALL swing highs",
                "trend_bullish_options": "long calls at swing lows ONLY above 200d SMA",
                "trend_bearish_options": "long puts at swing highs ONLY below 200d SMA"}[label]
        print(f"  {label} ({desc}):")
        for h in HORIZONS:
            m = matched.get(label, {}).get(h)
            if m:
                print(f"    {h}d: n={m['n']:6d}  hit_rate={m['pct_positive']*100:5.1f}%  avg_pnl={m['avg_return_pct']:+6.1f}%")

    print("\nBootstrapping confidence intervals on the edge deltas "
          f"({stats_utils.N_BOOT} CLUSTER resamples per signal/horizon, resampling by ticker "
          "-- same-ticker instances aren't independent draws)...", flush=True)
    summary = summarize(records, matched, raw_matched)
    
    # Determine output file based on flags
    if args.regime_filter:
        out_path = "backtest_results_regime_filtered.json"
    elif args.extra_tickers:
        out_path = "backtest_results_expanded.json"
    else:
        out_path = "backtest_results.json"
    
    with open(out_path, "w") as f:
        json.dump({"records": records, "summary": summary, "ticker_meta": ticker_meta,
                    "baseline": baseline, "matched_baseline": matched, "skipped": skipped,
                    "regime_filter": args.regime_filter}, f, indent=2)

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
            hci, hp = s.get("edge_hit_ci_pp"), s.get("edge_hit_p")
            rci, rp = s.get("edge_ret_ci_pp"), s.get("edge_ret_p")
            ncl = s.get("n_signal_clusters")
            if hci is not None:
                sig = "significant" if hp < 0.05 else "NOT significant"
                print(f"      95% CI (cluster boot, {ncl} tickers): hit edge [{hci[0]:+5.1f}, {hci[1]:+5.1f}]pp (p={hp:.3f}), "
                      f"ret edge [{rci[0]:+5.1f}, {rci[1]:+5.1f}]pp (p={rp:.3f}) -- {sig} at 5% level")
            elif ncl is None and s.get("n", 0) > 0:
                print("      95% CI: not enough independent tickers to compute a cluster bootstrap")
            oh = s.get("options_edge_hit_pp")
            opnl = s.get("options_edge_pnl_pp")
            if s.get("options_n"):
                opt_edge_str = (f"  EDGE vs swing (options): hit {oh:+5.1f}pp, pnl {opnl:+5.1f}pp"
                                 if oh is not None else "")
                print(f"      OPTIONS: n={s['options_n']:4d}  hit={s['options_hit_rate']*100:5.1f}%  "
                      f"avg_pnl={s['options_avg_pnl_pct']:+6.1f}%{opt_edge_str}")
                ohci, ohp = s.get("options_edge_hit_ci_pp"), s.get("options_edge_hit_p")
                opci, opp = s.get("options_edge_pnl_ci_pp"), s.get("options_edge_pnl_p")
                if ohci is not None:
                    osig = "significant" if ohp < 0.05 else "NOT significant"
                    print(f"      95% CI (options): hit edge [{ohci[0]:+5.1f}, {ohci[1]:+5.1f}]pp (p={ohp:.3f}), "
                          f"pnl edge [{opci[0]:+5.1f}, {opci[1]:+5.1f}]pp (p={opp:.3f}) -- {osig} at 5% level")
    print(f"\nFull records saved to {out_path}")


if __name__ == "__main__":
    main()
