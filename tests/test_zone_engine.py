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
from analysis.zone_engine.scoring import confluence_rating, entry_recommendation, time_at_base_points
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

    # Rule: DEMAND NORMAL marking —
    #   proximal = highest BODY top of the base = max(max(111,112), max(112,113)) = 113
    #   distal   = lowest WICK (lowest low) of the base = min(109, 108) = 108
    assert zone.proximal == pytest.approx(113)
    assert zone.distal == pytest.approx(108)
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

    # Rule: SUPPLY NORMAL marking —
    #   proximal = lowest BODY bottom of the base = min(min(111,110), min(110,109)) = 109
    #   distal   = highest WICK (highest high) of the base = max(113, 112) = 113
    assert zone.proximal == pytest.approx(109)
    assert zone.distal == pytest.approx(113)
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
# ODD score: one return = activation touch (M3), NOT a test -> still fresh
# ---------------------------------------------------------------------------

# Same clean DBR structure (proximal = 113) followed by candles engineered
# so price re-enters the zone (low <= 113) in exactly one contiguous visit.
# GTF M3: the first return is the activation touch, not a test.
_DBR_ONE_RETURN_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal = 113
    (114, 125, 113, 124),   # 3: legout (bullish, exciting), no gap, single candle
    (124, 128, 120, 126),   # 4: away from zone   (low=120 > 113 -> no touch)
    (118, 120, 111, 119),   # 5: enters the zone  (low=111 <= 113 -> activation touch)
    (119, 123, 115, 122),   # 6: leaves the zone  (low=115 > 113 -> touch ends)
    (118, 122, 116, 121),   # 7: stays away       (low=116 > 113 -> still no touch)
]


def test_m3_one_return_is_activation_touch_not_a_test():
    """GTF M3: the first return to the zone is your planned entry, not a
    test. times_tested should be 0 and freshness should be 3.0 (fresh)."""
    df = _make_df(_DBR_ONE_RETURN_ROWS)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]

    assert zone.proximal == pytest.approx(113)
    assert zone.times_tested == 0
    assert zone.is_fresh is True
    assert zone.freshness_points == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# "No Trade" recommendation when the total ODD score is below 5
# ---------------------------------------------------------------------------

# Same clean DBR structure (proximal = 113), but the legout is a single,
# non-gapping exciting candle (strength = 1) and price returns to the zone
# three times (M3: first return = activation, so tests = 3-1 = 2,
# freshness = 0). 0 (freshness) + 1 (strength) + 2 (time) = 3.
_DBR_NO_TRADE_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal = 113
    (113, 126, 112, 125),   # 3: legout opens at 113 (<= base high 114 -> no gap)
    (124, 128, 120, 126),   # 4: away from zone (low=120 > 113)
    (118, 120, 111, 119),   # 5: return #1 (activation touch, M3 — not a test)
    (119, 123, 115, 122),   # 6: leaves zone
    (116, 119, 109, 117),   # 7: return #2 (test #1)
    (117, 122, 114, 120),   # 8: leaves zone
    (116, 118, 110, 115),   # 9: return #3 (test #2)
    (115, 121, 114, 120),   # 10: leaves zone
]


def test_no_trade_recommendation_when_score_below_five():
    df = _make_df(_DBR_NO_TRADE_ROWS)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]

    assert zone.times_tested == 2
    assert zone.freshness_points == pytest.approx(0.0)
    assert zone.strength_points == pytest.approx(1.0)
    assert zone.time_points == pytest.approx(2.0)
    assert zone.odd_score == pytest.approx(3.0)
    assert zone.odd_score < 5
    assert zone.entry_recommendation == "No Trade"


# ---------------------------------------------------------------------------
# M3: two returns = one test (activation touch excluded)
# ---------------------------------------------------------------------------

_DBR_TWO_RETURNS_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal = 113
    (114, 125, 113, 124),   # 3: legout (bullish, exciting), no gap
    (124, 128, 120, 126),   # 4: away from zone
    (118, 120, 111, 119),   # 5: return #1 (activation touch — not a test)
    (119, 123, 115, 122),   # 6: leaves zone
    (116, 119, 109, 117),   # 7: return #2 (test #1)
    (117, 122, 114, 120),   # 8: leaves zone
]


def test_m3_two_returns_counts_as_one_test():
    """GTF M3: two returns = activation + 1 real test -> freshness 1.5."""
    df = _make_df(_DBR_TWO_RETURNS_ROWS)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.times_tested == 1
    assert zone.is_fresh is False
    assert zone.freshness_points == pytest.approx(1.5)


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
