"""Fibonacci retracement confluence — Stage 3's optional zone enhancer.

The documented Fibonacci confluence rule says a demand/supply zone becomes
higher probability when one of the well-watched retracement ratios — the
0.382/0.5/0.618 ("golden ratio")/0.786 levels of the most recent significant
swing — lines up with it: institutions defending a level *and* a price most
traders are independently watching is a stronger signal than either alone.

This mirrors the architecture of the Stage 2 enhancers (``trend``,
``enhancers``): three small, independently testable pieces —

  1. ``find_recent_swing``   — anchor the retracement to the most recent
     significant swing high/low.
  2. ``calculate_fib_levels`` — turn that swing into the four documented
     retracement price levels.
  3. ``fib_confluence``      — check whether any of those levels lines up
     with a given zone.

— that ``analysis.demand_supply`` composes into a per-zone confluence flag
and a separate ``confluence_rating`` (see ``analysis.zone_engine.scoring``).
None of this touches Stage 1 detection/scoring or Stage 2 trend/EMA20 math;
it only ever *reads* OHLCV closes/highs/lows and the zones already produced.

Like ``trend``/``enhancers``, this is purely additive *context* — it is
opt-in (``use_fibonacci``) and, when switched off, every Stage 3 field on a
``Zone`` stays at its conservative default (see ``analysis.zone_engine.models``).
"""

import math
from typing import TypedDict

import numpy as np
import pandas as pd

from analysis.zone_engine.models import Zone

# Rule: swing detection — within how many of the most recent candles do we
# look for the anchoring swing high/low, and the minimum history required to
# even attempt it (need at least two candles to have a meaningful high/low
# pair with a chronological order).
_DEFAULT_LOOKBACK = 120
_MIN_SWING_CANDLES = 2

# Rule: documented Fibonacci retracement ratios used for confluence — the
# 38.2%, 50%, 61.8% ("golden ratio") and 78.6% levels of the anchoring swing.
FIB_LEVELS: list[float] = [0.382, 0.5, 0.618, 0.786]

# Rule: golden ratio — 0.618 gets an extra confluence-rating bonus (see
# ``analysis.zone_engine.scoring.confluence_rating``) and is the top
# "strongest level" priority below.
GOLDEN_RATIO = 0.618

# Rule: "strongest level" priority — when more than one Fib level lines up
# with a zone, prefer reporting the golden ratio (0.618) first, then 0.786,
# then 0.5, then 0.382 (the documented importance ranking of these ratios).
_STRONGEST_LEVEL_PRIORITY: list[float] = [0.618, 0.786, 0.5, 0.382]

# Rule: "near zone" proximity — how close (as a percent of the boundary
# price) a Fib level must sit to a zone's edge to count as "near" it, even
# when it doesn't fall inside the zone's distal/proximal range.
_DEFAULT_PROXIMITY_PCT = 1.0


class SwingInfo(TypedDict):
    """Anchor points (swing high/low) for Fibonacci retracement levels."""

    swing_high: float | None    # highest high within the lookback window
    swing_low: float | None     # lowest low within the lookback window
    swing_high_idx: int | None  # row index of the swing high candle
    swing_low_idx: int | None   # row index of the swing low candle
    direction: str | None       # "up" | "down" | None (insufficient data)


class FibConfluence(TypedDict):
    """Fibonacci confluence check result for a single zone."""

    levels_in_zone: list[float]     # ratios whose price falls inside [distal, proximal]
    levels_near_zone: list[float]   # ratios within proximity_pct% of either boundary
    has_confluence: bool            # True when at least one level is in or near the zone
    confluence_count: int           # how many levels fall *inside* the zone
    strongest_level: float | None   # highest-priority level in (then near) the zone


def _no_swing() -> SwingInfo:
    """Conservative "can't tell yet" result for short/edge-case data —
    every field ``None`` (no direction can be inferred)."""
    return SwingInfo(
        swing_high=None,
        swing_low=None,
        swing_high_idx=None,
        swing_low_idx=None,
        direction=None,
    )


