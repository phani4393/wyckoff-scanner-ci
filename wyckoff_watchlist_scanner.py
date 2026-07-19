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
from pathlib import Path

import wyckoff_notify as notify
from wyckoff_common import BENCHMARK, fetch_bars, load_api_key, pivots, wilder_atr, build_close_by_date
from wyckoff_scanner import wheel_signals
from wyckoff_patterns import trading_range, climax_events, sos_sow_events, lps_lpsy_events, abc_pattern
from wyckoff_charts import plot_signal_chart

TICKER_FILE = Path(__file__).with_name("Core Watchlist.csv")
CHART_WINDOW = 90


def load_tickers():
    with open(TICKER_FILE, newline="", encoding="utf-8") as f:
        return [row["Symbol"] for row in csv.DictReader(f)]


def scan_ticker(sym, bars, spy_by_date):
    n = len(bars)
    if n < 60:
        return None
    atr = wilder_atr(bars)
    res, sup = pivots(bars)

    new_events = []   # human-readable strings for the notification
    markers = []       # chart annotations, {"idx", "kind", "text"}

    # ---- Wheel Zones: spring/upthrust, reusing the already-verified logic ----
    wheel = wheel_signals(bars, spy_by_date)
    if wheel:
        if wheel["sellPutNew"]:
            new_events.append(f"Spring at support (rising RS) @ {wheel['close']:.2f}")
        if wheel["sellCallNew"]:
            new_events.append(f"Upthrust at resistance @ {wheel['close']:.2f}")
    # mark recent springs/upthrusts in the chart window regardless of "new", for context
    for i in range(max(0, n - CHART_WINDOW), n):
        if sup[i] is not None and bars[i]["low"] < sup[i] and bars[i]["close"] > sup[i]:
            markers.append({"idx": i, "kind": "spring"})
        if res[i] is not None and bars[i]["high"] > res[i] and bars[i]["close"] < res[i]:
            markers.append({"idx": i, "kind": "upthrust"})

    # ---- Trading range: flag just entering one ----
    tr_today = trading_range(bars)
    tr_yday = trading_range(bars[:-1])
    if tr_today and tr_yday and tr_today["inRange"] and not tr_yday["inRange"]:
        new_events.append(f"Entered a trading range ({tr_today['rangeLow']:.2f}-{tr_today['rangeHigh']:.2f})")

    # ---- Climax + Automatic Reaction/Rally ----
    climaxes = climax_events(bars, atr)
    for e in climaxes:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["arIdx"] is not None and e["arIdx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["arIdx"], "kind": "AR", "text": "AR", "price": e["arPrice"]})
        if e["idx"] == n - 1:
            label = "Selling Climax" if e["type"] == "SC" else "Buying Climax"
            new_events.append(f"{label} today @ {e['price']:.2f} (watch for the Automatic Rally/Reaction)")
        if e["arIdx"] == n - 1:
            label = "Automatic Rally" if e["type"] == "SC" else "Automatic Reaction"
            new_events.append(f"{label} just confirmed @ {e['arPrice']:.2f} (range boundary set from the {e['date']} {e['type']})")

    # ---- Sign of Strength/Weakness ----
    sos_sow = sos_sow_events(bars, res, sup, atr)
    for e in sos_sow:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["idx"] == n - 1:
            label = "Sign of Strength (breakout held)" if e["type"] == "SOS" else "Sign of Weakness (breakdown held)"
            new_events.append(f"{label} @ {e['level']:.2f}")

    # ---- Last Point of Support/Supply ----
    lps = lps_lpsy_events(bars, sos_sow, atr)
    for e in lps:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["idx"] == n - 1:
            label = "Last Point of Support" if e["type"] == "LPS" else "Last Point of Supply"
            new_events.append(f"{label} @ {e['level']:.2f} (classic entry after the earlier breakout)")

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
            new_events.append(f"ABC correction complete, {abc['direction']} (B retraced {abc['bRetrace']*100:.0f}% of A, C extended {abc['cExtension']*100:.0f}% of A)")

    if not new_events:
        return None

    chart_path = plot_signal_chart(
        sym, bars, res=res[-1], sup=sup[-1],
        range_high=tr_today["rangeHigh"] if tr_today else None,
        range_low=tr_today["rangeLow"] if tr_today else None,
        markers=markers, title_suffix="Wyckoff signals", window=CHART_WINDOW,
    )
    return {"events": new_events, "chart": chart_path}


def scan(tickers, api_key, progress=False):
    spy_bars = fetch_bars(BENCHMARK, api_key)
    if not spy_bars:
        raise RuntimeError("Could not fetch benchmark (SPY) data -- aborting scan.")
    spy_by_date = build_close_by_date(spy_bars)

    hits = []
    skipped = []
    for idx, sym in enumerate(tickers, 1):
        bars = fetch_bars(sym, api_key)
        if not bars:
            skipped.append(sym)
        else:
            result = scan_ticker(sym, bars, spy_by_date)
            if result:
                hits.append((sym, result))
        if progress:
            print(f"...{idx}/{len(tickers)} scanned", flush=True)
    return hits, skipped


def main():
    api_key = load_api_key()
    tickers = load_tickers()
    hits, skipped = scan(tickers, api_key, progress=True)
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

    for sym, result in hits:
        print(f"{sym}: chart -> {result['chart']}")
        for e in result["events"]:
            print(f"  - {e}")

    tickers_str = ", ".join(sym for sym, _ in hits[:8])
    more = f" +{len(hits) - 8} more" if len(hits) > 8 else ""
    print()
    print(f"SUMMARY: {len(hits)} watchlist ticker(s) with new signals -- {tickers_str}{more}")
    print(f"Charts saved in: {Path(__file__).with_name('charts')}")

    events_only = [(sym, result["events"]) for sym, result in hits]
    chart_paths = {sym: result["chart"] for sym, result in hits}
    notify.notify_signals(f"Wyckoff watchlist scan: {len(hits)} ticker(s) with new signals", events_only, chart_paths)


if __name__ == "__main__":
    main()
