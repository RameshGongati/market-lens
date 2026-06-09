"""Unit tests for config.trading_config — the Stage A foundation constants.

These tests assert the structure and content of the two-axis trading-type
configuration WITHOUT exercising any UI or analysis logic.  They serve as a
living specification: if a future stage changes a constant (e.g. adds a new
trading type) these tests will immediately surface the need to update
all dependent tables (TIMEFRAME, DEFAULTS, PRIMARY_AVAILABLE) in sync.
"""

from __future__ import annotations

import pytest

from config.trading_config import (
    ENHANCERS,
    PRIMARY_AVAILABLE,
    PRIMARY_STRATEGIES,
    TRADING_TYPE_DEFAULTS,
    TRADING_TYPE_TIMEFRAME,
    TRADING_TYPES,
    get_available_primaries,
    get_defaults,
    get_timeframe,
    is_valid_combination,
)


# ---------------------------------------------------------------------------
# Structural completeness — every trading type must appear in every table
# ---------------------------------------------------------------------------

def test_every_trading_type_has_a_timeframe_mapping():
    """Rule: TRADING_TYPE_TIMEFRAME must have an entry for every value in
    TRADING_TYPES so that get_timeframe() never silently falls back to a
    wrong default in production."""
    for tt in TRADING_TYPES:
        assert tt in TRADING_TYPE_TIMEFRAME, (
            f"No timeframe entry for trading type {tt!r}"
        )


def test_every_timeframe_entry_has_period_and_interval():
    """Each timeframe entry must carry both 'period' and 'interval' keys —
    those are the exact kwargs consumed by the data-source manager."""
    for tt, spec in TRADING_TYPE_TIMEFRAME.items():
        assert "period" in spec, f"Missing 'period' for {tt!r}"
        assert "interval" in spec, f"Missing 'interval' for {tt!r}"
        assert isinstance(spec["period"], str) and spec["period"], (
            f"'period' must be a non-empty string for {tt!r}"
        )
        assert isinstance(spec["interval"], str) and spec["interval"], (
            f"'interval' must be a non-empty string for {tt!r}"
        )


def test_every_trading_type_has_defaults():
    """Rule: TRADING_TYPE_DEFAULTS must have an entry for every value in
    TRADING_TYPES so that get_defaults() always returns a complete dict."""
    for tt in TRADING_TYPES:
        assert tt in TRADING_TYPE_DEFAULTS, (
            f"No defaults entry for trading type {tt!r}"
        )


def test_every_defaults_entry_has_primary_and_enhancers():
    """Each defaults entry must carry both 'primary' (str) and 'enhancers'
    (list) so the UI can unconditionally read both keys without KeyError."""
    for tt, defaults in TRADING_TYPE_DEFAULTS.items():
        assert "primary" in defaults, f"Missing 'primary' in defaults for {tt!r}"
        assert "enhancers" in defaults, f"Missing 'enhancers' in defaults for {tt!r}"
        assert isinstance(defaults["primary"], str), (
            f"'primary' must be a str for {tt!r}"
        )
        assert isinstance(defaults["enhancers"], list), (
            f"'enhancers' must be a list for {tt!r}"
        )


def test_every_trading_type_has_available_primaries():
    """Rule: PRIMARY_AVAILABLE must have an entry for every value in
    TRADING_TYPES and each entry must be a non-empty list."""
    for tt in TRADING_TYPES:
        assert tt in PRIMARY_AVAILABLE, (
            f"No PRIMARY_AVAILABLE entry for trading type {tt!r}"
        )
        assert PRIMARY_AVAILABLE[tt], (
            f"PRIMARY_AVAILABLE[{tt!r}] must not be empty"
        )


# ---------------------------------------------------------------------------
# Default primary strategy per trading type
# ---------------------------------------------------------------------------

def test_default_primary_for_long_term_is_trend_following():
    """Design rule: Long-term Investment defaults to Trend Following
    (SMA50/EMA20) — the primary strategy most suited to multi-year charts."""
    defaults = get_defaults("Long-term Investment")
    assert defaults["primary"] == "Trend Following (SMA50/EMA20)"


