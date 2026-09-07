"""
Deep-scan Wyckoff watchlist scanner -- runs every detector we have (Wheel
Zones spring/upthrust, trading-range context, Buying/Selling Climax +
Automatic Reaction, Sign of Strength/Weakness + Last Point of
Support/Supply, and an Elliott-style ABC correction) across the smaller
Core Watchlist.csv, and saves an annotated chart PNG for any ticker with a
NEW signal today.

Small ticker count (44) means this comfortably fits the Twelve Data free
tier's rate limit in a few minutes, unlike the full S&P 500 sweep.

Usage: python wyckoff_watchlist_scanner.py
Prints a summary and the chart file paths; see main() for what gets
reported to a push notification.
"""

import csv
import os
from pathlib import Path

import alert_log
import regime_filter
import trade_journal
import wyckoff_notify as notify
from wyckoff_common import (BENCHMARK, fetch_bars, load_api_key, pivots, wilder_atr,
                             build_close_by_date, is_pure_spring, is_pure_upthrust,
                             get_confidence_tier)
from wyckoff_patterns import trading_range, climax_events, sos_sow_events, lps_lpsy_events, abc_pattern
from wyckoff_charts import plot_signal_chart
from pretrade import expected_move, format_line

TICKER_FILE = Path(__file__).resolve().parent.parent / "data" / "core_watchlist.csv"
CHART_WINDOW = 90

# Auto-drafting into trades.csv only makes sense for a local run -- the
# GitHub Actions runner's filesystem is discarded when the job ends, so a
# draft written there would vanish immediately with no benefit. The alert
# log is different: it's committed back to the repo by a workflow step, so
# it's logged unconditionally, in CI and locally alike.
DRAFT_JOURNAL = not os.environ.get("GITHUB_ACTIONS")


# Regime filter mode: "strict" filters signals against regime, "permissive" allows
# all signals with regime context, "adaptive" is strict only in strong trends.
# Set via REGIME_MODE env var, defaults to "strict".
REGIME_MODE = os.environ.get("REGIME_MODE", "strict")


def _draft(sym, setup, direction, thesis, close, regime_filtered=False):
    """Log alert and optionally draft to trade journal.
    
    If regime_filtered=True, the alert is logged with a '[REGIME-FILTERED]' prefix
    but NOT drafted to the trade journal (you shouldn't trade against the regime)."""
    if regime_filtered:
        thesis = f"[REGIME-FILTERED] {thesis}"
    logged = alert_log.log_alert("watchlist", sym, setup, direction, thesis, close)
    if not logged:
        return  # same-day duplicate of an already-logged alert -- don't draft it twice either
    if not DRAFT_JOURNAL:
        return
    if regime_filtered:
        return  # don't draft regime-filtered signals
    try:
        trade_journal.add_draft(sym, setup, direction, thesis, close)
    except OSError as e:
        print(f"  (could not write trade journal draft for {sym}: {e})")


def load_tickers():
    with open(TICKER_FILE, newline="", encoding="utf-8") as f:
        return [row["Symbol"] for row in csv.DictReader(f)]


