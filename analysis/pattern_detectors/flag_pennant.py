"""Bull/Bear Flag and Pennant continuation-pattern detection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from analysis.pattern_detectors.pattern_types import (
    BEAR_FLAG_TYPE,
    BEAR_PENNANT_TYPE,
    BULL_FLAG_TYPE,
    BULL_PENNANT_TYPE,
    FLAG_FAMILY,
    FLAG_TYPES,
)
from analysis.pattern_detectors.shared import (
    BREAK_BUFFER,
    PatternCandidate,
    candidate_to_match,
    fit_point_line,
    mean_volume,
)
from analysis.pattern_detectors.triangles import _prepare_frame
from analysis.pattern_models import PatternMatch, PatternPoint


def detect_flag_pennant_patterns(
    data: pd.DataFrame,
    *,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    pattern_type: str = FLAG_TYPES[0],
    zone_result: dict[str, Any] | None = None,
    detected_at: datetime | None = None,
) -> list[PatternMatch]:
    """Detect Bull/Bear Flag and Pennant continuation setups."""
    df = _prepare_frame(data)
    if df is None:
        return []
    wanted = set(FLAG_TYPES[1:]) if pattern_type == FLAG_TYPES[0] else {pattern_type}
    candidates = _flag_candidates(df, wanted)
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


def _flag_candidates(df: pd.DataFrame, wanted: set[str]) -> list[PatternCandidate]:
    candidates: list[PatternCandidate] = []
    for direction in ("long", "short"):
        best: PatternCandidate | None = None
        for cons_len in (8, 10, 12, 15):
            cand = _flag_candidate(df, cons_len, direction, wanted)
            if cand and (best is None or cand.quality > best.quality):
                best = cand
        if best:
            candidates.append(best)
    return candidates


def _flag_candidate(
    df: pd.DataFrame,
    cons_len: int,
    direction: str,
    wanted: set[str],
) -> PatternCandidate | None:
    pole_len = 14
    if len(df) < cons_len + pole_len + 5:
        return None
    setup = df.iloc[-cons_len - 1 : -1]
    pole = df.iloc[-cons_len - pole_len - 1 : -cons_len - 1]
    if len(setup) < cons_len or len(pole) < pole_len:
        return None

    pole_start = float(pole["Close"].iloc[0])
    pole_end = float(pole["Close"].iloc[-1])
    pole_return = (pole_end - pole_start) / max(pole_start, 1.0)
    pole_move = abs(pole_end - pole_start)
    current = float(df["Close"].iloc[-1])
    if direction == "long" and pole_return < 0.07:
        return None
    if direction == "short" and pole_return > -0.07:
        return None

    cons_high = float(setup["High"].max())
    cons_low = float(setup["Low"].min())
    cons_range = cons_high - cons_low
    if cons_range <= 0 or pole_move <= 0 or cons_range > pole_move * 0.72:
        return None
    if cons_range / max(current, 1.0) > 0.16:
        return None

    local_highs = [
        PatternPoint(idx, setup.index[idx], float(setup["High"].iloc[idx]))
        for idx in range(len(setup))
    ]
    local_lows = [
        PatternPoint(idx, setup.index[idx], float(setup["Low"].iloc[idx]))
        for idx in range(len(setup))
    ]
    high_m, high_b = fit_point_line(local_highs)
    low_m, low_b = fit_point_line(local_lows)
    avg_price = max(float(setup["Close"].mean()), 1.0)
    slope_norm = (high_m + low_m) / 2 / avg_price
    if direction == "long" and slope_norm > 0.006:
        return None
    if direction == "short" and slope_norm < -0.006:
        return None

    first_range = float(setup["High"].iloc[: cons_len // 2].max() - setup["Low"].iloc[: cons_len // 2].min())
    last_range = float(setup["High"].iloc[cons_len // 2 :].max() - setup["Low"].iloc[cons_len // 2 :].min())
    pennant = last_range <= first_range * 0.72
    if direction == "long":
        result_type = BULL_PENNANT_TYPE if pennant else BULL_FLAG_TYPE
        continuation_level = cons_high
        confirmed = current > continuation_level * (1.0 + BREAK_BUFFER)
    else:
        result_type = BEAR_PENNANT_TYPE if pennant else BEAR_FLAG_TYPE
        continuation_level = cons_low
        confirmed = current < continuation_level * (1.0 - BREAK_BUFFER)
    if result_type not in wanted:
        return None

    pole_vol = mean_volume(pole)
    setup_vol = mean_volume(setup)
    vol_ratio = setup_vol / pole_vol if pole_vol else 1.0
    volume_contracting = vol_ratio <= 0.9
    range_score = max(0.0, min(1.0, 1.0 - cons_range / max(pole_move * 0.72, 0.01)))
    volume_score = max(0.0, min(1.0, 1.0 - vol_ratio))
    quality = 0.48 + range_score * 0.24 + volume_score * 0.16
    if confirmed:
        quality += 0.08
    if pennant:
        quality += 0.04

    start_x = len(df) - cons_len - 1
    high_b_global = high_b - high_m * start_x
    low_b_global = low_b - low_m * start_x
    notes = [
        "A sharp impulse move is pausing in a compact continuation base.",
        "Continuation is confirmed only after price clears the flag boundary.",
    ]
    if volume_contracting:
        notes.append("Volume has cooled during the flag/pennant pause.")

    stride = max(1, cons_len // 3)
    return PatternCandidate(
        pattern_family=FLAG_FAMILY,
        pattern_type=result_type,
        upper_m=high_m,
        upper_b=high_b_global,
        lower_m=low_m,
        lower_b=low_b_global,
        start_x=start_x,
        end_x=len(df) - 1,
        quality=max(0.0, min(1.0, quality)),
        direction=direction,
        notes=notes,
        swing_highs=[
            PatternPoint(start_x + point.index, point.timestamp, point.price)
            for point in local_highs[::stride]
        ],
        swing_lows=[
            PatternPoint(start_x + point.index, point.timestamp, point.price)
            for point in local_lows[::stride]
        ],
        trigger_label="Continuation Trigger",
        volume_contraction_override=volume_contracting,
        volume_ratio_override=vol_ratio,
    )
