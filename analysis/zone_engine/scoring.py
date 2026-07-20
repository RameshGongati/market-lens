"""ODD (freshness / strength / time-at-base) trade scoring for zones.

Implements the documented institutional "Trade Score" (ODD — Odds
Enhancers, max 7 points) used to grade a freshly detected demand/supply
zone and recommend an entry approach, plus the qualitative zone-strength
label derived from the legout candles. Each helper's docstring cites the
exact rule it encodes.
"""

from __future__ import annotations

from typing import Sequence, TypedDict

import pandas as pd

from analysis.zone_engine.candles import CandleInfo
from analysis.zone_engine.models import Zone

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
_MEDIUM_BASE_POINTS = 1.0    # 4-5 base candles
_LONG_BASE_POINTS = 0.0      # > 5 base candles

# Rule: Entry recommendation thresholds (score is freshness+strength+time).
_AGGRESSIVE_ENTRY_SCORE = 7.0
_CONFIRMATION_ENTRY_SCORE = 5.0

# Rule: Stage 3 confluence rating — a SEPARATE bonus scorecard layered on
# top of the documented 7-point ODD ``odd_score`` above (never merged into
# it; see ``analysis.zone_engine.fibonacci`` / ``analysis.zone_engine.enhancers``
# module docstrings for why this stays additive context). Points:
#   * EMA 20 confluence (the zone's ``ema20_enhancer`` flag)        -> +1
#   * each Fibonacci retracement level inside the zone, capped at 2 -> +1 each
#   * the golden ratio (0.618) specifically inside the zone         -> +1 extra
_EMA20_CONFLUENCE_POINTS = 1
_FIB_LEVEL_POINTS = 1
_MAX_FIB_LEVEL_POINTS = 2
_GOLDEN_RATIO_BONUS_POINTS = 1
_GOLDEN_RATIO = 0.618

# Rule: Confluence labels — 0 points reads as "None", 1-2 as "Moderate",
# 3 or more as "High".
_MODERATE_CONFLUENCE_MIN = 1
_HIGH_CONFLUENCE_MIN = 3


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
    activation_touch: bool
    is_invalidated: bool


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
    """Rule: Time at base — 0-3 candles = 2, 4-5 candles = 1,
    more than 5 candles = 0.  Zero base candles = missing-base
    (M17 instant reversal) — maximum speed, maximum score."""
    if num_base_candles <= 3:
        return _SHORT_BASE_POINTS
    if 4 <= num_base_candles <= 5:
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


# Persistent habitation: if price re-enters the zone and N consecutive
# candles all close inside (between proximal and distal), the zone's
# institutional imbalance is exhausted and the zone is invalidated.
_HABITATION_LIMIT = 4


def count_zone_tests(
    df: pd.DataFrame, category: str, proximal: float, distal: float, start_idx: int,
) -> tuple[int, bool, bool]:
    """Rule: Count test cycles and detect zone invalidation (M3 + M46 + habitation).

    A test cycle is one complete round-trip: price enters the zone
    (wick touches proximal) AND exits (candle closes outside the zone).
    Entry is wick-based, exit is close-based.  A single candle can
    complete a full cycle if its wick enters the zone AND it closes
    outside the zone on the same bar (same-bar test).

    ``activation_touch`` is True when price has entered the zone at
    least once.

    Zone invalidation occurs on any of:
    - **M46 distal breach:** wick or close past the distal (strict >).
    - **Persistent habitation:** once inside, if ``_HABITATION_LIMIT``
      consecutive candles all close inside the zone, the imbalance is
      exhausted and the zone is dead.  The counter resets whenever a
      candle closes outside the zone (completing a test cycle).

    Args:
        df: Full OHLCV DataFrame (chronological order).
        category: ``"demand"`` or ``"supply"``.
        proximal: The zone's NORMAL proximal price line.
        distal: The zone's NORMAL distal price line.
        start_idx: First row index to scan from (typically ``legout_end + 1``).

    Returns:
        ``(times_tested, activation_touch, is_invalidated)``
    """
    tests = 0
    activation_touch = False
    is_invalidated = False
    inside = False
    consecutive_closes_inside = 0
    n = len(df)

    for idx in range(max(start_idx, 0), n):
        low = float(df["Low"].iloc[idx])
        high = float(df["High"].iloc[idx])
        close = float(df["Close"].iloc[idx])

        # M46: distal breach via wick — strict inequality (exactly AT = held)
        # M3: wick-based entry, close-based exit
        if category == "demand":
            in_zone = low <= proximal
            exited_zone = close > proximal
            breached = low < distal
        else:
            in_zone = high >= proximal
            exited_zone = close < proximal
            breached = high > distal

        if breached:
            activation_touch = True
            is_invalidated = True
            break

        if in_zone and not inside:
            inside = True
            activation_touch = True

        if inside and exited_zone:
            inside = False
            consecutive_closes_inside = 0
            tests += 1

        # Persistent habitation: count consecutive candles closing inside
        # the zone.  Resets on any close outside (exit branch above).
        if inside and not exited_zone:
            consecutive_closes_inside += 1
            if consecutive_closes_inside >= _HABITATION_LIMIT:
                is_invalidated = True
                break

    return tests, activation_touch, is_invalidated