def scan_ticker(sym, bars, spy_by_date, regime_info):
    n = len(bars)
    if n < 60:
        return None
    atr = wilder_atr(bars)
    res, sup = pivots(bars)

    actionable_events = []   # (setup_type, thesis) tuples for regime-aligned signals
    filtered_events = []     # thesis strings for regime-filtered signals (watchlist only)
    markers = []             # chart annotations, {"idx", "kind", "text"}

    # Helper to check regime and decide whether to alert or filter
    def _check_regime(direction):
        """Returns (should_alert, is_filtered) tuple."""
        if regime_filter.should_take_signal(regime_info, direction):
            return True, False
        else:
            return False, True

    # ---- Spring / upthrust: TEXTBOOK definition only (undercut-and-recover /
    # poke-and-fail), no RS gate, no 'near the level' dilution. Edge-triggered
    # on today. NOTE: backtesting (see BACKTEST_FINDINGS.md) shows neither of
    # these beats naive swing-trading -- they are DISCRETIONARY REVIEW TRIGGERS,
    # not a validated mechanical edge. Direction is stated for a LONG option.
    def _pure_spring(j):
        return is_pure_spring(bars, sup, j)

    def _pure_upthrust(j):
        return is_pure_upthrust(bars, res, j)

    if _pure_spring(n - 1) and not _pure_spring(n - 2):
        thesis = f"Spring at support {sup[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bullish bias, review for a LONG CALL"
        should_alert, is_filtered = _check_regime("bullish")
        if should_alert:
            actionable_events.append(("spring", thesis))
            _draft(sym, "spring", "long_call", thesis, bars[-1]["close"])
        else:
            filtered_events.append(thesis)
            _draft(sym, "spring", "long_call", thesis, bars[-1]["close"], regime_filtered=True)
    if _pure_upthrust(n - 1) and not _pure_upthrust(n - 2):
        thesis = f"Upthrust at resistance {res[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bearish bias, review for a LONG PUT"
        should_alert, is_filtered = _check_regime("bearish")
        if should_alert:
            actionable_events.append(("upthrust", thesis))
            _draft(sym, "upthrust", "long_put", thesis, bars[-1]["close"])
        else:
            filtered_events.append(thesis)
            _draft(sym, "upthrust", "long_put", thesis, bars[-1]["close"], regime_filtered=True)
    # mark recent springs/upthrusts in the chart window for context
    for i in range(max(0, n - CHART_WINDOW), n):
        if _pure_spring(i):
            markers.append({"idx": i, "kind": "spring"})
        if _pure_upthrust(i):
            markers.append({"idx": i, "kind": "upthrust"})

    # ---- Trading range: flag just entering one ----
    tr_today = trading_range(bars)
    tr_yday = trading_range(bars[:-1])
    if tr_today and tr_yday and tr_today["inRange"] and not tr_yday["inRange"]:
        actionable_events.append(("trading_range", f"Entered a trading range ({tr_today['rangeLow']:.2f}-{tr_today['rangeHigh']:.2f})"))

    # ---- Climax + Automatic Reaction/Rally ----
    climaxes = climax_events(bars, atr)
    for e in climaxes:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["arIdx"] is not None and e["arIdx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["arIdx"], "kind": "AR", "text": "AR", "price": e["arPrice"]})
        if e["idx"] == n - 1:
            label, bias = ("Selling Climax", "bullish bias, review for a LONG CALL") if e["type"] == "SC" \
                else ("Buying Climax", "bearish bias, review for a LONG PUT")
            thesis = f"{label} @ {e['price']:.2f} -- {bias}"
            direction = "bullish" if e["type"] == "SC" else "bearish"
            should_alert, is_filtered = _check_regime(direction)
            setup_type = e["type"].lower()
            if should_alert:
                actionable_events.append((setup_type, thesis))
                _draft(sym, setup_type, "long_call" if e["type"] == "SC" else "long_put", thesis, e["price"])
            else:
                filtered_events.append(thesis)
                _draft(sym, setup_type, "long_call" if e["type"] == "SC" else "long_put", thesis, e["price"], regime_filtered=True)
        if e["arIdx"] == n - 1:
            label = "Automatic Rally" if e["type"] == "SC" else "Automatic Reaction"
            actionable_events.append(("ar", f"{label} @ {e['arPrice']:.2f} (range boundary from the {e['date']} {e['type']}) -- context, not an entry"))

    # ---- Sign of Strength/Weakness ----
    sos_sow = sos_sow_events(bars, res, sup, atr)
    for e in sos_sow:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["idx"] == n - 1:
            label, bias = ("Sign of Strength (breakout held)", "bullish bias, review for a LONG CALL") if e["type"] == "SOS" \
                else ("Sign of Weakness (breakdown held)", "bearish bias, review for a LONG PUT")
            thesis = f"{label} @ {e['level']:.2f} -- {bias}"
            direction = "bullish" if e["type"] == "SOS" else "bearish"
            should_alert, is_filtered = _check_regime(direction)
            setup_type = e["type"].lower()
            if should_alert:
                actionable_events.append((setup_type, thesis))
                _draft(sym, setup_type, "long_call" if e["type"] == "SOS" else "long_put", thesis, bars[-1]["close"])
            else:
                filtered_events.append(thesis)
                _draft(sym, setup_type, "long_call" if e["type"] == "SOS" else "long_put", thesis, bars[-1]["close"], regime_filtered=True)

    # ---- Last Point of Support/Supply ----
    lps = lps_lpsy_events(bars, sos_sow, atr)
    for e in lps:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["idx"] == n - 1:
            label, bias = ("Last Point of Support", "bullish bias, review for a LONG CALL") if e["type"] == "LPS" \
                else ("Last Point of Supply", "bearish bias, review for a LONG PUT")
            thesis = f"{label} @ {e['level']:.2f} -- {bias}"
            direction = "bullish" if e["type"] == "LPS" else "bearish"
            should_alert, is_filtered = _check_regime(direction)
            setup_type = e["type"].lower()
            if should_alert:
                actionable_events.append((setup_type, thesis))
                _draft(sym, setup_type, "long_call" if e["type"] == "LPS" else "long_put", thesis, bars[-1]["close"])
            else:
                filtered_events.append(thesis)
                _draft(sym, setup_type, "long_call" if e["type"] == "LPS" else "long_put", thesis, bars[-1]["close"], regime_filtered=True)

    # ---- ABC correction ----
    abc = abc_pattern(bars)
    if abc:
        abc_kinds = {"start": "abc_start", "pointA": "abc_a", "pointB": "abc_b", "pointC": "abc_c"}
        abc_labels = {"start": "0", "pointA": "A", "pointB": "B", "pointC": "C"}
        for key, kind in abc_kinds.items():
            pt = abc[key]
            if pt["idx"] >= max(0, n - CHART_WINDOW):
                markers.append({"idx": pt["idx"], "kind": kind, "text": abc_labels[key], "price": pt["price"]})
        if abc["isNew"]:
            opt = "LONG CALL" if abc["direction"].startswith("bullish") else "LONG PUT"
            thesis = (f"ABC correction complete, {abc['direction']} -- review for a {opt} "
                      f"(B retraced {abc['bRetrace']*100:.0f}% of A, C extended {abc['cExtension']*100:.0f}% of A)")
            direction = "bullish" if abc["direction"].startswith("bullish") else "bearish"
            should_alert, is_filtered = _check_regime(direction)
            if should_alert:
                actionable_events.append(("abc", thesis))
                _draft(sym, "abc", "long_call" if opt == "LONG CALL" else "long_put", thesis, bars[-1]["close"])
            else:
                filtered_events.append(thesis)
                _draft(sym, "abc", "long_call" if opt == "LONG CALL" else "long_put", thesis, bars[-1]["close"], regime_filtered=True)

    if not actionable_events and not filtered_events:
        return None

    # Pre-trade context (free): realized-vol expected move over ~30 trading
    # days, to sanity-check strikes/targets. IV rank NOT included (paid).
    em = expected_move(bars, 30)
    context_line = "Context: " + format_line(em) + " | check IV rank + earnings in broker before entry"

    # Add regime context
    regime_line = regime_filter.regime_context_line(regime_info)

    chart_path = plot_signal_chart(
        sym, bars, res=res[-1], sup=sup[-1],
        range_high=tr_today["rangeHigh"] if tr_today else None,
        range_low=tr_today["rangeLow"] if tr_today else None,
        markers=markers, title_suffix="Wyckoff signals", window=CHART_WINDOW,
    )
    
    return {
        "actionable_events": actionable_events,  # list of (setup_type, thesis)
        "filtered_events": filtered_events,      # list of thesis strings
        "context_line": context_line,
        "regime_line": regime_line,
        "chart": chart_path,
    }


def scan(tickers, api_key, progress=False):
    spy_bars = fetch_bars(BENCHMARK, api_key)
    if not spy_bars:
        raise RuntimeError("Could not fetch benchmark (SPY) data -- aborting scan.")
    spy_by_date = build_close_by_date(spy_bars)

    # Compute market regime from SPY
    regime_info = regime_filter.get_regime(spy_bars, mode=REGIME_MODE)
    if progress:
        print(f"Market regime: {regime_info['message']}")
        print()

    hits = []
    skipped = []
    filtered_count = 0
    for idx, sym in enumerate(tickers, 1):
        bars = fetch_bars(sym, api_key)
        if not bars:
            skipped.append(sym)
        else:
            result = scan_ticker(sym, bars, spy_by_date, regime_info)
            if result:
                hits.append((sym, result))
        if progress:
            print(f"...{idx}/{len(tickers)} scanned", flush=True)
    return hits, skipped, regime_info


def main():
    api_key = load_api_key()
    tickers = load_tickers()
    hits, skipped, regime_info = scan(tickers, api_key, progress=True)
    hits.sort(key=lambda x: x[0])

    print(f"Scanned {len(tickers)} watchlist tickers, {len(skipped)} skipped.")
    if skipped:
        print("Skipped:", ", ".join(skipped))
    print()

    if len(skipped) > len(tickers) / 2:
        notify.send_message(f"Wyckoff watchlist scan degraded: {len(skipped)}/{len(tickers)} tickers failed to fetch.")

    if not hits:
        print("No new Wyckoff signals today.")
        return

    # Separate actionable vs filtered signals and compute tiers
    actionable_signals = []  # (sym, tier_label, [lines])
    watchlist_signals = []   # (sym, [lines])
    chart_paths = {}
    
    for sym, result in hits:
        chart_paths[sym] = result["chart"]
        
        # Process actionable events with tiers
        if result["actionable_events"]:
            # Get the highest tier among all signals for this ticker
            best_tier = "REVIEW"
            tier_order = {"HIGH": 0, "MEDIUM": 1, "REVIEW": 2}
            lines = []
            for setup_type, thesis in result["actionable_events"]:
                tier_info = get_confidence_tier(setup_type, regime_aligned=True)
                if tier_order.get(tier_info["tier"], 99) < tier_order.get(best_tier, 99):
                    best_tier = tier_info["tier"]
                lines.append(thesis)
            
            # Add context lines
            lines.append(result["context_line"])
            lines.append(result["regime_line"])
            
            tier_label = {"HIGH": "⭐⭐⭐", "MEDIUM": "⭐⭐", "REVIEW": "⭐"}[best_tier]
            actionable_signals.append((sym, tier_label, lines))
        
        # Process filtered events (watchlist only)
        if result["filtered_events"]:
            lines = result["filtered_events"] + [result["regime_line"]]
            watchlist_signals.append((sym, lines))
    
    # Print summary
    for sym, result in hits:
        print(f"{sym}: chart -> {result['chart']}")
        for setup_type, thesis in result["actionable_events"]:
            tier_info = get_confidence_tier(setup_type, regime_aligned=True)
            print(f"  {tier_info['label']} {thesis}")
        for thesis in result["filtered_events"]:
            print(f"  [WATCHLIST] {thesis}")

    tickers_with_actionable = [s[0] for s in actionable_signals]
    tickers_str = ", ".join(tickers_with_actionable[:8])
    more = f" +{len(tickers_with_actionable) - 8} more" if len(tickers_with_actionable) > 8 else ""
    print()
    print(f"SUMMARY: {len(actionable_signals)} actionable + {len(watchlist_signals)} watchlist-only signals -- {tickers_str}{more}")
    print(f"Charts saved in: {Path(__file__).resolve().parent.parent / 'charts'}")

    regime_line = regime_filter.regime_context_line(regime_info)
    header = (f"Wyckoff watchlist: {len(actionable_signals)} actionable + {len(watchlist_signals)} watchlist signals. "
              f"{regime_line}. "
              "Tiers based on historical performance (⭐⭐⭐=HIGH, ⭐⭐=MEDIUM, ⭐=REVIEW ONLY).")
    
    notify.notify_signals_tiered(header, actionable_signals, watchlist_signals, chart_paths)


if __name__ == "__main__":
    main()