@pytest.mark.parametrize("trading_type", [
    "Options Trading",
    "Intraday Trading",
    "Short-term Trading",
])
def test_default_primary_for_non_longterm_is_demand_supply(trading_type: str):
    """Design rule: every trading type other than Long-term Investment
    defaults to Demand/Supply Zones as its primary strategy."""
    defaults = get_defaults(trading_type)
    assert defaults["primary"] == "Demand/Supply Zones", (
        f"{trading_type!r} should default to Demand/Supply Zones, "
        f"got {defaults['primary']!r}"
    )


# ---------------------------------------------------------------------------
# Default enhancers per trading type
# ---------------------------------------------------------------------------

def test_options_trading_default_enhancers_include_fibonacci():
    """Options Trading is designed to lean on Fibonacci Confluence as its
    primary enhancer — this is a key design intent for the stage."""
    enhancers = get_defaults("Options Trading")["enhancers"]
    assert "Fibonacci Confluence" in enhancers


def test_intraday_trading_default_enhancers_include_ema20():
    """Intraday Trading defaults to EMA 20 Confluence as its fast-moving
    technical filter — verify the design intent is encoded."""
    enhancers = get_defaults("Intraday Trading")["enhancers"]
    assert "EMA 20 Confluence" in enhancers


def test_long_term_investment_default_enhancers_are_empty():
    """Long-term Investment defaults to no enhancers — Trend Following
    is the whole strategy; no additional layers are pre-ticked."""
    enhancers = get_defaults("Long-term Investment")["enhancers"]
    assert enhancers == []


# ---------------------------------------------------------------------------
# get_available_primaries()
# ---------------------------------------------------------------------------

def test_get_available_primaries_returns_nonempty_list_for_each_type():
    """Every trading type must expose at least one available primary strategy
    — an empty list would leave the UI with nothing to offer."""
    for tt in TRADING_TYPES:
        primaries = get_available_primaries(tt)
        assert isinstance(primaries, list) and primaries, (
            f"get_available_primaries({tt!r}) must return a non-empty list"
        )


def test_get_available_primaries_long_term_leads_with_trend_following():
    """For Long-term Investment, Trend Following should be the *first*
    entry in the available list — it is both the default and the recommended
    option, so the UI should show it pre-selected."""
    primaries = get_available_primaries("Long-term Investment")
    assert primaries[0] == "Trend Following (SMA50/EMA20)"


def test_get_available_primaries_all_entries_are_known_strategies():
    """Every string returned by get_available_primaries must be a member of
    PRIMARY_STRATEGIES — no typos or stale names should slip through."""
    for tt in TRADING_TYPES:
        for strategy in get_available_primaries(tt):
            assert strategy in PRIMARY_STRATEGIES, (
                f"get_available_primaries({tt!r}) returned unknown strategy "
                f"{strategy!r}"
            )


# ---------------------------------------------------------------------------
# get_timeframe()
# ---------------------------------------------------------------------------

def test_get_timeframe_intraday_uses_15m_interval():
    """Intraday Trading must use a 15-minute interval — this is a hard
    requirement for intraday chart fidelity."""
    tf = get_timeframe("Intraday Trading")
    assert tf["interval"] == "15m"


def test_get_timeframe_long_term_uses_weekly_interval():
    """Long-term Investment must use a weekly interval to avoid noise on
    multi-year lookback windows."""
    tf = get_timeframe("Long-term Investment")
    assert tf["interval"] == "1wk"
    assert tf["period"] == "5y"


def test_get_timeframe_unknown_type_returns_a_safe_default():
    """Graceful fallback: an unknown trading type must return a non-empty
    dict (the Options Trading default) rather than raising KeyError."""
    tf = get_timeframe("Completely Unknown Type")
    assert "period" in tf and "interval" in tf