def score_zone(
    df: pd.DataFrame,
    category: str,
    proximal: float,
    distal: float,
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
        distal: The zone's NORMAL distal price line.
        num_base_candles: Number of candles forming the base.
        has_gap: Whether the legout opened with a gap away from the base.
        legout_candles: Classification info for every legout candle.
        test_scan_start_idx: First row index to scan for re-entries
            (typically the index right after the legout run ends).

    Returns:
        A ``ZoneScore`` dict with the full scorecard and derived labels.
    """
    times_tested, activation_touch, is_invalidated = count_zone_tests(
        df, category, proximal, distal, test_scan_start_idx,
    )
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
        activation_touch=activation_touch,
        is_invalidated=is_invalidated,
    )


class ConfluenceRating(TypedDict):
    """Stage 3 confluence scorecard — additive context, kept entirely
    separate from the documented 7-point ODD ``odd_score`` above (see
    ``confluence_rating``)."""

    confluence_score: int
    confluence_label: str
    factors: list[str]


def confluence_rating(ema20_enhancer: bool, fib_result: dict) -> ConfluenceRating:
    """Rate how much independent confluence supports a zone — a SEPARATE
    bonus scorecard from the documented 7-point ODD ``odd_score`` (see the
    constants block above for the exact rule this encodes; never folded into
    ``odd_score`` — see ``analysis.zone_engine.fibonacci``/``enhancers``
    module docstrings for why this stays additive context).

    Points awarded (each cited inline):
      * EMA 20 confluence (``ema20_enhancer`` is True)                 -> +1
      * each Fibonacci retracement level inside the zone, capped at 2  -> +1 each
      * the golden ratio (0.618) specifically inside the zone          -> +1 extra

    Labels: 0 points -> "None", 1-2 points -> "Moderate", 3+ points -> "High".

    Args:
        ema20_enhancer: The zone's Stage 2 EMA 20 confluence flag (see
            ``analysis.zone_engine.enhancers.ema20_confluence``).
        fib_result: A ``FibConfluence`` dict (or any mapping with a
            ``levels_in_zone`` key), typically from
            ``analysis.zone_engine.fibonacci.fib_confluence``. Gracefully
            treated as "no Fib levels" when missing/empty.

    Returns:
        A ``ConfluenceRating`` dict with the total ``confluence_score``, its
        derived ``confluence_label``, and a human-readable ``factors`` list
        explaining what contributed (for summaries/tooltips).
    """
    factors: list[str] = []
    score = 0

    if ema20_enhancer:
        score += _EMA20_CONFLUENCE_POINTS
        factors.append("EMA 20 confluence")

    levels_in_zone: list[float] = list((fib_result or {}).get("levels_in_zone", []))
    fib_points = min(len(levels_in_zone), _MAX_FIB_LEVEL_POINTS) * _FIB_LEVEL_POINTS
    if fib_points:
        score += fib_points
        noun = "level" if fib_points == _FIB_LEVEL_POINTS else "levels"
        factors.append(f"{fib_points} Fib {noun} in zone")

    if _GOLDEN_RATIO in levels_in_zone:
        score += _GOLDEN_RATIO_BONUS_POINTS
        factors.append("Golden ratio (0.618) in zone")

    if score >= _HIGH_CONFLUENCE_MIN:
        label = "High"
    elif score >= _MODERATE_CONFLUENCE_MIN:
        label = "Moderate"
    else:
        label = "None"

    return ConfluenceRating(confluence_score=score, confluence_label=label, factors=factors)


# ---------------------------------------------------------------------------
# M8: Closing concept — leg-out quality vs. opposing zones
# ---------------------------------------------------------------------------

def assess_closing_quality(zones: list[Zone], df: pd.DataFrame) -> None:
    """M8 (closing concept): for each zone, check whether its leg-out
    CLOSED beyond the nearest opposing zone's proximal line.

    A close beyond proves the opposing orders were absorbed (strong
    departure).  A wick-only penetration or a close that fell short
    means the departure is unconvincing (weak).  When no opposing zone
    sits in the leg-out's path, the check is inapplicable (unchecked).

    Mutates ``zone.closing_quality`` in place:
      * ``"strong"``    — leg-out close cleared the opposing proximal
      * ``"weak"``      — leg-out close did NOT clear
      * ``"unchecked"`` — no opposing zone in the leg-out's path

    This is a quality flag only — it does NOT change the ODD score.

    Args:
        zones: The full list of detected (non-invalidated) zones,
               in chronological order (by ``created_at_index``).
        df:    The OHLCV DataFrame used during detection.
    """
    for i, zone in enumerate(zones):
        opposing = _find_nearest_opposing_zone(zone, zones[:i], df)
        if opposing is None:
            zone.closing_quality = "unchecked"
            continue

        legout_close = float(df["Close"].iloc[zone.legout_idx])

        if zone.category == "demand":
            # Demand leg-out rallies up — must close ABOVE opposing
            # supply zone's proximal to prove strength.
            zone.closing_quality = (
                "strong" if legout_close > opposing.proximal else "weak"
            )
        else:
            # Supply leg-out drops down — must close BELOW opposing
            # demand zone's proximal to prove strength.
            zone.closing_quality = (
                "strong" if legout_close < opposing.proximal else "weak"
            )


def _find_nearest_opposing_zone(
    zone: Zone,
    prior_zones: list[Zone],
    df: pd.DataFrame,
) -> Zone | None:
    """Find the nearest opposing zone whose proximal sits in the leg-out's
    departure path — the first obstacle the leg-out encountered.

    "Nearest" means closest to the base in price (the first obstacle),
    not chronologically closest.

    For a DEMAND zone (bullish leg-out):
      Search for SUPPLY zones whose proximal is between the base top
      and the leg-out's highest high.

    For a SUPPLY zone (bearish leg-out):
      Search for DEMAND zones whose proximal is between the base bottom
      and the leg-out's lowest low.

    Only zones that formed BEFORE this zone (``created_at_index`` <
    this zone's) are considered — future zones can't be obstacles the
    leg-out needed to overcome.  Invalidated zones are excluded because
    they are already absent from the ``zones`` list.

    Args:
        zone:        The zone being assessed.
        prior_zones: All zones with ``created_at_index`` < this zone's
                     (the slice ``zones[:i]`` from the caller).
        df:          The OHLCV DataFrame for reading leg-out extremes.

    Returns:
        The nearest opposing Zone, or None if no opposing zone sits
        in the leg-out's path.
    """
    # Determine the leg-out's price range (the path it traversed).
    legout_start = zone.legout_idx
    # Find legout end: scan forward from legout_start for exciting
    # candles in the same direction, bounded by created_at_index of
    # the next zone or the end of the dataframe.
    legout_end = legout_start
    n = len(df)
    for idx in range(legout_start + 1, n):
        # Stop at a reasonable bound — legout runs are short.
        if idx - legout_start > 6:
            break
        legout_end = idx

    if zone.category == "demand":
        # Bullish leg-out: path goes from base top up to leg-out high.
        base_top = float(df["High"].iloc[zone.base_start_idx: zone.base_end_idx + 1].max())
        legout_extreme = float(df["High"].iloc[legout_start: legout_end + 1].max())

        # Find supply zones whose proximal is in the leg-out's path.
        candidates = [
            z for z in prior_zones
            if z.category == "supply"
            and z.created_at_index < zone.created_at_index
            and base_top < z.proximal <= legout_extreme
        ]
        if not candidates:
            return None
        # Nearest to base = lowest proximal (first obstacle going up).
        return min(candidates, key=lambda z: z.proximal)

    else:
        # Bearish leg-out: path goes from base bottom down to leg-out low.
        base_bottom = float(df["Low"].iloc[zone.base_start_idx: zone.base_end_idx + 1].min())
        legout_extreme = float(df["Low"].iloc[legout_start: legout_end + 1].min())

        # Find demand zones whose proximal is in the leg-out's path.
        candidates = [
            z for z in prior_zones
            if z.category == "demand"
            and z.created_at_index < zone.created_at_index
            and legout_extreme <= z.proximal < base_bottom
        ]
        if not candidates:
            return None
        # Nearest to base = highest proximal (first obstacle going down).
        return max(candidates, key=lambda z: z.proximal)
