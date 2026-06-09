"""Offline unit tests for Stage D — TrendFollowingAnalysis and routing.

All tests use hand-crafted synthetic price series; no network calls are made.

Coverage:
  * Golden cross detection → signal BUY
  * Death cross detection  → signal SELL
  * No cross / sideways data → signal HOLD / neutral
  * Insufficient data (< 210 candles) → neutral + error in result
  * Status mapping: BUY → bullish, SELL → bearish, HOLD → neutral
  * Strength mapping: recent cross → Strong, old cross → Medium, HOLD → Weak
  * _find_last_cross: correct type/candles_ago/price from controlled SMAs
  * _determine_signal: all branches
  * _compute_strength: all branches
  * get_analyzer_for_primary: correct instance per primary_strategy string
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from analysis.trend_following import (
    SMA_FAST,
    SMA_SLOW,
    TrendFollowingAnalysis,
    _compute_strength,
    _determine_signal,
    _find_last_cross,
)
from ui.pages.dashboard import get_analyzer_for_primary


# ---------------------------------------------------------------------------
# Synthetic data helpers
# ---------------------------------------------------------------------------

def _make_df(prices: list[float] | np.ndarray) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of closing prices."""
    p = list(prices)
    return pd.DataFrame({
        "Open":   p,
        "High":   [v * 1.005 for v in p],
        "Low":    [v * 0.995 for v in p],
        "Close":  p,
        "Volume": [1_000_000] * len(p),
    })


def _make_golden_cross_df(n_total: int = 310) -> pd.DataFrame:
    """Price series that produces a golden cross (fast SMA crosses above slow).

    Phase 1 (200 bars): declining 200 → 100 (puts fast SMA below slow SMA).
    Phase 2 (remaining bars): sharply rising 100 → 400 (fast SMA overtakes slow).
    """
    decline = np.linspace(200, 100, 200)
    rise = np.linspace(100, 400, n_total - 200)
    return _make_df(np.concatenate([decline, rise]))


def _make_death_cross_df(n_total: int = 310) -> pd.DataFrame:
    """Price series that produces a death cross (fast SMA crosses below slow).

    Phase 1 (200 bars): rising 100 → 200 (puts fast SMA above slow SMA).
    Phase 2 (remaining bars): sharply falling 200 → 50 (fast SMA drops below slow).
    """
    rise = np.linspace(100, 200, 200)
    fall = np.linspace(200, 50, n_total - 200)
    return _make_df(np.concatenate([rise, fall]))


def _make_flat_df(n: int = 250) -> pd.DataFrame:
    """Constant prices → both SMAs equal → no cross / SIDEWAYS trend."""
    return _make_df([100.0] * n)


def _make_short_df(n: int = 100) -> pd.DataFrame:
    """Fewer candles than _MIN_CANDLES (210) → insufficient data."""
    return _make_df([100.0] * n)


# ---------------------------------------------------------------------------
# Insufficient data
# ---------------------------------------------------------------------------

def test_insufficient_data_returns_neutral_hold() -> None:
    """< 210 candles must give HOLD / neutral / Weak — never raise."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("TEST", _make_short_df(100))

    assert result["signal"] == "HOLD"
    assert result["status"] == "neutral"
    assert result["strength"] == "Weak"
    assert "error" in result
    assert "insufficient" in result["error"].lower()


def test_insufficient_data_summary_mentions_sma() -> None:
    """The summary must mention the SMA period so the user knows what's needed."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("TEST", _make_short_df(50))
    assert str(SMA_SLOW) in result.get("summary", "")


def test_empty_dataframe_returns_neutral() -> None:
    """An empty DataFrame must never raise."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("TEST", pd.DataFrame())
    assert result["signal"] == "HOLD"
    assert result["status"] == "neutral"


# ---------------------------------------------------------------------------
# Golden cross → BUY / bullish
# ---------------------------------------------------------------------------

def test_golden_cross_data_returns_buy_signal() -> None:
    """A price series that creates a golden cross must yield signal == 'BUY'."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("GOLDEN", _make_golden_cross_df())
    assert result["signal"] == "BUY", (
        f"Expected BUY; got {result['signal']}. "
        f"sma_fast={result.get('sma_fast_now'):.2f}, "
        f"sma_slow={result.get('sma_slow_now'):.2f}, "
        f"trend={result.get('trend')}"
    )


