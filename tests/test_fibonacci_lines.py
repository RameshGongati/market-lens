"""Regression tests for the Fibonacci line price plumbing.

Root cause covered: ``find_recent_swing`` anchored the retracement on the raw
``High.max()`` / ``Low.min()`` of the window with no validity filter. When the
fetched data contained a partial/empty candle (a ``0`` or ``NaN`` bar — common
in the intraday/resampled feeds the interval selector re-fetches), the swing
low collapsed to 0 and every Fibonacci level was dragged toward the bottom of
the chart.

These tests pin the guards now in place:
  * find_recent_swing ignores non-positive / NaN bars
  * calculate_fib_levels returns in-range prices (never ~0) for a valid swing,
    and {} for a degenerate swing
  * the chart helper _add_fibonacci_lines skips 0/NaN/None level prices and
    survives string (round-tripped) ratio keys
  * analyse(use_fibonacci=True) populates finite, in-range fib_levels;
    analyse(use_fibonacci=False) omits the key entirely
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest
from plotly.subplots import make_subplots

from analysis.demand_supply import DemandSupplyAnalysis
from analysis.zone_engine.fibonacci import calculate_fib_levels, find_recent_swing
from ui.components import stock_detail as sd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_df(n: int = 130, lo: float = 190.0, hi: float = 260.0) -> pd.DataFrame:
    """A clean, all-positive OHLCV frame trending from *lo* to *hi*."""
    base = np.linspace(lo, hi, n)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"Open": base, "High": base + 3, "Low": base - 3,
         "Close": base, "Volume": [1_000_000] * n},
        index=idx,
    )


def _fib_y_values(result: dict) -> list[float]:
    """Run the real chart helper and return the y-values of the lines drawn."""
    fig = make_subplots(rows=2, cols=1)
    sd._add_fibonacci_lines(fig, result, _clean_df())
    return [s["y0"] for s in fig.layout.shapes]


# ---------------------------------------------------------------------------
# find_recent_swing — validity guard
# ---------------------------------------------------------------------------

def test_swing_ignores_zero_priced_candle() -> None:
    """A 0.0 Low (partial candle) must NOT become the swing low."""
    df = _clean_df()
    df.iloc[5] = [0, 0, 0, 0, 0]
    swing = find_recent_swing(df)
    assert swing["swing_low"] is not None
    assert swing["swing_low"] > 100, (
        f"swing_low collapsed to {swing['swing_low']} — the zero bar leaked in"
    )


def test_swing_ignores_nan_candle() -> None:
    """A NaN bar must not poison the swing high/low."""
    df = _clean_df()
    df.iloc[10] = [np.nan] * 5
    swing = find_recent_swing(df)
    assert swing["swing_high"] is not None and math.isfinite(swing["swing_high"])
    assert swing["swing_low"] is not None and math.isfinite(swing["swing_low"])


def test_swing_all_invalid_returns_no_swing() -> None:
    """If every bar is non-positive, swing detection returns the empty result."""
    n = 130
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    df = pd.DataFrame(
        {"Open": [0.0] * n, "High": [0.0] * n, "Low": [0.0] * n,
         "Close": [0.0] * n, "Volume": [0] * n},
        index=idx,
    )
    swing = find_recent_swing(df)
    assert swing["swing_high"] is None
    assert swing["swing_low"] is None
    assert swing["direction"] is None


def test_swing_clean_data_unchanged() -> None:
    """On clean data the guard is a no-op — real extremes are returned.

    Uses n=100 (< the 120-candle lookback) so the whole series is scanned and
    the extremes are the very first Low (190-3) and last High (260+3).
    """
    df = _clean_df(n=100, lo=190, hi=260)
    swing = find_recent_swing(df)
    assert swing["swing_high"] == pytest.approx(263.0)   # 260 + 3
    assert swing["swing_low"] == pytest.approx(187.0)    # 190 - 3


# ---------------------------------------------------------------------------
# calculate_fib_levels — in-range prices, never ~0
# ---------------------------------------------------------------------------

def test_fib_levels_within_swing_range() -> None:
    """Every level price must sit between swing_low and swing_high — not ~0."""
    df = _clean_df(lo=190, hi=260)
    levels = calculate_fib_levels(find_recent_swing(df))
    assert len(levels) == 4
    for ratio, price in levels.items():
        assert 187.0 <= price <= 263.0, (
            f"Fib {ratio} priced at {price} — outside the swing's 187-263 range"
        )


def test_fib_levels_with_zero_candle_still_in_range() -> None:
    """The regression case: a zero candle must not drag levels toward 0."""
    df = _clean_df(lo=190, hi=260)
    df.iloc[5] = [0, 0, 0, 0, 0]
    levels = calculate_fib_levels(find_recent_swing(df))
    assert levels, "levels should still be produced"
    assert all(150.0 <= p <= 263.0 for p in levels.values()), (
        f"levels collapsed low: {levels}"
    )


def test_fib_levels_degenerate_swing_returns_empty() -> None:
    """A swing with a None/0 anchor yields no levels (not 0-priced ones)."""
    assert calculate_fib_levels(
        {"swing_high": None, "swing_low": None, "direction": None}
    ) == {}
    assert calculate_fib_levels(
        {"swing_high": 0.0, "swing_low": 0.0, "direction": "up"}
    ) == {}


def test_fib_levels_nan_swing_returns_empty() -> None:
    assert calculate_fib_levels(
        {"swing_high": float("nan"), "swing_low": 100.0, "direction": "up"}
    ) == {}


# ---------------------------------------------------------------------------
# _add_fibonacci_lines — drawing guards
# ---------------------------------------------------------------------------

def test_chart_draws_valid_levels() -> None:
    result = {"fib_levels": {0.382: 240.0, 0.5: 230.0, 0.618: 220.0, 0.786: 210.0}}
    ys = _fib_y_values(result)
    assert sorted(ys) == [210.0, 220.0, 230.0, 240.0]


def test_chart_skips_zero_nan_none_levels() -> None:
    """0 / NaN / None level prices must be skipped, valid ones still drawn."""
    result = {"fib_levels": {
        0.382: 0.0,
        0.5: float("nan"),
        0.618: None,
        0.786: 225.0,
    }}
    ys = _fib_y_values(result)
    assert ys == [225.0], f"expected only the valid 225.0 level, got {ys}"


def test_chart_draws_nothing_when_all_levels_invalid() -> None:
    result = {"fib_levels": {0.382: 0.0, 0.5: float("nan"), 0.618: None}}
    assert _fib_y_values(result) == []


def test_chart_handles_string_ratio_keys() -> None:
    """A JSON round-tripped result (string keys) must still draw correctly."""
    result = {"fib_levels": {0.382: 240.0, 0.618: 220.0}}
    round_tripped = json.loads(json.dumps(result))
    assert all(isinstance(k, str) for k in round_tripped["fib_levels"])
    ys = _fib_y_values(round_tripped)
    assert sorted(ys) == [220.0, 240.0]


def test_chart_missing_fib_levels_draws_nothing() -> None:
    assert _fib_y_values({}) == []
    assert _fib_y_values({"fib_levels": {}}) == []


# ---------------------------------------------------------------------------
# analyse() end-to-end — fib_levels presence and validity
# ---------------------------------------------------------------------------

def test_analyse_with_fibonacci_populates_finite_in_range_levels() -> None:
    df = _clean_df(n=160)
    result = DemandSupplyAnalysis().analyse("TEST", df, use_fibonacci=True)
    fib_levels = result.get("fib_levels")
    assert fib_levels, "use_fibonacci=True must populate fib_levels"
    for price in fib_levels.values():
        assert math.isfinite(price) and price > 0
        assert 180.0 <= price <= 270.0


def test_analyse_without_fibonacci_omits_levels() -> None:
    df = _clean_df(n=160)
    result = DemandSupplyAnalysis().analyse("TEST", df, use_fibonacci=False)
    assert "fib_levels" not in result


def test_analyse_with_zero_candle_levels_stay_in_range() -> None:
    """End-to-end regression: a zero candle in the data must not collapse the
    drawn Fib lines toward 0."""
    df = _clean_df(n=160)
    df.iloc[7] = [0, 0, 0, 0, 0]
    result = DemandSupplyAnalysis().analyse("TEST", df, use_fibonacci=True)
    ys = _fib_y_values(result)
    assert ys, "fib lines should be drawn"
    assert all(150.0 <= y <= 270.0 for y in ys), f"levels collapsed: {ys}"
