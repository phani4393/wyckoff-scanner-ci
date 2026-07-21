"""
Tests one specific, pre-committed hypothesis: does SPY's own market regime at
the time of a signal explain any of pure_spring's / pure_upthrust's edge --
i.e. does a spring fail more often when the broader market is in a downtrend?

Definition committed BEFORE looking at results (not fished across variants):
regime = 'bull' if SPY's close > SPY's own 200-day SMA on that date, else
'bear'. This is the standard, least-arbitrary trend definition available --
picking a different one after seeing which gives a better answer would be
exactly the kind of p-hacking the rest of this backtest has been built to
avoid.

Reuses backtest.py's exact detectors and matched-baseline construction
(pure_spring_upthrust_events, swing_baseline_events, forward_return,
option_pnl_pct) and stats_utils.py's cluster bootstrap unmodified -- the only
new thing is splitting every observation (signal AND matched baseline) by
regime before comparing, so each side gets a regime-matched, apples-to-apples
control (bull-regime springs vs bull-regime naive swing entries, not vs the
pooled all-regime baseline).
"""

import json
import statistics
import time

import backtest as bt
import options_pricing as opt
import stats_utils
import wyckoff_common as c

HORIZONS = bt.HORIZONS
SMA_LEN = 200
REGIMES = ("bull", "bear")


def spy_regime_lookup(spy_bars):
    sma = bt._sma(spy_bars, SMA_LEN)
    out = {}
    for i, b in enumerate(spy_bars):
        if sma[i] is None:
            continue
        out[b["date"]] = "bull" if b["close"] > sma[i] else "bear"
    return out


def run(tickers, api_key, progress=True):
    start = time.monotonic()
    print("Fetching SPY benchmark history...", flush=True)
    spy_bars = bt.fetch_full_history("SPY", api_key)
    regime_by_date = spy_regime_lookup(spy_bars)
    n_bull = sum(1 for v in regime_by_date.values() if v == "bull")
    n_bear = sum(1 for v in regime_by_date.values() if v == "bear")
    print(f"SPY: {len(spy_bars)} bars -- regime known for {len(regime_by_date)} "
          f"({n_bull} bull days, {n_bear} bear days)\n", flush=True)

    sig_keys = [("pure_spring", r) for r in REGIMES] + [("pure_upthrust", r) for r in REGIMES]
    sig_vals = {k: {h: [] for h in HORIZONS} for k in sig_keys}
    sig_syms = {k: {h: [] for h in HORIZONS} for k in sig_keys}
    sig_opt_vals = {k: {h: [] for h in HORIZONS} for k in sig_keys}
    sig_opt_syms = {k: {h: [] for h in HORIZONS} for k in sig_keys}

    # Matched baseline, split the same way: by regime AND by ticker (for the
    # cluster bootstrap), separately for the bull-direction and bear-direction
    # naive swing control (pure_spring is 100% bullish direction, pure_upthrust
    # 100% bearish, so each only ever needs its own-direction baseline).
    base_bull = {r: {h: {} for h in HORIZONS} for r in REGIMES}
    base_bear = {r: {h: {} for h in HORIZONS} for r in REGIMES}
    base_bull_opt = {r: {h: {} for h in HORIZONS} for r in REGIMES}
    base_bear_opt = {r: {h: {} for h in HORIZONS} for r in REGIMES}

    skipped = []
    for i, sym in enumerate(tickers, 1):
        if sym == "SPY":
            continue
        bars = bt.fetch_full_history(sym, api_key)
        if not bars or len(bars) < 60:
            skipped.append(sym)
            if progress:
                print(f"[{i}/{len(tickers)}] {sym}: SKIPPED", flush=True)
            continue

        for e in bt.pure_spring_upthrust_events(bars):
            regime = regime_by_date.get(e["date"])
            if regime is None:
                continue
            key = (e["type"], regime)
            for h in HORIZONS:
                r = bt.forward_return(bars, e["idx"], h)
                if r is None:
                    continue
                sig_vals[key][h].append(r if e["direction"] == "bullish" else -r)
                sig_syms[key][h].append(sym)
                ov = opt.option_pnl_pct(bars, e["idx"], h, e["direction"])
                if ov is not None:
                    sig_opt_vals[key][h].append(ov)
                    sig_opt_syms[key][h].append(sym)

        for entry_idx, direction, aligned in bt.swing_baseline_events(bars):
            regime = regime_by_date.get(bars[entry_idx]["date"])
            if regime is None:
                continue
            for h in HORIZONS:
                r = bt.forward_return(bars, entry_idx, h)
                if r is None:
                    continue
                signed = r if direction == "bullish" else -r
                ov = opt.option_pnl_pct(bars, entry_idx, h, direction)
                if direction == "bullish":
                    base_bull[regime][h].setdefault(sym, []).append(signed)
                    if ov is not None:
                        base_bull_opt[regime][h].setdefault(sym, []).append(ov)
                else:
                    base_bear[regime][h].setdefault(sym, []).append(signed)
                    if ov is not None:
                        base_bear_opt[regime][h].setdefault(sym, []).append(ov)

        if progress:
            elapsed = time.monotonic() - start
            remaining = (elapsed / i) * (len(tickers) - i)
            print(f"[{i}/{len(tickers)}] {sym}: done -- ~{remaining/60:.1f} min left", flush=True)

    return {
        "sig_vals": sig_vals, "sig_syms": sig_syms,
        "sig_opt_vals": sig_opt_vals, "sig_opt_syms": sig_opt_syms,
        "base_bull": base_bull, "base_bear": base_bear,
        "base_bull_opt": base_bull_opt, "base_bear_opt": base_bear_opt,
        "skipped": skipped, "n_bull_days": n_bull, "n_bear_days": n_bear,
    }


