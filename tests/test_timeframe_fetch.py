"""Unit tests for Stage C timeframe-aware fetch logic.

All tests are fully offline — they inject a fake ``fetch_fn`` so no network
call is ever made.  The tests cover:

  * ``_is_intraday`` recognises the right intervals
  * Intraday fallback when the primary fetch returns empty / short data
  * No fallback when intraday data IS available
  * No fallback for non-intraday intervals (daily, weekly) even when short
  * ``FetchMeta`` field values in every case
  * ``interval_display_label`` human-readable mapping
  * Integration cross-check: ``get_timeframe`` maps all trading types
"""

from __future__ import annotations

import pandas as pd
import pytest

from config.trading_config import TRADING_TYPES, get_timeframe
from data.manager import (
    FetchMeta,
    _is_intraday,
    _is_insufficient,
    fetch_for_trading_type,
    interval_display_label,
)


# ---------------------------------------------------------------------------
# Helpers — fake DataFrames and fetch functions
# ---------------------------------------------------------------------------

def _make_df(n_rows: int = 25) -> pd.DataFrame:
    """Return a valid-looking OHLCV DataFrame with *n_rows* rows."""
    return pd.DataFrame({
        "Open":   [100.0] * n_rows,
        "High":   [105.0] * n_rows,
        "Low":    [95.0]  * n_rows,
        "Close":  [102.0] * n_rows,
        "Volume": [1000]  * n_rows,
    })


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def _fake_empty(_symbol: str, _period: str, _interval: str) -> pd.DataFrame:
    """Always returns an empty DataFrame (simulates data unavailable)."""
    return _empty_df()


def _fake_good(_symbol: str, _period: str, _interval: str) -> pd.DataFrame:
    """Always returns a valid 25-row DataFrame."""
    return _make_df(25)


def _fake_intraday_unavailable(
    _symbol: str, _period: str, interval: str
) -> pd.DataFrame:
    """Returns empty for intraday intervals, valid for daily."""
    if _is_intraday(interval):
        return _empty_df()
    return _make_df(25)


def _fake_short_intraday(
    _symbol: str, _period: str, interval: str
) -> pd.DataFrame:
    """Returns a too-short DataFrame for intraday, valid for daily."""
    if _is_intraday(interval):
        return _make_df(5)   # < _MIN_SUFFICIENT_ROWS (20)
    return _make_df(25)


# ---------------------------------------------------------------------------
# _is_intraday
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("interval", ["1m", "2m", "3m", "5m", "10m", "15m",
                                       "30m", "60m", "75m", "90m", "1h"])
def test_is_intraday_returns_true_for_intraday_intervals(interval: str) -> None:
    """Every interval listed in the spec must be classified as intraday."""
    assert _is_intraday(interval) is True


@pytest.mark.parametrize("interval", ["1d", "1wk", "1mo", "5d"])
def test_is_intraday_returns_false_for_daily_weekly(interval: str) -> None:
    """Daily and weekly intervals are NOT intraday and must not trigger fallback."""
    assert _is_intraday(interval) is False


# ---------------------------------------------------------------------------
# Intraday fallback — empty primary result
# ---------------------------------------------------------------------------

def test_intraday_fallback_when_primary_fetch_returns_empty() -> None:
    """When an intraday fetch returns an empty DataFrame, fetch_for_trading_type
    must retry with daily data and set fell_back=True."""
    df, meta = fetch_for_trading_type(
        "TEST.NS", "Intraday Trading", fetch_fn=_fake_intraday_unavailable
    )

    assert df is not None and len(df) >= 20, "Fallback daily df must be returned"
    assert meta["fell_back"] is True
    assert meta["requested_interval"] == "15m"
    assert meta["used_interval"] == "1d"
    assert meta["used_period"] == "1y"
    assert "unavailable" in meta["message"].lower()


def test_intraday_fallback_when_primary_fetch_returns_too_few_rows() -> None:
    """A short (< 20 rows) intraday result must also trigger the fallback."""
    df, meta = fetch_for_trading_type(
        "TEST.NS", "Intraday Trading", fetch_fn=_fake_short_intraday
    )

    assert df is not None and len(df) >= 20
    assert meta["fell_back"] is True
    assert meta["used_interval"] == "1d"


# ---------------------------------------------------------------------------
# Intraday — no fallback when data IS available
# ---------------------------------------------------------------------------

def test_intraday_no_fallback_when_data_sufficient() -> None:
    """When the intraday fetch returns >= 20 rows, no fallback must occur."""
    call_log: list[str] = []

    def _log_and_return(symbol: str, period: str, interval: str) -> pd.DataFrame:
        call_log.append(interval)
        return _make_df(25)

    df, meta = fetch_for_trading_type(
        "TEST.NS", "Intraday Trading", fetch_fn=_log_and_return
    )

    assert df is not None and len(df) >= 20
    assert meta["fell_back"] is False
    assert meta["requested_interval"] == "15m"
    assert meta["used_interval"] == "15m"
    assert meta["message"] == ""
    # Only one fetch must have been attempted — no fallback retry.
    assert call_log == ["15m"], f"Expected one fetch; got: {call_log}"


# ---------------------------------------------------------------------------
# Non-intraday intervals — fallback must NEVER fire
# ---------------------------------------------------------------------------

def test_daily_does_not_trigger_fallback_even_when_result_is_short() -> None:
    """Short daily data is a genuine gap, not a brokerage restriction.
    fetch_for_trading_type must NOT retry for daily intervals."""
    call_log: list[str] = []

    def _short_daily(symbol: str, period: str, interval: str) -> pd.DataFrame:
        call_log.append(interval)
        return _make_df(25)   # sufficient — just checking fallback never fires

    df, meta = fetch_for_trading_type(
        "TEST.NS", "Short-term Trading", fetch_fn=_short_daily
    )

    assert meta["fell_back"] is False
    assert meta["used_interval"] == "1d"
    assert len(call_log) == 1, "Only one fetch expected; fallback must not fire"


