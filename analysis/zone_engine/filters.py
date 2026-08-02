"""Display-oriented filtering for detected demand/supply zones.

``detect_zones`` deliberately reports every structure it finds across the
whole price history — useful for analysis, but far too noisy to draw on a
chart (20+ overlapping rectangles). ``filter_zones`` reduces that raw list
down to the small set of zones that are actually meaningful and tradeable
*right now*, for display purposes only.

This module does not alter any detection or scoring math from Stage 1 —
it only selects, merges and ranks the ``Zone`` objects ``detect_zones``
already produced.
"""

from dataclasses import replace
from typing import Sequence

from analysis.zone_engine.models import Zone

# Rule: Freshness filter — a zone tested twice or more is considered "used
# up"; only fresh (0) and once-tested (1) zones remain interesting.
_MAX_TIMES_TESTED = 1

# Rule: Score filter — mirrors the documented "no trade below 5" cutoff
# from the ODD entry-recommendation thresholds.
_MIN_DISPLAY_SCORE = 5.0

# Rule: Nearest-N filter — keep at most this many zones on each side of
# the current price (so at most 2 * N zones are ever drawn).
_MAX_ZONES_PER_SIDE = 3


def _zone_range(zone: Zone) -> tuple[float, float]:
    """Return a zone's (low, high) price range.

    ``proximal``/``distal`` are not consistently ordered — for demand
    zones ``proximal`` sits above ``distal`` (nearer to price from below),
    while for supply zones it's the other way around — so overlap checks
    and merges need the orientation-independent [low, high] span.
    """
    return (min(zone.proximal, zone.distal), max(zone.proximal, zone.distal))


