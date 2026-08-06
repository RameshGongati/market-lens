"""Shared helpers for Pattern Scanner pages."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from urllib.parse import urlencode

import streamlit as st

from analysis.pattern_models import PatternMatch
from utils.helpers import (
    get_nse_batch_stocks,
    get_nse_stock_batches,
    load_predefined_watchlists,
)
from watchlist.manager import get_all_watchlists, get_stocks


def resolve_pattern_universe(scope: str) -> tuple[str, list[SimpleNamespace]]:
    """Resolve a pattern-scan scope into a label and stock list."""
    if scope == "Nifty 50":
        return _predefined("Nifty 50")
    if scope == "F&O Stocks":
        return _predefined("F&O Stocks")
    if scope == "All NSE":
        stocks: list[SimpleNamespace] = []
        for batch in get_nse_stock_batches():
            for row in get_nse_batch_stocks(batch["start"], batch["end"]):
                stocks.append(
                    SimpleNamespace(
                        symbol=row["symbol"], exchange=row.get("exchange", "NSE"), id=0
                    )
                )
        return "All NSE", stocks
    return _current_watchlist()


def match_by_symbol(matches: list[PatternMatch], symbol: str) -> PatternMatch | None:
    """Return the first match for *symbol* from the current results."""
    sym = symbol.upper()
    return next((m for m in matches if m.symbol.upper() == sym), None)


def build_pattern_detail_url(scan_id: str, symbol: str) -> str:
    """Deep link for opening Pattern Detail in a new browser tab."""
    return "?" + urlencode({"pattern_scan": scan_id, "pattern_symbol": symbol})


def pattern_counts(matches: list[PatternMatch]) -> dict[str, int]:
    """Summary counts used by setup/results pages."""
    return {
        "total": len(matches),
        "forming": sum(1 for m in matches if m.stage == "Forming"),
        "near_apex": sum(1 for m in matches if m.stage == "Near Apex"),
        "breakout": sum(1 for m in matches if m.stage == "Breakout Confirmed"),
        "triangle": sum(1 for m in matches if m.pattern_family == "Triangle Patterns"),
        "vcp": sum(1 for m in matches if m.pattern_family == "VCP / Tight Base"),
        "range": sum(1 for m in matches if m.pattern_family == "Range Breakouts"),
        "flag": sum(1 for m in matches if m.pattern_family == "Flag / Pennant"),
        "double": sum(1 for m in matches if m.pattern_family == "Double Top / Bottom"),
        "contraction": sum(
            1
            for m in matches
            if m.pattern_family in ("Triangle Patterns", "VCP / Tight Base")
        ),
        "continuation": sum(
            1
            for m in matches
            if m.pattern_family in ("Flag / Pennant", "Range Breakouts")
        ),
        "reversal": sum(1 for m in matches if m.pattern_family == "Double Top / Bottom"),
        "symmetrical": sum(1 for m in matches if m.pattern_type == "Symmetrical Triangle"),
        "ascending": sum(1 for m in matches if m.pattern_type == "Ascending Triangle"),
        "descending": sum(1 for m in matches if m.pattern_type == "Descending Triangle"),
    }


def _predefined(name: str) -> tuple[str, list[SimpleNamespace]]:
    predefined = load_predefined_watchlists()
    row = next((w for w in predefined if w.get("name") == name), None)
    if not row:
        return name, []
    return name, [
        SimpleNamespace(symbol=sym, exchange="NSE", id=0)
        for sym in row.get("symbols", [])
    ]


def _current_watchlist() -> tuple[str, list[SimpleNamespace]]:
    source = st.session_state.get("watchlist_source", "Index Watchlists")
    if source == "Index Watchlists":
        name = st.session_state.get("selected_predefined_watchlist")
        if not name:
            return "Current Watchlist", []
        return _predefined(name)
    if source == "All NSE Stocks":
        start = int(st.session_state.get("selected_nse_batch_start", 0) or 0)
        end = int(st.session_state.get("selected_nse_batch_end", 200) or 200)
        rows = get_nse_batch_stocks(start, end)
        label = st.session_state.get("selected_nse_batch", "Selected NSE batch")
        return str(label), [
            SimpleNamespace(symbol=row["symbol"], exchange=row.get("exchange", "NSE"), id=0)
            for row in rows
        ]

    watchlist_id = st.session_state.get("selected_watchlist_id")
    if watchlist_id is None:
        return "Current Watchlist", []
    try:
        watchlists = get_all_watchlists()
        wl = next((w for w in watchlists if w.id == watchlist_id), None)
        label = wl.name if wl else "Current Watchlist"
        return label, [
            SimpleNamespace(symbol=s.symbol, exchange=s.exchange, id=s.id)
            for s in get_stocks(watchlist_id)
        ]
    except Exception:
        return "Current Watchlist", []


def serialise_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Store only plain values in session state."""
    return {
        "pattern_family": settings.get("pattern_family", "All Chart Patterns"),
        "pattern_type": settings.get("pattern_type", "All Chart Pattern Types"),
        "detection_stages": list(settings.get("detection_stages") or []),
        "timeframe": settings.get("timeframe", "Daily"),
        "scope": settings.get("scope", "Current Watchlist"),
        "recent_only": bool(settings.get("recent_only", True)),
        "freshness_window": int(settings.get("freshness_window", 5) or 5),
        "require_volatility_contraction": bool(
            settings.get("require_volatility_contraction", False)
        ),
        "require_zone_context": bool(settings.get("require_zone_context", False)),
        "selected_filters": list(settings.get("selected_filters") or []),
    }
