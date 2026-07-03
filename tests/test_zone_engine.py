"""Unit tests for the institutional demand/supply zone engine.

These tests hand-craft small OHLC sequences that deliberately produce
specific candle shapes / legin-base-legout structures, and assert the
zone engine classifies and scores them exactly per the documented
methodology (see analysis/zone_engine/*).
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from analysis.demand_supply import DemandSupplyAnalysis, _apply_trend_alignment
from utils.helpers import format_currency
from analysis.zone_engine.candles import classify_candle
from analysis.zone_engine.enhancers import ema20_confluence
from analysis.zone_engine.fibonacci import (
    SwingInfo,
    calculate_fib_levels,
    fib_confluence,
    find_recent_swing,
)
from analysis.zone_engine.filters import filter_zones
from analysis.zone_engine.models import Zone
from analysis.zone_engine.patterns import detect_zones
from analysis.zone_engine.scoring import (
    assess_closing_quality,
    confluence_rating,
    entry_recommendation,
    time_at_base_points,
)
from analysis.zone_engine.trend import detect_trend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from (open, high, low, close) tuples."""
    return pd.DataFrame(
        {
            "Open": [r[0] for r in rows],
            "High": [r[1] for r in rows],
            "Low": [r[2] for r in rows],
            "Close": [r[3] for r in rows],
            "Volume": [10_000] * len(rows),
        }
    )


def _make_zone(
    *,
    category: str = "demand",
    proximal: float = 100.0,
    distal: float = 95.0,
    odd_score: float = 7.0,
    times_tested: int = 0,
    zone_type: str | None = None,
) -> Zone:
    """Build a ``Zone`` directly with only the fields ``filter_zones`` cares
    about varying; everything else gets a plausible, fixed default.

    ``filter_zones`` operates purely on ``Zone`` attributes (category,
    proximal/distal, odd_score, times_tested, ...), so constructing zones
    directly gives the tests precise control without needing to hand-craft
    OHLC sequences that happen to produce specific scores/test counts.
    """
    if zone_type is None:
        zone_type = "DBR" if category == "demand" else "RBD"
    return Zone(
        zone_type=zone_type,
        category=category,
        proximal=proximal,
        distal=distal,
        proximal_exceptional=proximal,
        distal_exceptional=distal,
        base_start_idx=0,
        base_end_idx=1,
        legout_idx=2,
        num_base_candles=2,
        odd_score=odd_score,
        freshness_points=3.0 if times_tested == 0 else (1.5 if times_tested == 1 else 0.0),
        strength_points=2.0,
        time_points=2.0,
        times_tested=times_tested,
        zone_strength="Strong",
        entry_recommendation="Entry Type 1 (aggressive)",
        created_at_index=2,
        is_fresh=times_tested == 0,
    )


