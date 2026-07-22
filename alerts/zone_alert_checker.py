"""Zone proximity alert checker — finds stocks approaching demand/supply zones."""

from dataclasses import dataclass
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AlertMatch:
    """A stock whose current price is within proximity of a zone."""

    symbol: str
    current_price: float
    zone: dict[str, Any]
    distance_pct: float
    trend: str = ""


def check_zone_alerts(
    results: dict[str, dict],
    config: dict[str, Any],
) -> list[AlertMatch]:
    """Scan cached analysis results for zone proximity matches.

    Uses already-computed analysis results from the dashboard rather than
    re-fetching data or re-running the zone engine, keeping it lightweight.

    Args:
        results: ``{symbol: analysis_result_dict}`` from session state.
        config: Alert config dict with ``conditions`` sub-dict.

    Returns:
        List of :class:`AlertMatch` objects sorted by distance (nearest first).
    """
    cond = config.get("conditions", {})
    proximity_pct = cond.get("proximity_pct", 1.0)
    min_score = cond.get("min_score", 6.0)
    zone_type_filter = cond.get("zone_type", "both")

    matches: list[AlertMatch] = []

    for symbol, result in results.items():
        price = result.get("current_price", 0.0)
        if not price or price <= 0:
            continue
        trend = result.get("trend", "")

        for zone_key, category in (
            ("nearest_demand", "demand"),
            ("nearest_supply", "supply"),
        ):
            if zone_type_filter == "demand" and category != "demand":
                continue
            if zone_type_filter == "supply" and category != "supply":
                continue

            zone = result.get(zone_key)
            if not zone or not zone.get("proximal"):
                continue

            score = zone.get("odd_score", 0)
            if score < min_score:
                continue

            proximal = zone["proximal"]
            # Distance from CMP to zone proximal
            if category == "demand":
                # Approaching demand from above
                distance = (price - proximal) / proximal * 100
            else:
                # Approaching supply from below
                distance = (proximal - price) / proximal * 100

            # Negative distance means price is already past the proximal
            # (inside or through the zone) — still alert-worthy.
            if distance <= proximity_pct:
                matches.append(AlertMatch(
                    symbol=symbol,
                    current_price=price,
                    zone=zone,
                    distance_pct=round(max(distance, 0), 2),
                    trend=trend,
                ))

    matches.sort(key=lambda m: m.distance_pct)
    return matches
