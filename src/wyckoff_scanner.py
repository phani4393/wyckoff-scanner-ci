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
from pathlib import Path

import wyckoff_notify as notify
from wyckoff_common import (
    BENCHMARK,
    fetch_bars,
    load_api_key,
    pivots,
)

TICKER_FILE = Path(__file__).resolve().parent.parent / "data" / "top50_plus_ai.csv"

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

    hits = []
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
            signals = []
            if m >= 2 and sup[-1] is not None:
                sp_now = bars[-1]["low"] < sup[-1] and bars[-1]["close"] > sup[-1]
                sp_prev = sup[-2] is not None and bars[-2]["low"] < sup[-2] and bars[-2]["close"] > sup[-2]
                if sp_now and not sp_prev:
                    signals.append(f"Spring at support {sup[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bullish bias, review for a LONG CALL")
            if m >= 2 and res[-1] is not None:
                ut_now = bars[-1]["high"] > res[-1] and bars[-1]["close"] < res[-1]
                ut_prev = res[-2] is not None and bars[-2]["high"] > res[-2] and bars[-2]["close"] < res[-2]
                if ut_now and not ut_prev:
                    signals.append(f"Upthrust at resistance {res[-1]:.2f} (close {bars[-1]['close']:.2f}) -- bearish bias, review for a LONG PUT")
            weis = weis_wave_signal(bars)
            if weis and weis["newWaveToday"] and weis["flagged"]:
                direction = "up" if weis["direction"] == 1 else "down"
                signals.append(f"Weis Wave volume-exhaustion flag on new {direction} wave -- context only")
            if signals:
                hits.append((sym, signals))
        if progress and idx % 25 == 0:
            print(f"...{idx}/{len(tickers)} scanned", flush=True)

    return hits, skipped


def main():
    api_key = load_api_key()
    tickers = load_tickers()
    hits, skipped = scan(tickers, api_key, progress=True)
    hits.sort(key=lambda x: x[0])

    print(f"Scanned {len(tickers)} tickers, {len(skipped)} skipped (fetch failed or insufficient history).")
    if skipped:
        print("Skipped:", ", ".join(skipped[:30]) + (" ..." if len(skipped) > 30 else ""))
    print()

    if len(skipped) > len(tickers) / 2:
        notify.send_message(f"Wyckoff S&P scan degraded: {len(skipped)}/{len(tickers)} tickers failed to fetch.")

    if not hits:
        print("No new Wyckoff signals today.")
        return

    for sym, signals in hits:
        for s in signals:
            print(f"{sym}: {s}")

    tickers_str = ", ".join(sym for sym, _ in hits[:8])
    more = f" +{len(hits) - 8} more" if len(hits) > 8 else ""
    print()
    print(f"SUMMARY: {len(hits)} ticker(s) flagged for review -- {tickers_str}{more}")

    header = (f"Wyckoff top-50 scan: {len(hits)} ticker(s) flagged for REVIEW. "
              "Discretionary review triggers, NOT validated edges -- backtesting shows none "
              "beat naive swing-trading. Apply your own judgment before any entry.")
    notify.notify_signals(header, hits)


if __name__ == "__main__":
    main()