def _closes_df(closes: list[float]) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame from a list of closing prices, with
    Open == High == Low == Close — perfectly fine for trend/EMA helpers,
    which only ever read the ``Close`` column."""
    return _make_df([(c, c, c, c) for c in closes])


# ---------------------------------------------------------------------------
# Candle classification
# ---------------------------------------------------------------------------

def test_classify_candle_boring():
    """body_pct = 3/15 = 0.20 <= 0.50 -> boring, not exciting/strong."""
    info = classify_candle(open_=100, high=110, low=95, close=103)
    assert info["body_pct"] == pytest.approx(0.20)
    assert info["is_boring"] is True
    assert info["is_exciting"] is False
    assert info["is_strong"] is False
    assert info["direction"] == "bullish"


def test_classify_candle_exciting_not_strong():
    """body_pct = 9/15 = 0.60 -> exciting (>= 0.50) but not strong (< 0.80)."""
    info = classify_candle(open_=100, high=110, low=95, close=109)
    assert info["body_pct"] == pytest.approx(0.60)
    assert info["is_exciting"] is True
    assert info["is_strong"] is False
    assert info["is_boring"] is False
    assert info["direction"] == "bullish"


def test_classify_candle_exactly_at_threshold_is_exciting():
    """body_pct exactly 0.50 is the boundary and must classify as exciting (>=)."""
    # range = 10, body = 5 -> body_pct = 0.50
    info = classify_candle(open_=100, high=105, low=95, close=105)
    assert info["body_pct"] == pytest.approx(0.50)
    assert info["is_exciting"] is True
    assert info["is_strong"] is False
    assert info["is_boring"] is False


def test_classify_candle_strong():
    """body_pct = 12/12 = 1.00 -> strong exciting (>= 0.80), implies exciting."""
    info = classify_candle(open_=100, high=112, low=100, close=112)
    assert info["body_pct"] == pytest.approx(1.0)
    assert info["is_exciting"] is True
    assert info["is_strong"] is True
    assert info["is_boring"] is False
    assert info["direction"] == "bullish"


def test_classify_candle_just_below_threshold_is_boring():
    """body_pct just under 0.50 must be boring (no separate indecisive band)."""
    # range = 6.5, body = 3 -> body_pct = 3/6.5 ≈ 0.4615 (< 0.50)
    info = classify_candle(open_=100, high=106.5, low=100, close=103)
    assert info["body_pct"] < 0.50
    assert info["is_boring"] is True
    assert info["is_exciting"] is False


def test_classify_candle_doji_is_boring():
    """A doji (close == open) carries no conviction and is treated as boring."""
    info = classify_candle(open_=100, high=105, low=95, close=100)
    assert info["direction"] == "doji"
    assert info["body_pct"] == pytest.approx(0.0)
    assert info["is_boring"] is True
    assert info["is_exciting"] is False


def test_classify_candle_zero_range_guard():
    """A zero-range candle (high == low) must not raise ZeroDivisionError."""
    info = classify_candle(open_=100, high=100, low=100, close=100)
    assert info["body_pct"] == 0.0
    assert info["is_boring"] is True


# ---------------------------------------------------------------------------
# Clean DBR demand zone detection (Drop-Base-Rally)
# ---------------------------------------------------------------------------

# idx 0: legin  — bearish, exciting   (body_pct = 9/11 ≈ 0.818)
# idx 1: base 1 — boring              (body_pct = 1/5  = 0.20)
# idx 2: base 2 — boring              (body_pct = 1/7 ≈ 0.143)
# idx 3: legout — bullish, exciting, closes above the base range (114)
_DBR_ROWS = [
    (120, 121, 110, 111),   # legin (bearish, exciting)
    (111, 114, 109, 112),   # base candle 1 (boring)
    (112, 115, 108, 113),   # base candle 2 (boring)
    (114, 125, 113, 124),   # legout (bullish, exciting, clears base high=115... )
]


def test_clean_dbr_demand_zone_detected_with_correct_proximal_distal():
    df = _make_df(_DBR_ROWS)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zone_type == "DBR"
    assert zone.category == "demand"

    # Rule: M13 wick-to-wick marking (clean narrow base, ratio < 1.5) —
    #   proximal = highest HIGH of the base = max(114, 115) = 115
    #   distal   = lowest LOW of the base = min(109, 108) = 108
    assert zone.proximal == pytest.approx(115)
    assert zone.distal == pytest.approx(108)
    assert zone.proximal_marking == "Wick-to-Wick"
    assert zone.base_start_idx == 1
    assert zone.base_end_idx == 2
    assert zone.num_base_candles == 2
    assert zone.legout_idx == 3


# ---------------------------------------------------------------------------
# Clean RBD supply zone detection (Rally-Base-Drop)
# ---------------------------------------------------------------------------

# idx 0: legin  — bullish, exciting   (body_pct = 11/13 ≈ 0.846)
# idx 1: base 1 — boring              (body_pct = 1/5 = 0.20)
# idx 2: base 2 — boring              (body_pct = 1/5 = 0.20)
# idx 3: legout — bearish, exciting, closes below the base range (107)
_RBD_ROWS = [
    (100, 112, 99, 111),   # legin (bullish, exciting)
    (111, 113, 108, 110),  # base candle 1 (boring)
    (110, 112, 107, 109),  # base candle 2 (boring)
    (109, 110, 97, 98),    # legout (bearish, exciting, clears base low=107)
]


def test_clean_rbd_supply_zone_detected_with_correct_proximal_distal():
    df = _make_df(_RBD_ROWS)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zone_type == "RBD"
    assert zone.category == "supply"

    # Rule: M13 wick-to-wick marking (clean narrow base, ratio 1.5 = not > 1.5) —
    #   proximal = lowest LOW of the base = min(108, 107) = 107
    #   distal   = highest HIGH of the base = max(113, 112) = 113
    assert zone.proximal == pytest.approx(107)
    assert zone.distal == pytest.approx(113)
    assert zone.proximal_marking == "Wick-to-Wick"
    assert zone.base_start_idx == 1
    assert zone.base_end_idx == 2
    assert zone.num_base_candles == 2


# ---------------------------------------------------------------------------
# ODD score: fresh zone, 2 base candles, gap legout -> 3 + 2 + 2 = 7
# ---------------------------------------------------------------------------

# Same DBR shape as above but the legout candle GAPS away from the base
# (opens above the final base candle's high of 114) and nothing re-tests
# the zone afterwards.
_DBR_GAP_FRESH_ROWS = [
    (120, 121, 110, 111),   # legin (bearish, exciting)
    (111, 113, 109, 112),   # base candle 1 (boring)
    (112, 114, 108, 113),   # base candle 2 (boring) -> base high = 114
    (116, 126, 115, 125),   # legout opens at 116 > 114 -> GAP, bullish & exciting
]


def test_odd_score_fresh_zone_two_base_candles_gap_legout_scores_seven():
    df = _make_df(_DBR_GAP_FRESH_ROWS)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]

    assert zone.num_base_candles == 2
    assert zone.times_tested == 0
    assert zone.is_fresh is True

    # Rule: Freshness=3 (never tested) + Strength=2 (gap) + Time=2 (1-3 base candles)
    assert zone.freshness_points == pytest.approx(3.0)
    assert zone.strength_points == pytest.approx(2.0)
    assert zone.time_points == pytest.approx(2.0)
    assert zone.odd_score == pytest.approx(7.0)
    assert zone.entry_recommendation == "Entry Type 1 (aggressive)"


# ---------------------------------------------------------------------------
# M3: one complete enter+exit cycle = 1 test
# ---------------------------------------------------------------------------

# Same clean DBR structure (WTW proximal = 115, distal = 108) followed by
# candles where price enters and exits the zone once (one complete cycle).
_DBR_ONE_CYCLE_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> WTW proximal = 115, distal = 108
    (114, 125, 113, 124),   # 3: legout (bullish, exciting), no gap, single candle
    (124, 128, 120, 126),   # 4: away from zone   (low=120 > 115)
    (118, 120, 111, 119),   # 5: enters zone      (low=111 <= 115 -> activation touch)
    (119, 123, 116, 122),   # 6: exits proximal   (low=116 > 115 -> test #1)
    (118, 122, 117, 121),   # 7: stays away       (low=117 > 115)
]


def test_m3_single_cycle_counts_as_one_test():
    """GTF M3: one complete enter+exit-through-proximal cycle = 1 test.
    activation_touch is True (price entered the zone)."""
    df = _make_df(_DBR_ONE_CYCLE_ROWS)
    zones = detect_zones(df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    zone = dbr[0]

    assert zone.proximal == pytest.approx(115)
    assert zone.times_tested == 1
    assert zone.is_fresh is False
    assert zone.freshness_points == pytest.approx(1.5)
    assert zone.activation_touch is True


# ---------------------------------------------------------------------------
# "No Trade" recommendation when the total ODD score is below 5
# ---------------------------------------------------------------------------

# Same clean DBR structure (WTW proximal = 115, distal = 108), but the legout
# is a single, non-gapping exciting candle (strength = 1) and price
# completes three enter+exit cycles (M3: tests = 3, freshness = 0).
# 0 (freshness) + 1 (strength) + 2 (time) = 3.
_DBR_NO_TRADE_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> WTW proximal = 115, distal = 108
    (113, 126, 112, 125),   # 3: legout opens at 113 (<= base high 114 -> no gap)
    (124, 128, 120, 126),   # 4: away from zone (low=120 > 115)
    (118, 120, 111, 119),   # 5: enters zone (activation touch)
    (119, 123, 116, 122),   # 6: exits proximal (test #1)
    (117, 119, 109, 118),   # 7: enters zone
    (118, 122, 116, 120),   # 8: exits proximal (test #2)
    (117, 118, 110, 116),   # 9: enters zone
    (116, 121, 116, 120),   # 10: exits proximal (test #3)
]


def test_no_trade_recommendation_when_score_below_five():
    df = _make_df(_DBR_NO_TRADE_ROWS)
    zones = detect_zones(df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    zone = dbr[0]

    assert zone.times_tested == 3
    assert zone.freshness_points == pytest.approx(0.0)
    assert zone.strength_points == pytest.approx(1.0)
    assert zone.time_points == pytest.approx(2.0)
    assert zone.odd_score == pytest.approx(3.0)
    assert zone.odd_score < 5
    assert zone.entry_recommendation == "No Trade"


# ---------------------------------------------------------------------------
# M3: two complete enter+exit cycles = 2 tests
# ---------------------------------------------------------------------------

_DBR_TWO_CYCLES_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> WTW proximal = 115, distal = 108
    (114, 125, 113, 124),   # 3: legout (bullish, exciting), no gap
    (124, 128, 120, 126),   # 4: away from zone
    (118, 120, 111, 119),   # 5: enters zone (activation touch)
    (119, 123, 116, 122),   # 6: exits proximal (test #1)
    (117, 119, 109, 118),   # 7: enters zone
    (118, 122, 116, 120),   # 8: exits proximal (test #2)
]


def test_m3_two_cycles_count_as_two_tests():
    """GTF M3: two complete enter+exit cycles = 2 tests -> freshness 0."""
    df = _make_df(_DBR_TWO_CYCLES_ROWS)
    zones = detect_zones(df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    zone = dbr[0]
    assert zone.times_tested == 2
    assert zone.is_fresh is False
    assert zone.freshness_points == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# M3: zero returns = never touched -> fresh (3 points)
# ---------------------------------------------------------------------------

def test_m3_zero_returns_stays_fresh():
    """A zone with no returns at all is untouched — freshness = 3."""
    rows = [
        (120, 121, 110, 111),   # 0: legin (bearish, exciting)
        (111, 114, 109, 112),   # 1: base candle 1 (boring)
        (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal = 113
        (114, 125, 113, 124),   # 3: legout (bullish, exciting)
        (124, 128, 120, 126),   # 4: away (low=120 > 113)
        (126, 130, 118, 128),   # 5: away (low=118 > 113)
    ]
    df = _make_df(rows)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.times_tested == 0
    assert zone.is_fresh is True
    assert zone.freshness_points == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# M3 + M46: zone invalidation — CLOSE beyond distal (wick is noise)
# ---------------------------------------------------------------------------

def test_m46_demand_wick_below_distal_but_close_above_survives():
    """GTF M46: a demand zone whose distal is wicked through but the candle
    CLOSES above the distal line is NOT invalidated — the wick is noise
    (stop-hunt / spring), only the close counts."""
    rows = [
        (120, 121, 110, 111),   # 0: legin (bearish, exciting)
        (111, 114, 109, 112),   # 1: base candle 1 (boring)
        (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal=113, distal=108
        (114, 125, 113, 124),   # 3: legout (bullish, exciting)
        (124, 128, 120, 126),   # 4: away from zone
        (118, 120, 107, 119),   # 5: low=107 < distal=108 (wick breach), but close=119 > 108
    ]
    df = _make_df(rows)
    zones = detect_zones(df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1, "zone survives — wick through distal but close held"
    zone = dbr[0]
    assert zone.activation_touch is True
    assert zone.is_fresh is True


def test_m46_demand_close_below_distal_invalidated():
    """GTF M46: a demand zone where price CLOSES below the distal line
    is invalidated — this is a genuine breach, not just noise."""
    rows = [
        (120, 121, 110, 111),   # 0: legin (bearish, exciting)
        (111, 114, 109, 112),   # 1: base candle 1 (boring)
        (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal=113, distal=108
        (114, 125, 113, 124),   # 3: legout (bullish, exciting)
        (124, 128, 120, 126),   # 4: away from zone
        (110, 112, 106, 107),   # 5: close=107 < distal=108 → invalidated
    ]
    df = _make_df(rows)
    zones = detect_zones(df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 0, "zone invalidated — close below distal"


def test_m46_supply_wick_above_distal_but_close_below_survives():
    """GTF M46: a supply zone whose distal is wicked through but the candle
    CLOSES below the distal line is NOT invalidated."""
    rows = [
        (100, 109, 99, 108),    # 0: legin (bullish, exciting)
        (108, 111, 106, 107),   # 1: base candle 1 (boring)
        (107, 112, 105, 109),   # 2: base candle 2 (boring) -> proximal=107, distal=112
        (106, 107, 95, 96),     # 3: legout (bearish, exciting)
        (96, 98, 90, 92),       # 4: away from zone
        (100, 113, 99, 111),    # 5: high=113 > distal=112 (wick breach), but close=111 < 112
    ]
    df = _make_df(rows)
    zones = detect_zones(df)

    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 1, "zone survives — wick through distal but close held"
    zone = rbd[0]
    assert zone.activation_touch is True
    assert zone.is_fresh is True


def test_m46_supply_close_above_distal_invalidated():
    """GTF M46: a supply zone where price CLOSES above the distal line
    is invalidated."""
    rows = [
        (100, 109, 99, 108),    # 0: legin (bullish, exciting)
        (108, 111, 106, 107),   # 1: base candle 1 (boring)
        (107, 112, 105, 109),   # 2: base candle 2 (boring) -> proximal=107, distal=112
        (106, 107, 95, 96),     # 3: legout (bearish, exciting)
        (96, 98, 90, 92),       # 4: away from zone
        (100, 114, 99, 113),    # 5: close=113 > distal=112 → invalidated
    ]
    df = _make_df(rows)
    zones = detect_zones(df)

    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 0, "zone invalidated — close above distal"


def test_m46_close_exactly_at_distal_survives():
    """GTF M46: close exactly at the distal line means the level held —
    strict inequality required for invalidation."""
    rows = [
        (120, 121, 110, 111),   # 0: legin (bearish, exciting)
        (111, 114, 109, 112),   # 1: base candle 1 (boring)
        (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal=113, distal=108
        (114, 125, 113, 124),   # 3: legout (bullish, exciting)
        (124, 128, 120, 126),   # 4: away from zone
        (110, 112, 106, 108),   # 5: close=108 == distal=108 → survives (held the line)
    ]
    df = _make_df(rows)
    zones = detect_zones(df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1, "zone survives — close at distal means level held"


# ---------------------------------------------------------------------------
# M3: activation touch true when zone entered but no exit yet
# ---------------------------------------------------------------------------

def test_m3_activation_touch_without_complete_cycle():
    """GTF M3: price enters zone but data ends before exit -> activation_touch
    True, times_tested 0 (no complete cycle)."""
    rows = [
        (120, 121, 110, 111),   # 0: legin (bearish, exciting)
        (111, 114, 109, 112),   # 1: base candle 1 (boring)
        (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal = 113
        (114, 125, 113, 124),   # 3: legout (bullish, exciting)
        (124, 128, 120, 126),   # 4: away from zone
        (118, 120, 111, 119),   # 5: enters zone (low=111 <= 113), data ends while inside
    ]
    df = _make_df(rows)
    zones = detect_zones(df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    zone = dbr[0]
    assert zone.times_tested == 0
    assert zone.is_fresh is True
    assert zone.activation_touch is True
    assert zone.freshness_points == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Entry recommendation thresholds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "score, expected",
    [
        (7.0, "Entry Type 1 (aggressive)"),
        (6.0, "Entry Type 2/3 (confirmation)"),
        (5.5, "Entry Type 2/3 (confirmation)"),
        (5.0, "Entry Type 2/3 (confirmation)"),
        (4.5, "No Trade"),
        (3.0, "No Trade"),
        (0.0, "No Trade"),
    ],
)
def test_entry_recommendation_thresholds(score, expected):
    assert entry_recommendation(score) == expected


# ---------------------------------------------------------------------------
# Time-at-base scoring (GTF M28 — Episode 8 rule)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("num_candles, expected", [
    (1, 2.0),
    (2, 2.0),
    (3, 2.0),
])
def test_time_at_base_short_base_scores_two(num_candles, expected):
    assert time_at_base_points(num_candles) == pytest.approx(expected)


@pytest.mark.parametrize("num_candles, expected", [
    (4, 1.0),
    (5, 1.0),
])
def test_time_at_base_medium_base_scores_one(num_candles, expected):
    assert time_at_base_points(num_candles) == pytest.approx(expected)


@pytest.mark.parametrize("num_candles, expected", [
    (6, 0.0),
    (7, 0.0),
    (10, 0.0),
])
def test_time_at_base_long_base_scores_zero(num_candles, expected):
    """M28: 6+ base candles score 0 (Episode 8 rule — was 4-6=1, >6=0)."""
    assert time_at_base_points(num_candles) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Graceful handling of empty/short data
# ---------------------------------------------------------------------------

def test_detect_zones_handles_empty_dataframe():
    df = _make_df([])
    assert detect_zones(df) == []


def test_detect_zones_handles_short_dataframe():
    df = _make_df([(100, 105, 95, 102), (102, 108, 100, 106)])
    assert detect_zones(df) == []


# ---------------------------------------------------------------------------
# Display filtering: filter_zones() declutters the raw zone list
# ---------------------------------------------------------------------------

def test_filter_zones_removes_zones_tested_two_or_more_times():
    """Rule: FRESHNESS FILTER — drop zones with times_tested >= 2; keep
    only fresh (0) and once-tested (1) zones."""
    fresh = _make_zone(category="demand", proximal=90, distal=85, times_tested=0)
    once = _make_zone(category="demand", proximal=80, distal=75, times_tested=1)
    twice = _make_zone(category="demand", proximal=70, distal=65, times_tested=2)
    thrice = _make_zone(category="demand", proximal=60, distal=55, times_tested=3)

    result = filter_zones([fresh, once, twice, thrice], current_price=100.0)
    kept = {z.proximal for z in result}

    assert fresh.proximal in kept
    assert once.proximal in kept
    assert twice.proximal not in kept
    assert thrice.proximal not in kept


def test_filter_zones_removes_zones_scoring_below_five():
    """Rule: SCORE FILTER — drop zones with odd_score < 5 (the documented
    "no trade below 5" cutoff)."""
    high_score = _make_zone(category="supply", proximal=110, distal=115, odd_score=7.0)
    borderline = _make_zone(category="supply", proximal=120, distal=125, odd_score=5.0)
    low_score = _make_zone(category="supply", proximal=130, distal=135, odd_score=4.5)

    result = filter_zones([high_score, borderline, low_score], current_price=100.0)
    kept = {z.proximal for z in result}

    assert high_score.proximal in kept
    assert borderline.proximal in kept
    assert low_score.proximal not in kept


def test_filter_zones_keeps_only_nearest_three_per_side():
    """Rule: NEAREST-N FILTER — keep at most the 3 demand zones (proximal
    below current_price) and 3 supply zones (proximal above) closest to
    price, nearest first; at most 6 zones survive in total."""
    current_price = 100.0

    # Five non-overlapping demand zones below price and five non-overlapping
    # supply zones above it — none merge, so the nearest-N filter alone
    # determines what survives.
    demand_zones = [
        _make_zone(category="demand", proximal=p, distal=p - 4)
        for p in (95, 90, 85, 80, 75)
    ]
    supply_zones = [
        _make_zone(category="supply", proximal=p, distal=p + 4)
        for p in (105, 110, 115, 120, 125)
    ]

    result = filter_zones(demand_zones + supply_zones, current_price)
    kept_demand = [z for z in result if z.category == "demand"]
    kept_supply = [z for z in result if z.category == "supply"]

    assert len(result) == 6
    assert len(kept_demand) == 3
    assert len(kept_supply) == 3
    # Nearest-to-price first on each side.
    assert [z.proximal for z in kept_demand] == [95, 90, 85]
    assert [z.proximal for z in kept_supply] == [105, 110, 115]


def test_filter_zones_handles_empty_list():
    """Graceful handling of an empty input list."""
    assert filter_zones([], current_price=100.0) == []


# ---------------------------------------------------------------------------
# Stage 2: trend detection — the 50 SMA "clock method"
# ---------------------------------------------------------------------------

def test_detect_trend_uptrend():
    """A steadily rising close series pushes the 50 SMA up over the
    lookback window by well more than the flat threshold -> "UP"."""
    closes = [100.0 + i for i in range(80)]
    df = _closes_df(closes)

    info = detect_trend(df)

    assert info["trend"] == "UP"
    assert info["sma_now"] is not None and info["sma_past"] is not None
    assert info["sma_now"] > info["sma_past"]
    assert info["slope"] is not None and info["slope"] > 0
    assert info["angle"] is not None and 0 < info["angle"] <= 60


def test_detect_trend_downtrend():
    """A steadily falling close series pulls the 50 SMA down over the
    lookback window by well more than the flat threshold -> "DOWN"."""
    closes = [200.0 - i for i in range(80)]
    df = _closes_df(closes)

    info = detect_trend(df)

    assert info["trend"] == "DOWN"
    assert info["sma_now"] is not None and info["sma_past"] is not None
    assert info["sma_now"] < info["sma_past"]
    assert info["slope"] is not None and info["slope"] < 0
    assert info["angle"] is not None and -60 <= info["angle"] < 0


def test_detect_trend_sideways():
    """A perfectly flat close series leaves the 50 SMA unchanged over the
    lookback window (slope == 0, well within the +/- 0.3% flat threshold)
    -> "SIDEWAYS"."""
    closes = [100.0] * 80
    df = _closes_df(closes)

    info = detect_trend(df)

    assert info["trend"] == "SIDEWAYS"
    assert info["slope"] == pytest.approx(0.0)
    assert info["angle"] == pytest.approx(0.0)


def test_detect_trend_insufficient_data():
    """Rule: guard against insufficient data — fewer than
    sma_period + lookback (default 50 + 7 = 57) candles -> SIDEWAYS with
    every numeric field reported as None (the conservative "can't tell
    yet" answer)."""
    closes = [100.0 + i for i in range(30)]
    df = _closes_df(closes)

    info = detect_trend(df)

    assert info["trend"] == "SIDEWAYS"
    assert info["sma_now"] is None
    assert info["sma_past"] is None
    assert info["slope"] is None
    assert info["angle"] is None


# ---------------------------------------------------------------------------
# Stage 2: EMA 20 confluence enhancer
# ---------------------------------------------------------------------------

def test_ema20_confluence_in_zone():
    """A flat close series converges the EMA 20 to that constant price; a
    zone straddling it should be flagged in_zone (and therefore an
    enhancer) — a "high probability" confluence per the MA document."""
    df = _closes_df([100.0] * 30)
    zone = _make_zone(category="demand", proximal=102.0, distal=98.0)

    confluence = ema20_confluence(df, zone)

    assert confluence["ema_now"] == pytest.approx(100.0)
    assert confluence["in_zone"] is True
    assert confluence["is_enhancer"] is True


def test_ema20_confluence_far():
    """When the EMA sits well outside both the zone and its
    proximity_pct% tolerance band, neither in_zone nor near_zone fires,
    so the zone is not an EMA 20 enhancer."""
    df = _closes_df([100.0] * 30)
    zone = _make_zone(category="supply", proximal=200.0, distal=210.0)

    confluence = ema20_confluence(df, zone)

    assert confluence["ema_now"] == pytest.approx(100.0)
    assert confluence["in_zone"] is False
    assert confluence["near_zone"] is False
    assert confluence["is_enhancer"] is False


# ---------------------------------------------------------------------------
# Stage 2: trend-alignment safety rule (tradeability)
# ---------------------------------------------------------------------------

def test_demand_zone_in_downtrend_not_tradeable():
    """Rule: a DEMAND zone is tradeable ONLY when the trend is "UP" — in a
    downtrend it must be flagged not tradeable with the documented
    warning, while every Stage 1 field (e.g. odd_score) stays untouched."""
    zone = _make_zone(category="demand", odd_score=7.0)

    aligned = _apply_trend_alignment(zone, "DOWN")

    assert aligned.trend_at_zone == "DOWN"
    assert aligned.is_tradeable is False
    assert aligned.trade_warning == "Demand zone in downtrend - risky per methodology"
    assert aligned.odd_score == pytest.approx(7.0)   # Stage 1 score untouched


def test_supply_zone_in_uptrend_not_tradeable():
    """Rule: a SUPPLY zone is tradeable ONLY when the trend is "DOWN" — in
    an uptrend it must be flagged not tradeable with the documented
    warning, while every Stage 1 field (e.g. odd_score) stays untouched."""
    zone = _make_zone(category="supply", odd_score=6.0)

    aligned = _apply_trend_alignment(zone, "UP")

    assert aligned.trend_at_zone == "UP"
    assert aligned.is_tradeable is False
    assert aligned.trade_warning == "Supply zone in uptrend - risky per methodology"
    assert aligned.odd_score == pytest.approx(6.0)   # Stage 1 score untouched


def test_trend_alignment_marks_aligned_zones_tradeable():
    """Sanity check for the "happy path" of the alignment rule: a demand
    zone in an uptrend and a supply zone in a downtrend are tradeable with
    no warning, while a sideways market makes everything untradeable."""
    demand = _make_zone(category="demand")
    supply = _make_zone(category="supply")

    aligned_demand = _apply_trend_alignment(demand, "UP")
    assert aligned_demand.is_tradeable is True
    assert aligned_demand.trade_warning == ""

    aligned_supply = _apply_trend_alignment(supply, "DOWN")
    assert aligned_supply.is_tradeable is True
    assert aligned_supply.trade_warning == ""

    for zone, category in ((demand, "demand"), (supply, "supply")):
        aligned_sideways = _apply_trend_alignment(zone, "SIDEWAYS")
        assert aligned_sideways.is_tradeable is False
        assert aligned_sideways.trade_warning == "Sideways trend - avoid"


# ---------------------------------------------------------------------------
# Stage 3: Fibonacci confluence — swing detection
# ---------------------------------------------------------------------------

# The swing low (50, idx 0) occurs *before* the swing high (150, idx 5)
# -> chronologically "up" (price swung up into the high).
_SWING_UP_ROWS = [
    (100, 105, 50, 102),    # idx 0: swing low = 50
    (102, 110, 100, 108),   # idx 1
    (108, 115, 105, 112),   # idx 2
    (112, 120, 110, 118),   # idx 3
    (118, 130, 115, 125),   # idx 4
    (125, 150, 120, 145),   # idx 5: swing high = 150
]

# The mirror image: swing high (150, idx 0) occurs *before* the swing low
# (50, idx 5) -> chronologically "down" (price swung down into the low).
_SWING_DOWN_ROWS = [
    (125, 150, 120, 145),   # idx 0: swing high = 150
    (118, 130, 115, 125),   # idx 1
    (112, 120, 110, 118),   # idx 2
    (108, 115, 105, 112),   # idx 3
    (102, 110, 100, 108),   # idx 4
    (100, 105, 50, 102),    # idx 5: swing low = 50
]


def test_find_recent_swing_up():
    """Rule: direction — the swing low occurring *before* the swing high
    chronologically is an "up" swing (price swung up into the high; Fib
    retracements are then measured back down from it)."""
    df = _make_df(_SWING_UP_ROWS)
    swing = find_recent_swing(df, lookback=10)

    assert swing["swing_high"] == pytest.approx(150.0)
    assert swing["swing_low"] == pytest.approx(50.0)
    assert swing["swing_high_idx"] == 5
    assert swing["swing_low_idx"] == 0
    assert swing["direction"] == "up"


def test_find_recent_swing_down():
    """Rule: direction — the swing high occurring *before* the swing low
    chronologically is a "down" swing (price swung down into the low; Fib
    retracements are then measured back up from it)."""
    df = _make_df(_SWING_DOWN_ROWS)
    swing = find_recent_swing(df, lookback=10)

    assert swing["swing_high"] == pytest.approx(150.0)
    assert swing["swing_low"] == pytest.approx(50.0)
    assert swing["swing_high_idx"] == 0
    assert swing["swing_low_idx"] == 5
    assert swing["direction"] == "down"


# ---------------------------------------------------------------------------
# Stage 3: Fibonacci confluence — retracement levels
# ---------------------------------------------------------------------------

def test_calculate_fib_levels_up_swing():
    """Rule: for an "up" swing, level = swing_high - (range * ratio) — verify
    the documented golden-ratio (0.618) math precisely (range = 100,
    200 - 100*0.618 = 138.2), plus the other three documented levels."""
    swing = SwingInfo(
        swing_high=200.0, swing_low=100.0, swing_high_idx=10, swing_low_idx=2, direction="up",
    )

    levels = calculate_fib_levels(swing)

    assert set(levels.keys()) == {0.382, 0.5, 0.618, 0.786}
    assert levels[0.618] == pytest.approx(200.0 - 100.0 * 0.618)
    assert levels[0.618] == pytest.approx(138.2)
    assert levels[0.5] == pytest.approx(150.0)
    assert levels[0.382] == pytest.approx(200.0 - 100.0 * 0.382)
    assert levels[0.786] == pytest.approx(200.0 - 100.0 * 0.786)


# ---------------------------------------------------------------------------
# Stage 3: Fibonacci confluence — per-zone confluence check
# ---------------------------------------------------------------------------

def test_fib_confluence_level_in_zone():
    """Rule: a Fib level whose price falls between the zone's distal/proximal
    lines counts as "in zone" -> has_confluence True, with that ratio
    reported as the strongest level (0.618, the golden ratio, wins the
    documented priority — it's also the only one in or near this zone)."""
    zone = _make_zone(category="demand", proximal=102.0, distal=98.0)
    fib_levels = {0.382: 120.0, 0.5: 110.0, 0.618: 100.0, 0.786: 90.0}

    result = fib_confluence(zone, fib_levels)

    assert result["levels_in_zone"] == [0.618]
    assert result["levels_near_zone"] == []
    assert result["has_confluence"] is True
    assert result["confluence_count"] == 1
    assert result["strongest_level"] == 0.618


def test_fib_confluence_no_levels():
    """Rule: when no Fib level sits in or near the zone, has_confluence is
    False, both level lists are empty and strongest_level is None — the
    conservative "no confluence found" result."""
    zone = _make_zone(category="supply", proximal=500.0, distal=510.0)
    fib_levels = {0.382: 120.0, 0.5: 110.0, 0.618: 100.0, 0.786: 90.0}

    result = fib_confluence(zone, fib_levels)

    assert result["levels_in_zone"] == []
    assert result["levels_near_zone"] == []
    assert result["has_confluence"] is False
    assert result["confluence_count"] == 0
    assert result["strongest_level"] is None


# ---------------------------------------------------------------------------
# Stage 3: confluence rating — a SEPARATE scorecard from odd_score
# ---------------------------------------------------------------------------

def test_confluence_rating_high():
    """Rule: EMA 20 confluence (+1) plus the golden ratio 0.618 sitting in
    the zone (+1 for the level itself, +1 extra for being the golden ratio)
    totals 3 points -> the "High" label (3+ points)."""
    fib_result = {
        "levels_in_zone": [0.618],
        "levels_near_zone": [],
        "has_confluence": True,
        "confluence_count": 1,
        "strongest_level": 0.618,
    }

    rating = confluence_rating(ema20_enhancer=True, fib_result=fib_result)

    assert rating["confluence_score"] == 3
    assert rating["confluence_label"] == "High"
    assert rating["factors"]   # explains what contributed


def test_confluence_rating_none():
    """Rule: no EMA 20 confluence and no Fib levels in the zone -> 0 points
    -> the "None" label."""
    fib_result = {
        "levels_in_zone": [],
        "levels_near_zone": [],
        "has_confluence": False,
        "confluence_count": 0,
        "strongest_level": None,
    }

    rating = confluence_rating(ema20_enhancer=False, fib_result=fib_result)

    assert rating["confluence_score"] == 0
    assert rating["confluence_label"] == "None"
    assert rating["factors"] == []


# ---------------------------------------------------------------------------
# Stage 3: orchestrator — Fibonacci is OPT-IN
# ---------------------------------------------------------------------------

# A clean, fresh DBR demand zone (same shape/scores as _DBR_GAP_FRESH_ROWS —
# proximal=113, distal=108, odd_score=7) followed by twenty "boring" filler
# candles that stay well clear of the zone (low > 113 throughout, so it
# remains untested/fresh) and can't be mistaken for a legin/legout (their
# body_pct ~= 0.5/6 = 0.083 is well below the "exciting" >= 0.60 cutoff).
# This pads the DataFrame past _MIN_CANDLES (20) without disturbing the
# single zone the engine should detect, letting these orchestrator-level
# tests assert on a deterministic, single-zone result.
_FIB_ANALYSE_ROWS = [
    (120, 121, 110, 111),   # legin (bearish, exciting)
    (111, 113, 109, 112),   # base candle 1 (boring)
    (112, 114, 108, 113),   # base candle 2 (boring) -> proximal=113, distal=108
    (116, 126, 115, 125),   # legout opens at 116 > 114 -> GAP, bullish & exciting
] + [
    (130 + i, 134 + i, 128 + i, 130.5 + i) for i in range(20)   # boring filler, well above the zone
]


def test_analyse_without_fibonacci_leaves_fib_fields_default():
    """Stage 3 rule: Fibonacci is OPT-IN — with the checkbox off (the
    default), every zone keeps the Stage 2 defaults for every fib_*/
    confluence_* field, and the result carries no fib_swing/fib_levels keys
    at all (byte-for-byte identical to Stage 2 behaviour)."""
    df = _make_df(_FIB_ANALYSE_ROWS)

    result = DemandSupplyAnalysis().analyse("TEST", df)

    assert "error" not in result
    zones = result["all_zones"]
    assert zones   # sanity: the engineered data does produce zone(s) to check

    for zone in zones:
        assert zone["fib_confluence"] is False
        assert zone["fib_levels_in_zone"] == []
        assert zone["fib_strongest"] is None
        assert zone["confluence_score"] == 0
        assert zone["confluence_label"] == "None"

    assert "fib_swing" not in result
    assert "fib_levels" not in result


def test_odd_score_unchanged_when_fibonacci_enabled():
    """CRITICAL Stage 3 rule: enabling the Fibonacci confluence enhancer must
    NEVER change the documented 7-point ODD odd_score (or any other Stage 1
    scoring/structure field) — confluence_score/fib_* are always a separate,
    additive layer (see analysis.zone_engine.scoring.confluence_rating /
    analysis.zone_engine.fibonacci), composed in *after* detection/scoring/
    filtering have already produced the display zones."""
    df = _make_df(_FIB_ANALYSE_ROWS)

    without_fib = DemandSupplyAnalysis().analyse("TEST", df, use_fibonacci=False)
    with_fib = DemandSupplyAnalysis().analyse("TEST", df, use_fibonacci=True)

    zones_without = without_fib["all_zones"]
    zones_with = with_fib["all_zones"]
    assert zones_without and zones_with
    assert len(zones_without) == len(zones_with)

    for z_without, z_with in zip(zones_without, zones_with):
        assert z_with["odd_score"] == pytest.approx(z_without["odd_score"])
        assert z_with["freshness_points"] == pytest.approx(z_without["freshness_points"])
        assert z_with["strength_points"] == pytest.approx(z_without["strength_points"])
        assert z_with["time_points"] == pytest.approx(z_without["time_points"])
        assert z_with["times_tested"] == z_without["times_tested"]
        assert z_with["zone_strength"] == z_without["zone_strength"]
        assert z_with["entry_recommendation"] == z_without["entry_recommendation"]
        assert z_with["proximal"] == pytest.approx(z_without["proximal"])
        assert z_with["distal"] == pytest.approx(z_without["distal"])


# ---------------------------------------------------------------------------
# Price NaN-safety — format_currency() and demand_supply current_price guard
# ---------------------------------------------------------------------------

def test_format_currency_normal():
    """Smoke-test the happy path — a valid positive float is formatted with
    the currency symbol and exactly two decimal places."""
    assert format_currency(1234.5) == "₹1,234.50"
    assert format_currency(0.99) == "₹0.99"
    assert format_currency(100.0, "$") == "$100.00"


def test_format_currency_nan_returns_dash():
    """Rule: ``NaN`` must never produce "₹nan" — format_currency must return
    "—" (or any safe sentinel) instead so the UI can't accidentally render
    the raw floating-point token as a currency string."""
    assert format_currency(float("nan")) == "—"


def test_format_currency_none_returns_dash():
    """Rule: ``None`` (price not yet known) must be handled gracefully —
    return "—" rather than raising a TypeError."""
    assert format_currency(None) == "—"  # type: ignore[arg-type]


def test_format_currency_inf_returns_dash():
    """Rule: infinite values are also invalid prices — both +inf and -inf
    should produce "—", not "₹inf"."""
    assert format_currency(float("inf")) == "—"
    assert format_currency(float("-inf")) == "—"


def test_analyse_nan_close_never_returns_nan_price():
    """Rule: if the very last Close candle is NaN (e.g. a partial intraday
    row emitted by some data sources), analyse() must fall back to the last
    *valid* close and never store NaN in result["current_price"].

    NaN is truthy in Python, so a naive ``float(df['Close'].iloc[-1])`` would
    propagate NaN all the way to the dashboard card ("₹nan") if the fallback
    relied on an ``or`` guard — this test catches that regression."""
    df = _make_df(_FIB_ANALYSE_ROWS)
    # Inject a NaN close into the final row, simulating a partial candle.
    df.loc[df.index[-1], "Close"] = float("nan")

    result = DemandSupplyAnalysis().analyse("TEST", df)

    assert "error" not in result
    price = result.get("current_price")
    assert price is not None, "current_price must be present in result"
    assert math.isfinite(price), f"current_price must be finite, got {price}"
    assert price > 0, f"current_price must be positive, got {price}"


# ---------------------------------------------------------------------------
# M2: Exceptional distal auto-application
# ---------------------------------------------------------------------------

def test_m2_dbr_legin_wick_below_base_applies_exceptional():
    """DBR demand: legin low extends below base low → exceptional distal."""
    rows = [
        # Legin: bearish exciting candle dropping into the base, wick reaches 80
        (110, 112, 80, 90),
        # Base: boring candle, low = 88 (higher than legin's 80)
        (91, 93, 88, 92),
        # Legout: bullish exciting candle rallying away
        (93, 120, 92, 118),
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.marking == "Exceptional"
    # Distal should be legin's low (80), not base's low (88)
    assert z.distal == 80.0
    # M13: WTW proximal = base high = 93 (narrow base, ratio < 1.5)
    assert z.proximal == 93.0


def test_m2_dbr_legin_wick_not_below_base_stays_normal():
    """DBR demand: legin low does NOT extend below base low → normal."""
    rows = [
        # Legin: bearish exciting candle, low = 89 (above base low of 85)
        (110, 112, 89, 91),
        # Base: boring candle, low = 85
        (91, 93, 85, 92),
        # Legout: bullish exciting candle
        (93, 120, 92, 118),
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.marking == "Normal"
    assert z.distal == 85.0


def test_m2_rbd_legin_wick_above_base_applies_exceptional():
    """RBD supply: legin high extends above base high → exceptional distal."""
    rows = [
        # Legin: bullish exciting candle rallying into the base, wick reaches 125
        (90, 125, 88, 110),
        # Base: boring candle, high = 112 (lower than legin's 125)
        (109, 112, 108, 110),
        # Legout: bearish exciting candle dropping away
        (108, 109, 82, 84),
    ]
    zones = detect_zones(_make_df(rows))
    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 1
    z = rbd[0]
    assert z.marking == "Exceptional"
    assert z.distal == 125.0
    # M13: WTW proximal = base lowest low = 108 (narrow base, ratio < 1.5)
    assert z.proximal == 108.0


def test_m2_rbr_legout_wick_below_base_applies_exceptional():
    """RBR demand: legout low extends below base low → exceptional.
    RBR only checks legout, not legin."""
    rows = [
        # Legin: bullish exciting candle (rally)
        (80, 100, 78, 98),
        # Base: boring candle, low = 96
        (97, 99, 96, 98),
        # Legout: bullish exciting candle, wick dips to 90 (below base low 96)
        (97, 120, 90, 118),
    ]
    zones = detect_zones(_make_df(rows))
    rbr = [z for z in zones if z.zone_type == "RBR"]
    assert len(rbr) == 1
    z = rbr[0]
    assert z.marking == "Exceptional"
    assert z.distal == 90.0


def test_m2_dbd_legout_wick_above_base_applies_exceptional():
    """DBD supply: legout high extends above base high → exceptional.
    DBD only checks legout, not legin."""
    rows = [
        # Legin: bearish exciting candle (drop)
        (120, 122, 100, 102),
        # Base: boring candle, high = 104
        (103, 104, 101, 102),
        # Legout: bearish exciting candle, wick spikes to 110 (above base high 104)
        (103, 110, 82, 84),
    ]
    zones = detect_zones(_make_df(rows))
    dbd = [z for z in zones if z.zone_type == "DBD"]
    assert len(dbd) == 1
    z = dbd[0]
    assert z.marking == "Exceptional"
    assert z.distal == 110.0


def test_m2_proximal_independent_of_distal():
    """M2 affects distal; M13 may change proximal independently."""
    rows = [
        (110, 112, 75, 90),   # Legin: wick way below base
        (91, 93, 88, 92),     # Base
        (93, 120, 92, 118),   # Legout
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.marking == "Exceptional"
    # M13: WTW proximal = base high = 93 (narrow base, ratio < 1.5)
    assert z.proximal == 93.0
    # M2: Distal = legin low = 75 (more extreme than base low 88)
    assert z.distal == 75.0


# ---------------------------------------------------------------------------
# M13: Wick-to-wick vs body-to-wick proximal marking
# ---------------------------------------------------------------------------

def test_m13_clean_narrow_base_gets_wick_to_wick():
    """M13 P3: narrow base (ratio <= 1.5) → wick-to-wick proximal."""
    rows = [
        (120, 121, 110, 111),   # Legin (bearish exciting)
        (111, 113, 109, 112),   # Base (boring, body=1/4=0.25)
        (113, 125, 112, 124),   # Legout (bullish exciting, no gap, 1 candle)
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    # BTW: proximal=112, distal=109, width=3
    # WTW: proximal=113, distal=109, width=4
    # Ratio=4/3=1.33 < 1.5 → WTW
    assert z.proximal == 113.0
    assert z.proximal_marking == "Wick-to-Wick"


def test_m13_wide_wick_base_stays_body_to_wick():
    """M13 P3: wide wicks (ratio > 1.5) → body-to-wick proximal."""
    rows = [
        (120, 121, 100, 101),   # Legin (bearish exciting)
        (101, 110, 98, 102),    # Base (boring, body=1/12=0.08 — but also doji!)
        (103, 125, 102, 124),   # Legout (bullish exciting, no gap)
    ]
    # body_pct = 1/12 = 0.083 < 0.10 → doji → P2 forces BTW
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    assert z.proximal == 102.0
    assert z.proximal_marking == "Body-to-Wick"


def test_m13_doji_in_base_forces_body_to_wick():
    """M13 P2: doji candle in base (body < 10% of range) → BTW."""
    rows = [
        (120, 121, 100, 101),   # Legin (bearish exciting)
        (103, 108, 98, 103),    # Base 1 (boring, body=0/10=0, doji)
        (104, 106, 99, 105),    # Base 2 (boring, body=1/7=0.14, not doji)
        (106, 125, 105, 124),   # Legout (bullish exciting)
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    # P2: doji in base → body-to-wick
    assert z.proximal_marking == "Body-to-Wick"
    # BTW proximal = max body top = max(103, 105) = 105
    assert z.proximal == 105.0


def test_m13_explosive_legout_gap_plus_exciting_forces_wick_to_wick():
    """M13 P1: gap(1) + exciting candle(1) = 2 units → explosive → WTW."""
    rows = [
        (120, 121, 110, 111),   # Legin (bearish exciting)
        (111, 116, 107, 112),   # Base (boring, body=1/9=0.11, wide wicks)
        (117, 130, 116, 128),   # Legout: gap (opens 117 > 116) + exciting (body=0.79)
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    # gap=1 + exciting=1 = 2 units → explosive → WTW
    assert z.proximal_marking == "Wick-to-Wick"
    assert z.proximal == 116.0
    assert z.strength_points == pytest.approx(2.0)


def test_m13_explosive_legout_two_candles_forces_wick_to_wick():
    """M13 P1: 2+ exciting legout candles → explosive override → WTW."""
    rows = [
        (120, 121, 110, 111),   # Legin (bearish exciting)
        (111, 116, 107, 112),   # Base (boring, wide wicks)
        (113, 125, 112, 124),   # Legout 1 (bullish exciting, no gap)
        (124, 135, 123, 134),   # Legout 2 (bullish exciting, extends run)
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    # P1: 2 legout candles → WTW
    assert z.proximal_marking == "Wick-to-Wick"
    assert z.proximal == 116.0


def test_m13_width_ratio_exactly_1_5_picks_wick_to_wick():
    """M13 P3: width ratio exactly 1.5 (not > 1.5) → WTW."""
    # BTW width=2, WTW width=3, ratio=1.5 exactly → NOT > 1.5 → WTW
    rows = [
        (120, 121, 110, 111),   # Legin (bearish exciting)
        (111, 113, 110, 112),   # Base (boring, body=1/3, high=113, low=110)
        (113, 125, 112, 124),   # Legout (bullish exciting, no gap)
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    # BTW proximal=112, distal=110, width=2
    # WTW proximal=113, distal=110, width=3
    # ratio = 3/2 = 1.5, NOT > 1.5 → WTW
    assert z.proximal == 113.0
    assert z.proximal_marking == "Wick-to-Wick"


def test_m13_width_ratio_above_1_5_picks_body_to_wick():
    """M13 P3: width ratio > 1.5 → BTW (zone too wide for WTW)."""
    rows = [
        (120, 121, 100, 101),   # Legin (bearish exciting)
        (101, 108, 95, 103),    # Base (boring, body=2/13=0.15, wide wicks)
        (104, 125, 103, 124),   # Legout (bullish exciting, no gap, 1 candle)
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    # BTW proximal=103, distal=95, width=8
    # WTW proximal=108, distal=95, width=13
    # ratio = 13/8 = 1.625 > 1.5 → BTW
    assert z.proximal == 103.0
    assert z.proximal_marking == "Body-to-Wick"


# ---------------------------------------------------------------------------
# M8: Closing concept — leg-out quality vs. opposing zones
# ---------------------------------------------------------------------------

def test_m8_demand_legout_closes_beyond_supply_strong():
    """M8: demand zone's leg-out closes above the nearest supply zone's
    proximal → closing_quality = 'strong' (orders absorbed)."""
    rows = [
        # --- RBD supply zone (proximal=1904, distal=1912) ---
        (1880, 1912, 1878, 1910),   # 0: legin (bullish, exciting)
        (1908, 1912, 1904, 1906),   # 1: base (boring)
        (1905, 1906, 1868, 1870),   # 2: legout (bearish, exciting)
        # --- DBR demand zone ---
        (1870, 1872, 1833, 1835),   # 3: legin (bearish, exciting)
        (1835, 1840, 1832, 1838),   # 4: base (boring)
        (1839, 1912, 1838, 1910),   # 5: legout close=1910 > supply prox=1904 → STRONG
    ]
    df = _make_df(rows)
    zones = detect_zones(df)
    assess_closing_quality(zones, df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    assert dbr[0].closing_quality == "strong"


def test_m8_demand_legout_wicks_past_supply_but_closes_below_weak():
    """M8: demand zone's leg-out wicks above the supply proximal but closes
    below it → closing_quality = 'weak' (departure unconvincing)."""
    rows = [
        # --- RBD supply zone (proximal=1904, distal=1912) ---
        (1880, 1912, 1878, 1910),   # 0: legin (bullish, exciting)
        (1908, 1912, 1904, 1906),   # 1: base (boring)
        (1905, 1906, 1868, 1870),   # 2: legout (bearish, exciting)
        # --- DBR demand zone ---
        (1870, 1872, 1833, 1835),   # 3: legin (bearish, exciting)
        (1835, 1840, 1832, 1838),   # 4: base (boring)
        (1839, 1912, 1838, 1900),   # 5: legout close=1900 < supply prox=1904 → WEAK
    ]
    df = _make_df(rows)
    zones = detect_zones(df)
    assess_closing_quality(zones, df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    assert dbr[0].closing_quality == "weak"


def test_m8_no_opposing_zone_unchecked():
    """M8: when no opposing zone sits in the leg-out's path the closing
    concept cannot be checked → closing_quality = 'unchecked'."""
    rows = [
        (120, 121, 110, 111),   # 0: legin (bearish, exciting)
        (111, 114, 109, 112),   # 1: base (boring)
        (112, 115, 108, 113),   # 2: base (boring)
        (114, 125, 113, 124),   # 3: legout (bullish, exciting)
    ]
    df = _make_df(rows)
    zones = detect_zones(df)
    assess_closing_quality(zones, df)

    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    assert dbr[0].closing_quality == "unchecked"


def test_m8_supply_legout_closes_below_demand_strong():
    """M8: supply zone's leg-out closes below the nearest demand zone's
    proximal → closing_quality = 'strong'."""
    rows = [
        # --- DBR demand zone (proximal=1888, distal=1879) ---
        (1910, 1911, 1878, 1880),   # 0: legin (bearish, exciting)
        (1882, 1888, 1879, 1884),   # 1: base (boring)
        (1885, 1915, 1884, 1912),   # 2: legout (bullish, exciting)
        # --- RBD supply zone ---
        (1912, 1940, 1910, 1938),   # 3: legin (bullish, exciting)
        (1937, 1940, 1933, 1935),   # 4: base (boring)
        (1934, 1935, 1875, 1878),   # 5: legout close=1878 < demand prox=1888 → STRONG
    ]
    df = _make_df(rows)
    zones = detect_zones(df)
    assess_closing_quality(zones, df)

    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 1
    assert rbd[0].closing_quality == "strong"


def test_m8_supply_legout_closes_above_demand_weak():
    """M8: supply zone's leg-out wicks below the demand proximal but closes
    above it → closing_quality = 'weak'."""
    rows = [
        # --- DBR demand zone (proximal=1888, distal=1879) ---
        (1910, 1911, 1878, 1880),   # 0: legin (bearish, exciting)
        (1882, 1888, 1879, 1884),   # 1: base (boring)
        (1885, 1915, 1884, 1912),   # 2: legout (bullish, exciting)
        # --- RBD supply zone ---
        (1912, 1940, 1910, 1938),   # 3: legin (bullish, exciting)
        (1937, 1940, 1933, 1935),   # 4: base (boring)
        (1934, 1935, 1875, 1892),   # 5: legout close=1892 > demand prox=1888 → WEAK
    ]
    df = _make_df(rows)
    zones = detect_zones(df)
    assess_closing_quality(zones, df)

    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 1
    assert rbd[0].closing_quality == "weak"


def test_m8_closing_quality_does_not_change_odd_score():
    """M8: closing_quality is a quality flag only — it must NOT change
    the ODD score."""
    rows = [
        # --- RBD supply zone ---
        (1880, 1912, 1878, 1910),   # 0: legin (bullish, exciting)
        (1908, 1912, 1904, 1906),   # 1: base (boring)
        (1905, 1906, 1868, 1870),   # 2: legout (bearish, exciting)
        # --- DBR demand zone ---
        (1870, 1872, 1833, 1835),   # 3: legin (bearish, exciting)
        (1835, 1840, 1832, 1838),   # 4: base (boring)
        (1839, 1912, 1838, 1910),   # 5: legout (bullish, exciting) → strong
    ]
    df = _make_df(rows)
    zones = detect_zones(df)
    scores_before = {z.zone_type: z.odd_score for z in zones}
    assess_closing_quality(zones, df)
    scores_after = {z.zone_type: z.odd_score for z in zones}
    assert scores_before == scores_after


# ---------------------------------------------------------------------------
# M17: Missing-base (instant reversal) zones
# ---------------------------------------------------------------------------

def test_m17_missing_base_demand_dbr():
    """M17: bearish exciting → bullish exciting = DBR demand (no base)."""
    rows = [
        (130, 131, 118, 119),   # 0: legin (bearish exciting)
        (120, 121, 108, 109),   # 1: turning point (bearish exciting) — last legin candle
        (110, 125, 109, 124),   # 2: legout (bullish exciting, opposite direction)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.category == "demand"
    assert z.num_base_candles == 0
    assert z.proximal_marking == "Missing-Base"
    # Turning point (candle 1): O=120, C=109 → proximal = body top = 120
    # Distal = lowest low of tp/legout = min(108, 109) = 108
    assert z.proximal == 120.0
    assert z.distal == 108.0
    assert z.base_start_idx == 1
    assert z.base_end_idx == 1
    assert z.legout_idx == 2


def test_m17_missing_base_supply_rbd():
    """M17: bullish exciting → bearish exciting = RBD supply (no base)."""
    rows = [
        (100, 112, 99, 111),    # 0: legin (bullish exciting)
        (110, 122, 109, 121),   # 1: turning point (bullish exciting)
        (120, 121, 100, 102),   # 2: legout (bearish exciting)
    ]
    zones = detect_zones(_make_df(rows))
    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 1
    z = rbd[0]
    assert z.category == "supply"
    assert z.num_base_candles == 0
    assert z.proximal_marking == "Missing-Base"
    # Turning point (candle 1): O=110, C=121 → proximal = body bottom = 110
    # Distal = highest high of tp/legout = max(122, 121) = 122
    assert z.proximal == 110.0
    assert z.distal == 122.0


def test_m17_scoring_zero_base_gets_max_time():
    """M17: 0 base candles → time_at_base = 2 points (maximum speed)."""
    rows = [
        (120, 121, 108, 109),   # 0: turning point (bearish exciting)
        (110, 130, 109, 128),   # 1: legout (bullish exciting, gap: 110 > 108? no)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.time_points == pytest.approx(2.0)
    assert z.num_base_candles == 0


def test_m17_no_double_counting():
    """M17: bearish → bullish → bearish: only first zone, no reuse of candles."""
    rows = [
        (130, 131, 118, 119),   # 0: bearish exciting
        (120, 121, 108, 109),   # 1: bearish exciting (turning point of zone 1)
        (110, 125, 109, 124),   # 2: bullish exciting (legout of zone 1)
        (123, 124, 109, 112),   # 3: bearish exciting (NOT a second zone turning point)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    # Candle 2 is legout of zone 1, not turning point of zone 2
    assert dbr[0].legout_idx == 2


def test_m17_legout_must_clear_turning_point():
    """M17: legout must clear turning point range — weak reversal rejected."""
    rows = [
        (120, 121, 108, 109),   # 0: turning point (bearish exciting, high=121)
        (110, 118, 109, 117),   # 1: bullish exciting, but close=117 < turning high=121
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 0


def test_m17_multi_candle_legout_clears():
    """M17: single legout candle doesn't clear turning point, but extended legout does."""
    rows = [
        (150, 155, 148, 149),   # 0: bearish legin
        (148, 149, 130, 131),   # 1: turning point (bearish exciting, high=149)
        (132, 140, 131, 139),   # 2: bullish exciting but close=139 < turning high=149
        (140, 152, 139, 151),   # 3: bullish exciting, high=152 > turning high=149 ✓
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.num_base_candles == 0


def test_m17_legin_extends_backwards():
    """M17: leg-in extends backwards to include earlier same-direction candles."""
    rows = [
        (150, 151, 138, 139),   # 0: bearish exciting (part of legin run)
        (140, 141, 128, 129),   # 1: bearish exciting (part of legin run)
        (130, 131, 118, 119),   # 2: bearish exciting (turning point)
        (120, 140, 119, 138),   # 3: bullish exciting (legout, clears 131)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.base_start_idx == 2  # turning point
    assert z.legout_idx == 3


def test_m17_legout_extends_forwards():
    """M17: leg-out extends forwards to include same-direction exciting candles."""
    rows = [
        (120, 121, 108, 109),   # 0: turning point (bearish exciting)
        (110, 130, 109, 128),   # 1: legout (bullish exciting)
        (128, 145, 127, 143),   # 2: legout extension (bullish exciting)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1


def test_m17_same_direction_no_zone():
    """M17: two exciting candles in same direction → no missing-base zone."""
    rows = [
        (120, 121, 108, 109),   # 0: bearish exciting
        (110, 111, 98, 99),     # 1: bearish exciting (same direction, NOT a reversal)
        (100, 115, 99, 114),    # 2: bullish exciting
    ]
    zones = detect_zones(_make_df(rows))
    # No missing-base zone between candles 0 and 1 (same direction)
    # There might be a missing-base zone between candles 1 and 2
    dbr = [z for z in zones if z.zone_type == "DBR" and z.num_base_candles == 0]
    assert len(dbr) == 1
    assert dbr[0].base_start_idx == 1  # turning point is candle 1


def test_m17_with_m2_exceptional_distal():
    """M17: M2 exceptional distal still applies to missing-base zones."""
    rows = [
        (130, 131, 95, 109),    # 0: legin with deep wick (low=95, way below turning point)
        (120, 121, 108, 109),   # 1: turning point (bearish, low=108)
        (110, 135, 109, 133),   # 2: legout (bullish exciting)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.marking == "Exceptional"
    # M2: legin low (95) < normal distal (min(108,109)=108) → exceptional distal = 95
    assert z.distal == 95.0
    # Proximal = body top of turning point = max(120, 109) = 120
    assert z.proximal == 120.0


def test_m17_continuation_not_missing_base():
    """M17: same-direction exciting candles don't form missing-base (no reversal)."""
    rows = [
        (100, 112, 99, 111),    # 0: bullish exciting
        (112, 125, 111, 124),   # 1: bullish exciting (same direction)
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 0


def test_m17_gap_in_legout_scores_strength_2():
    """M17: gap away from turning point in legout → strength 2."""
    rows = [
        (120, 121, 108, 109),   # 0: turning point (bearish, high=121, low=108)
        (122, 140, 121, 138),   # 1: legout opens 122 > turning high 121 → GAP
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.strength_points == pytest.approx(2.0)


def test_m17_first_legout_clears_but_last_does_not():
    """M17: first legout close clears TP but extended run's last close doesn't.

    Without the ANY-candle fix, only the last extended candle is checked
    and the zone would be wrongly rejected.
    """
    rows = [
        (130, 131, 118, 119),   # 0: turning point (bearish, high=131)
        (120, 135, 119, 132),   # 1: legout (bullish, close=132 > tp high 131 ✓)
        (125, 132, 124, 130),   # 2: extended (bullish exciting, close=130 < 131)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.num_base_candles == 0
    assert z.base_start_idx == 0


def test_min_body_pct_of_price_rejects_small_candle():
    """Small candle body relative to price should not qualify as exciting."""
    info = classify_candle(1878.0, 1878.0, 1847.0, 1855.0)
    # body=23, range=31 → body_pct=0.74 (passes body-to-range)
    # body/price=23/1855=1.24% (fails 1.3% min body)
    assert info["body_pct"] == pytest.approx(23 / 31, rel=1e-6)
    assert info["is_exciting"] is False
    assert info["is_boring"] is True


def test_min_body_pct_of_price_accepts_large_candle():
    """Candle body above 1.3% of price should still qualify as exciting."""
    info = classify_candle(1882.0, 1882.0, 1847.0, 1855.0)
    # body=27, range=35 → body_pct=0.77 (passes body-to-range)
    # body/price=27/1855=1.46% (passes 1.3% min body)
    assert info["is_exciting"] is True
    assert info["is_boring"] is False


def test_m13_p1_overrides_doji():
    """M13: P1 (explosive) takes priority over P2 (doji) — WTW wins."""
    rows = [
        (120, 121, 100, 101),   # Legin (bearish exciting)
        (101, 108, 95, 101),    # Base (boring, body=0/13=0.0, doji!)
        (109, 125, 108, 124),   # Legout 1 opens 109 > base high 108 → GAP
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    # P1 (gap) overrides P2 (doji) → WTW
    assert z.proximal_marking == "Wick-to-Wick"
    assert z.proximal == 108.0
    assert z.strength_points == pytest.approx(2.0)


def test_m13_supply_zone_wick_to_wick():
    """M13: supply zone WTW — proximal = lowest low of base."""
    rows = [
        (100, 112, 99, 111),   # Legin (bullish exciting)
        (111, 113, 108, 110),  # Base (boring)
        (109, 110, 90, 92),    # Legout (bearish exciting)
    ]
    zones = detect_zones(_make_df(rows))
    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 1
    z = rbd[0]
    # BTW proximal=110, distal=113, width=3
    # WTW proximal=108, distal=113, width=5
    # ratio=5/3=1.67 > 1.5 → BTW
    assert z.proximal == 110.0
    assert z.proximal_marking == "Body-to-Wick"


def test_m13_gap_between_consecutive_legout_candles():
    """M13: gap between legout candles (Pattern 2) → explosive → WTW."""
    rows = [
        (120, 121, 110, 111),   # Legin (bearish exciting)
        (111, 116, 107, 112),   # Base (boring, wide wicks)
        (113, 120, 112, 119),   # Legout 1 (bullish exciting, no gap from base)
        (121, 135, 120, 134),   # Legout 2 opens 121 > legout1 high 120 → GAP
    ]
    zones = detect_zones(_make_df(rows))
    assert len(zones) == 1
    z = zones[0]
    assert z.strength_points == pytest.approx(2.0)
    assert z.proximal_marking == "Wick-to-Wick"
    assert z.proximal == 116.0


def test_m13_both_m2_and_m13_can_apply():
    """M2 (exceptional distal) and M13 (WTW proximal) are independent."""
    rows = [
        (110, 112, 75, 90),    # Legin: wick way below base (M2 trigger)
        (91, 93, 88, 92),      # Base
        (93, 120, 92, 118),    # Legout
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    # M2: exceptional distal = 75
    assert z.marking == "Exceptional"
    assert z.distal == 75.0
    # M13: narrow base → WTW proximal = 93
    assert z.proximal_marking == "Wick-to-Wick"
    assert z.proximal == 93.0


# ---------------------------------------------------------------------------
# Gap-in-base terminates the base
# ---------------------------------------------------------------------------

def test_gap_up_in_base_forms_zone_with_gap_as_legout():
    """Gap up between base candles → base stops, gap IS the legout (demand)."""
    rows = [
        (120, 121, 100, 101),   # 0: legin (bearish exciting)
        (101, 103, 98, 102),    # 1: base candle 1 (boring)
        (106, 108, 102, 107),   # 2: opens 106 > prev high 103 → 2.9% GAP UP = legout
        (108, 125, 107, 124),   # 3: additional candle
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.num_base_candles == 1
    assert z.base_start_idx == 1
    assert z.base_end_idx == 1


def test_gap_down_in_base_forms_supply_zone():
    """Gap down between base candles → base stops, gap IS the legout (supply)."""
    rows = [
        (100, 112, 99, 111),    # 0: legin (bullish exciting)
        (111, 114, 108, 110),   # 1: base candle 1 (boring)
        (106, 110, 103, 108),   # 2: opens 106 < prev low 108 → 1.9% GAP DOWN = legout
        (107, 109, 90, 92),     # 3: additional candle
    ]
    zones = detect_zones(_make_df(rows))
    rbd = [z for z in zones if z.zone_type == "RBD"]
    assert len(rbd) == 1
    z = rbd[0]
    assert z.num_base_candles == 1
    assert z.base_start_idx == 1
    assert z.base_end_idx == 1


def test_no_gap_in_base_extends_normally():
    """Base candles without gaps extend normally (control test)."""
    rows = [
        (120, 121, 100, 101),   # 0: legin (bearish exciting)
        (101, 103, 98, 102),    # 1: base candle 1 (boring)
        (102, 105, 99, 104),    # 2: base candle 2 — opens 102 <= prev high 103 → NO gap
        (105, 125, 104, 124),   # 3: legout (bullish exciting)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    assert dbr[0].num_base_candles == 2


def test_noise_gap_in_base_ignored():
    """A gap below 1.3% of price is noise — base extends through it.

    Reproduces the APLAPOLLO bug where a 0.9-point gap on a ~1850 stock
    (0.05%) falsely terminated the base at 1 candle instead of 2.
    """
    rows = [
        (1882, 1882, 1847, 1855),   # 0: legin (bearish exciting, body/price=1.46%)
        (1855, 1862, 1834, 1842),   # 1: base candle 1 (boring, body_pct=0.46)
        (1833, 1843, 1803, 1826),   # 2: opens 1833 < prev low 1834 → 0.05% gap = NOISE
        (1835, 1838, 1788, 1794),   # 3: legout (bearish exciting)
    ]
    zones = detect_zones(_make_df(rows))
    dbd = [z for z in zones if z.zone_type == "DBD"]
    assert len(dbd) == 1
    assert dbd[0].num_base_candles == 2
    assert dbd[0].base_start_idx == 1
    assert dbd[0].base_end_idx == 2


def test_real_gap_in_base_still_triggers():
    """A gap above 1.3% of price terminates the base (gap-as-legout works)."""
    rows = [
        (1882, 1882, 1847, 1855),   # 0: legin (bearish exciting, body/price=1.46%)
        (1855, 1862, 1834, 1842),   # 1: base candle 1 (boring, body_pct=0.46)
        (1808, 1843, 1780, 1800),   # 2: opens 1808 < prev low 1834 → 1.42% gap = REAL
        (1795, 1800, 1760, 1770),   # 3: additional candle
    ]
    zones = detect_zones(_make_df(rows))
    dbd = [z for z in zones if z.zone_type == "DBD"]
    assert len(dbd) == 1
    assert dbd[0].num_base_candles == 1


def test_gap_in_base_with_exciting_candle_after():
    """Gap between base candles + exciting candle after → gap is legout, exciting extends it."""
    rows = [
        (120, 121, 100, 101),   # 0: legin (bearish exciting)
        (101, 103, 98, 102),    # 1: base candle 1 (boring)
        (104, 120, 103, 118),   # 2: opens 104 > prev high 103 → GAP; exciting bullish
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.num_base_candles == 1
    assert z.base_start_idx == 1
    assert z.base_end_idx == 1


def test_gap_legout_has_gap_true_for_scoring():
    """Gap-as-legout zones get has_gap=True → strength 2. Gap alone = 1 unit,
    not explosive, so M13 falls to P3 (narrow base → WTW via ratio)."""
    rows = [
        (120, 121, 100, 101),   # 0: legin (bearish exciting)
        (101, 103, 98, 102),    # 1: base candle 1 (boring)
        (106, 108, 102, 107),   # 2: gap up (boring, 106 > 103 → 2.9% gap) → gap is legout
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    assert z.strength_points == pytest.approx(2.0)
    # Gap alone = 1 unit (not explosive), but narrow base ratio < 1.5 → P3 picks WTW
    assert z.proximal_marking == "Wick-to-Wick"


def test_gap_only_legout_not_explosive_wide_base_gets_btw():
    """Gap-only legout (1 unit) with wide-wick base → not explosive → P3 → BTW."""
    rows = [
        (120, 121, 100, 101),   # 0: legin (bearish exciting)
        (101, 108, 95, 103),    # 1: base (boring, wide wicks: body=2/13, high=108, low=95)
        (110, 113, 107, 112),   # 2: gap up (boring, opens 110 > prev high 108 → 1.9% gap)
    ]
    zones = detect_zones(_make_df(rows))
    dbr = [z for z in zones if z.zone_type == "DBR"]
    assert len(dbr) == 1
    z = dbr[0]
    # Gap=1 unit, 0 exciting=0 units, total=1 → NOT explosive
    # BTW proximal=103, distal=95, width=8
    # WTW proximal=108, distal=95, width=13
    # ratio=13/8=1.625 > 1.5 → BTW
    assert z.proximal == 103.0
    assert z.proximal_marking == "Body-to-Wick"
