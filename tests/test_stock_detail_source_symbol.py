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