def _ranges_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Rule: Overlap = the two zones' [distal, proximal] ranges intersect
    (touching edges count as overlapping)."""
    return a[0] <= b[1] and b[0] <= a[1]


def _pick_representative(cluster: Sequence[Zone]) -> Zone:
    """Rule: when merging, keep the zone with the higher ``odd_score``;
    if scores tie, keep the fresher one (fewer ``times_tested``)."""
    return min(cluster, key=lambda z: (-z.odd_score, z.times_tested))


def _merge_cluster(cluster: Sequence[Zone]) -> Zone:
    """Collapse a cluster of mutually-overlapping, same-category zones into
    a single representative zone whose boundaries are widened to cover the
    union of every zone's range (Rule: "widen its boundaries to cover both").
    """
    if len(cluster) == 1:
        return cluster[0]

    representative = _pick_representative(cluster)
    spans = [_zone_range(z) for z in cluster]
    lo = min(span[0] for span in spans)
    hi = max(span[1] for span in spans)

    # Re-express the widened [lo, hi] union range as proximal/distal,
    # respecting each category's orientation (see _zone_range docstring).
    if representative.category == "demand":
        proximal, distal = hi, lo
    else:
        proximal, distal = lo, hi

    return replace(representative, proximal=proximal, distal=distal)


def _merge_overlapping_zones(zones: Sequence[Zone]) -> list[Zone]:
    """Rule: MERGE OVERLAPPING ZONES — merge same-category zones whose
    price ranges intersect into one widened representative zone.

    Zones are merged within their category only (a demand zone never
    merges with a supply zone). Implemented as a classic "merge intervals"
    sweep: sort by range start, then fold each zone into the running
    cluster if it overlaps the cluster's combined range so far.
    """
    merged: list[Zone] = []
    for category in ("demand", "supply"):
        same_category = [z for z in zones if z.category == category]
        same_category.sort(key=lambda z: _zone_range(z)[0])

        cluster: list[Zone] = []
        cluster_lo: float | None = None
        cluster_hi: float | None = None
        for zone in same_category:
            lo, hi = _zone_range(zone)
            if cluster and cluster_lo is not None and _ranges_overlap((cluster_lo, cluster_hi), (lo, hi)):
                cluster.append(zone)
                cluster_lo = min(cluster_lo, lo)
                cluster_hi = max(cluster_hi, hi)
            else:
                if cluster:
                    merged.append(_merge_cluster(cluster))
                cluster, cluster_lo, cluster_hi = [zone], lo, hi
        if cluster:
            merged.append(_merge_cluster(cluster))

    return merged


def filter_zones(zones: Sequence[Zone], current_price: float) -> list[Zone]:
    """Reduce a raw list of detected zones to the meaningful, tradeable
    subset worth drawing on a chart.

    Applies, in order:
      1. **Freshness filter** — drop zones tested 2+ times.
      2. **Score filter** — drop zones scoring below 5 (the documented
         "no trade below 5" rule).
      3. **Nearest-N** — keep only the 3 demand zones whose proximal line
         sits closest below ``current_price``, and the 3 supply zones
         whose proximal line sits closest above it.

    Args:
        zones: Raw zones from ``detect_zones`` (any order).
        current_price: Latest close — used to rank zones by proximity and
            to decide which side of the market each zone is on.

    Returns:
        At most 6 zones (≤3 demand + ≤3 supply): demand zones first
        (nearest to price first), then supply zones (nearest first).
        Overlapping zones are kept separate — each zone's boundaries
        reflect its own base candles only.
        Returns an empty list when given no zones or none survive.
    """
    if not zones:
        return []

    # Rule 1: Freshness filter.
    candidates = [z for z in zones if z.times_tested <= _MAX_TIMES_TESTED]

    # Rule 2: Score filter ("no trade below 5").
    candidates = [z for z in candidates if z.odd_score >= _MIN_DISPLAY_SCORE]

    # Rule 3: Nearest-N — demand zones at or below price, supply zones at
    # or above.  Zones where price is inside (between proximal and distal)
    # are included; zones where price breached the distal are already
    # invalidated by M3.
    demand = sorted(
        (z for z in candidates if z.category == "demand" and z.distal <= current_price),
        key=lambda z: abs(current_price - z.proximal),
    )[:_MAX_ZONES_PER_SIDE]
    supply = sorted(
        (z for z in candidates if z.category == "supply" and z.distal >= current_price),
        key=lambda z: abs(current_price - z.proximal),
    )[:_MAX_ZONES_PER_SIDE]

    return demand + supply


# ---------------------------------------------------------------------------
# Zone confirmation — a SEPARATE selection, deliberately not part of
# filter_zones. Nothing above changes.
# ---------------------------------------------------------------------------

# How far price may have travelled from the proximal and still leave a trade
# worth taking. Past this the reaction has already played out.
_CONFIRMATION_MAX_DISTANCE_PCT = 8.0

# The lowest score a confirmed zone can be asked for. This is NOT a relaxation
# of the "no trade below 5" rule — it exists because that rule cannot apply
# here. Confirmation forces freshness off its top value, and what remains is
# too coarse for a 5.0 cutoff to grade:
#
#   tested once  -> freshness 1.5, so totals are 2.5, 3.5, 4.5, 5.5
#   tested 2+    -> freshness 0.0, so totals are 1.0, 2.0, 3.0, 4.0
#
# Neither ladder contains 5.0. A 5.0 cutoff therefore does not express
# "reasonable quality"; it silently demands strength 2 AND time 2 from a
# once-tested zone, the single perfect pairing, and excludes every zone tested
# more than once outright. Measured over 8 NIFTY stocks: 17 confirmed zones
# sat at 4.5 and were discarded by a margin no zone could ever have earned.
#
# 3.5 is the next rung down that still requires real quality: it admits a
# once-tested zone that earned at least one of strength or time, and a
# repeatedly-tested zone only at 4.0, its own ladder's maximum.
_CONFIRMATION_MIN_SCORE = 3.5

# How recently the zone must have been confirmed, in bars. Distance alone is
# not enough: a zone can sit 3% from price with its reaction two months old,
# because price wandered away and came back for unrelated reasons. That is a
# historical fact about the zone, not a setup on the table now. Observed on
# RELIANCE — a supply zone 3.4% from price whose last exit was 44 bars back,
# alongside a demand zone 4.3% away that exited 5 bars back. Only the second
# is a live signal.
_CONFIRMATION_MAX_BARS_SINCE_TEST = 10

# Rule: only the nearest confirmed zone on each side is offered — the level
# price would actually reach next. Older confirmed zones further out are real
# but are not the trade in front of you.
_MAX_CONFIRMATION_ZONES_PER_SIDE = 1


def _bars_since_last_confirmation(
    df, zone: Zone, current_idx: int,
) -> int | None:
    """Bars between the zone's most recent confirmed exit and the last bar.

    Replays the same cycle definition ``count_zone_tests`` uses — wick-based
    entry, close-based exit through the PROXIMAL — but records *when* the last
    completed exit happened rather than how many there were. Kept here, in the
    confirmation path, rather than added to ``Zone``: scoring already produced
    the counts it needs and must not be disturbed.

    Returns:
        Bars since the last confirmed exit, or ``None`` if no complete cycle
        is found (which disqualifies the zone).
    """
    inside = False
    last_exit_idx: int | None = None
    demand = zone.category == "demand"

    for idx in range(max(zone.legout_idx + 1, 0), current_idx + 1):
        low = float(df["Low"].iloc[idx])
        high = float(df["High"].iloc[idx])
        close = float(df["Close"].iloc[idx])

        if demand:
            entered, exited = low <= zone.proximal, close > zone.proximal
        else:
            entered, exited = high >= zone.proximal, close < zone.proximal

        if entered:
            inside = True
        # A bar that enters and closes back out completes the cycle on the
        # same bar, matching M3's same-bar test.
        if inside and exited:
            last_exit_idx = idx
            inside = False

    if last_exit_idx is None:
        return None
    return current_idx - last_exit_idx


def filter_confirmation_zones(
    zones: Sequence[Zone], current_price: float, df=None,
) -> list[Zone]:
    """Select zones price has TESTED and left, and is still close to.

    The trade this supports is different from the one ``filter_zones``
    supports. That one finds zones price is approaching, where entry is a
    prediction. This one finds zones price has already reacted to — entered,
    then closed back out through the proximal — which is evidence the orders
    were real. Price commonly returns to such a zone and reacts again.

    A zone qualifies when all of the following hold:

      * it has been confirmed at least once (``times_tested >= 1``). The exit
        side needs no separate check: ``count_zone_tests`` only counts a test
        when the candle closes back out through the PROXIMAL, and an exit
        through the distal invalidates the zone instead.
      * price is now OUTSIDE the zone, on the proximal side. Price still
        inside has not left yet, so nothing is confirmed.
      * price is within :data:`_CONFIRMATION_MAX_DISTANCE_PCT` of the proximal.
      * the confirmation happened within
        :data:`_CONFIRMATION_MAX_BARS_SINCE_TEST` bars, when ``df`` is given.
        Near in price and recent in time are independent conditions, and a
        zone can easily satisfy the first while failing the second.

    Distance is measured against ``current_price`` to match the convention in
    the dashboard screener.

    Args:
        zones: Raw zones from ``detect_zones``.
        current_price: Latest close.
        df: OHLCV frame used to date the last confirmation. When omitted the
            recency check is skipped — callers that can supply it should.

    Returns:
        At most one demand and one supply zone, nearest to price first.
        Empty when none qualify.
    """
    if not zones or current_price <= 0:
        return []

    current_idx = len(df) - 1 if df is not None and len(df) else None

    out: list[Zone] = []
    for zone in zones:
        if zone.times_tested < 1:
            continue
        if zone.odd_score < _CONFIRMATION_MIN_SCORE:
            continue

        if zone.category == "demand":
            # Demand sits below price; a confirmed bounce leaves price ABOVE
            # the proximal.
            if current_price <= zone.proximal:
                continue
        else:
            if current_price >= zone.proximal:
                continue

        distance = abs(current_price - zone.proximal) / current_price * 100
        if distance > _CONFIRMATION_MAX_DISTANCE_PCT:
            continue

        if current_idx is not None:
            bars_ago = _bars_since_last_confirmation(df, zone, current_idx)
            if bars_ago is None or bars_ago > _CONFIRMATION_MAX_BARS_SINCE_TEST:
                continue

        out.append(zone)

    # Nearest on each side only — the level price would meet next.
    by_distance = sorted(out, key=lambda z: abs(current_price - z.proximal))
    demand = [z for z in by_distance if z.category == "demand"][
        :_MAX_CONFIRMATION_ZONES_PER_SIDE
    ]
    supply = [z for z in by_distance if z.category == "supply"][
        :_MAX_CONFIRMATION_ZONES_PER_SIDE
    ]
    return sorted(demand + supply, key=lambda z: abs(current_price - z.proximal))
