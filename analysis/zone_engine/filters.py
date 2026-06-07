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
      3. **Merge** — collapse overlapping same-category zones into one
         widened representative zone.
      4. **Nearest-N** — keep only the 3 demand zones whose proximal line
         sits closest below ``current_price``, and the 3 supply zones
         whose proximal line sits closest above it.

    Args:
        zones: Raw zones from ``detect_zones`` (any order).
        current_price: Latest close — used to rank zones by proximity and
            to decide which side of the market each zone is on.

    Returns:
        At most 6 zones (≤3 demand + ≤3 supply): demand zones first
        (nearest to price first), then supply zones (nearest first).
        Returns an empty list when given no zones or none survive.
    """
    if not zones:
        return []

    # Rule 1: Freshness filter.
    candidates = [z for z in zones if z.times_tested <= _MAX_TIMES_TESTED]

    # Rule 2: Score filter ("no trade below 5").
    candidates = [z for z in candidates if z.odd_score >= _MIN_DISPLAY_SCORE]

    # Rule 3: Merge overlapping same-category zones.
    candidates = _merge_overlapping_zones(candidates)

    # Rule 4: Nearest-N — demand zones must sit below price, supply zones
    # above it; rank each side by how close its proximal line is to price.
    demand = sorted(
        (z for z in candidates if z.category == "demand" and z.proximal < current_price),
        key=lambda z: current_price - z.proximal,
    )[:_MAX_ZONES_PER_SIDE]
    supply = sorted(
        (z for z in candidates if z.category == "supply" and z.proximal > current_price),
        key=lambda z: z.proximal - current_price,
    )[:_MAX_ZONES_PER_SIDE]

    return demand + supply
