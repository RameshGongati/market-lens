"""Demand and Supply zone detection — institutional legin/base/legout engine.

This module is a thin orchestrator: all detection/scoring logic lives in
``analysis.zone_engine`` (candle classification, legin-base-legout pattern
matching, NORMAL/EXCEPTIONAL boundary marking and the ODD trade score).
``DemandSupplyAnalysis`` wires that engine into the app's ``BaseAnalysis``
contract, decides the overall market bias, builds the headline summary, and
shapes the result dict.

Backward compatibility: the rest of the app (``ui/pages/dashboard.py`` and
``ui/components/stock_detail.py``) was written against the previous
pivot-point engine's result shape and indexes zone dicts directly —
``zone["top"]``, ``zone["bottom"]``, ``zone.get("touches", 0)``,
``nd['mid']`` — and reads ``result["strength"]`` as one of the legacy
``Strength`` values ("Strong"/"Medium"/"Weak"). Per the Stage-1 brief those
files must not change, so every zone dict emitted here carries both the new
Zone-spec fields (``zone_type``, ``proximal``, ``distal``, ``odd_score``,
``zone_strength``, ``entry_recommendation``, ...) *and* legacy-compatible
alias keys (``mid``, ``top``, ``bottom``, ``touches``, ``bar_index``), and
``zone_strength`` is mapped down to a legacy ``Strength`` value for
``get_strength()``/``result["strength"]``.
"""

from typing import Any

import pandas as pd

from analysis.base import BaseAnalysis, Status, Strength
from analysis.zone_engine.models import Zone
from analysis.zone_engine.patterns import detect_zones
from utils.logger import get_logger

logger = get_logger(__name__)

# Minimum number of candles required before attempting zone detection — a
# legin + base (>=1) + legout needs at least 3, but we want enough history
# for the scan to find genuine structures and for "tested" counts to be
# meaningful.
_MIN_CANDLES = 20

# How close (as a fraction of price) the current price must be to a fresh
# zone's proximal line for that zone to drive the overall status.
_PROXIMITY_PCT = 0.02

# Rule: the new zone_strength vocabulary ("Normal"/"Strong"/"Very Strong")
# is richer than — and not the same as — the legacy Strength Literal the
# BaseAnalysis contract and strength badge UI expect
# ("Strong"/"Medium"/"Weak"). Map down so get_strength()/result["strength"]
# stay valid for existing code.
_ZONE_STRENGTH_TO_LEGACY: dict[str, Strength] = {
    "Very Strong": "Strong",
    "Strong": "Strong",
    "Normal": "Medium",
}