def test_weekly_does_not_trigger_fallback() -> None:
    """Weekly (Long-term Investment) is not intraday — no fallback."""
    df, meta = fetch_for_trading_type(
        "TEST.NS", "Long-term Investment", fetch_fn=_fake_good
    )

    assert meta["fell_back"] is False
    assert meta["used_interval"] == "1wk"


def test_options_trading_daily_does_not_trigger_fallback() -> None:
    """Options Trading uses daily data — must not trigger intraday fallback."""
    df, meta = fetch_for_trading_type(
        "TEST.NS", "Options Trading", fetch_fn=_fake_good
    )

    assert meta["fell_back"] is False
    assert meta["used_interval"] == "1d"


# ---------------------------------------------------------------------------
# FetchMeta field completeness
# ---------------------------------------------------------------------------

def test_meta_fields_when_intraday_falls_back() -> None:
    """All FetchMeta fields must have the correct values after a fallback."""
    df, meta = fetch_for_trading_type(
        "NIFTY50.NS", "Intraday Trading", fetch_fn=_fake_intraday_unavailable
    )

    assert meta["requested_interval"] == "15m"   # what was asked
    assert meta["used_interval"] == "1d"          # what was actually used
    assert meta["used_period"] == "1y"            # fallback period
    assert meta["fell_back"] is True
    assert isinstance(meta["message"], str) and meta["message"]


def test_meta_fields_when_no_fallback() -> None:
    """FetchMeta fields when first fetch succeeds."""
    df, meta = fetch_for_trading_type(
        "TEST.NS", "Short-term Trading", fetch_fn=_fake_good
    )

    assert meta["requested_interval"] == "1d"
    assert meta["used_interval"] == "1d"
    assert meta["used_period"] == "1y"
    assert meta["fell_back"] is False
    assert meta["message"] == ""


def test_meta_fields_for_long_term() -> None:
    """Long-term Investment uses weekly data — verify meta reflects that."""
    df, meta = fetch_for_trading_type(
        "TEST.NS", "Long-term Investment", fetch_fn=_fake_good
    )

    assert meta["requested_interval"] == "1wk"
    assert meta["used_interval"] == "1wk"
    assert meta["used_period"] == "5y"
    assert meta["fell_back"] is False


# ---------------------------------------------------------------------------
# No-data scenario
# ---------------------------------------------------------------------------

def test_returns_none_when_all_fetches_return_empty() -> None:
    """When both primary and fallback fetches are empty, the function must
    return (None, meta) with a non-empty message — never raise."""
    df, meta = fetch_for_trading_type(
        "GHOST.NS", "Intraday Trading", fetch_fn=_fake_empty
    )

    assert df is None, "No usable data → df must be None"
    assert isinstance(meta["message"], str) and meta["message"]


def test_returns_none_does_not_raise_for_non_intraday_no_data() -> None:
    """For a non-intraday type with no data, returns (None, meta) gracefully."""
    df, meta = fetch_for_trading_type(
        "GHOST.NS", "Long-term Investment", fetch_fn=_fake_empty
    )

    assert df is None
    assert meta["message"]


# ---------------------------------------------------------------------------
# interval_display_label
# ---------------------------------------------------------------------------

def test_interval_display_label_daily() -> None:
    assert interval_display_label("1d") == "Daily"


def test_interval_display_label_weekly() -> None:
    assert interval_display_label("1wk") == "Weekly"


def test_interval_display_label_intraday() -> None:
    assert interval_display_label("15m") == "15m (intraday)"


def test_interval_display_label_fell_back() -> None:
    """When fell_back=True, the suffix must indicate intraday was unavailable."""
    label = interval_display_label("1d", fell_back=True)
    assert "unavailable" in label.lower()
    assert "Daily" in label


def test_interval_display_label_unknown_interval() -> None:
    """An unknown interval string is returned as-is (no crash)."""
    label = interval_display_label("42d")
    assert label == "42d"


# ---------------------------------------------------------------------------
# Integration cross-check: get_timeframe covers all trading types
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("trading_type", TRADING_TYPES)
def test_get_timeframe_maps_all_trading_types(trading_type: str) -> None:
    """Every trading type must produce a valid period+interval via
    get_timeframe, and the interval must be classifiable by _is_intraday."""
    tf = get_timeframe(trading_type)

    assert "period" in tf and tf["period"]
    assert "interval" in tf and tf["interval"]
    # _is_intraday must not raise on any of these intervals.
    _ = _is_intraday(tf["interval"])


@pytest.mark.parametrize("trading_type", TRADING_TYPES)
def test_fetch_for_trading_type_uses_correct_interval(trading_type: str) -> None:
    """The requested_interval in FetchMeta must match what get_timeframe returns."""
    expected_interval = get_timeframe(trading_type)["interval"]
    call_intervals: list[str] = []

    def _log_fn(_symbol: str, _period: str, interval: str) -> pd.DataFrame:
        call_intervals.append(interval)
        return _make_df(25)   # always sufficient — just checking interval routing

    _, meta = fetch_for_trading_type("TEST.NS", trading_type, fetch_fn=_log_fn)

    assert meta["requested_interval"] == expected_interval, (
        f"{trading_type}: expected interval {expected_interval!r}, "
        f"got {meta['requested_interval']!r}"
    )
    assert call_intervals[0] == expected_interval, (
        f"{trading_type}: fetch_fn was called with {call_intervals[0]!r}, "
        f"expected {expected_interval!r}"
    )
