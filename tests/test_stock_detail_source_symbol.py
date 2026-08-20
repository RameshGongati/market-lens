"""Regression tests for source-aware Stock Detail chart symbols."""

from __future__ import annotations

from ui.components import stock_detail


def test_source_symbol_formats_yahoo_exchange_suffixes() -> None:
    assert stock_detail._source_symbol("SBIN", "NSE", "Yahoo Finance") == "SBIN.NS"
    assert stock_detail._source_symbol("RELIANCE", "BSE", "Yahoo Finance") == "RELIANCE.BO"


def test_source_symbol_formats_tradingview_exchange_prefix() -> None:
    assert stock_detail._source_symbol("SBIN", "NSE", "TradingView") == "NSE:SBIN"


def test_source_symbol_keeps_plain_symbol_for_other_sources() -> None:
    assert stock_detail._source_symbol("SBIN", "NSE", "Jugaad") == "SBIN"


def test_normalise_live_quote_keeps_valid_live_values() -> None:
    assert stock_detail._normalise_live_quote({
        "current_price": "35395",
        "change": "155",
        "change_pct": "0.44",
    }) == {"price": 35395.0, "change": 155.0, "change_pct": 0.44}


def test_normalise_live_quote_rejects_missing_or_invalid_price() -> None:
    assert stock_detail._normalise_live_quote({"current_price": 0}) is None
    assert stock_detail._normalise_live_quote({"current_price": "not-a-price"}) is None