def _dist(vals):
    if not vals:
        return None
    wins = [v for v in vals if v > 0]
    return {"n": len(vals), "hit_rate": len(wins) / len(vals), "avg_pct": statistics.mean(vals) * 100}


def summarize(result):
    out = {}
    for sig_type in ("pure_spring", "pure_upthrust"):
        # own_* = this signal's own-direction baseline (used for the point
        # estimate). real_bull_*/real_bear_* = BOTH real matched pools, always
        # populated regardless of sig_type -- required by the cluster
        # bootstrap, which blends them via frac_bull (1.0 or 0.0 here since
        # these signals are single-direction). Passing an empty dict for the
        # unused side would trip stats_utils' MIN_CLUSTERS floor and always
        # return None, regardless of frac_bull -- that was the bug in the
        # first draft of this script, caught by a small-sample sanity check
        # before the full run.
        frac_bull = 1.0 if sig_type == "pure_spring" else 0.0
        own_base = result["base_bull"] if sig_type == "pure_spring" else result["base_bear"]
        own_base_opt = result["base_bull_opt"] if sig_type == "pure_spring" else result["base_bear_opt"]
        out[sig_type] = {}
        for regime in REGIMES:
            out[sig_type][regime] = {}
            key = (sig_type, regime)
            real_bull_pool = result["base_bull"][regime]
            real_bear_pool = result["base_bear"][regime]
            real_bull_opt_pool = result["base_bull_opt"][regime]
            real_bear_opt_pool = result["base_bear_opt"][regime]
            for h in HORIZONS:
                vals = result["sig_vals"][key][h]
                syms = result["sig_syms"][key][h]
                if not vals:
                    continue
                d = _dist(vals)
                own_pool = own_base[regime][h]  # {ticker: [vals]}, this signal's own direction only
                b = _dist([v for vs in own_pool.values() for v in vs])
                edge_hit = (d["hit_rate"] - b["hit_rate"]) * 100 if b else None
                edge_ret = (d["avg_pct"] - b["avg_pct"]) if b else None

                boot = stats_utils.bootstrap_edge_ci(vals, syms, real_bull_pool[h], real_bear_pool[h], frac_bull)

                opt_vals = result["sig_opt_vals"][key][h]
                opt_syms = result["sig_opt_syms"][key][h]
                opt_d = _dist(opt_vals) if opt_vals else None
                own_opt_pool = own_base_opt[regime][h]
                opt_b = _dist([v for vs in own_opt_pool.values() for v in vs])
                opt_edge_hit = (opt_d["hit_rate"] - opt_b["hit_rate"]) * 100 if (opt_d and opt_b) else None
                opt_edge_pnl = (opt_d["avg_pct"] - opt_b["avg_pct"]) if (opt_d and opt_b) else None
                opt_boot = None
                if opt_d:
                    opt_boot = stats_utils.bootstrap_edge_ci(
                        opt_vals, opt_syms, real_bull_opt_pool[h], real_bear_opt_pool[h], frac_bull)

                out[sig_type][regime][h] = {
                    "n": d["n"], "hit_rate": d["hit_rate"], "avg_pct": d["avg_pct"],
                    "baseline_n": b["n"] if b else 0, "baseline_hit_rate": b["hit_rate"] if b else None,
                    "baseline_avg_pct": b["avg_pct"] if b else None,
                    "edge_hit_pp": edge_hit, "edge_ret_pp": edge_ret,
                    "edge_hit_ci_pp": tuple(x * 100 for x in boot["hit_edge_ci"]) if boot else None,
                    "edge_hit_p": boot["hit_edge_p"] if boot else None,
                    "edge_ret_ci_pp": tuple(x * 100 for x in boot["mean_edge_ci"]) if boot else None,
                    "edge_ret_p": boot["mean_edge_p"] if boot else None,
                    "n_clusters": boot["n_signal_clusters"] if boot else None,
                    "options_n": opt_d["n"] if opt_d else 0,
                    "options_hit_rate": opt_d["hit_rate"] if opt_d else None,
                    "options_avg_pnl_pct": opt_d["avg_pct"] if opt_d else None,
                    "options_edge_hit_pp": opt_edge_hit, "options_edge_pnl_pp": opt_edge_pnl,
                    "options_edge_hit_ci_pp": tuple(x * 100 for x in opt_boot["hit_edge_ci"]) if opt_boot else None,
                    "options_edge_hit_p": opt_boot["hit_edge_p"] if opt_boot else None,
                    "options_edge_pnl_ci_pp": tuple(x * 100 for x in opt_boot["mean_edge_ci"]) if opt_boot else None,
                    "options_edge_pnl_p": opt_boot["mean_edge_p"] if opt_boot else None,
                }
    return out


