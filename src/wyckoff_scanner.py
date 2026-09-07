"""
Wyckoff S&P 500 scanner.

Python port of the logic already in Weis Wave Volume.pine and
Wyckoff Wheel Zones.pine, run once across every ticker in SP500 Tickers.csv
instead of one symbol at a time on a TradingView chart. Reports only NEW
signals (today, not already true yesterday) so it can be run on a schedule
without repeating the same alert every day a price sits in a zone.

Data source: Twelve Data's time_series endpoint (official, documented,
free-tier API key required). The free tier caps out at 8 API credits/minute
(1 credit = 1 symbol), so this scans sequentially, paced to stay under that
cap -- a full S&P 500 scan takes roughly 60-90 minutes on the free tier.

Usage: python wyckoff_scanner.py
Prints one line per ticker with a fresh signal, plus a one-line summary
suitable for a push notification.
"""

import csv
import os
from pathlib import Path

import alert_log
import regime_filter
import wyckoff_notify as notify
from wyckoff_common import (
    BENCHMARK,
    fetch_bars,
    load_api_key,
    pivots,
    is_pure_spring,
    is_pure_upthrust,
    get_confidence_tier,
)

TICKER_FILE = Path(__file__).resolve().parent.parent / "data" / "top50_plus_ai.csv"

# Regime filter mode: "strict" filters signals against regime, "permissive" allows
# all signals with regime context, "adaptive" is strict only in strong trends.
# Set via REGIME_MODE env var, defaults to "strict".
REGIME_MODE = os.environ.get("REGIME_MODE", "strict")

# ---- Weis Wave params (match Weis Wave Volume.pine defaults, Percent mode) ----
REVERSAL_PCT = 1.0
FLAG_RATIO = 1.5


def weis_wave_signal(bars):
    n = len(bars)
    if n < 30:
        return None
    trend = 0
    wave_high = wave_low = wave_vol = None
    prev_vol_per_pt = None
    wave_started_idx = 0
    events = []  # (idx, direction, endedVolPerPt, prevVolPerPt)

    for i in range(n):
        b = bars[i]
        vol = b["volume"] or 0
        if trend == 0:
            trend = 1 if b["close"] >= (bars[i - 1]["close"] if i > 0 else b["close"]) else -1
            wave_high, wave_low, wave_vol = b["high"], b["low"], vol
            wave_started_idx = i
            continue

        if trend == 1:
            rev_amt = wave_high * REVERSAL_PCT / 100
            if b["close"] <= wave_high - rev_amt:
                ended_range = wave_high - wave_low
                ended_vpp = (wave_vol / ended_range) if ended_range > 0 else None
                events.append((i, 1, ended_vpp, prev_vol_per_pt))
                prev_vol_per_pt = ended_vpp
                trend, wave_high, wave_low, wave_vol = -1, b["high"], b["low"], vol
                wave_started_idx = i
            else:
                wave_high, wave_low = max(wave_high, b["high"]), min(wave_low, b["low"])
                wave_vol += vol
        else:
            rev_amt = wave_low * REVERSAL_PCT / 100
            if b["close"] >= wave_low + rev_amt:
                ended_range = wave_high - wave_low
                ended_vpp = (wave_vol / ended_range) if ended_range > 0 else None
                events.append((i, -1, ended_vpp, prev_vol_per_pt))
                prev_vol_per_pt = ended_vpp
                trend, wave_high, wave_low, wave_vol = 1, b["high"], b["low"], vol
                wave_started_idx = i
            else:
                wave_high, wave_low = max(wave_high, b["high"]), min(wave_low, b["low"])
                wave_vol += vol

    if not events:
        return None
    last_idx, direction, ended_vpp, prior_vpp = events[-1]
    flagged = prior_vpp is not None and ended_vpp is not None and ended_vpp > prior_vpp * FLAG_RATIO
    is_today = last_idx == n - 1
    return {"newWaveToday": is_today, "direction": direction, "flagged": flagged}


def load_tickers():
    with open(TICKER_FILE, newline="", encoding="utf-8") as f:
        return [row["Symbol"] for row in csv.DictReader(f)]


