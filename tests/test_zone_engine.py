"""Unit tests for the institutional demand/supply zone engine.

These tests hand-craft small OHLC sequences that deliberately produce
specific candle shapes / legin-base-legout structures, and assert the
zone engine classifies and scores them exactly per the documented
methodology (see analysis/zone_engine/*).
"""

from __future__ import annotations

import pandas as pd
import pytest

from analysis.zone_engine.candles import classify_candle
from analysis.zone_engine.patterns import detect_zones
from analysis.zone_engine.scoring import entry_recommendation


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
    """body_pct = 9/15 = 0.60 -> exciting (>= 0.60) but not strong (< 0.80)."""
    info = classify_candle(open_=100, high=110, low=95, close=109)
    assert info["body_pct"] == pytest.approx(0.60)
    assert info["is_exciting"] is True
    assert info["is_strong"] is False
    assert info["is_boring"] is False
    assert info["direction"] == "bullish"


def test_classify_candle_strong():
    """body_pct = 12/12 = 1.00 -> strong exciting (>= 0.80), implies exciting."""
    info = classify_candle(open_=100, high=112, low=100, close=112)
    assert info["body_pct"] == pytest.approx(1.0)
    assert info["is_exciting"] is True
    assert info["is_strong"] is True
    assert info["is_boring"] is False
    assert info["direction"] == "bullish"


def test_classify_candle_indecisive_band_treated_as_boring():
    """0.50 < body_pct < 0.60 is the documented 'indecisive' band, which the
    spec says to treat as boring for base purposes."""
    # range = 5.5, body = 3 -> body_pct = 3/5.5 ≈ 0.545 (within the 0.50-0.60 band)
    info = classify_candle(open_=100, high=105.5, low=100, close=103)
    assert 0.50 < info["body_pct"] < 0.60
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
# ODD score: zone tested exactly once -> freshness points = 1.5
# ---------------------------------------------------------------------------

# Same clean DBR structure (proximal = 113) followed by candles engineered
# so price re-enters the zone (low <= 113) in exactly one contiguous visit.
_DBR_TESTED_ONCE_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal = 113
    (114, 125, 113, 124),   # 3: legout (bullish, exciting), no gap, single candle
    (124, 128, 120, 126),   # 4: away from zone   (low=120 > 113 -> no touch)
    (118, 120, 111, 119),   # 5: enters the zone  (low=111 <= 113 -> touch #1 starts)
    (119, 123, 115, 122),   # 6: leaves the zone  (low=115 > 113 -> touch #1 ends)
    (118, 122, 116, 121),   # 7: stays away       (low=116 > 113 -> still no touch)
]


def test_odd_score_zone_tested_once_has_freshness_points_one_point_five():
    df = _make_df(_DBR_TESTED_ONCE_ROWS)
    zones = detect_zones(df)

    assert len(zones) == 1
    zone = zones[0]

    assert zone.proximal == pytest.approx(113)
    assert zone.times_tested == 1
    assert zone.is_fresh is False
    # Rule: Freshness — tested exactly once = 1.5 points
    assert zone.freshness_points == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# "No Trade" recommendation when the total ODD score is below 5
# ---------------------------------------------------------------------------

# Same clean DBR structure (proximal = 113), but the legout is a single,
# non-gapping exciting candle (strength = 1) and price re-tests the zone
# twice afterwards (freshness = 0). 0 (freshness) + 1 (strength) + 2 (time) = 3.
_DBR_NO_TRADE_ROWS = [
    (120, 121, 110, 111),   # 0: legin (bearish, exciting)
    (111, 114, 109, 112),   # 1: base candle 1 (boring)
    (112, 115, 108, 113),   # 2: base candle 2 (boring) -> proximal = 113
    (113, 126, 112, 125),   # 3: legout opens at 113 (<= base high 114 -> no gap)
    (124, 128, 120, 126),   # 4: away from zone (low=120 > 113)
    (118, 120, 111, 119),   # 5: touch #1 starts (low=111 <= 113)
    (119, 123, 115, 122),   # 6: touch #1 ends   (low=115 > 113)
    (116, 119, 109, 117),   # 7: touch #2 starts (low=109 <= 113)
    (117, 122, 114, 120),   # 8: touch #2 ends   (low=114 > 113)
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
# Graceful handling of empty/short data
# ---------------------------------------------------------------------------

def test_detect_zones_handles_empty_dataframe():
    df = _make_df([])
    assert detect_zones(df) == []


def test_detect_zones_handles_short_dataframe():
    df = _make_df([(100, 105, 95, 102), (102, 108, 100, 106)])
    assert detect_zones(df) == []
