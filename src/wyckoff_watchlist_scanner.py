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
from wyckoff_patterns import trading_range, climax_events, sos_sow_events, lps_lpsy_events, abc_pattern
from wyckoff_charts import plot_signal_chart
from pretrade import expected_move, format_line

TICKER_FILE = Path(__file__).resolve().parent.parent / "data" / "core_watchlist.csv"
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

    # ---- Spring / upthrust: TEXTBOOK definition only (undercut-and-recover /
    # poke-and-fail), no RS gate, no 'near the level' dilution. Edge-triggered
    # on today. NOTE: backtesting (see BACKTEST_FINDINGS.md) shows neither of
    # these beats naive swing-trading -- they are DISCRETIONARY REVIEW TRIGGERS,
    # not a validated mechanical edge. Direction is stated for a LONG option.
    def _pure_spring(j):
        return sup[j] is not None and bars[j]["low"] < sup[j] and bars[j]["close"] > sup[j]

    def _pure_upthrust(j):
        return res[j] is not None and bars[j]["high"] > res[j] and bars[j]["close"] < res[j]

    if _pure_spring(n - 1) and not _pure_spring(n - 2):
        new_events.append(f"Spring at support {sup[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bullish bias, review for a LONG CALL")
    if _pure_upthrust(n - 1) and not _pure_upthrust(n - 2):
        new_events.append(f"Upthrust at resistance {res[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bearish bias, review for a LONG PUT")
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
        new_events.append(f"Entered a trading range ({tr_today['rangeLow']:.2f}-{tr_today['rangeHigh']:.2f})")

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
            new_events.append(f"{label} @ {e['price']:.2f} -- {bias}")
        if e["arIdx"] == n - 1:
            label = "Automatic Rally" if e["type"] == "SC" else "Automatic Reaction"
            new_events.append(f"{label} @ {e['arPrice']:.2f} (range boundary from the {e['date']} {e['type']}) -- context, not an entry")

    # ---- Sign of Strength/Weakness ----
    sos_sow = sos_sow_events(bars, res, sup, atr)
    for e in sos_sow:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["idx"] == n - 1:
            label, bias = ("Sign of Strength (breakout held)", "bullish bias, review for a LONG CALL") if e["type"] == "SOS" \
                else ("Sign of Weakness (breakdown held)", "bearish bias, review for a LONG PUT")
            new_events.append(f"{label} @ {e['level']:.2f} -- {bias}")

    # ---- Last Point of Support/Supply ----
    lps = lps_lpsy_events(bars, sos_sow, atr)
    for e in lps:
        if e["idx"] >= max(0, n - CHART_WINDOW):
            markers.append({"idx": e["idx"], "kind": e["type"], "text": e["type"]})
        if e["idx"] == n - 1:
            label, bias = ("Last Point of Support", "bullish bias, review for a LONG CALL") if e["type"] == "LPS" \
                else ("Last Point of Supply", "bearish bias, review for a LONG PUT")
            new_events.append(f"{label} @ {e['level']:.2f} -- {bias}")

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
            new_events.append(f"ABC correction complete, {abc['direction']} -- review for a {opt} "
                              f"(B retraced {abc['bRetrace']*100:.0f}% of A, C extended {abc['cExtension']*100:.0f}% of A)")

    if not new_events:
        return None

    # Pre-trade context (free): realized-vol expected move over ~30 trading
    # days, to sanity-check strikes/targets. IV rank NOT included (paid).
    em = expected_move(bars, 30)
    new_events.append("Context: " + format_line(em) + " | check IV rank + earnings in broker before entry")

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
    print(f"Charts saved in: {Path(__file__).resolve().parent.parent / 'charts'}")

    events_only = [(sym, result["events"]) for sym, result in hits]
    chart_paths = {sym: result["chart"] for sym, result in hits}
    header = (f"Wyckoff watchlist: {len(hits)} ticker(s) flagged for REVIEW. "
              "Discretionary review triggers, NOT validated edges -- backtesting shows none "
              "beat naive swing-trading. Apply your own judgment (context, IV, news) before any entry.")
    notify.notify_signals(header, events_only, chart_paths)


if __name__ == "__main__":
    main()
