"""Watchlist-level orchestration for the Pattern Scanner."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import pandas as pd

from analysis.demand_supply import DemandSupplyAnalysis
from analysis.pattern_detectors import (
    detect_double_patterns,
    detect_flag_pennant_patterns,
    detect_range_breakout_patterns,
    detect_triangle_patterns,
    detect_vcp_patterns,
)
from analysis.pattern_detectors.pattern_types import (
    ALL_CHART_PATTERNS_FAMILY,
    ALL_CHART_PATTERN_TYPES,
    ALL_CHART_PATTERN_TYPES_LABEL,
    ALL_PATTERN_TYPES,
    BEAR_FLAG_TYPE,
    BEAR_PENNANT_TYPE,
    BEAR_RECTANGLE_TYPE,
    BULL_FLAG_TYPE,
    BULL_PENNANT_TYPE,
    BULL_RECTANGLE_TYPE,
    DOUBLE_TYPES,
    DOUBLE_BOTTOM_TYPE,
    DOUBLE_FAMILY,
    DOUBLE_TOP_TYPE,
    FLAG_TYPES,
    FLAG_FAMILY,
    PATTERN_FAMILIES,
    PATTERN_TYPES_BY_FAMILY,
    RANGE_TYPES,
    RANGE_FAMILY,
    RECTANGLE_RANGE_TYPE,
    TRIANGLE_FAMILY,
    TRIANGLE_TYPES,
    VCP_TYPES,
    VCP_FAMILY,
    VCP_TYPE,
    is_all_pattern_type,
    normalise_pattern_family,
    normalise_pattern_type,
)
from analysis.pattern_models import PatternMatch
from data.manager import build_source_manager, fetch_by_interval
from utils.helpers import get_company_name
from utils.logger import get_logger

logger = get_logger(__name__)

DETECTION_STAGES = ["Forming", "Near Apex", "Breakout Confirmed"]
TIMEFRAMES = ["Daily", "Weekly", "75m", "15m"]
SCOPES = ["Current Watchlist", "Nifty 50", "F&O Stocks", "All NSE"]
SUGGESTED_FILTERS = [
    "Near Demand Zone",
    "Near Supply Zone",
    "Volume Contraction",
    "Breakout Pending",
    "Confirmed Breakout",
    "Zone Context",
]


@dataclass
class PatternScanOutput:
    """Results and sidecar data produced by a pattern scan."""

    matches: list[PatternMatch]
    chart_data: dict[str, pd.DataFrame] = field(default_factory=dict)
    zone_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    scanned_symbols: int = 0
    fallback_symbols: list[str] = field(default_factory=list)
    completed_at: datetime = field(default_factory=datetime.now)


def default_settings() -> dict[str, Any]:
    """Default Pattern Scanner settings for a new session."""
    return {
        "pattern_family": ALL_CHART_PATTERNS_FAMILY,
        "pattern_type": ALL_CHART_PATTERN_TYPES_LABEL,
        "detection_stages": list(DETECTION_STAGES),
        "timeframe": "Daily",
        "scope": "Current Watchlist",
        "recent_only": True,
        "freshness_window": 5,
        "require_volatility_contraction": False,
        "require_zone_context": False,
        "selected_filters": [],
    }


def pattern_types_for_family(family: str) -> list[str]:
    """Return selectable pattern-type labels for a pattern family."""
    return PATTERN_TYPES_BY_FAMILY.get(
        normalise_pattern_family(family),
        ALL_CHART_PATTERN_TYPES,
    )


def run_pattern_scan(
    *,
    settings: dict[str, Any],
    stocks: Iterable[Any],
    source_name: str,
    credentials: dict[str, str] | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> PatternScanOutput:
    """Run a saved pattern scan across a concrete stock universe."""
    stock_list = [_coerce_stock(s) for s in stocks]
    output = PatternScanOutput(matches=[], scanned_symbols=len(stock_list))
    if not stock_list:
        return output

    try:
        manager = build_source_manager(source_name, credentials or {})
    except Exception as exc:  # noqa: BLE001
        output.errors["source"] = str(exc)
        return output

    family = normalise_pattern_family(settings.get("pattern_family"))
    pattern_type = normalise_pattern_type(settings.get("pattern_type"))
    timeframe = settings.get("timeframe", "Daily")

    for idx, stock in enumerate(stock_list, start=1):
        if progress_callback:
            progress_callback(stock.symbol, idx, len(stock_list))
        full_symbol = _make_symbol(stock.symbol, stock.exchange, source_name)
        try:
            hist, meta = fetch_by_interval(
                full_symbol, timeframe, fetch_fn=manager.get_history
            )
            if meta.get("fell_back"):
                output.fallback_symbols.append(stock.symbol)
            if hist is None or hist.empty:
                output.errors[stock.symbol] = meta.get("message") or "No usable data."
                continue

            candidates = _detect_family(
                family=family,
                data=hist,
                symbol=stock.symbol,
                company_name=get_company_name(stock.symbol),
                exchange=stock.exchange,
                timeframe=timeframe,
                pattern_type=pattern_type,
                zone_result=None,
            )
            if not candidates:
                continue

            zone_result = _zone_context_result(stock.symbol, hist)
            candidates = _detect_family(
                family=family,
                data=hist,
                symbol=stock.symbol,
                company_name=get_company_name(stock.symbol),
                exchange=stock.exchange,
                timeframe=timeframe,
                pattern_type=pattern_type,
                zone_result=zone_result,
            )
            candidates = [m for m in candidates if _passes_scan_settings(m, settings)]
            if not candidates:
                continue

            quote = manager.get_quote(full_symbol)
            best = max(candidates, key=lambda m: m.confidence_score)
            _apply_quote(best, quote)
            output.matches.append(best)
            output.chart_data[stock.symbol] = hist
            output.zone_results[stock.symbol] = zone_result
        except Exception as exc:  # noqa: BLE001
            logger.error("Pattern scan error for %s: %s", stock.symbol, exc)
            output.errors[stock.symbol] = str(exc)

    output.matches.sort(key=lambda m: m.confidence_score, reverse=True)
    return output


def _detect_family(
    *,
    family: str,
    data: pd.DataFrame,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    pattern_type: str,
    zone_result: dict[str, Any] | None,
) -> list[PatternMatch]:
    family = normalise_pattern_family(family)
    pattern_type = normalise_pattern_type(pattern_type)
    matches: list[PatternMatch] = []
    if _should_scan_family(family, TRIANGLE_FAMILY, pattern_type, TRIANGLE_TYPES):
        if is_all_pattern_type(pattern_type) or pattern_type in TRIANGLE_TYPES:
            triangle_type = (
                pattern_type
                if pattern_type in TRIANGLE_TYPES
                else "All Triangle Patterns"
            )
            matches.extend(
                detect_triangle_patterns(
                    data,
                    symbol=symbol,
                    company_name=company_name,
                    exchange=exchange,
                    timeframe=timeframe,
                    pattern_type=triangle_type,
                    zone_result=zone_result,
                )
            )
    if _should_scan_family(family, VCP_FAMILY, pattern_type, VCP_TYPES):
        matches.extend(
            detect_vcp_patterns(
                data,
                symbol=symbol,
                company_name=company_name,
                exchange=exchange,
                timeframe=timeframe,
                zone_result=zone_result,
            )
        )
    if _should_scan_family(family, RANGE_FAMILY, pattern_type, RANGE_TYPES):
        range_type = pattern_type if pattern_type in RANGE_TYPES else RANGE_TYPES[0]
        matches.extend(
            detect_range_breakout_patterns(
                data,
                symbol=symbol,
                company_name=company_name,
                exchange=exchange,
                timeframe=timeframe,
                pattern_type=range_type,
                zone_result=zone_result,
            )
        )
    if _should_scan_family(family, FLAG_FAMILY, pattern_type, FLAG_TYPES):
        flag_type = pattern_type if pattern_type in FLAG_TYPES else FLAG_TYPES[0]
        matches.extend(
            detect_flag_pennant_patterns(
                data,
                symbol=symbol,
                company_name=company_name,
                exchange=exchange,
                timeframe=timeframe,
                pattern_type=flag_type,
                zone_result=zone_result,
            )
        )
    if _should_scan_family(family, DOUBLE_FAMILY, pattern_type, DOUBLE_TYPES):
        double_type = pattern_type if pattern_type in DOUBLE_TYPES else DOUBLE_TYPES[0]
        matches.extend(
            detect_double_patterns(
                data,
                symbol=symbol,
                company_name=company_name,
                exchange=exchange,
                timeframe=timeframe,
                pattern_type=double_type,
                zone_result=zone_result,
            )
        )
    return matches


def _should_scan_family(
    selected_family: str,
    detector_family: str,
    pattern_type: str,
    family_types: list[str],
) -> bool:
    if selected_family not in (ALL_CHART_PATTERNS_FAMILY, detector_family):
        return False
    return is_all_pattern_type(pattern_type) or pattern_type in family_types


def _zone_context_result(symbol: str, hist: pd.DataFrame) -> dict[str, Any]:
    try:
        return DemandSupplyAnalysis().analyse(symbol, hist, use_fibonacci=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Zone context failed for %s: %s", symbol, exc)
        return {}


def _passes_scan_settings(match: PatternMatch, settings: dict[str, Any]) -> bool:
    stages = settings.get("detection_stages") or []
    if stages and match.stage not in stages:
        return False
    if settings.get("recent_only", True):
        window = int(settings.get("freshness_window", 5) or 5)
        if match.freshness_candles > window:
            return False
    if settings.get("require_volatility_contraction") and not match.volume_contraction:
        return False
    if settings.get("require_zone_context") and match.zone_context not in (
        "Near Demand Zone",
        "Near Supply Zone",
    ):
        return False

    filters = set(settings.get("selected_filters") or [])
    zone_filters = {"Near Demand Zone", "Near Supply Zone"} & filters
    if zone_filters and match.zone_context not in zone_filters:
        return False
    if "Volume Contraction" in filters and not match.volume_contraction:
        return False
    if "Breakout Pending" in filters and match.stage == "Breakout Confirmed":
        return False
    if "Confirmed Breakout" in filters and match.stage != "Breakout Confirmed":
        return False
    if "Zone Context" in filters and match.zone_context not in (
        "Near Demand Zone",
        "Near Supply Zone",
    ):
        return False
    return True


def apply_result_filters(
    matches: list[PatternMatch],
    *,
    pattern_family: str,
    pattern_type: str,
    stages: list[str],
    timeframe: str,
    recent_only: bool,
    freshness_window: int,
    sort_by: str,
    selected_filters: list[str],
) -> list[PatternMatch]:
    """Filter already-generated results for the Pattern Results page."""
    filtered = list(matches)
    if pattern_family != "All Families":
        filtered = [m for m in filtered if m.pattern_family == pattern_family]
    if not is_all_pattern_type(pattern_type):
        filtered = [m for m in filtered if m.pattern_type == pattern_type]
    if stages:
        filtered = [m for m in filtered if m.stage in stages]
    if timeframe != "All Timeframes":
        filtered = [m for m in filtered if m.timeframe == timeframe]
    if recent_only:
        filtered = [m for m in filtered if m.freshness_candles <= freshness_window]
    if selected_filters:
        filtered = [
            m
            for m in filtered
            if _passes_scan_settings(
                m,
                {
                    "detection_stages": [],
                    "recent_only": False,
                    "selected_filters": selected_filters,
                },
            )
        ]

    if sort_by == "Confidence":
        filtered.sort(key=lambda m: m.confidence_score, reverse=True)
    elif sort_by in ("Apex Proximity", "Trigger Distance"):
        filtered.sort(key=lambda m: m.apex_proximity)
    elif sort_by == "Symbol":
        filtered.sort(key=lambda m: m.symbol)
    elif sort_by == "Breakout Candidates":
        filtered.sort(key=lambda m: m.stage != "Breakout Confirmed")
    else:
        filtered.sort(key=lambda m: (m.freshness_candles, -m.confidence_score))
    return filtered


def matches_to_export_rows(matches: list[PatternMatch]) -> list[dict[str, Any]]:
    """Flatten matches for Excel/PDF export."""
    rows: list[dict[str, Any]] = []
    for idx, m in enumerate(matches, start=1):
        rows.append(
            {
                "Rank": idx,
                "Symbol": m.symbol,
                "Company": m.company_name,
                "Pattern": m.pattern_type,
                "Stage": m.stage,
                "Timeframe": m.timeframe,
                "Freshness": m.freshness_candles,
                "Confidence %": m.confidence_score,
                "Trigger Distance %": round(m.apex_proximity, 1),
                "Breakout Bias": m.breakout_bias,
                "Zone Context": m.zone_context,
                "Volume Contraction": "Yes" if m.volume_contraction else "No",
                "Risk Reward": m.risk_reward or "",
            }
        )
    return rows


def _apply_quote(match: PatternMatch, quote: dict[str, Any]) -> None:
    price = _valid_number(quote.get("current_price"))
    if price is not None and price > 0:
        match.current_price = price
    change_pct = _valid_number(quote.get("change_pct"))
    change = _valid_number(quote.get("change"))
    match.change_pct = change_pct or 0.0
    match.change = change or (
        round(match.current_price * match.change_pct / 100, 2)
        if match.current_price
        else 0.0
    )


def _valid_number(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
        if math.isfinite(value):
            return value
    except (TypeError, ValueError):
        return None
    return None


def _make_symbol(symbol: str, exchange: str, source: str) -> str:
    if source == "Yahoo Finance":
        suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
        return f"{symbol}{suffix}"
    if source == "TradingView":
        return f"{exchange.upper()}:{symbol}"
    return symbol


def _coerce_stock(stock: Any) -> SimpleNamespace:
    if isinstance(stock, dict):
        return SimpleNamespace(
            symbol=str(stock.get("symbol", "")).upper(),
            exchange=str(stock.get("exchange", "NSE")).upper(),
            id=int(stock.get("id", 0) or 0),
        )
    return SimpleNamespace(
        symbol=str(getattr(stock, "symbol", "")).upper(),
        exchange=str(getattr(stock, "exchange", "NSE")).upper(),
        id=int(getattr(stock, "id", 0) or 0),
    )
