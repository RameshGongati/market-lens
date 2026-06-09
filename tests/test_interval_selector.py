"""Offline unit tests for the candle-interval selector (data layer).

All tests inject fake fetch functions — no network calls are made.

Coverage:
  * INTERVAL_OPTIONS has the correct interval/period/fetch_interval/resample
    for each label
  * resample_to_75m aggregates 15m OHLCV correctly (open=first, high=max,
    low=min, close=last, volume=sum)
  * default_interval_label maps trading types to the correct label
  * fetch_by_interval routes to the correct yfinance period/interval
  * fetch_by_interval falls back to Daily when intraday data is empty/short
  * fetch_by_interval returns (None, meta) when all fetches return empty
  * 75m label fetches 15m and resamples (fetch_fn is called with "15m")
  * Recompute path: the correct analyser produces a result dict for any df
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from config.trading_config import TRADING_TYPES
from data.manager import (
    INTERVAL_OPTIONS,
    _is_intraday,
    default_interval_label,
    fetch_by_interval,
    resample_to_75m,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_15m_df(n_bars: int = 40) -> pd.DataFrame:
    """Return a 15-minute OHLCV DataFrame with a DatetimeIndex.

    Starts at midnight so bars align with pandas' 75-minute resample bins
    (which anchor at 00:00, 01:15, 02:30 …).  Five bars (00:00, 00:15,
    00:30, 00:45, 01:00) all fall inside the 00:00–01:15 bin → exactly
    one 75m candle, as expected.  Forty bars span exactly eight complete
    bins (40 × 15 min / 75 min = 8).
    """
    idx = pd.date_range("2024-01-02 00:00", periods=n_bars, freq="15min")
    closes = np.linspace(100.0, 120.0, n_bars)
    return pd.DataFrame(
        {
            "Open":   closes - 0.5,
            "High":   closes + 1.0,
            "Low":    closes - 1.0,
            "Close":  closes,
            "Volume": [1_000] * n_bars,
        },
        index=idx,
    )


def _make_good_daily_df(n: int = 25) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    closes = [100.0 + i for i in range(n)]
    return pd.DataFrame(
        {
            "Open": closes, "High": [c + 1 for c in closes],
            "Low": [c - 1 for c in closes], "Close": closes,
            "Volume": [1_000_000] * n,
        },
        index=idx,
    )


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def _fake_empty(_sym: str, _period: str, _interval: str) -> pd.DataFrame:
    return _empty_df()


def _fake_good(_sym: str, _period: str, _interval: str) -> pd.DataFrame:
    return _make_good_daily_df(25)


def _fake_intraday_unavailable(
    _sym: str, _period: str, interval: str
) -> pd.DataFrame:
    """Returns empty for intraday intervals, valid for daily."""
    return _empty_df() if _is_intraday(interval) else _make_good_daily_df(25)


# ---------------------------------------------------------------------------
# INTERVAL_OPTIONS structure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", list(INTERVAL_OPTIONS.keys()))
def test_interval_options_has_required_keys(label: str) -> None:
    spec = INTERVAL_OPTIONS[label]
    assert "interval" in spec
    assert "period" in spec
    assert "fetch_interval" in spec
    assert "resample" in spec


def test_daily_spec() -> None:
    s = INTERVAL_OPTIONS["Daily"]
    assert s["interval"] == "1d"
    assert s["period"] == "1y"
    assert s["fetch_interval"] == "1d"
    assert s["resample"] is False


def test_weekly_spec() -> None:
    s = INTERVAL_OPTIONS["Weekly"]
    assert s["interval"] == "1wk"
    assert s["period"] == "5y"
    assert s["resample"] is False


def test_monthly_spec() -> None:
    s = INTERVAL_OPTIONS["Monthly"]
    assert s["interval"] == "1mo"
    assert s["period"] == "10y"
    assert s["resample"] is False


def test_15m_spec() -> None:
    s = INTERVAL_OPTIONS["15m"]
    assert s["interval"] == "15m"
    assert s["period"] == "60d"
    assert s["fetch_interval"] == "15m"
    assert s["resample"] is False


def test_75m_spec_uses_15m_fetch_and_resample() -> None:
    """75m label must fetch 15m data and resample (yfinance has no 75m)."""
    s = INTERVAL_OPTIONS["75m"]
    assert s["fetch_interval"] == "15m"
    assert s["resample"] is True
    assert s["period"] == "60d"


# ---------------------------------------------------------------------------
# resample_to_75m
# ---------------------------------------------------------------------------

def test_resample_to_75m_aggregates_five_15m_bars() -> None:
    """Five consecutive 15-minute bars should collapse into one 75-minute bar."""
    df = _make_15m_df(5)  # exactly one 75m bar
    result = resample_to_75m(df)
    assert len(result) == 1


def test_resample_to_75m_open_is_first() -> None:
    df = _make_15m_df(5)
    result = resample_to_75m(df)
    assert result["Open"].iloc[0] == pytest.approx(df["Open"].iloc[0])


def test_resample_to_75m_high_is_max() -> None:
    df = _make_15m_df(5)
    result = resample_to_75m(df)
    assert result["High"].iloc[0] == pytest.approx(df["High"].max())


def test_resample_to_75m_low_is_min() -> None:
    df = _make_15m_df(5)
    result = resample_to_75m(df)
    assert result["Low"].iloc[0] == pytest.approx(df["Low"].min())


def test_resample_to_75m_close_is_last() -> None:
    df = _make_15m_df(5)
    result = resample_to_75m(df)
    assert result["Close"].iloc[0] == pytest.approx(df["Close"].iloc[-1])


def test_resample_to_75m_volume_is_sum() -> None:
    df = _make_15m_df(5)
    result = resample_to_75m(df)
    assert result["Volume"].iloc[0] == pytest.approx(df["Volume"].sum())


def test_resample_to_75m_multiple_bars() -> None:
    """40 × 15m bars aligned to midnight → exactly 8 × 75m bars (40 / 5 = 8)."""
    df = _make_15m_df(40)
    result = resample_to_75m(df)
    assert len(result) == 8


def test_resample_to_75m_empty_returns_empty() -> None:
    result = resample_to_75m(_empty_df())
    assert result.empty


# ---------------------------------------------------------------------------
# default_interval_label
# ---------------------------------------------------------------------------

def test_default_interval_label_long_term_is_weekly() -> None:
    assert default_interval_label("Long-term Investment") == "Weekly"


def test_default_interval_label_intraday_is_15m() -> None:
    assert default_interval_label("Intraday Trading") == "15m"


def test_default_interval_label_short_term_is_daily() -> None:
    assert default_interval_label("Short-term Trading") == "Daily"


def test_default_interval_label_options_is_daily() -> None:
    assert default_interval_label("Options Trading") == "Daily"


@pytest.mark.parametrize("trading_type", TRADING_TYPES)
def test_default_interval_label_returns_valid_label(trading_type: str) -> None:
    """Every trading type must map to a key in INTERVAL_OPTIONS."""
    label = default_interval_label(trading_type)
    assert label in INTERVAL_OPTIONS, (
        f"default_interval_label({trading_type!r}) returned {label!r} "
        f"which is not in INTERVAL_OPTIONS"
    )


# ---------------------------------------------------------------------------
# fetch_by_interval — routing
# ---------------------------------------------------------------------------

def test_fetch_by_interval_daily_calls_correct_interval() -> None:
    call_log: list[str] = []

    def _log(_sym: str, _period: str, interval: str) -> pd.DataFrame:
        call_log.append(interval)
        return _make_good_daily_df(25)

    df, meta = fetch_by_interval("TEST.NS", "Daily", fetch_fn=_log)
    assert call_log == ["1d"]
    assert meta["requested_interval"] == "1d"
    assert df is not None


def test_fetch_by_interval_weekly_calls_correct_interval() -> None:
    call_log: list[str] = []

    def _log(_sym: str, _period: str, interval: str) -> pd.DataFrame:
        call_log.append(interval)
        return _make_good_daily_df(25)

    fetch_by_interval("TEST.NS", "Weekly", fetch_fn=_log)
    assert call_log == ["1wk"]


def test_fetch_by_interval_15m_calls_15m() -> None:
    call_log: list[str] = []

    def _log(_sym: str, _period: str, interval: str) -> pd.DataFrame:
        call_log.append(interval)
        return _make_good_daily_df(25)

    fetch_by_interval("TEST.NS", "15m", fetch_fn=_log)
    assert call_log[0] == "15m"


def test_fetch_by_interval_75m_fetches_15m() -> None:
    """75m label must call the fetch_fn with interval='15m' (then resample)."""
    call_log: list[str] = []

    def _log(_sym: str, _period: str, interval: str) -> pd.DataFrame:
        call_log.append(interval)
        return _make_15m_df(40)

    fetch_by_interval("TEST.NS", "75m", fetch_fn=_log)
    assert call_log[0] == "15m", (
        f"75m label should fetch 15m data; fetch_fn was called with {call_log}"
    )


def test_fetch_by_interval_75m_result_is_resampled() -> None:
    """Result for '75m' must have fewer rows than the raw 15m source.

    Uses 100 bars (100 × 15m → 20 × 75m after resampling) so the resampled
    count stays >= _MIN_SUFFICIENT_ROWS (20) and the intraday fallback is NOT
    triggered — letting us verify the resampling was actually applied.
    """
    raw_15m = _make_15m_df(100)  # 100 × 15m → 20 × 75m bars (exactly sufficient)

    def _always_15m(_sym: str, _period: str, _interval: str) -> pd.DataFrame:
        return raw_15m

    df, meta = fetch_by_interval("TEST.NS", "75m", fetch_fn=_always_15m)
    assert df is not None
    assert len(df) < len(raw_15m), (
        f"Resampled 75m df has {len(df)} rows; expected < {len(raw_15m)}"
    )
    assert meta["fell_back"] is False


def test_fetch_by_interval_unknown_label_defaults_to_daily() -> None:
    """An unknown label must not crash — defaults to Daily spec."""
    call_log: list[str] = []

    def _log(_sym: str, _period: str, interval: str) -> pd.DataFrame:
        call_log.append(interval)
        return _make_good_daily_df(25)

    df, meta = fetch_by_interval("TEST.NS", "UnknownLabel", fetch_fn=_log)
    assert call_log[0] == "1d"


# ---------------------------------------------------------------------------
# fetch_by_interval — intraday fallback
# ---------------------------------------------------------------------------

def test_15m_falls_back_to_daily_when_empty() -> None:
    """When the 15m fetch returns empty, must retry with 1d and fell_back=True."""
    df, meta = fetch_by_interval(
        "TEST.NS", "15m", fetch_fn=_fake_intraday_unavailable
    )
    assert df is not None and len(df) >= 20
    assert meta["fell_back"] is True
    assert meta["used_interval"] == "1d"
    assert "unavailable" in meta["message"].lower()


def test_75m_falls_back_to_daily_when_15m_empty() -> None:
    """75m also falls back when the underlying 15m fetch is empty."""
    df, meta = fetch_by_interval(
        "TEST.NS", "75m", fetch_fn=_fake_intraday_unavailable
    )
    assert meta["fell_back"] is True
    assert meta["used_interval"] == "1d"


def test_daily_no_fallback_when_data_available() -> None:
    """Daily label must not trigger intraday fallback — it is not intraday."""
    call_count = [0]

    def _count_calls(_sym: str, _period: str, _interval: str) -> pd.DataFrame:
        call_count[0] += 1
        return _make_good_daily_df(25)

    df, meta = fetch_by_interval("TEST.NS", "Daily", fetch_fn=_count_calls)
    assert meta["fell_back"] is False
    assert call_count[0] == 1  # only one fetch, no fallback retry


def test_returns_none_when_all_fetches_fail() -> None:
    """When both primary and fallback return empty, return (None, meta)."""
    df, meta = fetch_by_interval("GHOST.NS", "15m", fetch_fn=_fake_empty)
    assert df is None
    assert meta["message"]  # must have a descriptive message


# ---------------------------------------------------------------------------
# Recompute path smoke-test
# ---------------------------------------------------------------------------

def test_recompute_demand_supply_produces_result_dict() -> None:
    """Calling DemandSupplyAnalysis.analyse on a new df returns a valid dict."""
    from analysis.demand_supply import DemandSupplyAnalysis
    # Need enough candles for zone detection
    n = 100
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    closes = np.linspace(100, 200, n)
    df = pd.DataFrame(
        {"Open": closes, "High": closes + 1, "Low": closes - 1,
         "Close": closes, "Volume": [1_000_000] * n},
        index=idx,
    )
    result = DemandSupplyAnalysis().analyse("RELIANCE", df)
    assert "status" in result
    assert "summary" in result
    assert result.get("status") in ("bullish", "bearish", "neutral")


def test_recompute_trend_following_produces_result_dict() -> None:
    """Calling TrendFollowingAnalysis.analyse on a 250-row df returns a valid dict."""
    from analysis.trend_following import TrendFollowingAnalysis
    n = 250
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    closes = [100.0 + i * 0.1 for i in range(n)]
    df = pd.DataFrame(
        {"Open": closes, "High": [c + 0.5 for c in closes],
         "Low": [c - 0.5 for c in closes], "Close": closes,
         "Volume": [500_000] * n},
        index=idx,
    )
    result = TrendFollowingAnalysis().analyse("INFY", df)
    assert result.get("strategy") == "Trend Following"
    assert result.get("signal") in ("BUY", "SELL", "HOLD")
