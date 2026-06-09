"""Pure-logic tests for the watchlist Add-Stock autocomplete fill.

These exercise the option <-> {symbol, exchange} mapping helpers directly —
no Streamlit runtime — so the selection-fill logic is verified in isolation
from the widget plumbing.

Regression covered: selecting a result from the dropdown must resolve to the
correct symbol/exchange so the Symbol field can be populated. (The original
bug was a no-op selectbox callback; these tests pin the data mapping that the
fixed callback relies on.)
"""

from __future__ import annotations

from ui.components.watchlist_panel import (
    build_option_map,
    format_stock_option,
    lookup_selected_stock,
)


# ---------------------------------------------------------------------------
# format_stock_option
# ---------------------------------------------------------------------------

def test_format_stock_option_nse() -> None:
    opt = format_stock_option(
        {"symbol": "SUZLON", "name": "Suzlon Energy Ltd", "exchange": "NSE"}
    )
    assert opt == "SUZLON — Suzlon Energy Ltd (NSE)"


def test_format_stock_option_bse() -> None:
    opt = format_stock_option(
        {"symbol": "TCS", "name": "Tata Consultancy Services", "exchange": "BSE"}
    )
    assert opt == "TCS — Tata Consultancy Services (BSE)"


# ---------------------------------------------------------------------------
# build_option_map + lookup_selected_stock — NSE / BSE round trips
# ---------------------------------------------------------------------------

def test_lookup_nse_example() -> None:
    matches = [{"symbol": "SUZLON", "name": "Suzlon Energy Ltd", "exchange": "NSE"}]
    omap = build_option_map(matches)
    option = "SUZLON — Suzlon Energy Ltd (NSE)"
    assert lookup_selected_stock(option, omap) == {"symbol": "SUZLON", "exchange": "NSE"}


def test_lookup_bse_example() -> None:
    matches = [{"symbol": "TCS", "name": "Tata Consultancy Services", "exchange": "BSE"}]
    omap = build_option_map(matches)
    option = "TCS — Tata Consultancy Services (BSE)"
    assert lookup_selected_stock(option, omap) == {"symbol": "TCS", "exchange": "BSE"}


def test_lookup_name_with_multiple_spaces() -> None:
    """A long company name with several spaces must still resolve exactly."""
    matches = [{
        "symbol": "BAJFINANCE",
        "name": "Bajaj Finance Limited Consumer Lending",
        "exchange": "NSE",
    }]
    omap = build_option_map(matches)
    option = format_stock_option(matches[0])
    assert lookup_selected_stock(option, omap) == {
        "symbol": "BAJFINANCE", "exchange": "NSE",
    }


def test_lookup_name_containing_em_dash() -> None:
    """A company name that itself contains an em-dash must not break the
    lookup (the map keys on the full string, so no fragile parsing)."""
    matches = [{
        "symbol": "XYZ",
        "name": "Xyz — Holdings — Group",
        "exchange": "BSE",
    }]
    omap = build_option_map(matches)
    option = format_stock_option(matches[0])
    assert lookup_selected_stock(option, omap) == {"symbol": "XYZ", "exchange": "BSE"}


def test_build_option_map_multiple_entries() -> None:
    matches = [
        {"symbol": "SUZLON", "name": "Suzlon Energy Ltd", "exchange": "NSE"},
        {"symbol": "TCS", "name": "Tata Consultancy Services", "exchange": "BSE"},
    ]
    omap = build_option_map(matches)
    assert len(omap) == 2
    assert lookup_selected_stock("SUZLON — Suzlon Energy Ltd (NSE)", omap)["exchange"] == "NSE"
    assert lookup_selected_stock("TCS — Tata Consultancy Services (BSE)", omap)["exchange"] == "BSE"


# ---------------------------------------------------------------------------
# Empty / invalid selection -> None (Add Stock validation still triggers)
# ---------------------------------------------------------------------------

def test_lookup_none_selection_returns_none() -> None:
    """index=None (placeholder) selection — nothing chosen yet."""
    assert lookup_selected_stock(None, {"X": {"symbol": "X", "exchange": "NSE"}}) is None


def test_lookup_empty_string_returns_none() -> None:
    assert lookup_selected_stock("", {"X": {"symbol": "X", "exchange": "NSE"}}) is None


def test_lookup_unknown_option_returns_none() -> None:
    """A selection that isn't in the current option map resolves to None
    rather than raising — so stale selections never crash the fill."""
    omap = build_option_map(
        [{"symbol": "SUZLON", "name": "Suzlon Energy Ltd", "exchange": "NSE"}]
    )
    assert lookup_selected_stock("GHOST — Not Real (NSE)", omap) is None


def test_lookup_against_empty_map_returns_none() -> None:
    assert lookup_selected_stock("ANYTHING", {}) is None
