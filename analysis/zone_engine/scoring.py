"""ODD (freshness / strength / time-at-base) trade scoring for zones.

Implements the documented institutional "Trade Score" (ODD — Odds
Enhancers, max 7 points) used to grade a freshly detected demand/supply
zone and recommend an entry approach, plus the qualitative zone-strength
label derived from the legout candles. Each helper's docstring cites the
exact rule it encodes.
"""

from typing import Sequence, TypedDict

import pandas as pd

from analysis.zone_engine.candles import CandleInfo

# Rule: Freshness points — how many times has price re-tested the zone since
# the legout completed?
_FRESH_POINTS = 3.0          # never tested
_TESTED_ONCE_POINTS = 1.5    # tested exactly once
_TESTED_MANY_POINTS = 0.0    # tested twice or more

# Rule: Strength points — quality of the legout move away from the base.
_STRONG_LEGOUT_POINTS = 2.0  # gap away from base, OR 2+ exciting legout candles
_WEAK_LEGOUT_POINTS = 1.0    # exactly one exciting candle and no gap

# Rule: Time-at-base points — fewer base candles means a fresher imbalance.
_SHORT_BASE_POINTS = 2.0     # 1-3 base candles
_MEDIUM_BASE_POINTS = 1.0    # 4-6 base candles
_LONG_BASE_POINTS = 0.0      # > 6 base candles

# Rule: Entry recommendation thresholds (score is freshness+strength+time).
_AGGRESSIVE_ENTRY_SCORE = 7.0
_CONFIRMATION_ENTRY_SCORE = 5.0


class ZoneScore(TypedDict):
    """Computed ODD scorecard plus the labels derived from it."""

    odd_score: float
    freshness_points: float
    strength_points: float
    time_points: float
    times_tested: int
    zone_strength: str
    entry_recommendation: str
    is_fresh: bool


def freshness_points(times_tested: int) -> float:
    """Rule: Freshness — fresh (never tested) = 3, tested once = 1.5,
    tested twice or more = 0."""
    if times_tested <= 0:
        return _FRESH_POINTS
    if times_tested == 1:
        return _TESTED_ONCE_POINTS
    return _TESTED_MANY_POINTS


def strength_points(has_gap: bool, num_legout_candles: int) -> float:
    """Rule: Strength (legout quality) — a gap away from the base, or two
    or more exciting legout candles, scores 2; a single exciting candle
    with no gap scores 1."""
    if has_gap or num_legout_candles >= 2:
        return _STRONG_LEGOUT_POINTS
    return _WEAK_LEGOUT_POINTS


def time_at_base_points(num_base_candles: int) -> float:
    """Rule: Time at base — 1-3 candles = 2, 4-6 candles = 1,
    more than 6 candles = 0."""
    if 1 <= num_base_candles <= 3:
        return _SHORT_BASE_POINTS
    if 4 <= num_base_candles <= 6:
        return _MEDIUM_BASE_POINTS
    return _LONG_BASE_POINTS


def entry_recommendation(score: float) -> str:
    """Rule: Entry recommendation — a perfect score of 7 supports an
    aggressive Entry Type 1; 5-6 calls for a confirmation-based Entry
    Type 2/3; anything below 5 means no trade."""
    if score >= _AGGRESSIVE_ENTRY_SCORE:
        return "Entry Type 1 (aggressive)"
    if score >= _CONFIRMATION_ENTRY_SCORE:
        return "Entry Type 2/3 (confirmation)"
    return "No Trade"


def zone_strength_label(legout_candles: Sequence[CandleInfo]) -> str:
    """Rule: Zone strength label — derived from how many STRONG exciting
    candles (body_pct >= 0.80) make up the legout:

      * 0 strong candles  -> "Normal"      (a single normal exciting candle
                                             was enough to leave the base)
      * 1 strong candle   -> "Strong"
      * 2+ strong candles -> "Very Strong" (continuation-grade momentum)
    """
    strong_count = sum(1 for c in legout_candles if c["is_strong"])
    if strong_count >= 2:
        return "Very Strong"
    if strong_count == 1:
        return "Strong"
    return "Normal"


def count_zone_tests(df: pd.DataFrame, category: str, proximal: float, start_idx: int) -> int:
    """Rule: "Tested" — count distinct re-entries into the zone after the
    legout completes. A demand zone is re-entered when a later candle's low
    crosses below its proximal line; a supply zone is re-entered when a
    later candle's high crosses above its proximal line. A run of
    consecutive candles that stay inside the zone counts as a single visit
    (one "test"), matching how traders describe a zone as "tested once" /
    "tested twice".

    Args:
        df: Full OHLCV DataFrame (chronological order).
        category: ``"demand"`` or ``"supply"``.
        proximal: The zone's NORMAL proximal price line.
        start_idx: First row index to scan from (typically ``legout_end + 1``).

    Returns:
        The number of distinct times price has re-entered the zone.
    """
    tests = 0
    inside = False
    n = len(df)
    for idx in range(max(start_idx, 0), n):
        low = float(df["Low"].iloc[idx])
        high = float(df["High"].iloc[idx])
        touching = (low <= proximal) if category == "demand" else (high >= proximal)
        if touching and not inside:
            tests += 1
        inside = touching
    return tests


def score_zone(
    df: pd.DataFrame,
    category: str,
    proximal: float,
    num_base_candles: int,
    has_gap: bool,
    legout_candles: Sequence[CandleInfo],
    test_scan_start_idx: int,
) -> ZoneScore:
    """Compute the full ODD Trade Score plus the labels derived from it.

    This combines the three ODD components (freshness + strength + time at
    base, each documented above) into the 0-7 ``odd_score``, and derives
    ``times_tested``, ``zone_strength``, ``entry_recommendation`` and
    ``is_fresh`` from the same inputs.

    Args:
        df: Full OHLCV DataFrame.
        category: ``"demand"`` or ``"supply"``.
        proximal: The zone's NORMAL proximal price line.
        num_base_candles: Number of candles forming the base.
        has_gap: Whether the legout opened with a gap away from the base.
        legout_candles: Classification info for every legout candle.
        test_scan_start_idx: First row index to scan for re-entries
            (typically the index right after the legout run ends).

    Returns:
        A ``ZoneScore`` dict with the full scorecard and derived labels.
    """
    times_tested = count_zone_tests(df, category, proximal, test_scan_start_idx)
    f_pts = freshness_points(times_tested)
    s_pts = strength_points(has_gap, len(legout_candles))
    t_pts = time_at_base_points(num_base_candles)
    total = f_pts + s_pts + t_pts

    return ZoneScore(
        odd_score=total,
        freshness_points=f_pts,
        strength_points=s_pts,
        time_points=t_pts,
        times_tested=times_tested,
        zone_strength=zone_strength_label(legout_candles),
        entry_recommendation=entry_recommendation(total),
        is_fresh=times_tested == 0,
    )