def scan(tickers, api_key, progress=False):
    spy_bars = fetch_bars(BENCHMARK, api_key)
    if not spy_bars:
        raise RuntimeError("Could not fetch benchmark (SPY) data -- aborting scan.")

    # Compute market regime from SPY
    regime_info = regime_filter.get_regime(spy_bars, mode=REGIME_MODE)
    if progress:
        print(f"Market regime: {regime_info['message']}")
        print()

    actionable_hits = []  # (sym, setup_type, thesis)
    filtered_hits = []    # (sym, thesis)
    skipped = []

    for idx, sym in enumerate(tickers, 1):
        bars = fetch_bars(sym, api_key)
        if not bars:
            skipped.append(sym)
        else:
            # Textbook spring/upthrust (undercut-and-recover / poke-and-fail),
            # edge-triggered on today. These are DISCRETIONARY REVIEW TRIGGERS
            # for a long option -- backtesting shows they don't beat naive
            # swing-trading, so direction is a bias to review, not an edge.
            res, sup = pivots(bars)
            m = len(bars)
            if m >= 2:
                sp_now = is_pure_spring(bars, sup, -1)
                sp_prev = is_pure_spring(bars, sup, -2)
                if sp_now and not sp_prev:
                    thesis = f"Spring at support {sup[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bullish bias, review for a LONG CALL"
                    if regime_filter.should_take_signal(regime_info, "bullish"):
                        actionable_hits.append((sym, "spring", thesis))
                        alert_log.log_alert("sp500_sweep", sym, "spring", "long_call", thesis, bars[-1]["close"])
                    else:
                        # Log as filtered but don't alert
                        filtered_hits.append((sym, thesis))
                        alert_log.log_alert("sp500_sweep", sym, "spring", "long_call", f"[REGIME-FILTERED] {thesis}", bars[-1]["close"])
                ut_now = is_pure_upthrust(bars, res, -1)
                ut_prev = is_pure_upthrust(bars, res, -2)
                if ut_now and not ut_prev:
                    thesis = f"Upthrust at resistance {res[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bearish bias, review for a LONG PUT"
                    if regime_filter.should_take_signal(regime_info, "bearish"):
                        actionable_hits.append((sym, "upthrust", thesis))
                        alert_log.log_alert("sp500_sweep", sym, "upthrust", "long_put", thesis, bars[-1]["close"])
                    else:
                        # Log as filtered but don't alert
                        filtered_hits.append((sym, thesis))
                        alert_log.log_alert("sp500_sweep", sym, "upthrust", "long_put", f"[REGIME-FILTERED] {thesis}", bars[-1]["close"])
            weis = weis_wave_signal(bars)
            if weis and weis["newWaveToday"] and weis["flagged"]:
                direction = "up" if weis["direction"] == 1 else "down"
                thesis = f"Weis Wave volume-exhaustion flag on new {direction} wave -- context only"
                # Weis Wave is context-only, not directional -- always include as actionable
                actionable_hits.append((sym, "weis_wave", thesis))
                alert_log.log_alert("sp500_sweep", sym, "weis_wave", None, thesis, bars[-1]["close"])
        if progress and idx % 25 == 0:
            print(f"...{idx}/{len(tickers)} scanned", flush=True)

    return actionable_hits, filtered_hits, skipped, regime_info


def main():
    api_key = load_api_key()
    tickers = load_tickers()
    actionable_hits, filtered_hits, skipped, regime_info = scan(tickers, api_key, progress=True)

    print(f"Scanned {len(tickers)} tickers, {len(skipped)} skipped (fetch failed or insufficient history).")
    if filtered_hits:
        print(f"Regime-filtered: {len(filtered_hits)} signal(s) logged but moved to watchlist section")
    if skipped:
        print("Skipped:", ", ".join(skipped[:30]) + (" ..." if len(skipped) > 30 else ""))
    print()

    if len(skipped) > len(tickers) / 2:
        notify.send_message(f"Wyckoff S&P scan degraded: {len(skipped)}/{len(tickers)} tickers failed to fetch.")

    if not actionable_hits and not filtered_hits:
        print("No new Wyckoff signals today.")
        return

    # Build tiered actionable signals
    actionable_signals = []  # (sym, tier_label, [lines])
    
    # Group actionable hits by symbol
    from collections import defaultdict
    by_sym = defaultdict(list)
    for sym, setup_type, thesis in actionable_hits:
        by_sym[sym].append((setup_type, thesis))
    
    for sym, signals in sorted(by_sym.items()):
        # Get the highest tier among all signals for this ticker
        best_tier = "REVIEW"
        tier_order = {"HIGH": 0, "MEDIUM": 1, "REVIEW": 2}
        lines = []
        for setup_type, thesis in signals:
            tier_info = get_confidence_tier(setup_type, regime_aligned=True)
            if tier_order.get(tier_info["tier"], 99) < tier_order.get(best_tier, 99):
                best_tier = tier_info["tier"]
            lines.append(thesis)
        
        tier_label = {"HIGH": "⭐⭐⭐", "MEDIUM": "⭐⭐", "REVIEW": "⭐"}[best_tier]
        actionable_signals.append((sym, tier_label, lines))
    
    # Build watchlist signals (regime-filtered)
    watchlist_signals = []  # (sym, [lines])
    filtered_by_sym = defaultdict(list)
    for sym, thesis in filtered_hits:
        filtered_by_sym[sym].append(thesis)
    
    for sym, theses in sorted(filtered_by_sym.items()):
        watchlist_signals.append((sym, theses))
    
    # Print results
    for sym, tier_label, lines in actionable_signals:
        for line in lines:
            print(f"{tier_label} {sym}: {line}")
    
    for sym, lines in watchlist_signals:
        for line in lines:
            print(f"[WATCHLIST] {sym}: {line}")

    tickers_str = ", ".join(sym for sym, _, _ in actionable_signals[:8])
    more = f" +{len(actionable_signals) - 8} more" if len(actionable_signals) > 8 else ""
    print()
    print(f"SUMMARY: {len(actionable_signals)} actionable + {len(watchlist_signals)} watchlist -- {tickers_str}{more}")

    regime_line = regime_filter.regime_context_line(regime_info)
    header = (f"Wyckoff top-50 scan: {len(actionable_signals)} actionable + {len(watchlist_signals)} watchlist. "
              f"{regime_line}. "
              "Tiers based on historical performance (⭐⭐⭐=HIGH, ⭐⭐=MEDIUM, ⭐=REVIEW ONLY).")
    notify.notify_signals_tiered(header, actionable_signals, watchlist_signals)


if __name__ == "__main__":
    main()
