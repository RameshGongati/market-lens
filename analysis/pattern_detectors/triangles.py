"""Triangle pattern detection for the Pattern Scanner.

This detector looks for recent swing-high/swing-low structures that are
compressing into one of three triangle families:

* Symmetrical Triangle: lower highs plus higher lows.
* Ascending Triangle: flat highs plus rising lows.
* Descending Triangle: falling highs plus flat lows.

It is intentionally lightweight and deterministic so it can run across a
watchlist from Streamlit without adding another heavy dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Any

import numpy as np
import pandas as pd

from analysis.pattern_models import PatternMatch, PatternPoint
from utils.market_hours import get_current_ist_time

_MIN_CANDLES = 35
_LOOKBACK = 120
_PIVOT_ORDER = 2
_MIN_SWINGS = 3
_APEX_NEAR_THRESHOLD = 15.0
_BREAKOUT_BUFFER = 0.0025
_INSIDE_BUFFER = 0.015
_MAX_APEX_MULTIPLE = 2.0


@dataclass(frozen=True)
class _Swing:
    index: int
    timestamp: Any
    price: float


@dataclass(frozen=True)
class _Candidate:
    pattern_type: str
    upper_m: float
    upper_b: float
    lower_m: float
    lower_b: float
    apex_x: float
    quality: float
    high_swings: list[_Swing]
    low_swings: list[_Swing]
    notes: list[str]


def detect_triangle_patterns(
    data: pd.DataFrame,
    *,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    pattern_type: str = "All Triangle Patterns",
    zone_result: dict[str, Any] | None = None,
    detected_at: datetime | None = None,
) -> list[PatternMatch]:
    """Detect triangle patterns in an OHLCV dataframe.

    Args:
        data: OHLCV data with Open/High/Low/Close columns.
        symbol: Display ticker without exchange suffix.
        company_name: Company name for result rows.
        exchange: Exchange label.
        timeframe: User-facing timeframe label.
        pattern_type: One of the triangle type labels or "All Triangle Patterns".
        zone_result: Optional Demand/Supply analysis result for zone context.
        detected_at: Timestamp for deterministic tests.

    Returns:
        A list of detected matches, sorted by confidence descending.
    """
    df = _prepare_frame(data)
    if df is None:
        return []

    highs = _find_swings(df, "high", _PIVOT_ORDER)
    lows = _find_swings(df, "low", _PIVOT_ORDER)
    if len(highs) < _MIN_SWINGS or len(lows) < _MIN_SWINGS:
        return []

    wanted = (
        ["Symmetrical Triangle", "Ascending Triangle", "Descending Triangle"]
        if pattern_type == "All Triangle Patterns"
        else [pattern_type]
    )

    candidates: list[_Candidate] = []
    if "Symmetrical Triangle" in wanted:
        cand = _symmetrical_candidate(df, highs, lows)
        if cand:
            candidates.append(cand)
    if "Ascending Triangle" in wanted:
        cand = _ascending_candidate(df, highs, lows)
        if cand:
            candidates.append(cand)
    if "Descending Triangle" in wanted:
        cand = _descending_candidate(df, highs, lows)
        if cand:
            candidates.append(cand)

    matches = [
        _candidate_to_match(
            df,
            cand,
            symbol=symbol,
            company_name=company_name,
            exchange=exchange,
            timeframe=timeframe,
            zone_result=zone_result,
            detected_at=detected_at or datetime.now(),
        )
        for cand in candidates
    ]
    matches = [m for m in matches if m.confidence_score >= 55.0]
    matches.sort(key=lambda m: m.confidence_score, reverse=True)
    return matches


def _prepare_frame(data: pd.DataFrame) -> pd.DataFrame | None:
    required = {"Open", "High", "Low", "Close"}
    if data is None or data.empty or not required.issubset(data.columns):
        return None
    df = _drop_incomplete_latest_bar(data).tail(_LOOKBACK).copy()
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df]
    df = df[cols].dropna(subset=["Open", "High", "Low", "Close"])
    if len(df) < _MIN_CANDLES:
        return None
    if "Volume" not in df:
        df["Volume"] = 0
    return df


def _drop_incomplete_latest_bar(data: pd.DataFrame) -> pd.DataFrame:
    """Drop the still-forming latest bar during market hours.

    Mirrors the demand/supply scanner's defensive treatment of today's
    candle: a live bar can briefly close beyond a trendline and then settle
    back inside by the market close.
    """
    if not isinstance(data.index, pd.DatetimeIndex) or data.empty:
        return data
    try:
        now = get_current_ist_time()
        if data.index[-1].date() >= now.date() and now.hour < 16:
            return data.iloc[:-1]
    except Exception:
        return data
    return data


def _find_swings(df: pd.DataFrame, side: str, order: int) -> list[_Swing]:
    col = "High" if side == "high" else "Low"
    vals = df[col].astype(float).to_numpy()
    swings: list[_Swing] = []
    for idx in range(order, len(df) - order):
        window = vals[idx - order : idx + order + 1]
        price = float(vals[idx])
        if side == "high" and price >= float(window.max()):
            if price > float(window[:order].max()) and price >= float(window[order + 1 :].max()):
                swings.append(_Swing(idx, df.index[idx], price))
        elif side == "low" and price <= float(window.min()):
            if price < float(window[:order].min()) and price <= float(window[order + 1 :].min()):
                swings.append(_Swing(idx, df.index[idx], price))
    return swings[-6:]


def _fit_line(swings: list[_Swing]) -> tuple[float, float]:
    xs = np.array([s.index for s in swings], dtype=float)
    ys = np.array([s.price for s in swings], dtype=float)
    if len(swings) == 1:
        return 0.0, float(ys[0])
    m, b = np.polyfit(xs, ys, 1)
    return float(m), float(b)


def _line_value(m: float, b: float, x: float) -> float:
    return float(m * x + b)


def _recent_swings(swings: list[_Swing]) -> list[_Swing]:
    return swings[-4:] if len(swings) >= 4 else swings


def _seq_score(values: list[float], direction: str, tolerance_pct: float = 0.004) -> float:
    if len(values) < 2:
        return 0.0
    avg = max(float(np.mean(values)), 1.0)
    ok = 0
    for prev, cur in zip(values, values[1:]):
        tol = avg * tolerance_pct
        if direction == "down" and cur <= prev + tol:
            ok += 1
        elif direction == "up" and cur >= prev - tol:
            ok += 1
    return ok / (len(values) - 1)


def _flat_score(values: list[float], tolerance_pct: float = 0.035) -> float:
    if not values:
        return 0.0
    avg = max(float(np.mean(values)), 1.0)
    spread = (max(values) - min(values)) / avg
    return max(0.0, min(1.0, 1.0 - spread / tolerance_pct))


def _change_pct(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = max(float(np.mean(values)), 1.0)
    return (values[-1] - values[0]) / avg * 100.0


def _intersect(upper_m: float, upper_b: float, lower_m: float, lower_b: float) -> float | None:
    denom = upper_m - lower_m
    if abs(denom) < 1e-9:
        return None
    x = (lower_b - upper_b) / denom
    return float(x) if math.isfinite(x) else None


def _validate_candidate(
    df: pd.DataFrame,
    pattern_type: str,
    upper_m: float,
    upper_b: float,
    lower_m: float,
    lower_b: float,
    high_swings: list[_Swing],
    low_swings: list[_Swing],
    quality: float,
    notes: list[str],
) -> _Candidate | None:
    start_x = min(high_swings[0].index, low_swings[0].index)
    current_x = len(df) - 1
    apex_x = _intersect(upper_m, upper_b, lower_m, lower_b)
    if apex_x is None:
        return None

    upper_start = _line_value(upper_m, upper_b, start_x)
    lower_start = _line_value(lower_m, lower_b, start_x)
    upper_now = _line_value(upper_m, upper_b, current_x)
    lower_now = _line_value(lower_m, lower_b, current_x)
    start_width = upper_start - lower_start
    current_width = upper_now - lower_now
    if start_width <= 0 or current_width <= 0:
        return None

    span = max(current_x - start_x, 1)
    max_future = current_x + max(12.0, span * _MAX_APEX_MULTIPLE)
    if apex_x <= current_x - 4 or apex_x > max_future:
        return None

    contraction = 1.0 - (current_width / start_width)
    if contraction < 0.18:
        return None

    close = float(df["Close"].iloc[-1])
    buffer = max(close, 1.0) * _INSIDE_BUFFER
    inside = lower_now - buffer <= close <= upper_now + buffer
    breakout = close > upper_now * (1.0 + _BREAKOUT_BUFFER)
    breakdown = close < lower_now * (1.0 - _BREAKOUT_BUFFER)
    if not (inside or breakout or breakdown):
        return None

    quality = min(1.0, quality + max(0.0, contraction) * 0.25)
    return _Candidate(
        pattern_type=pattern_type,
        upper_m=upper_m,
        upper_b=upper_b,
        lower_m=lower_m,
        lower_b=lower_b,
        apex_x=apex_x,
        quality=quality,
        high_swings=high_swings,
        low_swings=low_swings,
        notes=notes,
    )


def _symmetrical_candidate(
    df: pd.DataFrame, highs: list[_Swing], lows: list[_Swing]
) -> _Candidate | None:
    hs = _recent_swings(highs)
    ls = _recent_swings(lows)
    hv = [s.price for s in hs]
    lv = [s.price for s in ls]
    down = _seq_score(hv, "down")
    up = _seq_score(lv, "up")
    if down < 0.62 or up < 0.62:
        return None
    if _change_pct(hv) > -0.6 or _change_pct(lv) < 0.6:
        return None
    upper_m, upper_b = _fit_line(hs)
    lower_m, lower_b = _fit_line(ls)
    if upper_m >= 0 or lower_m <= 0:
        return None
    return _validate_candidate(
        df,
        "Symmetrical Triangle",
        upper_m,
        upper_b,
        lower_m,
        lower_b,
        hs,
        ls,
        (down + up) / 2,
        ["Lower highs and higher lows are compressing price."],
    )


def _ascending_candidate(
    df: pd.DataFrame, highs: list[_Swing], lows: list[_Swing]
) -> _Candidate | None:
    hs = _recent_swings(highs)
    ls = _recent_swings(lows)
    hv = [s.price for s in hs]
    lv = [s.price for s in ls]
    flat = _flat_score(hv)
    up = _seq_score(lv, "up")
    if flat < 0.35 or up < 0.62 or _change_pct(lv) < 0.6:
        return None
    upper_m, upper_b = 0.0, float(np.mean(hv))
    lower_m, lower_b = _fit_line(ls)
    if lower_m <= 0:
        return None
    return _validate_candidate(
        df,
        "Ascending Triangle",
        upper_m,
        upper_b,
        lower_m,
        lower_b,
        hs,
        ls,
        (flat + up) / 2,
        ["Repeated flat resistance with rising lows."],
    )


def _descending_candidate(
    df: pd.DataFrame, highs: list[_Swing], lows: list[_Swing]
) -> _Candidate | None:
    hs = _recent_swings(highs)
    ls = _recent_swings(lows)
    hv = [s.price for s in hs]
    lv = [s.price for s in ls]
    down = _seq_score(hv, "down")
    flat = _flat_score(lv)
    if down < 0.62 or flat < 0.35 or _change_pct(hv) > -0.6:
        return None
    upper_m, upper_b = _fit_line(hs)
    lower_m, lower_b = 0.0, float(np.mean(lv))
    if upper_m >= 0:
        return None
    return _validate_candidate(
        df,
        "Descending Triangle",
        upper_m,
        upper_b,
        lower_m,
        lower_b,
        hs,
        ls,
        (down + flat) / 2,
        ["Falling highs are pressing into a flat base."],
    )


def _candidate_to_match(
    df: pd.DataFrame,
    candidate: _Candidate,
    *,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    zone_result: dict[str, Any] | None,
    detected_at: datetime,
) -> PatternMatch:
    current_x = len(df) - 1
    start_x = min(candidate.high_swings[0].index, candidate.low_swings[0].index)
    upper_now = _line_value(candidate.upper_m, candidate.upper_b, current_x)
    lower_now = _line_value(candidate.lower_m, candidate.lower_b, current_x)
    current_price = float(df["Close"].iloc[-1])
    breakout = current_price > upper_now * (1.0 + _BREAKOUT_BUFFER)
    breakdown = current_price < lower_now * (1.0 - _BREAKOUT_BUFFER)

    total = max(candidate.apex_x - start_x, 1.0)
    remaining = max(candidate.apex_x - current_x, 0.0)
    apex_proximity = max(0.0, min(100.0, remaining / total * 100.0))
    if breakout or breakdown:
        stage = "Breakout Confirmed"
    elif apex_proximity <= _APEX_NEAR_THRESHOLD:
        stage = "Near Apex"
    else:
        stage = "Forming"

    latest_touch = _latest_touch_index(df, candidate, upper_now, lower_now)
    freshness = max(0, current_x - latest_touch)
    volume_contracting, vol_ratio = _volume_contraction(df)
    nearest_demand = (zone_result or {}).get("nearest_demand")
    nearest_supply = (zone_result or {}).get("nearest_supply")
    zone_context = _zone_context(current_price, nearest_demand, nearest_supply)
    direction = _risk_direction(candidate.pattern_type, breakout, breakdown)
    risk_reward = _risk_reward(current_price, upper_now, lower_now, candidate, direction)
    bias = _breakout_bias(candidate.pattern_type, breakout, breakdown, zone_context)
    confidence = _confidence(
        candidate,
        apex_proximity=apex_proximity,
        volume_contracting=volume_contracting,
        zone_context=zone_context,
        breakout=breakout,
        breakdown=breakdown,
        current_price=current_price,
        upper_now=upper_now,
        lower_now=lower_now,
    )

    line_start = min(candidate.high_swings[0].index, candidate.low_swings[0].index)
    line_end = max(candidate.apex_x, current_x)

    notes = [
        *candidate.notes,
        "Breakout direction is confirmed only after a candle closes outside the pattern.",
    ]
    if volume_contracting:
        notes.append("Recent volume is contracting versus the prior window.")
    if zone_context != "Between Zones":
        notes.append(f"Price has {zone_context.lower()} context.")

    return PatternMatch(
        symbol=symbol,
        company_name=company_name,
        exchange=exchange,
        timeframe=timeframe,
        pattern_family="Triangle Patterns",
        pattern_type=candidate.pattern_type,
        stage=stage,
        detected_at=detected_at,
        freshness_candles=freshness,
        confidence_score=confidence,
        apex_proximity=apex_proximity,
        breakout_bias=bias,
        zone_context=zone_context,
        volume_contraction=volume_contracting,
        upper_trendline_points=_line_points(df, candidate.upper_m, candidate.upper_b, line_start, line_end),
        lower_trendline_points=_line_points(df, candidate.lower_m, candidate.lower_b, line_start, line_end),
        apex_point=_point_for_x(df, candidate.apex_x, _line_value(candidate.upper_m, candidate.upper_b, candidate.apex_x)),
        breakout_level=upper_now,
        breakdown_level=lower_now,
        nearest_demand_zone=nearest_demand,
        nearest_supply_zone=nearest_supply,
        risk_reward=risk_reward,
        notes=notes,
        current_price=current_price,
        volume_contraction_ratio=vol_ratio,
        metadata={
            "swing_highs": [PatternPoint(s.index, s.timestamp, s.price) for s in candidate.high_swings],
            "swing_lows": [PatternPoint(s.index, s.timestamp, s.price) for s in candidate.low_swings],
            "upper_slope": candidate.upper_m,
            "lower_slope": candidate.lower_m,
            "start_index": start_x,
        },
    )


def _latest_touch_index(
    df: pd.DataFrame, candidate: _Candidate, upper_now: float, lower_now: float
) -> int:
    latest = max(candidate.high_swings[-1].index, candidate.low_swings[-1].index)
    tail_start = max(0, len(df) - 6)
    for idx in range(tail_start, len(df)):
        upper = _line_value(candidate.upper_m, candidate.upper_b, idx)
        lower = _line_value(candidate.lower_m, candidate.lower_b, idx)
        close = float(df["Close"].iloc[idx])
        high = float(df["High"].iloc[idx])
        low = float(df["Low"].iloc[idx])
        tol = max(close, 1.0) * 0.01
        if abs(high - upper) <= tol or abs(low - lower) <= tol or close > upper_now or close < lower_now:
            latest = idx
    return latest


def _volume_contraction(df: pd.DataFrame) -> tuple[bool, float]:
    if "Volume" not in df or len(df) < 25:
        return False, 1.0
    vol = df["Volume"].astype(float).replace(0, np.nan).dropna()
    if len(vol) < 25:
        return False, 1.0
    recent = float(vol.tail(8).mean())
    prior = float(vol.iloc[-28:-8].mean())
    if prior <= 0 or not math.isfinite(prior):
        return False, 1.0
    ratio = recent / prior
    return ratio <= 0.9, ratio


def _zone_context(
    current_price: float,
    nearest_demand: dict[str, Any] | None,
    nearest_supply: dict[str, Any] | None,
) -> str:
    if current_price <= 0:
        return "Between Zones"

    def _gap(zone: dict[str, Any] | None) -> float | None:
        if not zone or not zone.get("proximal"):
            return None
        top = max(float(zone["proximal"]), float(zone["distal"]))
        bottom = min(float(zone["proximal"]), float(zone["distal"]))
        if bottom <= current_price <= top:
            return 0.0
        return min(abs(current_price - top), abs(current_price - bottom)) / current_price

    demand_gap = _gap(nearest_demand)
    supply_gap = _gap(nearest_supply)
    demand_near = demand_gap is not None and demand_gap <= 0.035
    supply_near = supply_gap is not None and supply_gap <= 0.035
    if demand_near and (not supply_near or demand_gap <= (supply_gap or 99)):
        return "Near Demand Zone"
    if supply_near:
        return "Near Supply Zone"
    return "Between Zones"


def _risk_reward(
    current_price: float,
    upper_now: float,
    lower_now: float,
    candidate: _Candidate,
    direction: str | None,
) -> float | None:
    if current_price <= 0 or upper_now <= lower_now or direction is None:
        return None
    start_x = min(candidate.high_swings[0].index, candidate.low_swings[0].index)
    height = _line_value(candidate.upper_m, candidate.upper_b, start_x) - _line_value(
        candidate.lower_m, candidate.lower_b, start_x
    )
    projection = max(height * 0.65, 0.0)
    if projection <= 0:
        return None
    if direction == "short":
        stop = upper_now - current_price
        target = current_price - (lower_now - projection)
    else:
        stop = current_price - lower_now
        target = upper_now + projection - current_price
    if stop <= 0 or target <= 0:
        return None
    rr = target / stop
    return round(rr, 2) if math.isfinite(rr) else None


def _risk_direction(pattern_type: str, breakout: bool, breakdown: bool) -> str | None:
    """Return the side whose setup can be measured for risk/reward."""
    if breakout:
        return "long"
    if breakdown:
        return "short"
    if pattern_type == "Ascending Triangle":
        return "long"
    if pattern_type == "Descending Triangle":
        return "short"
    return None


def _breakout_bias(
    pattern_type: str,
    breakout: bool,
    breakdown: bool,
    zone_context: str,
) -> str:
    if breakout:
        return "Up Break"
    if breakdown:
        return "Down Break"
    if pattern_type == "Ascending Triangle":
        return "Bullish Pressure"
    if pattern_type == "Descending Triangle":
        return "Bearish Pressure"
    if zone_context == "Near Demand Zone":
        return "Neutral Until Break"
    if zone_context == "Near Supply Zone":
        return "Neutral Until Break"
    return "Neutral Until Break"


def _confidence(
    candidate: _Candidate,
    *,
    apex_proximity: float,
    volume_contracting: bool,
    zone_context: str,
    breakout: bool,
    breakdown: bool,
    current_price: float,
    upper_now: float,
    lower_now: float,
) -> float:
    score = 50.0 + candidate.quality * 24.0
    if volume_contracting:
        score += 7.0
    if zone_context in ("Near Demand Zone", "Near Supply Zone"):
        score += 6.0
    if breakout or breakdown:
        score += 8.0
    elif apex_proximity <= _APEX_NEAR_THRESHOLD:
        score += 6.0
    elif apex_proximity <= 35:
        score += 3.0
    width = max(upper_now - lower_now, 0.01)
    center = (upper_now + lower_now) / 2.0
    center_penalty = min(10.0, abs(current_price - center) / width * 8.0)
    score -= center_penalty
    return round(max(0.0, min(100.0, score)), 1)


def _point_for_x(df: pd.DataFrame, x: float, price: float) -> PatternPoint:
    idx = int(round(x))
    timestamp = _timestamp_for_x(df, x)
    return PatternPoint(idx, timestamp, round(float(price), 2))


def _line_points(
    df: pd.DataFrame, m: float, b: float, start_x: float, end_x: float
) -> list[PatternPoint]:
    return [
        _point_for_x(df, start_x, _line_value(m, b, start_x)),
        _point_for_x(df, end_x, _line_value(m, b, end_x)),
    ]


def _timestamp_for_x(df: pd.DataFrame, x: float) -> Any:
    if x <= len(df) - 1:
        return df.index[max(0, min(len(df) - 1, int(round(x))))]
    last = df.index[-1]
    if len(df.index) < 2:
        return last
    try:
        step = df.index[-1] - df.index[-2]
        return last + step * int(math.ceil(x - (len(df) - 1)))
    except Exception:
        return last
