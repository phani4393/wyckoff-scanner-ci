"""
Bootstrap confidence intervals for the backtest's signal-vs-baseline edge
deltas. A point estimate like "-19pp hit rate" doesn't by itself say whether
that gap is real or well within noise given the sample size -- especially for
the thinner signal types (SC/BC have n<350 at some horizons, vs thousands for
spring/upthrust). This resamples both the signal's own observations and a
matched pool of baseline observations (blended by the signal's own bull/bear
mix, same as the point estimate in backtest.summarize()) to get a confidence
interval on the edge itself and a two-sided bootstrap p-value for "is this
edge different from zero."

Vectorized with numpy -- a pure-Python nested loop over ~2000 iterations x
thousands of signal instances x dozens of signal/horizon combinations would
take too long to be practical to run alongside the backtest.
"""

import numpy as np

N_BOOT = 2000
CI = 0.95


def bootstrap_edge_ci(signal_vals, bull_pool, bear_pool, frac_bull, n_boot=N_BOOT, ci=CI, seed=0):
    """signal_vals: this signal's own direction-adjusted values (returns or
    options P&L) for one horizon. bull_pool/bear_pool: the matched swing
    baseline's raw values for the same horizon (already direction-signed).
    frac_bull: fraction of this signal's instances that were bullish, used to
    blend the baseline pools the same way the point-estimate edge does.
    Returns None if there isn't enough data to resample meaningfully."""
    n = len(signal_vals)
    if n < 5 or not bull_pool or not bear_pool:
        return None

    rng = np.random.default_rng(seed)
    sig = np.asarray(signal_vals, dtype=float)
    bull = np.asarray(bull_pool, dtype=float)
    bear = np.asarray(bear_pool, dtype=float)

    n_bull_draw = max(1, round(frac_bull * n))
    n_bear_draw = max(1, n - n_bull_draw)

    sig_boot = rng.choice(sig, size=(n_boot, n), replace=True)
    bull_boot = rng.choice(bull, size=(n_boot, n_bull_draw), replace=True)
    bear_boot = rng.choice(bear, size=(n_boot, n_bear_draw), replace=True)
    base_boot = np.concatenate([bull_boot, bear_boot], axis=1)

    mean_edges = sig_boot.mean(axis=1) - base_boot.mean(axis=1)
    hit_edges = (sig_boot > 0).mean(axis=1) - (base_boot > 0).mean(axis=1)

    def _ci_and_p(edges):
        lo, hi = np.percentile(edges, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
        below = float((edges <= 0).mean())
        above = float((edges >= 0).mean())
        return (float(lo), float(hi)), 2 * min(below, above, 0.5)

    mean_ci, mean_p = _ci_and_p(mean_edges)
    hit_ci, hit_p = _ci_and_p(hit_edges)
    return {"mean_edge_ci": mean_ci, "mean_edge_p": mean_p, "hit_edge_ci": hit_ci, "hit_edge_p": hit_p}
