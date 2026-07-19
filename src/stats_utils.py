"""
Cluster bootstrap confidence intervals for the backtest's signal-vs-baseline
edge deltas. A point estimate like "-19pp hit rate" doesn't by itself say
whether that gap is real or noise -- and the naive fix (resample individual
signal instances as if each were an independent draw) is ITSELF wrong here:
signal instances from the same ticker share trend/vol/beta and have
overlapping forward-return windows (a spring on day t and another on day
t+3 both look ahead into overlapping price history), so they are correlated,
not independent. Resampling instances directly understates the true CI width
and overstates significance -- exactly the kind of false confidence a
"no wiggle room" review should catch.

The fix is a CLUSTER bootstrap: the ticker, not the individual signal
instance, is the unit of (approximate) independence. Each bootstrap replicate
resamples TICKERS with replacement and pulls each drawn ticker's entire set of
instances along with it, preserving whatever within-ticker correlation exists
in the original data instead of shuffling it away.

Known remaining limitation: this only addresses same-ticker (within-cluster)
correlation, not cross-ticker correlation on the same calendar date (e.g. a
market-wide selloff triggering springs on many tickers at once isn't actually
N independent events). A full fix would need two-way clustering (ticker x
time block); this is ticker-only clustering, which is the dominant and
simpler-to-justify correction but not a complete one.
"""

import numpy as np

N_BOOT = 2000
CI = 0.95
MIN_CLUSTERS = 3  # fewer independent tickers than this and a CI is not meaningful


def _cluster(values, groups):
    """values: list of floats. groups: parallel list of group keys (ticker
    symbols). Returns {group: np.array([values...])}."""
    out = {}
    for v, g in zip(values, groups):
        out.setdefault(g, []).append(v)
    return {g: np.asarray(v, dtype=float) for g, v in out.items()}


def _draw_clusters(rng, cluster_arrays, keys, n_draw):
    if not keys:
        return np.array([])
    idx = rng.integers(0, len(keys), size=n_draw)
    return np.concatenate([cluster_arrays[keys[i]] for i in idx])


def bootstrap_edge_ci(signal_vals, signal_groups, bull_by_ticker, bear_by_ticker,
                       frac_bull, n_boot=N_BOOT, ci=CI, seed=0):
    """signal_vals/signal_groups: this signal's own direction-adjusted values
    (returns or options P&L) for one horizon, and the ticker each value came
    from. bull_by_ticker/bear_by_ticker: {ticker: [values...]} for the
    matched swing baseline's raw values for the same horizon (already
    direction-signed), grouped by ticker. frac_bull: fraction of this
    signal's instances that were bullish, used to blend the baseline pools
    the same way the point-estimate edge does.

    Resamples TICKERS (not individual instances) with replacement each
    iteration -- the cluster bootstrap, appropriate because same-ticker
    instances are correlated (shared trend/vol/beta, overlapping forward-
    return windows), not independent draws.

    Returns None if there aren't enough independent tickers to say anything
    (fewer than MIN_CLUSTERS contributing to either side)."""
    sig_by_g = _cluster(signal_vals, signal_groups)
    bull_arrays = {g: np.asarray(v, dtype=float) for g, v in bull_by_ticker.items()}
    bear_arrays = {g: np.asarray(v, dtype=float) for g, v in bear_by_ticker.items()}

    sig_keys = list(sig_by_g.keys())
    bull_keys = list(bull_arrays.keys())
    bear_keys = list(bear_arrays.keys())
    if len(sig_keys) < MIN_CLUSTERS or len(bull_keys) < MIN_CLUSTERS or len(bear_keys) < MIN_CLUSTERS:
        return None

    rng = np.random.default_rng(seed)
    n_sig_g = len(sig_keys)
    n_bull_g = max(1, round(frac_bull * n_sig_g))
    n_bear_g = max(1, n_sig_g - n_bull_g)

    mean_edges = np.full(n_boot, np.nan)
    hit_edges = np.full(n_boot, np.nan)
    for b in range(n_boot):
        sig_sample = _draw_clusters(rng, sig_by_g, sig_keys, n_sig_g)
        bull_sample = _draw_clusters(rng, bull_arrays, bull_keys, n_bull_g)
        bear_sample = _draw_clusters(rng, bear_arrays, bear_keys, n_bear_g)
        base_sample = np.concatenate([bull_sample, bear_sample])
        if len(sig_sample) == 0 or len(base_sample) == 0:
            continue
        mean_edges[b] = sig_sample.mean() - base_sample.mean()
        hit_edges[b] = (sig_sample > 0).mean() - (base_sample > 0).mean()

    mean_edges = mean_edges[~np.isnan(mean_edges)]
    hit_edges = hit_edges[~np.isnan(hit_edges)]
    if len(mean_edges) < n_boot // 2:
        return None

    def _ci_and_p(edges):
        lo, hi = np.percentile(edges, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
        below = float((edges <= 0).mean())
        above = float((edges >= 0).mean())
        return (float(lo), float(hi)), 2 * min(below, above, 0.5)

    mean_ci, mean_p = _ci_and_p(mean_edges)
    hit_ci, hit_p = _ci_and_p(hit_edges)
    return {
        "mean_edge_ci": mean_ci, "mean_edge_p": mean_p,
        "hit_edge_ci": hit_ci, "hit_edge_p": hit_p,
        "n_signal_clusters": n_sig_g, "n_baseline_clusters": len(bull_keys) + len(bear_keys),
    }
