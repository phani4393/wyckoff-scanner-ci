"""
Wyckoff scanner -- top 50 S&P 500 names by market cap plus a handful of
AI-relevant tickers not already in that top 50 (see Top50 Plus AI.csv).

Python port of the logic already in Weis Wave Volume.pine and
Wyckoff Wheel Zones.pine, run once across every ticker in the list instead
of one symbol at a time on a TradingView chart. Reports only NEW signals
(today, not already true yesterday) so it can be run on a schedule without
repeating the same alert every day a price sits in a zone.

Data source: Twelve Data's time_series endpoint (official, documented,
free-tier API key required). The free tier caps out at 8 API credits/minute
(1 credit = 1 symbol); at ~59 tickers this finishes in well under 10 minutes.

Usage: python wyckoff_scanner.py
Prints one line per ticker with a fresh signal, plus a one-line summary
suitable for a push notification.
"""

import csv
from pathlib import Path

import wyckoff_notify as notify
from wyckoff_common import (
    BENCHMARK,
    LEFT_BARS,
    RIGHT_BARS,
    ZONE_MULT,
    fetch_bars,
    load_api_key,
    pivots,
    wilder_atr,
    build_close_by_date,
)

TICKER_FILE = Path(__file__).with_name("Top50 Plus AI.csv")

# ---- Weis Wave params (match Weis Wave Volume.pine defaults, Percent mode) ----
REVERSAL_PCT = 1.0
FLAG_RATIO = 1.5


def wheel_signals(bars, spy_by_date):
    n = len(bars)
    if n < LEFT_BARS + RIGHT_BARS + 10:
        return None
    res, sup = pivots(bars)
    atr = wilder_atr(bars)

    rs = [None] * n
    for i, b in enumerate(bars):
        spy_close = spy_by_date.get(b["date"])
        rs[i] = (b["close"] / spy_close) if spy_close else None

    def sell_put(j):
        if res[j] is None and sup[j] is None:
            return False
        b = bars[j]
        spring = sup[j] is not None and b["low"] < sup[j] and b["close"] > sup[j]
        near_sup = sup[j] is not None and atr[j] and abs(b["close"] - sup[j]) <= ZONE_MULT * atr[j]
        rs_rising = j >= 5 and rs[j] is not None and rs[j - 5] is not None and rs[j] > rs[j - 5]
        return (spring or near_sup) and rs_rising

    def sell_call(j):
        b = bars[j]
        upthrust = res[j] is not None and b["high"] > res[j] and b["close"] < res[j]
        near_res = res[j] is not None and atr[j] and abs(b["close"] - res[j]) <= ZONE_MULT * atr[j]
        return upthrust or near_res

    last = n - 1
    put_today, put_yday = sell_put(last), sell_put(last - 1)
    call_today, call_yday = sell_call(last), sell_call(last - 1)
    return {
        "sellPutNew": put_today and not put_yday,
        "sellCallNew": call_today and not call_yday,
        "close": bars[last]["close"],
        "res": res[last],
        "sup": sup[last],
    }


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
    spy_by_date = build_close_by_date(spy_bars)

    hits = []
    skipped = []

    for idx, sym in enumerate(tickers, 1):
        bars = fetch_bars(sym, api_key)
        if not bars:
            skipped.append(sym)
        else:
            wheel = wheel_signals(bars, spy_by_date)
            weis = weis_wave_signal(bars)
            signals = []
            if wheel:
                if wheel["sellPutNew"]:
                    signals.append(f"SELL-PUT zone (spring/support + rising RS) @ {wheel['close']:.2f}")
                if wheel["sellCallNew"]:
                    signals.append(f"SELL-CALL zone (upthrust/resistance) @ {wheel['close']:.2f}")
            if weis and weis["newWaveToday"] and weis["flagged"]:
                direction = "up" if weis["direction"] == 1 else "down"
                signals.append(f"Weis Wave exhaustion flag on new {direction} wave")
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
        notify.send_message(f"Wyckoff S&P 500 scan degraded: {len(skipped)}/{len(tickers)} tickers failed to fetch.")

    if not hits:
        print("No new Wyckoff signals today.")
        return

    for sym, signals in hits:
        for s in signals:
            print(f"{sym}: {s}")

    tickers_str = ", ".join(sym for sym, _ in hits[:8])
    more = f" +{len(hits) - 8} more" if len(hits) > 8 else ""
    print()
    print(f"SUMMARY: {len(hits)} new setup(s) -- {tickers_str}{more}")

    notify.notify_signals(f"Wyckoff S&P 500 scan: {len(hits)} new setup(s)", hits)


if __name__ == "__main__":
    main()