def _no_fib_confluence() -> FibConfluence:
    """Conservative "no confluence found" result — used both when there are
    no Fib levels to check (e.g. swing detection failed) and when none of
    them line up with the zone."""
    return FibConfluence(
        levels_in_zone=[],
        levels_near_zone=[],
        has_confluence=False,
        confluence_count=0,
        strongest_level=None,
    )


def find_recent_swing(df: pd.DataFrame, lookback: int = _DEFAULT_LOOKBACK) -> SwingInfo:
    """Identify the most recent significant swing high and swing low to
    anchor Fibonacci retracements.

    Uses the simple, robust "absolute extremes" method: within the last
    ``lookback`` candles, the swing high is the single highest ``High`` and
    the swing low is the single lowest ``Low``. The swing's ``direction`` is
    derived from which one came first chronologically:

      * ``"up"``   — the swing low occurred *before* the swing high (price
        swung up into the high — retracements are measured down from it).
      * ``"down"`` — the swing high occurred *before* (or at the same
        candle as) the swing low (price swung down into the low —
        retracements are measured up from it).

    Args:
        df: Full OHLCV DataFrame (chronological order, needs ``High``/``Low``
            columns).
        lookback: How many of the most recent candles to scan for the swing
            (default 120). When the DataFrame is shorter than this, the
            entire history is used.

    Returns:
        A ``SwingInfo`` dict. When there isn't enough history to identify a
        meaningful swing (fewer than two candles, or an empty DataFrame),
        every field is conservatively ``None`` — "can't anchor yet".
    """
    if df.empty or len(df) < _MIN_SWING_CANDLES:
        return _no_swing()

    start = max(0, len(df) - lookback)
    high = df["High"].iloc[start:].to_numpy(dtype=float)
    low = df["Low"].iloc[start:].to_numpy(dtype=float)

    # Guard: ignore partial/empty candles whose High/Low are non-finite or
    # non-positive. Intraday/resampled feeds (and fallback fetches) can carry
    # a 0.0 or NaN bar; without this filter ``Low.min()`` would anchor
    # ``swing_low`` at 0, and ``calculate_fib_levels`` would then place every
    # retracement near the bottom of the chart instead of in the real price
    # range. Invalid bars are masked to -inf (for the max) / +inf (for the
    # min) so ``argmax``/``argmin`` skip them while positional indices —
    # which the direction check below depends on — stay intact.
    high_valid = np.isfinite(high) & (high > 0)
    low_valid = np.isfinite(low) & (low > 0)
    if not high_valid.any() or not low_valid.any():
        return _no_swing()

    high_masked = np.where(high_valid, high, -np.inf)
    low_masked = np.where(low_valid, low, np.inf)
    high_pos = int(high_masked.argmax())
    low_pos = int(low_masked.argmin())

    swing_high_idx = start + high_pos
    swing_low_idx = start + low_pos
    swing_high = float(high[high_pos])
    swing_low = float(low[low_pos])

    direction = "up" if swing_low_idx < swing_high_idx else "down"

    return SwingInfo(
        swing_high=swing_high,
        swing_low=swing_low,
        swing_high_idx=swing_high_idx,
        swing_low_idx=swing_low_idx,
        direction=direction,
    )