class DemandSupplyAnalysis(BaseAnalysis):
    """Detects demand/supply zones using the legin-base-legout methodology.

    Scans the supplied OHLCV history for the four documented zone patterns
    (DBR, RBR, RBD, DBD), scores each one with the ODD (freshness/strength/
    time-at-base) trade score, and reports the nearest fresh zones relative
    to the current price together with an overall bullish/bearish/neutral
    bias.
    """

    def __init__(self) -> None:
        self._result: dict[str, Any] = {}
        self._status: Status = "neutral"
        self._strength: Strength = "Weak"
        self._summary: str = "No analysis run yet."

    def analyse(self, symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        """Detect demand/supply zones and classify the current price position.

        Returns a dict with (at minimum):
            * ``all_zones`` — every detected zone, as dicts (Zone.to_dict()
              plus legacy alias keys).
            * ``nearest_demand`` / ``nearest_supply`` — nearest zone (as a
              dict) below/above the current price, or ``None``.
            * ``current_price`` — latest close.
            * ``status`` — "bullish" near a fresh demand zone, "bearish"
              near a fresh supply zone, otherwise "neutral".
            * ``summary`` — one-line human-readable summary.

            Plus the legacy keys ``demand_zones``, ``supply_zones`` and
            ``strength`` that the existing UI already depends on (see the
            module docstring).
        """
        if data.empty or len(data) < _MIN_CANDLES:
            self._result = {"error": "Insufficient data for demand/supply analysis."}
            self._status = "neutral"
            self._strength = "Weak"
            self._summary = "Not enough data."
            return self._result

        try:
            current_price = float(data["Close"].iloc[-1])

            # --- Rule: scan all 4 patterns (DBR/RBR/RBD/DBD) -----------------
            zones = detect_zones(data)
            demand_zones = [z for z in zones if z.category == "demand"]
            supply_zones = [z for z in zones if z.category == "supply"]

            nearest_demand = _nearest_below(demand_zones, current_price)
            nearest_supply = _nearest_above(supply_zones, current_price)

            nd_dict = _zone_dict(nearest_demand) if nearest_demand else None
            ns_dict = _zone_dict(nearest_supply) if nearest_supply else None

            # --- Rule: status — bullish near fresh demand, bearish near
            # fresh supply, otherwise neutral -------------------------------
            status = _determine_status(current_price, nd_dict, ns_dict)
            active_zone = nd_dict if status == "bullish" else ns_dict if status == "bearish" else None
            strength = _legacy_strength(active_zone)

            summary = _build_summary(demand_zones, supply_zones, nd_dict, ns_dict, current_price, status)

            self._status = status
            self._strength = strength
            self._summary = summary
            self._result = {
                "symbol": symbol,
                "current_price": current_price,
                # New Zone-spec shape
                "all_zones": [_zone_dict(z) for z in zones],
                "nearest_demand": nd_dict,
                "nearest_supply": ns_dict,
                "status": status,
                "summary": summary,
                # Legacy-compatible keys still read by ui/pages/dashboard.py
                # and ui/components/stock_detail.py — see module docstring.
                "demand_zones": [_zone_dict(z) for z in demand_zones],
                "supply_zones": [_zone_dict(z) for z in supply_zones],
                "strength": strength,
            }
        except Exception as exc:
            logger.error("DemandSupplyAnalysis failed for %s: %s", symbol, exc)
            self._result = {"error": str(exc)}
            self._status = "neutral"
            self._strength = "Weak"
            self._summary = "Analysis error."

        return self._result

    def get_status(self) -> Status:
        return self._status

    def get_strength(self) -> Strength:
        return self._strength

    def get_summary(self) -> str:
        return self._summary


# ---------------------------------------------------------------------------
# Result shaping helpers
# ---------------------------------------------------------------------------

def _zone_dict(zone: Zone) -> dict[str, Any]:
    """Convert a ``Zone`` into a dict carrying both the new Zone-spec fields
    and the legacy alias keys (``mid``, ``top``, ``bottom``, ``touches``,
    ``bar_index``) that ``stock_detail.py``'s metrics and chart-overlay code
    indexes directly (``zone["bottom"]``, ``zone["top"]``, ``nd['mid']``,
    ``zone.get('touches', 0)``) — see module docstring for why these must
    stay present without editing the UI.
    """
    d = zone.to_dict()
    top = max(zone.proximal, zone.distal)
    bottom = min(zone.proximal, zone.distal)
    d.update(
        mid=round((top + bottom) / 2, 2),
        top=round(top, 2),
        bottom=round(bottom, 2),
        touches=zone.times_tested,
        bar_index=zone.created_at_index,
    )
    return d


def _zone_mid(zone: dict[str, Any]) -> float:
    """Midpoint of a zone dict's proximal/distal lines (used for ranking)."""
    return (zone["proximal"] + zone["distal"]) / 2.0


def _nearest_below(zones: list[Zone], price: float) -> Zone | None:
    """Return the demand zone whose midpoint sits closest below *price*."""
    below = [z for z in zones if (z.proximal + z.distal) / 2.0 < price]
    if not below:
        return None
    return max(below, key=lambda z: (z.proximal + z.distal) / 2.0)


def _nearest_above(zones: list[Zone], price: float) -> Zone | None:
    """Return the supply zone whose midpoint sits closest above *price*."""
    above = [z for z in zones if (z.proximal + z.distal) / 2.0 > price]
    if not above:
        return None
    return min(above, key=lambda z: (z.proximal + z.distal) / 2.0)


def _is_near(price: float, zone: dict[str, Any]) -> bool:
    """True when *price* sits within ``_PROXIMITY_PCT`` of the zone's
    proximal line (the edge nearest to where price currently trades)."""
    if price <= 0:
        return False
    return abs(zone["proximal"] - price) / price <= _PROXIMITY_PCT


def _determine_status(
    price: float, nearest_demand: dict[str, Any] | None, nearest_supply: dict[str, Any] | None
) -> Status:
    """Rule: status — "bullish" when price sits near a *fresh* demand zone,
    "bearish" when it sits near a *fresh* supply zone, otherwise "neutral"."""
    if nearest_demand and nearest_demand.get("is_fresh") and _is_near(price, nearest_demand):
        return "bullish"
    if nearest_supply and nearest_supply.get("is_fresh") and _is_near(price, nearest_supply):
        return "bearish"
    return "neutral"


def _legacy_strength(active_zone: dict[str, Any] | None) -> Strength:
    """Map the active zone's rich ``zone_strength`` label down to the
    legacy ``Strength`` literal (see ``_ZONE_STRENGTH_TO_LEGACY``)."""
    if active_zone is None:
        return "Weak"
    return _ZONE_STRENGTH_TO_LEGACY.get(active_zone.get("zone_strength", ""), "Medium")


def _fmt_score(score: float) -> str:
    """Format an ODD score without a trailing ``.0`` for whole numbers."""
    return f"{score:g}"


def _build_summary(
    demand_zones: list[Zone],
    supply_zones: list[Zone],
    nearest_demand: dict[str, Any] | None,
    nearest_supply: dict[str, Any] | None,
    price: float,
    status: Status,
) -> str:
    """Build a one-line summary, e.g.:

    "3 demand zones, 2 supply zones | Nearest demand 1121-1178
    (DBR, score 6, Strong) | price between zones"
    """
    parts = [f"{len(demand_zones)} demand zones, {len(supply_zones)} supply zones"]

    candidates = []
    if nearest_demand:
        candidates.append(("Nearest demand", nearest_demand))
    if nearest_supply:
        candidates.append(("Nearest supply", nearest_supply))

    if candidates:
        label, zone = min(candidates, key=lambda lz: abs(_zone_mid(lz[1]) - price))
        parts.append(
            f"{label} {zone['bottom']:.0f}-{zone['top']:.0f} "
            f"({zone['zone_type']}, score {_fmt_score(zone['odd_score'])}, {zone['zone_strength']})"
        )

    bias = {
        "bullish": "near fresh demand",
        "bearish": "near fresh supply",
        "neutral": "between zones",
    }
    parts.append(f"price {bias[status]}")

    return " | ".join(parts)
