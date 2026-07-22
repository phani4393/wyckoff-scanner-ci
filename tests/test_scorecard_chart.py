"""
Tests for scorecard_chart.py -- pure logic (_cumulative) plus a file-creation
smoke test for render_equity_curve. No network calls.

Run with: python -m pytest tests/ -v   (from the repo root)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import scorecard_chart as sc


def test_cumulative_sorts_and_sums():
    # Deliberately out of order -- _cumulative must sort by date first.
    dated = [("2026-01-03", 0.02), ("2026-01-02", 0.01), ("2026-01-05", -0.03)]
    result = sc._cumulative(dated)
    assert [d for d, _ in result] == ["2026-01-02", "2026-01-03", "2026-01-05"]
    vals = [v for _, v in result]
    assert abs(vals[0] - 0.01) < 1e-9
    assert abs(vals[1] - 0.03) < 1e-9
    assert abs(vals[2] - 0.00) < 1e-9


def test_cumulative_empty():
    assert sc._cumulative([]) == []


def test_render_equity_curve_returns_none_when_no_signal():
    assert sc.render_equity_curve([], [("2026-01-02", 0.01)], 10) is None


def test_render_equity_curve_creates_file(tmp_path):
    signal = [("2026-01-02", 0.02), ("2026-01-09", -0.01)]
    baseline = [("2026-01-02", 0.03), ("2026-01-09", 0.01)]
    out_path = sc.render_equity_curve(signal, baseline, 10, out_dir=tmp_path)
    assert out_path is not None
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_render_equity_curve_without_baseline(tmp_path):
    # baseline_series can be empty (no live-swing instances yet on these
    # tickers) -- must still render the signal-only line, not crash.
    signal = [("2026-01-02", 0.02)]
    out_path = sc.render_equity_curve(signal, [], 10, out_dir=tmp_path)
    assert out_path is not None
    assert out_path.exists()
