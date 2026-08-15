"""Triangle detector tests for the Pattern Scanner."""

from __future__ import annotations

from datetime import datetime
import math

import pandas as pd

import analysis.pattern_detectors.triangles as triangles
from analysis.pattern_models import PatternMatch
from analysis.pattern_detectors.triangles import detect_triangle_patterns


def _triangle_frame(kind: str, n: int = 72, breakout: bool = False) -> pd.DataFrame:
    """Synthetic but realistic oscillation inside triangle boundaries."""
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    rows: list[list[float]] = []
    prev = 100.0
    for idx in range(n):
        if kind == "ascending":
            upper = 120.0
            lower = 82.0 + idx * 0.38
        elif kind == "descending":
            upper = 120.0 - idx * 0.38
            lower = 82.0
        else:
            upper = 121.0 - idx * 0.28
            lower = 81.0 + idx * 0.28

        phase = math.sin(2 * math.pi * idx / 8)
        close = (upper + lower) / 2 + phase * (upper - lower) * 0.42
        if breakout and idx == n - 1:
            close = lower * 0.985 if kind == "descending" else upper * 1.015
        open_ = prev
        high = max(open_, close) + 0.8
        low = min(open_, close) - 0.8
        volume = 1_000_000 - idx * 8_000
        rows.append([open_, high, low, close, volume])
        prev = close
    return pd.DataFrame(rows, index=dates, columns=["Open", "High", "Low", "Close", "Volume"])


def test_detects_symmetrical_triangle_near_apex() -> None:
    matches = detect_triangle_patterns(
        _triangle_frame("symmetrical"),
        symbol="TEST",
        company_name="Test Limited",
        exchange="NSE",
        timeframe="Daily",
        detected_at=datetime(2025, 3, 15),
    )

    assert matches
    best = matches[0]
    assert best.pattern_type == "Symmetrical Triangle"
    assert best.stage == "Near Apex"
    assert best.breakout_bias == "Neutral Until Break"
    assert best.apex_proximity <= 15
    assert best.volume_contraction is True

    restored = PatternMatch.from_dict(best.to_dict())
    assert restored.symbol == best.symbol
    assert restored.pattern_type == best.pattern_type
    assert restored.apex_point.price == best.apex_point.price


def test_detects_ascending_triangle_breakout_confirmed() -> None:
    matches = detect_triangle_patterns(
        _triangle_frame("ascending", breakout=True),
        symbol="TEST",
        company_name="Test Limited",
        exchange="NSE",
        timeframe="Daily",
        pattern_type="Ascending Triangle",
        detected_at=datetime(2025, 3, 15),
    )

    assert matches
    best = matches[0]
    assert best.pattern_type == "Ascending Triangle"
    assert best.stage == "Breakout Confirmed"
    assert best.breakout_bias == "Up Break"
    assert best.breakout_level > best.breakdown_level


def test_detects_descending_triangle() -> None:
    matches = detect_triangle_patterns(
        _triangle_frame("descending"),
        symbol="TEST",
        company_name="Test Limited",
        exchange="NSE",
        timeframe="Daily",
        pattern_type="Descending Triangle",
        detected_at=datetime(2025, 3, 15),
    )

    assert matches
    best = matches[0]
    assert best.pattern_type == "Descending Triangle"
    assert best.stage == "Forming"
    assert best.breakout_bias == "Bearish Pressure"
    assert best.confidence_score >= 60


def test_down_break_risk_reward_uses_short_side() -> None:
    matches = detect_triangle_patterns(
        _triangle_frame("descending", breakout=True),
        symbol="TEST",
        company_name="Test Limited",
        exchange="NSE",
        timeframe="Daily",
        pattern_type="Descending Triangle",
        detected_at=datetime(2025, 3, 15),
    )

    assert matches
    best = matches[0]
    assert best.stage == "Breakout Confirmed"
    assert best.breakout_bias == "Down Break"
    assert best.risk_reward is not None
    assert 0 < best.risk_reward < 10


def test_in_progress_latest_bar_is_ignored_during_market_hours(monkeypatch) -> None:
    frame = _triangle_frame("ascending")
    last_close = float(frame["Close"].iloc[-1])
    frame.loc[pd.Timestamp("2025-03-14")] = [
        last_close,
        124.0,
        min(last_close, 123.0) - 0.8,
        123.0,
        100_000,
    ]
    monkeypatch.setattr(
        triangles,
        "get_current_ist_time",
        lambda: datetime(2025, 3, 14, 11, 0),
    )

    matches = triangles.detect_triangle_patterns(
        frame,
        symbol="TEST",
        company_name="Test Limited",
        exchange="NSE",
        timeframe="Daily",
        pattern_type="Ascending Triangle",
        detected_at=datetime(2025, 3, 14, 11, 0),
    )

    assert matches
    assert matches[0].stage == "Forming"
    assert matches[0].breakout_bias == "Bullish Pressure"
