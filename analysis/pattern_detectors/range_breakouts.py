"""Rectangle range, breakout, breakdown, and retest detection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from analysis.pattern_detectors.pattern_types import (
    BEAR_RECTANGLE_TYPE,
    BULL_RECTANGLE_TYPE,
    RANGE_FAMILY,
    RANGE_TYPES,
    RECTANGLE_RANGE_TYPE,
)
from analysis.pattern_detectors.shared import (
    BREAK_BUFFER,
    PatternCandidate,
    candidate_to_match,
    held_retest,
    point_from_label,
)
from analysis.pattern_detectors.triangles import _prepare_frame
from analysis.pattern_models import PatternMatch


def detect_range_breakout_patterns(
    data: pd.DataFrame,
    *,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    pattern_type: str = RANGE_TYPES[0],
    zone_result: dict[str, Any] | None = None,
    detected_at: datetime | None = None,
) -> list[PatternMatch]:
    """Detect rectangle ranges and confirmed range breaks."""
    df = _prepare_frame(data)
    if df is None:
        return []
    wanted = set(RANGE_TYPES[1:]) if pattern_type == RANGE_TYPES[0] else {pattern_type}
    candidate = _rectangle_candidate(df, wanted)
    if candidate is None:
        return []
    match = candidate_to_match(
        df,
        candidate,
        symbol=symbol,
        company_name=company_name,
        exchange=exchange,
        timeframe=timeframe,
        zone_result=zone_result,
        detected_at=detected_at or datetime.now(),
    )
    return [match] if match.confidence_score >= 55.0 else []


def _rectangle_candidate(df: pd.DataFrame, wanted: set[str]) -> PatternCandidate | None:
    if len(df) < 50:
        return None
    setup = df.iloc[:-1].tail(44)
    if len(setup) < 35:
        return None
    current = float(df["Close"].iloc[-1])
    resistance = float(setup["High"].quantile(0.97))
    support = float(setup["Low"].quantile(0.03))
    if resistance <= support or current <= 0:
        return None
    range_pct = (resistance - support) / current * 100
    if range_pct < 4 or range_pct > 28:
        return None

    tol = max(current * 0.018, (resistance - support) * 0.08)
    high_touches = setup[setup["High"] >= resistance - tol]
    low_touches = setup[setup["Low"] <= support + tol]
    if len(high_touches) < 2 or len(low_touches) < 2:
        return None

    breakout = current > resistance * (1.0 + BREAK_BUFFER)
    breakdown = current < support * (1.0 - BREAK_BUFFER)
    bullish_retest = held_retest(df, resistance, "long")
    bearish_retest = held_retest(df, support, "short")

    if breakout or bullish_retest:
        result_type = BULL_RECTANGLE_TYPE
        direction = "long"
    elif breakdown or bearish_retest:
        result_type = BEAR_RECTANGLE_TYPE
        direction = "short"
    else:
        result_type = RECTANGLE_RANGE_TYPE
        top_gap = abs(resistance - current) / current * 100
        bottom_gap = abs(current - support) / current * 100
        direction = "long" if top_gap <= bottom_gap else "short"

    all_range_types = {RECTANGLE_RANGE_TYPE, BULL_RECTANGLE_TYPE, BEAR_RECTANGLE_TYPE}
    if result_type not in wanted and not all_range_types.issubset(wanted):
        return None

    high_spread = (
        float(high_touches["High"].max()) - float(high_touches["High"].min())
    ) / max(resistance, 1.0)
    low_spread = (
        float(low_touches["Low"].max()) - float(low_touches["Low"].min())
    ) / max(support, 1.0)
    flat_score = 1.0 - min(1.0, high_spread / 0.045)
    flat_score += 1.0 - min(1.0, low_spread / 0.045)
    quality = max(
        0.0,
        min(1.0, 0.45 + flat_score * 0.22 + min(len(high_touches) + len(low_touches), 8) * 0.035),
    )

    notes = ["Price has respected a horizontal support/resistance range."]
    if bullish_retest:
        notes.append("A bullish breakout retest is holding near former resistance.")
    if bearish_retest:
        notes.append("A bearish breakdown retest is holding near former support.")

    return PatternCandidate(
        pattern_family=RANGE_FAMILY,
        pattern_type=result_type,
        upper_m=0.0,
        upper_b=resistance,
        lower_m=0.0,
        lower_b=support,
        start_x=len(df) - len(setup) - 1,
        end_x=len(df) - 1,
        quality=quality,
        direction=direction,
        notes=notes,
        swing_highs=[point_from_label(df, idx, resistance) for idx in high_touches.index[-3:]],
        swing_lows=[point_from_label(df, idx, support) for idx in low_touches.index[-3:]],
        trigger_label="Range Trigger",
    )