def test_golden_cross_data_returns_bullish_status() -> None:
    """BUY signal must map to 'bullish' status."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("GOLDEN", _make_golden_cross_df())
    assert result["status"] == "bullish"


def test_golden_cross_detected_in_last_cross() -> None:
    """last_cross["type"] must be 'golden' for a golden-cross price series."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("GOLDEN", _make_golden_cross_df())
    assert result["last_cross"]["type"] == "golden"
    assert result["last_cross"]["candles_ago"] is not None
    assert isinstance(result["last_cross"]["candles_ago"], int)


def test_golden_cross_strategy_field() -> None:
    """Result must carry strategy == 'Trend Following'."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("GOLDEN", _make_golden_cross_df())
    assert result["strategy"] == "Trend Following"


# ---------------------------------------------------------------------------
# Death cross → SELL / bearish
# ---------------------------------------------------------------------------

def test_death_cross_data_returns_sell_signal() -> None:
    """A price series that creates a death cross must yield signal == 'SELL'."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("DEATH", _make_death_cross_df())
    assert result["signal"] == "SELL", (
        f"Expected SELL; got {result['signal']}. "
        f"sma_fast={result.get('sma_fast_now'):.2f}, "
        f"sma_slow={result.get('sma_slow_now'):.2f}, "
        f"trend={result.get('trend')}"
    )


def test_death_cross_data_returns_bearish_status() -> None:
    """SELL signal must map to 'bearish' status."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("DEATH", _make_death_cross_df())
    assert result["status"] == "bearish"


def test_death_cross_detected_in_last_cross() -> None:
    """last_cross["type"] must be 'death' for a death-cross price series."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("DEATH", _make_death_cross_df())
    assert result["last_cross"]["type"] == "death"


# ---------------------------------------------------------------------------
# No cross / flat data → HOLD / neutral
# ---------------------------------------------------------------------------

def test_flat_prices_returns_hold() -> None:
    """Constant prices → both SMAs equal → HOLD (neither cross fires)."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("FLAT", _make_flat_df(250))
    assert result["signal"] == "HOLD"
    assert result["status"] == "neutral"


def test_no_cross_detected_for_flat_data() -> None:
    """No cross should be detected when both SMAs are equal."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("FLAT", _make_flat_df(250))
    assert result["last_cross"]["type"] is None


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

def test_status_buy_maps_to_bullish() -> None:
    """_determine_signal BUY → status 'bullish'."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("GOLDEN", _make_golden_cross_df())
    if result["signal"] == "BUY":
        assert result["status"] == "bullish"


def test_status_sell_maps_to_bearish() -> None:
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("DEATH", _make_death_cross_df())
    if result["signal"] == "SELL":
        assert result["status"] == "bearish"


def test_status_hold_maps_to_neutral() -> None:
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("FLAT", _make_flat_df(250))
    if result["signal"] == "HOLD":
        assert result["status"] == "neutral"


# ---------------------------------------------------------------------------
# Strength mapping
# ---------------------------------------------------------------------------

def test_hold_signal_gives_weak_strength() -> None:
    """HOLD → Weak — no conviction when neither trend side is dominant."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("FLAT", _make_flat_df(250))
    assert result["strength"] == "Weak"


def test_result_has_sma_values() -> None:
    """Sufficient data → sma_fast_now and sma_slow_now must be finite floats."""
    analyser = TrendFollowingAnalysis()
    result = analyser.analyse("GOLDEN", _make_golden_cross_df())
    assert result.get("sma_fast_now") is not None
    assert result.get("sma_slow_now") is not None
    assert isinstance(result["sma_fast_now"], float)
    assert isinstance(result["sma_slow_now"], float)


# ---------------------------------------------------------------------------
# _find_last_cross — unit tests on the pure helper
# ---------------------------------------------------------------------------

