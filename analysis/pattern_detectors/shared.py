"""Shared helpers for named chart-pattern detectors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

import numpy as np
import pandas as pd

from analysis.pattern_models import PatternMatch, PatternPoint
from analysis.pattern_detectors.triangles import (
    _line_points,
    _line_value,
    _point_for_x,
    _volume_contraction,
    _zone_context,
)

BREAK_BUFFER = 0.003
NEAR_TRIGGER_PCT = 3.0


@dataclass(frozen=True)
class PatternCandidate:
    pattern_family: str
    pattern_type: str
    upper_m: float
    upper_b: float
    lower_m: float
    lower_b: float
    start_x: int
    end_x: int
    quality: float
    direction: str | None
    notes: list[str]
    swing_highs: list[PatternPoint]
    swing_lows: list[PatternPoint]
    trigger_label: str = "Trigger"
    volume_contraction_override: bool | None = None
    volume_ratio_override: float | None = None


def candidate_to_match(
    df: pd.DataFrame,
    candidate: PatternCandidate,
    *,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    zone_result: dict[str, Any] | None,
    detected_at: datetime,
) -> PatternMatch:
    current_x = len(df) - 1
    current_price = float(df["Close"].iloc[-1])
    upper_now = _line_value(candidate.upper_m, candidate.upper_b, current_x)
    lower_now = _line_value(candidate.lower_m, candidate.lower_b, current_x)
    breakout = current_price > upper_now * (1.0 + BREAK_BUFFER)
    breakdown = current_price < lower_now * (1.0 - BREAK_BUFFER)
    if candidate.direction == "long":
        trigger_distance = max(0.0, (upper_now - current_price) / max(current_price, 1.0) * 100)
        confirmed = breakout
    elif candidate.direction == "short":
        trigger_distance = max(0.0, (current_price - lower_now) / max(current_price, 1.0) * 100)
        confirmed = breakdown
    else:
        trigger_distance = min(
            abs(upper_now - current_price),
            abs(current_price - lower_now),
        ) / max(current_price, 1.0) * 100
        confirmed = breakout or breakdown

    if confirmed:
        stage = "Breakout Confirmed"
    elif trigger_distance <= NEAR_TRIGGER_PCT:
        stage = "Near Apex"
    else:
        stage = "Forming"

    volume_contracting, vol_ratio = _volume_contraction(df)
    if candidate.volume_contraction_override is not None:
        volume_contracting = candidate.volume_contraction_override
    if candidate.volume_ratio_override is not None:
        vol_ratio = candidate.volume_ratio_override

    nearest_demand = (zone_result or {}).get("nearest_demand")
    nearest_supply = (zone_result or {}).get("nearest_supply")
    zone_context = _zone_context(current_price, nearest_demand, nearest_supply)
    bias = breakout_bias(candidate, breakout, breakdown)
    confidence = confidence_score(
        candidate,
        stage=stage,
        trigger_distance=trigger_distance,
        volume_contracting=volume_contracting,
        zone_context=zone_context,
        breakout=breakout,
        breakdown=breakdown,
    )
    risk_reward = risk_reward_ratio(current_price, upper_now, lower_now, candidate.direction)
    freshness = freshness_candles(df, candidate, upper_now, lower_now, breakout, breakdown)

    line_end = max(candidate.end_x, current_x)
    notes = [*candidate.notes]
    if volume_contracting:
        notes.append("Recent volume supports the setup by contracting during consolidation.")
    if zone_context != "Between Zones":
        notes.append(f"Demand/supply context: {zone_context.lower()}.")

    trigger_price = upper_now if candidate.direction != "short" else lower_now
    return PatternMatch(
        symbol=symbol,
        company_name=company_name,
        exchange=exchange,
        timeframe=timeframe,
        pattern_family=candidate.pattern_family,
        pattern_type=candidate.pattern_type,
        stage=stage,
        detected_at=detected_at,
        freshness_candles=freshness,
        confidence_score=confidence,
        apex_proximity=round(min(100.0, trigger_distance), 1),
        breakout_bias=bias,
        zone_context=zone_context,
        volume_contraction=volume_contracting,
        upper_trendline_points=_line_points(df, candidate.upper_m, candidate.upper_b, candidate.start_x, line_end),
        lower_trendline_points=_line_points(df, candidate.lower_m, candidate.lower_b, candidate.start_x, line_end),
        apex_point=_point_for_x(df, current_x, trigger_price),
        breakout_level=upper_now,
        breakdown_level=lower_now,
        nearest_demand_zone=nearest_demand,
        nearest_supply_zone=nearest_supply,
        risk_reward=risk_reward,
        notes=notes,
        current_price=current_price,
        volume_contraction_ratio=vol_ratio,
        metadata={
            "swing_highs": candidate.swing_highs,
            "swing_lows": candidate.swing_lows,
            "upper_slope": candidate.upper_m,
            "lower_slope": candidate.lower_m,
            "start_index": candidate.start_x,
            "trigger_label": candidate.trigger_label,
            "direction": candidate.direction or "neutral",
        },
    )


def breakout_bias(candidate: PatternCandidate, breakout: bool, breakdown: bool) -> str:
    if breakout:
        return "Up Break"
    if breakdown:
        return "Down Break"
    if candidate.direction == "long":
        return "Bullish Pressure"
    if candidate.direction == "short":
        return "Bearish Pressure"
    return "Neutral Until Break"


def confidence_score(
    candidate: PatternCandidate,
    *,
    stage: str,
    trigger_distance: float,
    volume_contracting: bool,
    zone_context: str,
    breakout: bool,
    breakdown: bool,
) -> float:
    score = 50.0 + candidate.quality * 26.0
    if volume_contracting:
        score += 6.0
    if zone_context in ("Near Demand Zone", "Near Supply Zone"):
        score += 5.0
    if breakout or breakdown:
        score += 8.0
    elif stage == "Near Apex":
        score += 5.0
    score -= min(7.0, trigger_distance * 0.7)
    return round(max(0.0, min(100.0, score)), 1)


def risk_reward_ratio(
    current_price: float,
    upper_now: float,
    lower_now: float,
    direction: str | None,
) -> float | None:
    if current_price <= 0 or upper_now <= lower_now or direction is None:
        return None
    height = upper_now - lower_now
    if direction == "short":
        stop = upper_now - current_price
        target = current_price - (lower_now - height * 0.7)
    else:
        stop = current_price - lower_now
        target = upper_now + height * 0.7 - current_price
    if stop <= 0 or target <= 0:
        return None
    rr = target / stop
    return round(rr, 2) if math.isfinite(rr) else None


def freshness_candles(
    df: pd.DataFrame,
    candidate: PatternCandidate,
    upper_now: float,
    lower_now: float,
    breakout: bool,
    breakdown: bool,
) -> int:
    latest = max(candidate.start_x, *(p.index for p in candidate.swing_highs + candidate.swing_lows))
    tail_start = max(0, len(df) - 8)
    for idx in range(tail_start, len(df)):
        close = float(df["Close"].iloc[idx])
        high = float(df["High"].iloc[idx])
        low = float(df["Low"].iloc[idx])
        upper = _line_value(candidate.upper_m, candidate.upper_b, idx)
        lower = _line_value(candidate.lower_m, candidate.lower_b, idx)
        tol = max(close, 1.0) * 0.012
        if (
            abs(high - upper) <= tol
            or abs(low - lower) <= tol
            or close > upper_now
            or close < lower_now
            or breakout
            or breakdown
        ):
            latest = idx
    return max(0, len(df) - 1 - latest)


def held_retest(df: pd.DataFrame, level: float, direction: str) -> bool:
    tail = df.tail(8)
    if len(tail) < 5:
        return False
    tol = level * 0.018
    if direction == "long":
        had_break = bool((tail["Close"].iloc[:-1] > level * (1.0 + BREAK_BUFFER)).any())
        return had_break and float(tail["Low"].iloc[-1]) <= level + tol and float(tail["Close"].iloc[-1]) >= level
    had_break = bool((tail["Close"].iloc[:-1] < level * (1.0 - BREAK_BUFFER)).any())
    return had_break and float(tail["High"].iloc[-1]) >= level - tol and float(tail["Close"].iloc[-1]) <= level


def matching_pair(swings: list[Any], side: str) -> tuple[Any, Any] | None:
    recent = swings[-6:]
    best: tuple[Any, Any] | None = None
    best_score = 99.0
    for idx, first in enumerate(recent):
        for second in recent[idx + 1 :]:
            if second.index - first.index < 6:
                continue
            avg = max((first.price + second.price) / 2, 1.0)
            diff = abs(first.price - second.price) / avg
            if diff > 0.035:
                continue
            if side == "low" and second.price < first.price * 0.94:
                continue
            if side == "high" and second.price > first.price * 1.06:
                continue
            if diff < best_score:
                best = (first, second)
                best_score = diff
    return best


def fit_point_line(points: list[PatternPoint]) -> tuple[float, float]:
    xs = np.array([p.index for p in points], dtype=float)
    ys = np.array([p.price for p in points], dtype=float)
    if len(points) == 1:
        return 0.0, float(ys[0])
    m, b = np.polyfit(xs, ys, 1)
    return float(m), float(b)


def mean_volume(df: pd.DataFrame) -> float:
    if "Volume" not in df:
        return 0.0
    vol = df["Volume"].astype(float).replace(0, np.nan).dropna()
    if vol.empty:
        return 0.0
    return float(vol.mean())


def point_from_label(df: pd.DataFrame, label: Any, price: float) -> PatternPoint:
    try:
        idx = int(df.index.get_loc(label))
    except Exception:
        idx = len(df) - 1
    return PatternPoint(idx, label, round(float(price), 2))
