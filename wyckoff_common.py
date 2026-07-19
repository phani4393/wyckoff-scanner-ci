"""
Shared data-fetch and indicator building blocks for the Wyckoff scanners
(wyckoff_scanner.py for the S&P 500 sweep, wyckoff_watchlist_scanner.py for
the deep-scan core watchlist). Keeps one implementation of the Twelve Data
fetch/rate-limit logic and the ATR/pivot math both scripts rely on, so they
can't drift out of sync with each other.
"""

import json
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from pathlib import Path

API_KEY_FILE = Path(__file__).with_name("twelvedata_api_key.txt")
API_KEY_ENV_VAR = "TWELVEDATA_API_KEY"
BENCHMARK = "SPY"

TIME_SERIES_URL = "https://api.twelvedata.com/time_series"
OUTPUT_SIZE = 300  # ~1y of daily bars

# ---- Wheel Zones params (match Wyckoff Wheel Zones.pine defaults) ----
LEFT_BARS = 20
RIGHT_BARS = 5
ATR_LEN = 14
ZONE_MULT = 0.5

RETRY = 2

# ---- Free-tier rate limit: 8 credits/minute. Stay at 7/min for headroom. ----
MAX_CALLS_PER_MINUTE = 7
_call_times = deque()


def load_api_key():
    """CI (GitHub Actions) sets TWELVEDATA_API_KEY as an env var from a repo
    secret; local runs fall back to the plain-text file next to this script."""
    env_key = os.environ.get(API_KEY_ENV_VAR)
    if env_key:
        return env_key.strip()
    if not API_KEY_FILE.exists():
        raise RuntimeError(
            f"No {API_KEY_ENV_VAR} env var set, and {API_KEY_FILE.name} not found next to this script. "
            "Either set the env var, or create the file with your free Twelve Data API key (twelvedata.com) in it."
        )
    key = API_KEY_FILE.read_text(encoding="utf-8").strip()
    if not key:
        raise RuntimeError(f"{API_KEY_FILE.name} is empty -- put your Twelve Data API key in it.")
    return key


def _throttle():
    now = time.monotonic()
    while _call_times and now - _call_times[0] > 60:
        _call_times.popleft()
    if len(_call_times) >= MAX_CALLS_PER_MINUTE:
        wait = 60 - (now - _call_times[0]) + 0.1
        if wait > 0:
            time.sleep(wait)
    _call_times.append(time.monotonic())


def fetch_bars(sym, api_key):
    params = {
        "symbol": sym,
        "interval": "1day",
        "outputsize": OUTPUT_SIZE,
        "order": "ASC",
        "apikey": api_key,
    }
    url = f"{TIME_SERIES_URL}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(RETRY + 1):
        _throttle()
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            if data.get("status") == "error":
                code = data.get("code")
                if code == 429:
                    time.sleep(10)
                    last_err = data.get("message")
                    continue
                return None  # bad symbol / not found -- don't retry
            values = data.get("values")
            if not values:
                return None
            bars = []
            for v in values:
                try:
                    bars.append({
                        "date": v["datetime"][:10],
                        "open": float(v["open"]),
                        "high": float(v["high"]),
                        "low": float(v["low"]),
                        "close": float(v["close"]),
                        "volume": float(v["volume"]) if v.get("volume") else 0,
                    })
                except (KeyError, ValueError, TypeError):
                    continue
            bars.sort(key=lambda b: b["date"])
            return drop_unconfirmed_last_bar(bars)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            last_err = e
            time.sleep(1)
    return None


def drop_unconfirmed_last_bar(bars):
    # Heuristic: if the freshest bar's volume is far below the recent average,
    # the session likely hasn't settled yet (relevant when this runs right at
    # the close) -- use the last fully-formed bar instead.
    if len(bars) < 21:
        return bars
    recent_avg = statistics.mean(b["volume"] for b in bars[-21:-1])
    if recent_avg > 0 and bars[-1]["volume"] < 0.1 * recent_avg:
        return bars[:-1]
    return bars


def wilder_atr(bars, length=ATR_LEN):
    trs = [None]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = [None] * len(bars)
    if len(bars) <= length:
        return atr
    seed = statistics.mean(trs[1:length + 1])
    atr[length] = seed
    for i in range(length + 1, len(bars)):
        atr[i] = (atr[i - 1] * (length - 1) + trs[i]) / length
    return atr


def pivots(bars, left=LEFT_BARS, right=RIGHT_BARS):
    """Returns (res, sup) arrays: last CONFIRMED swing high/low as of each bar,
    mirroring Pine's var res/sup carried forward from ta.pivothigh/pivotlow."""
    n = len(bars)
    res = [None] * n
    sup = [None] * n
    cur_res = None
    cur_sup = None
    for j in range(n):
        k = j - right
        if k >= left and k + right < n:
            window_hi = [bars[i]["high"] for i in range(k - left, k + right + 1)]
            if bars[k]["high"] == max(window_hi):
                cur_res = bars[k]["high"]
            window_lo = [bars[i]["low"] for i in range(k - left, k + right + 1)]
            if bars[k]["low"] == min(window_lo):
                cur_sup = bars[k]["low"]
        res[j] = cur_res
        sup[j] = cur_sup
    return res, sup


def zigzag(bars, left=LEFT_BARS, right=RIGHT_BARS):
    """Ordered list of alternating swing points: [{'idx', 'date', 'price', 'type'}, ...]
    type is 'H' (swing high) or 'L' (swing low). Built from the same confirmed-pivot
    definition as pivots(), but keeps the full sequence instead of just the latest of
    each, and enforces strict alternation (if two same-type pivots occur back to back
    with no opposite pivot between them, keep only the more extreme one)."""
    n = len(bars)
    raw = []
    for k in range(left, n - right):
        window_hi = [bars[i]["high"] for i in range(k - left, k + right + 1)]
        if bars[k]["high"] == max(window_hi):
            raw.append({"idx": k, "date": bars[k]["date"], "price": bars[k]["high"], "type": "H"})
        window_lo = [bars[i]["low"] for i in range(k - left, k + right + 1)]
        if bars[k]["low"] == min(window_lo):
            raw.append({"idx": k, "date": bars[k]["date"], "price": bars[k]["low"], "type": "L"})
    raw.sort(key=lambda p: p["idx"])

    swings = []
    for p in raw:
        if swings and swings[-1]["type"] == p["type"]:
            if (p["type"] == "H" and p["price"] >= swings[-1]["price"]) or \
               (p["type"] == "L" and p["price"] <= swings[-1]["price"]):
                swings[-1] = p
        else:
            swings.append(p)
    return swings


def build_close_by_date(bars):
    return {b["date"]: b["close"] for b in bars}
