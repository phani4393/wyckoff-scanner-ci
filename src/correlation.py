"""
Correlation / portfolio-heat monitor -- the concentration X-ray.

Your watchlist is almost entirely tech/AI (NVDA, AMD, AVGO, PLTR, CRWD,
SMCI, MRVL, ...). When you hold several long-call positions across those,
you do NOT have several diversified trades -- you have ONE leveraged bet on
the Nasdaq wearing multiple hats. This tool measures that so it stops being
invisible:

  - pairwise return correlation among your open positions (from daily bars)
  - each position's correlation to SPY (systematic / "it's just beta" check)
  - "effective number of bets" -- how many truly independent positions your
    book really represents
  - portfolio heat -- total premium at risk (a long option's max loss is its
    premium), optionally as % of account

Reads open positions from trades.csv (the journal), or pass tickers directly.

Usage:
  python correlation.py                          # analyze current open trades
  python correlation.py --tickers NVDA,AMD,AVGO  # ad-hoc set
  python correlation.py --add PLTR               # what-if: does adding PLTR
                                                  # diversify, or pile on?
  python correlation.py --account 50000          # show heat as % of account
"""

import argparse
import csv
import math
import statistics
from pathlib import Path

import wyckoff_common as c

JOURNAL = Path(__file__).resolve().parent.parent / "trades.csv"
CORR_WINDOW = 90       # trading days of returns for correlation
HIGH_CORR = 0.70       # avg pairwise correlation above this = "effectively one bet"


def load_open_positions():
    if not JOURNAL.exists():
        return []
    out = []
    with open(JOURNAL, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("status") != "open":
                continue
            try:
                premium = float(r["entry_opt_price"]) * 100 * float(r["contracts"] or 1)
            except (ValueError, KeyError):
                premium = None
            out.append({"ticker": r["ticker"].upper(), "direction": r.get("direction", ""),
                         "premium_at_risk": premium})
    return out


def _returns(bars, window):
    closes = [b["close"] for b in bars[-(window + 1):]]
    return {bars[-(window + 1) + i]["date"]: math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0}


def _corr(a, b):
    common = sorted(set(a) & set(b))
    if len(common) < 20:
        return None
    xa = [a[d] for d in common]
    xb = [b[d] for d in common]
    try:
        return statistics.correlation(xa, xb)
    except (statistics.StatisticsError, ValueError):
        return None


def build_return_series(tickers, api_key):
    series = {}
    for t in tickers:
        bars = c.fetch_bars(t, api_key)
        if bars and len(bars) > CORR_WINDOW:
            series[t] = _returns(bars, CORR_WINDOW)
    return series


def analyze(tickers, api_key, account=None, positions=None):
    tickers = list(dict.fromkeys(tickers))  # dedupe, keep order
    lines = []
    series = build_return_series(tickers, api_key)
    spy = c.fetch_bars(c.BENCHMARK, api_key)
    spy_ret = _returns(spy, CORR_WINDOW) if spy else {}

    have = [t for t in tickers if t in series]
    if len(have) < 1:
        return "No usable price data for the given tickers."

    # pairwise correlations
    pairs = []
    for i in range(len(have)):
        for j in range(i + 1, len(have)):
            cval = _corr(series[have[i]], series[have[j]])
            if cval is not None:
                pairs.append((have[i], have[j], cval))
    avg_pair = statistics.mean(p[2] for p in pairs) if pairs else None

    lines.append(f"PORTFOLIO CONCENTRATION -- {len(have)} position(s): {', '.join(have)}")
    if avg_pair is not None:
        # Effective number of independent bets ~ N / (1 + (N-1)*avg_corr)
        n = len(have)
        eff = n / (1 + (n - 1) * max(avg_pair, 0)) if n > 1 else 1
        lines.append(f"  Avg pairwise correlation: {avg_pair:.2f}  "
                     f"-> effective independent bets: {eff:.1f} of {n}")
        if avg_pair >= HIGH_CORR:
            lines.append(f"  ** CONCENTRATION WARNING: at {avg_pair:.2f} avg correlation these move together -- "
                         f"this is ~{eff:.1f} real bet(s), not {n}. A tech selloff hits all at once. Size accordingly.")
    # beta-to-SPY
    if spy_ret:
        betas = [(t, _corr(series[t], spy_ret)) for t in have]
        betas = [(t, b) for t, b in betas if b is not None]
        if betas:
            lines.append("  Correlation to SPY (systematic exposure):")
            for t, b in sorted(betas, key=lambda x: -x[1]):
                lines.append(f"    {t:6} {b:+.2f}" + ("  (basically trades as the market)" if b >= 0.8 else ""))

    # most-correlated pair callout
    if pairs:
        hi = max(pairs, key=lambda p: p[2])
        lines.append(f"  Most-correlated pair: {hi[0]}/{hi[1]} = {hi[2]:.2f}")

    # portfolio heat
    if positions:
        prem = [p["premium_at_risk"] for p in positions if p.get("premium_at_risk")]
        if prem:
            total = sum(prem)
            lines.append(f"\n  Portfolio heat (total premium at risk): ${total:,.0f}")
            if account:
                lines.append(f"    = {total / account * 100:.1f}% of ${account:,.0f} account"
                             + ("  ** high -- >20% of account in premium that can all expire worthless"
                                if total / account > 0.20 else ""))
    return "\n".join(lines)


def what_if(current, candidate, api_key):
    cand = candidate.upper()
    series = build_return_series(current + [cand], api_key)
    if cand not in series:
        return f"Could not fetch {cand}."
    corrs = [(t, _corr(series[cand], series[t])) for t in current if t in series]
    corrs = [(t, v) for t, v in corrs if v is not None]
    if not corrs:
        return f"{cand}: no overlap to compare."
    avg = statistics.mean(v for _, v in corrs)
    verdict = ("adds little diversification -- more of the same bet" if avg >= HIGH_CORR
               else "adds some diversification" if avg >= 0.4 else "genuinely diversifying vs your book")
    out = [f"WHAT-IF: adding {cand} to [{', '.join(current)}]",
           f"  Avg correlation to your open book: {avg:.2f} -> {verdict}"]
    for t, v in sorted(corrs, key=lambda x: -x[1]):
        out.append(f"    vs {t:6} {v:+.2f}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Correlation / portfolio-heat monitor")
    ap.add_argument("--tickers", help="comma-separated tickers (default: open trades from journal)")
    ap.add_argument("--add", help="what-if: correlation of adding this ticker to your book")
    ap.add_argument("--account", type=float, default=None, help="account size, to show heat as %%")
    a = ap.parse_args()
    key = c.load_api_key()

    positions = load_open_positions()
    if a.tickers:
        tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
        positions = None
    else:
        tickers = [p["ticker"] for p in positions]

    if not tickers:
        raise SystemExit("No open trades in the journal and no --tickers given. "
                          "Log trades or pass --tickers NVDA,AMD,...")

    if a.add:
        print(what_if(tickers, a.add, key))
    else:
        print(analyze(tickers, key, account=a.account, positions=positions))


if __name__ == "__main__":
    main()
