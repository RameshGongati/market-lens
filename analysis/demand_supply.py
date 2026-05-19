"""Demand and Supply zone detection using pivot point method."""

from typing import Any

import numpy as np
import pandas as pd

from analysis.base import BaseAnalysis, Status, Strength
from utils.logger import get_logger

logger = get_logger(__name__)

_PIVOT_LOOKBACK = 5
_ZONE_PCT = 0.025


class DemandSupplyAnalysis(BaseAnalysis):
    """Detects demand and supply zones from OHLCV data using pivot highs/lows."""

    def __init__(self) -> None:
        self._result: dict[str, Any] = {}
        self._status: Status = "neutral"
        self._strength: Strength = "Weak"
        self._summary: str = "No analysis run yet."

    def analyse(self, symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        """Detect demand/supply zones and classify current price position.

        Returns:
            Dict with: symbol, demand_zones, supply_zones, nearest_demand,
            nearest_supply, current_price, status, strength, summary.
        """
        if data.empty or len(data) < _PIVOT_LOOKBACK * 2 + 1:
            self._result = {"error": "Insufficient data for demand/supply analysis."}
            self._status = "neutral"
            self._strength = "Weak"
            self._summary = "Not enough data."
            return self._result

        try:
            highs = data["High"].values
            lows = data["Low"].values
            closes = data["Close"].values
            current_price = float(closes[-1])

            supply_zones = self._find_supply_zones(highs, closes, lows)
            demand_zones = self._find_demand_zones(lows, closes, highs)

            nearest_supply = _nearest_above(supply_zones, current_price)
            nearest_demand = _nearest_below(demand_zones, current_price)

            dist_supply = (
                abs(nearest_supply["mid"] - current_price) / current_price
                if nearest_supply else float("inf")
            )
            dist_demand = (
                abs(nearest_demand["mid"] - current_price) / current_price
                if nearest_demand else float("inf")
            )

            if nearest_demand and dist_demand < 0.02:
                status: Status = "bullish"
            elif nearest_supply and dist_supply < 0.02:
                status = "bearish"
            else:
                status = "neutral"

            # Strength: based on touch count of the nearest active zone
            active_zone = nearest_demand if status == "bullish" else nearest_supply
            strength = _zone_strength(active_zone)

            summary = _build_summary(symbol, current_price, nearest_demand, nearest_supply, status, strength)

            self._status = status
            self._strength = strength
            self._summary = summary
            self._result = {
                "symbol": symbol,
                "current_price": current_price,
                "demand_zones": demand_zones,
                "supply_zones": supply_zones,
                "nearest_demand": nearest_demand,
                "nearest_supply": nearest_supply,
                "status": status,
                "strength": strength,
                "summary": summary,
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

    def _find_supply_zones(
        self, highs: np.ndarray, closes: np.ndarray, lows: np.ndarray
    ) -> list[dict[str, Any]]:
        """Identify supply zones at pivot highs and count subsequent touches."""
        zones: list[dict[str, Any]] = []
        n = len(highs)
        lb = _PIVOT_LOOKBACK
        for i in range(lb, n - lb):
            if highs[i] == max(highs[i - lb: i + lb + 1]):
                mid = float(highs[i])
                top = round(mid * (1 + _ZONE_PCT), 2)
                bottom = round(mid * (1 - _ZONE_PCT), 2)
                # Count touches after zone formation
                touches = _count_touches(highs[i + 1:], lows[i + 1:], top, bottom)
                zones.append({
                    "mid": mid,
                    "top": top,
                    "bottom": bottom,
                    "bar_index": i,
                    "touches": touches,
                })
        return _deduplicate_zones(zones)[-5:]

    def _find_demand_zones(
        self, lows: np.ndarray, closes: np.ndarray, highs: np.ndarray
    ) -> list[dict[str, Any]]:
        """Identify demand zones at pivot lows and count subsequent touches."""
        zones: list[dict[str, Any]] = []
        n = len(lows)
        lb = _PIVOT_LOOKBACK
        for i in range(lb, n - lb):
            if lows[i] == min(lows[i - lb: i + lb + 1]):
                mid = float(lows[i])
                top = round(mid * (1 + _ZONE_PCT), 2)
                bottom = round(mid * (1 - _ZONE_PCT), 2)
                touches = _count_touches(highs[i + 1:], lows[i + 1:], top, bottom)
                zones.append({
                    "mid": mid,
                    "top": top,
                    "bottom": bottom,
                    "bar_index": i,
                    "touches": touches,
                })
        return _deduplicate_zones(zones)[-5:]


def _count_touches(
    highs: np.ndarray, lows: np.ndarray, top: float, bottom: float
) -> int:
    """Count how many bars entered the zone range after it was formed."""
    count = 0
    for h, l in zip(highs, lows):
        if l <= top and h >= bottom:
            count += 1
    return count


def _zone_strength(zone: dict | None) -> Strength:
    """Map touch count to strength rating."""
    if zone is None:
        return "Weak"
    touches = zone.get("touches", 0)
    if touches >= 3:
        return "Strong"
    if touches == 2:
        return "Medium"
    return "Weak"


def _deduplicate_zones(zones: list[dict]) -> list[dict]:
    """Remove zones whose midpoints are within 1% of each other."""
    if not zones:
        return []
    unique: list[dict] = [zones[0]]
    for z in zones[1:]:
        if all(abs(z["mid"] - u["mid"]) / u["mid"] > 0.01 for u in unique):
            unique.append(z)
    return unique


def _nearest_above(zones: list[dict], price: float) -> dict | None:
    above = [z for z in zones if z["mid"] > price]
    if not above:
        return None
    return min(above, key=lambda z: z["mid"] - price)


def _nearest_below(zones: list[dict], price: float) -> dict | None:
    below = [z for z in zones if z["mid"] < price]
    if not below:
        return None
    return max(below, key=lambda z: z["mid"])


def _build_summary(
    symbol: str,
    price: float,
    demand: dict | None,
    supply: dict | None,
    status: Status,
    strength: Strength,
) -> str:
    parts = [f"{symbol} @ ₹{price:.2f}"]
    if demand:
        parts.append(f"Demand: ₹{demand['bottom']:.2f}–{demand['top']:.2f}")
    if supply:
        parts.append(f"Supply: ₹{supply['bottom']:.2f}–{supply['top']:.2f}")
    bias = {
        "bullish": "near demand — watch for bounce",
        "bearish": "near supply — watch for reversal",
        "neutral": "between zones",
    }
    parts.append(f"{bias[status]} [{strength}]")
    return " | ".join(parts)