def _make_controlled_series(n: int, transition_at: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Build fast/slow/close series with a sign flip at *transition_at*.

    Before transition_at: fast < slow (death state).
    After  transition_at: fast > slow (golden state → "golden" cross).
    """
    idx = pd.RangeIndex(n)
    fast_vals = [90.0] * transition_at + [110.0] * (n - transition_at)
    slow_vals = [100.0] * n
    close_vals = [100.0] * n
    return (
        pd.Series(fast_vals, index=idx),
        pd.Series(slow_vals, index=idx),
        pd.Series(close_vals, index=idx),
    )


def test_find_last_cross_golden_type() -> None:
    """Controlled series: fast crosses above slow → type must be 'golden'."""
    fast, slow, close = _make_controlled_series(50, 25)
    result = _find_last_cross(fast, slow, close)
    assert result["type"] == "golden"


def test_find_last_cross_golden_candles_ago() -> None:
    """Cross happens at index 25 in a 50-element series → candles_ago = 24."""
    fast, slow, close = _make_controlled_series(50, 25)
    result = _find_last_cross(fast, slow, close)
    # The flip is at position 25 (0-indexed); distance from last bar = 50 - 1 - 25 = 24
    assert result["candles_ago"] == 24


def test_find_last_cross_death_type() -> None:
    """fast crosses below slow → type must be 'death'."""
    n, t = 50, 25
    idx = pd.RangeIndex(n)
    fast = pd.Series([110.0] * t + [90.0] * (n - t), index=idx)
    slow = pd.Series([100.0] * n, index=idx)
    close = pd.Series([100.0] * n, index=idx)
    result = _find_last_cross(fast, slow, close)
    assert result["type"] == "death"


def test_find_last_cross_no_cross_returns_none() -> None:
    """When fast is always above slow, no cross should be detected."""
    n = 50
    idx = pd.RangeIndex(n)
    fast = pd.Series([110.0] * n, index=idx)
    slow = pd.Series([100.0] * n, index=idx)
    close = pd.Series([105.0] * n, index=idx)
    result = _find_last_cross(fast, slow, close)
    assert result["type"] is None
    assert result["candles_ago"] is None


# ---------------------------------------------------------------------------
# _determine_signal — pure function unit tests
# ---------------------------------------------------------------------------

def test_determine_signal_buy() -> None:
    assert _determine_signal(110.0, 100.0, "UP") == "BUY"


def test_determine_signal_sell() -> None:
    assert _determine_signal(90.0, 100.0, "DOWN") == "SELL"


def test_determine_signal_hold_sideways() -> None:
    assert _determine_signal(110.0, 100.0, "SIDEWAYS") == "HOLD"


def test_determine_signal_hold_conflicting_fast_above_but_down_trend() -> None:
    """Fast above slow but trend is DOWN → conflicting signals → HOLD."""
    assert _determine_signal(110.0, 100.0, "DOWN") == "HOLD"


def test_determine_signal_hold_conflicting_fast_below_but_up_trend() -> None:
    """Fast below slow but trend is UP → conflicting signals → HOLD."""
    assert _determine_signal(90.0, 100.0, "UP") == "HOLD"


# ---------------------------------------------------------------------------
# _compute_strength — pure function unit tests
# ---------------------------------------------------------------------------

def test_compute_strength_hold_is_weak() -> None:
    cross = {"type": "golden", "candles_ago": 5, "price": 100.0}
    assert _compute_strength("HOLD", cross) == "Weak"


def test_compute_strength_recent_cross_is_strong() -> None:
    cross = {"type": "golden", "candles_ago": 10, "price": 100.0}
    assert _compute_strength("BUY", cross) == "Strong"


def test_compute_strength_old_cross_is_medium() -> None:
    cross = {"type": "golden", "candles_ago": 100, "price": 100.0}
    assert _compute_strength("BUY", cross) == "Medium"


def test_compute_strength_no_cross_is_medium() -> None:
    cross = {"type": None, "candles_ago": None, "price": None}
    assert _compute_strength("BUY", cross) == "Medium"


# ---------------------------------------------------------------------------
# get_analyzer_for_primary routing
# ---------------------------------------------------------------------------

def test_get_analyzer_demand_supply_returns_demand_supply_instance() -> None:
    from analysis.demand_supply import DemandSupplyAnalysis
    analyser = get_analyzer_for_primary("Demand/Supply Zones")
    assert isinstance(analyser, DemandSupplyAnalysis)


def test_get_analyzer_trend_following_returns_tf_instance() -> None:
    analyser = get_analyzer_for_primary("Trend Following (SMA50/EMA20)")
    assert isinstance(analyser, TrendFollowingAnalysis)


def test_get_analyzer_unknown_falls_back_to_demand_supply() -> None:
    from analysis.demand_supply import DemandSupplyAnalysis
    analyser = get_analyzer_for_primary("Some Future Strategy Not Yet Built")
    assert isinstance(analyser, DemandSupplyAnalysis)


def test_get_analyzer_for_all_primary_strategies_returns_base_analysis() -> None:
    """Every string in PRIMARY_STRATEGIES must produce a BaseAnalysis subclass."""
    from analysis.base import BaseAnalysis
    from config.trading_config import PRIMARY_STRATEGIES
    for ps in PRIMARY_STRATEGIES:
        analyser = get_analyzer_for_primary(ps)
        assert isinstance(analyser, BaseAnalysis), (
            f"get_analyzer_for_primary({ps!r}) did not return a BaseAnalysis instance"
        )