def main():
    api_key = c.load_api_key()
    tickers = bt.load_tickers()
    result = run(tickers, api_key, progress=True)

    print(f"\n{len(result['skipped'])} tickers skipped: {result['skipped'] or 'none'}")
    print("\nBootstrapping (cluster, by ticker) confidence intervals per regime...", flush=True)
    summary = summarize(result)

    with open("regime_analysis_results.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 78)
    print("MARKET-REGIME SPLIT: does SPY's own trend explain spring/upthrust's edge?")
    print("regime = 'bull' if SPY close > SPY's own 200d SMA on the signal date, else 'bear'")
    print("=" * 78)
    for sig_type in ("pure_spring", "pure_upthrust"):
        print(f"\n=== {sig_type} ===")
        for regime in REGIMES:
            print(f"  -- SPY regime: {regime} --")
            for h in HORIZONS:
                s = summary[sig_type][regime].get(h)
                if not s:
                    print(f"    {h}d: no data")
                    continue
                eh, er = s["edge_hit_pp"], s["edge_ret_pp"]
                print(f"    {h}d: n={s['n']:4d} (baseline n={s['baseline_n']})  hit={s['hit_rate']*100:5.1f}%  "
                      f"avg={s['avg_pct']:+6.2f}%  EDGE vs regime-matched swing: hit {eh:+5.1f}pp, ret {er:+5.2f}pp")
                hci, hp = s["edge_hit_ci_pp"], s["edge_hit_p"]
                if hci is not None:
                    sig_word = "significant" if hp < 0.05 else "NOT significant"
                    print(f"        95% CI ({s['n_clusters']} tickers): hit edge [{hci[0]:+5.1f}, {hci[1]:+5.1f}]pp "
                          f"(p={hp:.3f}) -- {sig_word} at 5% level")
                if s["options_n"]:
                    oh, opnl = s["options_edge_hit_pp"], s["options_edge_pnl_pp"]
                    print(f"        OPTIONS: n={s['options_n']:4d}  hit={s['options_hit_rate']*100:5.1f}%  "
                          f"avg_pnl={s['options_avg_pnl_pct']:+6.1f}%  EDGE: hit {oh:+5.1f}pp, pnl {opnl:+5.1f}pp")
                    ohci, ohp = s["options_edge_hit_ci_pp"], s["options_edge_hit_p"]
                    if ohci is not None:
                        osig = "significant" if ohp < 0.05 else "NOT significant"
                        print(f"        95% CI (options): hit edge [{ohci[0]:+5.1f}, {ohci[1]:+5.1f}]pp "
                              f"(p={ohp:.3f}) -- {osig} at 5% level")


if __name__ == "__main__":
    main()