def calculate_fib_levels(swing: SwingInfo) -> dict[float, float]:
    """Turn an anchoring swing into the four documented Fibonacci retracement
    price levels.

    Rule: the retracement is measured back *into* the swing's range
    (``range = swing_high - swing_low``) from the end the move finished at:

      * ``"up"`` swing   — retracements pull back *down* from the high:
        ``level = swing_high - (range * ratio)``
      * ``"down"`` swing — retracements pull back *up* from the low:
        ``level = swing_low + (range * ratio)``

    Args:
        swing: A ``SwingInfo`` dict, typically from ``find_recent_swing``.

    Returns:
        A ``{ratio: price}`` dict for each ratio in ``FIB_LEVELS`` (0.382,
        0.5, 0.618, 0.786). Returns an empty dict when the swing couldn't be
        determined (any of ``swing_high``/``swing_low``/``direction`` is
        ``None``) — graceful "nothing to anchor to" handling.
    """
    swing_high = swing.get("swing_high")
    swing_low = swing.get("swing_low")
    direction = swing.get("direction")
    if swing_high is None or swing_low is None or direction is None:
        return {}

    # Guard: refuse to build levels from a degenerate swing — a non-finite
    # (NaN/inf) or non-positive anchor would otherwise yield NaN or ~0 prices
    # that collapse the Fibonacci lines to the bottom of the chart. Returning
    # ``{}`` makes the drawing code (and confluence checks) draw nothing,
    # exactly as when no swing could be found.
    if not (math.isfinite(swing_high) and math.isfinite(swing_low)):
        return {}
    if swing_high <= 0 or swing_low <= 0:
        return {}

    swing_range = swing_high - swing_low
    levels: dict[float, float] = {}
    for ratio in FIB_LEVELS:
        if direction == "up":
            price = swing_high - (swing_range * ratio)
        else:
            price = swing_low + (swing_range * ratio)
        # Final defensive check — never emit a non-finite or non-positive
        # level price.
        if math.isfinite(price) and price > 0:
            levels[ratio] = price
    return levels


def fib_confluence(
    zone: Zone,
    fib_levels: dict[float, float],
    proximity_pct: float = _DEFAULT_PROXIMITY_PCT,
) -> FibConfluence:
    """Check whether any Fibonacci retracement level lines up with *zone*
    (a confluence bonus, mirroring ``enhancers.ema20_confluence``).

    Steps (each cited inline):
      1. ``levels_in_zone`` — ratios whose price sits between the zone's
         ``distal``/``proximal`` lines (orientation-independent — see
         ``analysis.zone_engine.filters._zone_range`` for why the raw
         ``[distal, proximal]`` pair can't be compared directly).
      2. ``levels_near_zone`` — ratios that don't fall inside the zone but
         whose price sits within ``proximity_pct`` percent of *either*
         boundary price.
      3. ``has_confluence`` — True when at least one level is in *or* near
         the zone.
      4. ``confluence_count`` — how many levels fall *inside* the zone (the
         strict, higher-conviction count feeding ``confluence_rating``).
      5. ``strongest_level`` — the highest-priority ratio among those in the
         zone (falling back to those merely near it) per the documented
         importance ranking: 0.618 ("golden ratio") > 0.786 > 0.5 > 0.382.

    Args:
        zone: The zone to check for Fibonacci confluence.
        fib_levels: ``{ratio: price}`` mapping, typically from
            ``calculate_fib_levels``.
        proximity_pct: How close (percent of the boundary price) counts as
            "near" a zone boundary (default 1.0%).

    Returns:
        A ``FibConfluence`` dict. When there are no levels to check (e.g.
        swing detection failed and ``fib_levels`` is empty), or none of them
        line up with the zone, every field is conservatively
        ``False``/``[]``/``0``/``None`` — "no confluence found".
    """
    if not fib_levels:
        return _no_fib_confluence()

    lo = min(zone.proximal, zone.distal)
    hi = max(zone.proximal, zone.distal)
    tolerance_lo = abs(lo) * (proximity_pct / 100.0)
    tolerance_hi = abs(hi) * (proximity_pct / 100.0)

    levels_in_zone: list[float] = []
    levels_near_zone: list[float] = []
    for ratio, price in fib_levels.items():
        if lo <= price <= hi:
            levels_in_zone.append(ratio)
        elif abs(price - lo) <= tolerance_lo or abs(price - hi) <= tolerance_hi:
            levels_near_zone.append(ratio)

    strongest_level: float | None = None
    for preferred in _STRONGEST_LEVEL_PRIORITY:
        if preferred in levels_in_zone:
            strongest_level = preferred
            break
    if strongest_level is None:
        for preferred in _STRONGEST_LEVEL_PRIORITY:
            if preferred in levels_near_zone:
                strongest_level = preferred
                break

    return FibConfluence(
        levels_in_zone=levels_in_zone,
        levels_near_zone=levels_near_zone,
        has_confluence=bool(levels_in_zone or levels_near_zone),
        confluence_count=len(levels_in_zone),
        strongest_level=strongest_level,
    )
