"""VCP / Tight Base pattern detection."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from analysis.pattern_detectors.pattern_types import VCP_FAMILY, VCP_TYPE
from analysis.pattern_detectors.shared import PatternCandidate, candidate_to_match, point_from_label
from analysis.pattern_detectors.triangles import _prepare_frame, _volume_contraction
from analysis.pattern_models import PatternMatch


def detect_vcp_patterns(
    data: pd.DataFrame,
    *,
    symbol: str,
    company_name: str,
    exchange: str,
    timeframe: str,
    zone_result: dict[str, Any] | None = None,
    detected_at: datetime | None = None,
) -> list[PatternMatch]:
    """Detect VCP / Tight Base setups."""
    df = _prepare_frame(data)
    if df is None:
        return []
    candidate = _vcp_candidate(df)
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


def _vcp_candidate(df: pd.DataFrame) -> PatternCandidate | None:
    if len(df) < 55:
        return None
    base = df.tail(48)
    chunks = [base.iloc[i : i + 12] for i in range(0, 48, 12)]
    ranges = [
        (float(chunk["High"].max()) - float(chunk["Low"].min()))
        / max(float(chunk["Close"].mean()), 1.0)
        for chunk in chunks
    ]
    if ranges[-1] > ranges[0] * 0.72:
        return None
    if sum(cur <= prev * 1.12 for prev, cur in zip(ranges, ranges[1:])) < 2:
        return None

    setup = df.iloc[:-1].tail(35)
    if len(setup) < 24:
        return None
    breakout_level = float(setup["High"].max())
    support_level = float(setup["Low"].min())
    current = float(df["Close"].iloc[-1])
    if breakout_level <= support_level or current <= 0:
        return None
    width_pct = (breakout_level - support_level) / current * 100
    if width_pct > 18:
        return None

    ema20 = df["Close"].astype(float).ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = df["Close"].astype(float).ewm(span=50, adjust=False).mean().iloc[-1]
    trend_score = 1.0 if current >= ema50 and ema20 >= ema50 * 0.985 else 0.65
    contraction_score = min(1.0, (ranges[0] - ranges[-1]) / max(ranges[0], 0.001))
    tight_score = max(0.0, min(1.0, 1.0 - width_pct / 18.0))
    volume_contracting, vol_ratio = _volume_contraction(df)
    quality = 0.35 * contraction_score + 0.35 * tight_score + 0.2 * trend_score
    if volume_contracting:
        quality += 0.1

    start_x = len(df) - len(setup) - 1
    return PatternCandidate(
        pattern_family=VCP_FAMILY,
        pattern_type=VCP_TYPE,
        upper_m=0.0,
        upper_b=breakout_level,
        lower_m=0.0,
        lower_b=support_level,
        start_x=start_x,
        end_x=len(df) - 1,
        quality=min(1.0, quality),
        direction="long",
        notes=[
            "Volatility is contracting into a tight base.",
            "Breakout is only confirmed after price clears the pivot area.",
        ],
        swing_highs=[point_from_label(df, setup["High"].idxmax(), breakout_level)],
        swing_lows=[point_from_label(df, setup["Low"].idxmin(), support_level)],
        trigger_label="Pivot",
        volume_contraction_override=volume_contracting,
        volume_ratio_override=vol_ratio,
    )