# ---------------------------------------------------------------------------
# is_valid_combination()
# ---------------------------------------------------------------------------

def test_is_valid_combination_accepts_sensible_combo():
    """A well-formed (trading_type, primary, enhancers) triple that matches
    the documented constants must be accepted with an empty reason string."""
    ok, reason = is_valid_combination(
        "Options Trading",
        "Demand/Supply Zones",
        ["Fibonacci Confluence"],
    )
    assert ok is True
    assert reason == ""


def test_is_valid_combination_accepts_empty_enhancers():
    """Zero enhancers is a valid choice — Demand/Supply Zones alone is a
    complete analysis strategy."""
    ok, reason = is_valid_combination(
        "Short-term Trading",
        "Demand/Supply Zones",
        [],
    )
    assert ok is True
    assert reason == ""


def test_is_valid_combination_rejects_unknown_trading_type():
    """An unknown trading type string must be rejected immediately with a
    reason that identifies the bad value."""
    ok, reason = is_valid_combination(
        "Swing Trading",          # not in TRADING_TYPES
        "Demand/Supply Zones",
        [],
    )
    assert ok is False
    assert "Swing Trading" in reason


def test_is_valid_combination_rejects_unknown_primary():
    """A primary strategy that is not in PRIMARY_AVAILABLE for the given
    trading type must be rejected."""
    ok, reason = is_valid_combination(
        "Long-term Investment",
        "Scalping Strategy",      # not a known strategy
        [],
    )
    assert ok is False
    assert "Scalping Strategy" in reason


def test_is_valid_combination_rejects_unknown_enhancer():
    """An unrecognised enhancer name must be caught and reported, even when
    the trading type and primary are both valid."""
    ok, reason = is_valid_combination(
        "Intraday Trading",
        "Demand/Supply Zones",
        ["EMA 20 Confluence", "VWAP"],  # VWAP not in ENHANCERS
    )
    assert ok is False
    assert "VWAP" in reason


def test_is_valid_combination_accepts_all_documented_enhancers():
    """Every enhancer in ENHANCERS must be individually accepted by
    is_valid_combination — tests that the constant and the validator agree."""
    for enhancer in ENHANCERS:
        ok, reason = is_valid_combination(
            "Short-term Trading",
            "Demand/Supply Zones",
            [enhancer],
        )
        assert ok is True, (
            f"Enhancer {enhancer!r} was unexpectedly rejected: {reason}"
        )


# ---------------------------------------------------------------------------
# Constant integrity — sanity-check the raw lists/dicts
# ---------------------------------------------------------------------------

def test_trading_types_is_nonempty_list_of_strings():
    assert TRADING_TYPES and all(isinstance(t, str) for t in TRADING_TYPES)


def test_primary_strategies_is_nonempty_list_of_strings():
    assert PRIMARY_STRATEGIES and all(isinstance(s, str) for s in PRIMARY_STRATEGIES)


def test_enhancers_is_nonempty_list_of_strings():
    assert ENHANCERS and all(isinstance(e, str) for e in ENHANCERS)


def test_default_enhancers_are_all_known():
    """Every enhancer listed in any TRADING_TYPE_DEFAULTS entry must be
    a member of ENHANCERS — a typo in a default would silently pass
    through until the UI tried to look it up."""
    for tt, defaults in TRADING_TYPE_DEFAULTS.items():
        for enhancer in defaults["enhancers"]:  # type: ignore[union-attr]
            assert enhancer in ENHANCERS, (
                f"Default enhancer {enhancer!r} for {tt!r} is not in ENHANCERS"
            )


def test_default_primaries_are_all_known():
    """Every primary in TRADING_TYPE_DEFAULTS must appear in PRIMARY_STRATEGIES."""
    for tt, defaults in TRADING_TYPE_DEFAULTS.items():
        primary = defaults["primary"]
        assert primary in PRIMARY_STRATEGIES, (
            f"Default primary {primary!r} for {tt!r} is not in PRIMARY_STRATEGIES"
        )
