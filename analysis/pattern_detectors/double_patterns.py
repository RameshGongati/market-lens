"""Double Top and Double Bottom reversal-pattern detection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from analysis.pattern_detectors.pattern_types import (
    DOUBLE_BOTTOM_TYPE,
    DOUBLE_FAMILY,
    DOUBLE_TOP_TYPE,
    DOUBLE_TYPES,
)
from analysis.pattern_detectors.shared import PatternCandidate, candidate_to_match, matching_pair
from analysis.pattern_detectors.triangles import _find_swings, _point_for_x, _prepare_frame
from analysis.pattern_models import PatternMatch, PatternPoint


def detect_double_patterns(
    data: pd.DataFrame,
    *,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    pattern_type: str = DOUBLE_TYPES[0],
    zone_result: dict[str, Any] | None = None,
    detected_at: datetime | None = None,
) -> list[PatternMatch]:
    """Detect Double Top and Double Bottom reversal setups."""
    df = _prepare_frame(data)
    if df is None:
        return []
    wanted = set(DOUBLE_TYPES[1:]) if pattern_type == DOUBLE_TYPES[0] else {pattern_type}
    candidates = _double_candidates(df, wanted)
    matches = [
        candidate_to_match(
            df,
            candidate,
            symbol=symbol,
            company_name=company_name,
            exchange=exchange,
            timeframe=timeframe,
            zone_result=zone_result,
            detected_at=detected_at or datetime.now(),
        )
        for candidate in candidates
    ]
    return [match for match in matches if match.confidence_score >= 55.0]


def _double_candidates(df: pd.DataFrame, wanted: set[str]) -> list[PatternCandidate]:
    highs = _find_swings(df, "high", 2)
    lows = _find_swings(df, "low", 2)
    candidates: list[PatternCandidate] = []
    if DOUBLE_BOTTOM_TYPE in wanted:
        cand = _double_bottom_candidate(df, lows)
        if cand:
            candidates.append(cand)
    if DOUBLE_TOP_TYPE in wanted:
        cand = _double_top_candidate(df, highs)
        if cand:
            candidates.append(cand)
    return candidates


def _double_bottom_candidate(df: pd.DataFrame, lows: list[Any]) -> PatternCandidate | None:
    pair = matching_pair(lows, "low")
    if pair is None:
        return None
    first, second = pair
    neckline = float(df["High"].iloc[first.index : second.index + 1].max())
    base_low = min(first.price, second.price)
    current = float(df["Close"].iloc[-1])
    if neckline <= base_low or current <= 0:
        return None
    if second.index < len(df) - 32:
        return None
    prior_idx = max(0, first.index - 12)
    prior_close = float(df["Close"].iloc[prior_idx])
    trend_score = 1.0 if prior_close > first.price * 1.04 else 0.65
    symmetry = 1.0 - abs(first.price - second.price) / max((first.price + second.price) / 2, 1.0) / 0.04
    quality = 0.52 + max(0.0, symmetry) * 0.26 + trend_score * 0.12
    return PatternCandidate(
        pattern_family=DOUBLE_FAMILY,
        pattern_type=DOUBLE_BOTTOM_TYPE,
        upper_m=0.0,
        upper_b=neckline,
        lower_m=0.0,
        lower_b=base_low,
        start_x=first.index,
        end_x=len(df) - 1,
        quality=max(0.0, min(1.0, quality)),
        direction="long",
        notes=[
            "Two similar lows show sellers failing near support.",
            "The neckline break confirms the reversal attempt.",
        ],
        swing_highs=[_point_for_x(df, (first.index + second.index) / 2, neckline)],
        swing_lows=[
            PatternPoint(first.index, first.timestamp, first.price),
            PatternPoint(second.index, second.timestamp, second.price),
        ],
        trigger_label="Neckline",
    )


def _double_top_candidate(df: pd.DataFrame, highs: list[Any]) -> PatternCandidate | None:
    pair = matching_pair(highs, "high")
    if pair is None:
        return None
    first, second = pair
    neckline = float(df["Low"].iloc[first.index : second.index + 1].min())
    top_high = max(first.price, second.price)
    current = float(df["Close"].iloc[-1])
    if top_high <= neckline or current <= 0:
        return None
    if second.index < len(df) - 32:
        return None
    prior_idx = max(0, first.index - 12)
    prior_close = float(df["Close"].iloc[prior_idx])
    trend_score = 1.0 if prior_close < first.price * 0.96 else 0.65
    symmetry = 1.0 - abs(first.price - second.price) / max((first.price + second.price) / 2, 1.0) / 0.04
    quality = 0.52 + max(0.0, symmetry) * 0.26 + trend_score * 0.12
    return PatternCandidate(
        pattern_family=DOUBLE_FAMILY,
        pattern_type=DOUBLE_TOP_TYPE,
        upper_m=0.0,
        upper_b=top_high,
        lower_m=0.0,
        lower_b=neckline,
        start_x=first.index,
        end_x=len(df) - 1,
        quality=max(0.0, min(1.0, quality)),
        direction="short",
        notes=[
            "Two similar highs show buyers failing near resistance.",
            "The neckline break confirms the reversal attempt.",
        ],
        swing_highs=[
            PatternPoint(first.index, first.timestamp, first.price),
            PatternPoint(second.index, second.timestamp, second.price),
        ],
        swing_lows=[_point_for_x(df, (first.index + second.index) / 2, neckline)],
        trigger_label="Neckline",
    )
