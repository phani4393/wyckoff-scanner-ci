"""
Renders the live scorecard as a cumulative-return chart -- turns
score_alerts.py's text digest into something you can eyeball for drift over
time, instead of re-reading numbers on every run. Two lines: the scored
alerts' own cumulative direction-adjusted return, and a live, time-matched
swing baseline's cumulative return over the same tickers/period. If the
signal line stays persistently below the baseline line, that's the
BACKTEST_FINDINGS.md verdict replicating live; if it climbs above, that's
the first real evidence live/discretionary signals might differ from the
historical mechanical replay.

Kept as its own module (mirrors wyckoff_charts.py being separate from the
scanners) so score_alerts.py's scoring/statistics logic doesn't have to
import matplotlib.
"""

import datetime as _dt
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # no display available when run headless from a scheduled task
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

CHART_DIR = Path(__file__).resolve().parent.parent / "charts"


def _cumulative(dated_values):
    """dated_values: [(date_str 'YYYY-MM-DD', value), ...], not necessarily
    sorted. Returns chronologically sorted (date, running_total) pairs."""
    rows = sorted(dated_values, key=lambda dv: dv[0])
    out = []
    running = 0.0
    for date, v in rows:
        running += v
        out.append((date, running))
    return out


def render_equity_curve(signal_series, baseline_series, horizon_days, out_dir=None):
    """signal_series / baseline_series: [(date_str, signed_return_fraction), ...].
    Renders a cumulative % return line chart comparing the two and saves a
    PNG. Returns the saved path, or None if signal_series is empty (nothing
    to plot yet)."""
    if not signal_series:
        return None
    out_dir = Path(out_dir) if out_dir else CHART_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    sig_cum = _cumulative(signal_series)
    sig_dates = [_dt.date.fromisoformat(d) for d, _ in sig_cum]
    sig_vals = [v * 100 for _, v in sig_cum]

    fig, ax = plt.subplots(figsize=(10, 5.5))
    ax.plot(sig_dates, sig_vals, marker="o", markersize=3, linewidth=1.6,
            color="#1a5fb4", label=f"Scored alerts, {horizon_days}d (cumulative)")

    if baseline_series:
        base_cum = _cumulative(baseline_series)
        base_dates = [_dt.date.fromisoformat(d) for d, _ in base_cum]
        base_vals = [v * 100 for _, v in base_cum]
        ax.plot(base_dates, base_vals, marker="o", markersize=3, linewidth=1.4,
                linestyle="--", color="#5f6368", label="Live swing baseline (cumulative)")

    ax.axhline(0, color="#bbbbbb", linewidth=0.8, zorder=0)
    ax.set_ylabel("Cumulative direction-adjusted return (%)")
    n = len(signal_series)
    ax.set_title(f"Live alert scorecard -- {horizon_days}d horizon ({n} scored alert{'s' if n != 1 else ''})")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()
    ax.legend(loc="best", fontsize=9)
    ax.grid(alpha=0.25)
    fig.tight_layout()

    out_path = out_dir / f"live_scorecard_{horizon_days}d.png"
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path
