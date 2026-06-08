"""EMA 20 confluence — an additive "high probability" zone enhancer.

The documented moving-average confluence rule says a zone becomes higher
probability when the 20-period EMA sits inside it (or just outside one of
its boundaries): institutions defending a level *and* a widely-watched
moving average lining up at the same price is a stronger signal than
either alone.

This is purely additive *context* — a bonus flag reported alongside a
zone, never folded into the documented 7-point ODD ``odd_score`` (Stage 1
scoring math is untouched; see ``analysis.zone_engine.scoring``).
"""

from typing import TypedDict

import pandas as pd

from analysis.zone_engine.models import Zone

# Rule: EMA 20 confluence — default EMA period and how close (as a percent
# of the boundary price) the EMA must sit to a zone's edge to count as
# "near" it.
_DEFAULT_EMA_PERIOD = 20
_DEFAULT_PROXIMITY_PCT = 2.0


class EmaConfluence(TypedDict):
    """EMA 20 confluence check result for a single zone."""

    ema_now: float | None   # EMA value at the latest candle (None if too little data)
    in_zone: bool           # True when ema_now sits between the zone's distal/proximal lines
    near_zone: bool         # True when ema_now sits within proximity_pct% of either boundary
    is_enhancer: bool       # True when in_zone OR near_zone — the "high probability" flag


def _no_confluence() -> EmaConfluence:
    """Conservative "can't tell yet" result for short/edge-case data."""
    return EmaConfluence(ema_now=None, in_zone=False, near_zone=False, is_enhancer=False)


def ema20_confluence(
    df: pd.DataFrame,
    zone: Zone,
    ema_period: int = _DEFAULT_EMA_PERIOD,
    proximity_pct: float = _DEFAULT_PROXIMITY_PCT,
) -> EmaConfluence:
    """Check whether the 20-period EMA lines up with *zone* (a confluence bonus).

    Steps (each cited inline):
      1. Compute the ``ema_period``-period EMA on closing prices.
      2. ``ema_now`` — the EMA value at the latest candle.
      3. ``in_zone`` — True when ``ema_now`` sits between the zone's
         ``distal``/``proximal`` lines (orientation-independent — see
         ``analysis.zone_engine.filters._zone_range`` for why the raw
         ``[distal, proximal]`` pair can't be compared directly).
      4. ``near_zone`` — True when ``ema_now`` sits within
         ``proximity_pct`` percent of *either* boundary price, even if
         it's not actually inside the zone.
      5. ``is_enhancer`` — True when the zone is "high probability" per
         the MA confluence rule: ``in_zone OR near_zone``.

    Args:
        df: Full OHLCV DataFrame (chronological order, needs a ``Close``
            column).
        zone: The zone to check for EMA confluence.
        ema_period: Number of candles in the EMA window (default 20).
        proximity_pct: How close (percent of the boundary price) counts
            as "near" a zone boundary (default 2.0%).

    Returns:
        An ``EmaConfluence`` dict. When there isn't enough history to
        compute the EMA (fewer than ``ema_period`` candles), every field
        is conservatively ``False``/``None`` — "no confluence found".
    """
    if df.empty or len(df) < ema_period:
        return _no_confluence()

    ema_series = df["Close"].ewm(span=ema_period, adjust=False).mean()
    ema_now_raw = ema_series.iloc[-1]
    if pd.isna(ema_now_raw):
        return _no_confluence()

    ema_now = float(ema_now_raw)
    lo = min(zone.proximal, zone.distal)
    hi = max(zone.proximal, zone.distal)

    in_zone = lo <= ema_now <= hi

    tolerance_lo = abs(lo) * (proximity_pct / 100.0)
    tolerance_hi = abs(hi) * (proximity_pct / 100.0)
    near_zone = abs(ema_now - lo) <= tolerance_lo or abs(ema_now - hi) <= tolerance_hi

    return EmaConfluence(
        ema_now=ema_now,
        in_zone=in_zone,
        near_zone=near_zone,
        is_enhancer=in_zone or near_zone,
    )
