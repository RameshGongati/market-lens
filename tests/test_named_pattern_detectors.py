"""Detector tests for VCP, ranges, flags, and double patterns."""

from __future__ import annotations

from datetime import datetime
import math

import pandas as pd

from analysis.pattern_detectors.double_patterns import detect_double_patterns
from analysis.pattern_detectors.flag_pennant import detect_flag_pennant_patterns
from analysis.pattern_detectors.range_breakouts import detect_range_breakout_patterns
from analysis.pattern_detectors.vcp import detect_vcp_patterns


def _frame(
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
) -> pd.DataFrame:
    dates = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    rows: list[list[float]] = []
    previous = closes[0]
    for idx, close in enumerate(closes):
        open_ = previous
        high = highs[idx] if highs else max(open_, close) * 1.01
        low = lows[idx] if lows else min(open_, close) * 0.99
        volume = volumes[idx] if volumes else 1_000_000
        rows.append([open_, high, low, close, volume])
        previous = close
    return pd.DataFrame(rows, index=dates, columns=["Open", "High", "Low", "Close", "Volume"])


def _vcp_frame() -> pd.DataFrame:
    closes: list[float] = []
    volumes: list[float] = []
    for idx in range(20):
        closes.append(80 + idx * 1.1)
        volumes.append(1_600_000)
    for chunk, (center, amplitude) in enumerate([(104, 8), (108, 5), (111, 3), (113, 1.5)]):
        for idx in range(12):
            closes.append(center + math.sin(2 * math.pi * idx / 6) * amplitude + idx * 0.05)
            volumes.append(1_200_000 - chunk * 180_000 - idx * 5_000)
    closes[-1] = max(closes[-10:]) * 0.995
    return _frame(closes, volumes=volumes)


def _rectangle_breakout_frame() -> pd.DataFrame:
    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    volumes: list[float] = []
    for idx in range(20):
        close = 95 + idx * 0.2
        closes.append(close)
        highs.append(close + 1)
        lows.append(close - 1)
        volumes.append(900_000)
    for idx in range(44):
        close = 100 + math.sin(2 * math.pi * idx / 8) * 8
        closes.append(close)
        highs.append(min(110, max(close, 100) + 2))
        lows.append(max(90, min(close, 100) - 2))
        volumes.append(800_000)
    closes.append(112)
    highs.append(114)
    lows.append(108)
    volumes.append(1_400_000)
    return _frame(closes, highs, lows, volumes)


def _flag_frame(direction: str) -> pd.DataFrame:
    closes: list[float] = []
    volumes: list[float] = []
    if direction == "bull":
        for idx in range(20):
            closes.append(80 + idx * 0.2)
            volumes.append(800_000)
        for idx in range(14):
            closes.append(84 + idx * 1.4)
            volumes.append(1_500_000)
        for idx in range(10):
            closes.append(104 - idx * 0.35 + math.sin(2 * math.pi * idx / 4) * 0.7)
            volumes.append(700_000)
        closes.append(max(closes[-10:]) + 1.2)
    else:
        for idx in range(20):
            closes.append(140 - idx * 0.1)
            volumes.append(800_000)
        for idx in range(14):
            closes.append(138 - idx * 1.5)
            volumes.append(1_500_000)
        for idx in range(10):
            closes.append(117 + idx * 0.35 + math.sin(2 * math.pi * idx / 4) * 0.7)
            volumes.append(700_000)
        closes.append(min(closes[-10:]) - 1.2)
    volumes.append(1_300_000)
    return _frame(closes, volumes=volumes)


def _double_frame(kind: str) -> pd.DataFrame:
    if kind == "bottom":
        pre = [150 - idx for idx in range(20)]
        pattern = [130, 125, 120, 114, 108, 102, 98, 95, 99, 104, 110, 116, 112, 106, 100, 96, 98, 104, 111, 117, 121]
    else:
        pre = [80 + idx for idx in range(20)]
        pattern = [100, 106, 112, 118, 122, 126, 123, 118, 112, 106, 110, 116, 122, 126, 124, 118, 112, 106, 100, 96]
    closes = pre + pattern
    highs = [close + 2 for close in closes]
    lows = [close - 2 for close in closes]
    volumes = [900_000 for _ in closes]
    return _frame(closes, highs, lows, volumes)


def test_detects_vcp_tight_base_near_trigger() -> None:
    matches = detect_vcp_patterns(
        _vcp_frame(),
        symbol="VCP",
        company_name="VCP Limited",
        exchange="NSE",
        timeframe="Daily",
        detected_at=datetime(2025, 3, 15),
    )

    assert matches
    best = matches[0]
    assert best.pattern_type == "VCP / Tight Base"
    assert best.stage == "Near Apex"
    assert best.breakout_bias == "Bullish Pressure"
    assert best.volume_contraction is True
    assert best.apex_proximity <= 3


def test_detects_rectangle_breakout_confirmed() -> None:
    matches = detect_range_breakout_patterns(
        _rectangle_breakout_frame(),
        symbol="RANGE",
        company_name="Range Limited",
        exchange="NSE",
        timeframe="Daily",
        detected_at=datetime(2025, 3, 15),
    )

    assert matches
    best = matches[0]
    assert best.pattern_type == "Bullish Rectangle Breakout"
    assert best.stage == "Breakout Confirmed"
    assert best.breakout_bias == "Up Break"


def test_detects_bull_and_bear_flags() -> None:
    bull = detect_flag_pennant_patterns(
        _flag_frame("bull"),
        symbol="BFLAG",
        company_name="Bull Flag Limited",
        exchange="NSE",
        timeframe="Daily",
        detected_at=datetime(2025, 3, 15),
    )
    bear = detect_flag_pennant_patterns(
        _flag_frame("bear"),
        symbol="SFLAG",
        company_name="Bear Flag Limited",
        exchange="NSE",
        timeframe="Daily",
        detected_at=datetime(2025, 3, 15),
    )

    assert bull and bull[0].pattern_type == "Bull Flag"
    assert bull[0].breakout_bias == "Up Break"
    assert bear and bear[0].pattern_type == "Bear Flag"
    assert bear[0].breakout_bias == "Down Break"


def test_detects_double_bottom_and_double_top_breaks() -> None:
    bottom = detect_double_patterns(
        _double_frame("bottom"),
        symbol="DBOT",
        company_name="Double Bottom Limited",
        exchange="NSE",
        timeframe="Daily",
        detected_at=datetime(2025, 3, 15),
    )
    top = detect_double_patterns(
        _double_frame("top"),
        symbol="DTOP",
        company_name="Double Top Limited",
        exchange="NSE",
        timeframe="Daily",
        detected_at=datetime(2025, 3, 15),
    )

    assert bottom and bottom[0].pattern_type == "Double Bottom"
    assert bottom[0].breakout_bias == "Up Break"
    assert top and top[0].pattern_type == "Double Top"
    assert top[0].breakout_bias == "Down Break"
