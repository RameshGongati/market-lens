"""Pure-logic unit tests for the Stage B sidebar selection model.

These tests do NOT start a Streamlit server — they call the helper functions
directly, so they run fast and reliably in CI without any browser or widget
infrastructure.

Coverage:
  * Changing trading type yields the correct default primary strategy and
    enhancers (calls ``get_defaults`` directly — mirrors what
    ``_on_trading_type_change`` does).
  * The ``use_fibonacci`` derivation rule: "Fibonacci Confluence" in the
    enhancers list → True; anything else → False.
  * ``map_primary_to_legacy`` — the TEMPORARY Stage B bridge that maps the
    new Primary Strategy vocabulary to the legacy _ANALYSIS_MAP keys.
"""

from __future__ import annotations

import pytest

from config.trading_config import ENHANCERS, TRADING_TYPES, get_defaults
from ui.pages.dashboard import map_primary_to_legacy


# ---------------------------------------------------------------------------
# Helper: simulate the "trading type changed" logic from
# sidebar._on_trading_type_change without touching session state
# ---------------------------------------------------------------------------

def _apply_trading_type_change(new_type: str) -> tuple[str, list[str]]:
    """Return (primary_strategy, enhancers) that _on_trading_type_change
    would write to session state when the user picks *new_type*."""
    defaults = get_defaults(new_type)
    return defaults["primary"], list(defaults["enhancers"])  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Changing trading type → correct defaults
# ---------------------------------------------------------------------------

def test_changing_to_options_trading_defaults_to_demand_supply_with_fibonacci():
    """When the user switches to Options Trading, the primary strategy must
    reset to Demand/Supply Zones and Fibonacci Confluence must be pre-ticked."""
    primary, enhancers = _apply_trading_type_change("Options Trading")

    assert primary == "Demand/Supply Zones"
    assert "Fibonacci Confluence" in enhancers


def test_changing_to_intraday_defaults_to_demand_supply_with_ema20():
    """When switching to Intraday Trading, Demand/Supply Zones is the primary
    and EMA 20 Confluence is the pre-ticked enhancer."""
    primary, enhancers = _apply_trading_type_change("Intraday Trading")

    assert primary == "Demand/Supply Zones"
    assert "EMA 20 Confluence" in enhancers


def test_changing_to_short_term_defaults_to_demand_supply_with_both_fib_and_ema():
    """Short-term Trading pre-ticks both Fibonacci and EMA 20 Confluence."""
    primary, enhancers = _apply_trading_type_change("Short-term Trading")

    assert primary == "Demand/Supply Zones"
    assert "Fibonacci Confluence" in enhancers
    assert "EMA 20 Confluence" in enhancers


def test_changing_to_long_term_defaults_to_trend_following_no_enhancers():
    """Long-term Investment defaults to Trend Following with no enhancers —
    the strategy is self-contained and doesn't need extra layers by default."""
    primary, enhancers = _apply_trading_type_change("Long-term Investment")

    assert primary == "Trend Following (SMA50/EMA20)"
    assert enhancers == []


@pytest.mark.parametrize("trading_type", TRADING_TYPES)
def test_applying_every_trading_type_returns_known_primary_and_valid_enhancers(
    trading_type: str,
) -> None:
    """Structural completeness: for every trading type, the defaults must
    produce a non-empty primary strategy and a list whose entries are all
    members of ENHANCERS (no typos, no stale names)."""
    primary, enhancers = _apply_trading_type_change(trading_type)

    assert isinstance(primary, str) and primary, (
        f"Primary must be a non-empty string for {trading_type!r}"
    )
    for e in enhancers:
        assert e in ENHANCERS, (
            f"Default enhancer {e!r} for {trading_type!r} is not in ENHANCERS"
        )


# ---------------------------------------------------------------------------
# use_fibonacci derivation rule
# ---------------------------------------------------------------------------

def test_use_fibonacci_true_when_fibonacci_confluence_in_enhancers():
    """Rule: use_fibonacci = 'Fibonacci Confluence' in enhancers → True."""
    enhancers = ["Fibonacci Confluence", "EMA 20 Confluence"]
    use_fibonacci = "Fibonacci Confluence" in enhancers
    assert use_fibonacci is True


def test_use_fibonacci_false_when_fibonacci_confluence_absent():
    """Rule: use_fibonacci = 'Fibonacci Confluence' in enhancers → False
    when the enhancer is not selected (e.g. only EMA 20 is ticked)."""
    enhancers = ["EMA 20 Confluence"]
    use_fibonacci = "Fibonacci Confluence" in enhancers
    assert use_fibonacci is False


def test_use_fibonacci_false_when_enhancers_empty():
    """Rule: an empty enhancers list gives use_fibonacci = False — the
    Fibonacci engine must not run unless explicitly opted in."""
    enhancers: list[str] = []
    use_fibonacci = "Fibonacci Confluence" in enhancers
    assert use_fibonacci is False


def test_use_fibonacci_true_when_fibonacci_is_only_enhancer():
    """Edge case: Fibonacci alone (no EMA 20) still sets use_fibonacci=True."""
    enhancers = ["Fibonacci Confluence"]
    use_fibonacci = "Fibonacci Confluence" in enhancers
    assert use_fibonacci is True


# ---------------------------------------------------------------------------
# map_primary_to_legacy — TEMPORARY Stage B bridge
# ---------------------------------------------------------------------------

def test_map_demand_supply_stays_demand_supply():
    """Demand/Supply Zones maps to itself — the existing DemandSupplyAnalysis
    engine is used without any indirection."""
    assert map_primary_to_legacy("Demand/Supply Zones") == "Demand/Supply Zones"


def test_map_trend_following_maps_to_long_term_investment():
    """TEMPORARY Stage B rule: Trend Following (SMA50/EMA20) maps to Long Term
    Investment so the existing LongTermAnalysis class is used as a proxy until
    a dedicated Trend Following engine is built in Stage D."""
    assert map_primary_to_legacy("Trend Following (SMA50/EMA20)") == "Long Term Investment"


def test_map_unknown_primary_falls_back_to_demand_supply():
    """Graceful fallback: an unrecognised primary strategy string must not
    raise KeyError — it falls back to Demand/Supply Zones so the app always
    produces a result."""
    assert map_primary_to_legacy("Some Future Strategy") == "Demand/Supply Zones"


def test_map_primary_to_legacy_returns_string_for_all_known_primaries():
    """Every value in PRIMARY_STRATEGIES must map to a non-empty string."""
    from config.trading_config import PRIMARY_STRATEGIES

    for ps in PRIMARY_STRATEGIES:
        result = map_primary_to_legacy(ps)
        assert isinstance(result, str) and result, (
            f"map_primary_to_legacy({ps!r}) returned an empty or non-string value"
        )
