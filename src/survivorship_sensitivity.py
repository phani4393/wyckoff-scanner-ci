"""
Quantifies how sensitive the backtest's headline "matched baseline" numbers
are to survivorship bias, without needing paid delisted-name data (which we
don't have on Twelve Data's free tier).

Every ticker in this backtest is, by construction, a company that is still
trading today -- so the empirical baseline (e.g. "73% hit rate, +4.7% avg over
10 days on a naive swing entry") never includes a single instance of "the
company went to zero." A true survivorship-free universe would. This can't be
measured directly on free data, but its MAGNITUDE can be bounded with a simple
stress test: blend the observed distribution with a hypothetical fraction of
catastrophic failures, and see how much that erodes the headline number.

This is a sensitivity analysis, not a measurement -- it does not claim to know
the true historical delisting/bankruptcy rate for this universe. It answers
"how much would the headline number have to be wrong for the conclusion to
flip," which is the honest way to bound this kind of bias without the data
needed to correct it directly.
"""

FAILURE_RATES = (0.0, 0.02, 0.05, 0.10, 0.20)
FAILURE_RETURN_PCT = -100.0  # a delisted/bankrupt position assumed to be a total loss


def blended_avg(observed_avg_pct, failure_rate, failure_return_pct=FAILURE_RETURN_PCT):
    """observed_avg_pct: the empirical average return (%) from survivor-only
    data. failure_rate: hypothetical fraction of a true peer universe that
    would have gone to zero over the same holding window (not observed --
    swapped in for the sensitivity check, not measured from this dataset).
    Returns the blended average."""
    return (1 - failure_rate) * observed_avg_pct + failure_rate * failure_return_pct


def sensitivity_table(observed_avg_pct, label):
    print(f"\n{label} (observed avg: {observed_avg_pct:+.2f}%):")
    for f in FAILURE_RATES:
        blended = blended_avg(observed_avg_pct, f)
        print(f"  if {f * 100:4.0f}% of a true peer universe had gone to zero instead: "
              f"blended avg = {blended:+.2f}%")


def main():
    # From the 44-name Core Watchlist backtest (docs/BACKTEST_FINDINGS.md),
    # 10-day matched swing baseline -- the "fair control" every signal is
    # measured against.
    sensitivity_table(4.69, "Swing baseline, stock return, 10d bullish")
    sensitivity_table(38.3, "Swing baseline, options P&L, 10d bullish (leverage amplifies the same fragility)")


if __name__ == "__main__":
    main()
