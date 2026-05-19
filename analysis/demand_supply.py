"""Demand and Supply zone detection using pivot point method."""

from typing import Any

import numpy as np
import pandas as pd

from analysis.base import BaseAnalysis, Status
from utils.logger import get_logger

logger = get_logger(__name__)

# A pivot high/low must be separated by at least this many bars on each side
_PIVOT_LOOKBACK = 5
# Zone width as a fraction of price (5% band around pivot)
_ZONE_PCT = 0.025


class DemandSupplyAnalysis(BaseAnalysis):
    """Detects demand and supply zones from OHLCV data using pivot highs/lows."""

    def __init__(self) -> None:
        self._result: dict[str, Any] = {}
        self._status: Status = "neutral"
        self._summary: str = "No analysis run yet."

    def analyse(self, symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        """Detect demand and supply zones and classify current price position.

        Args:
            symbol: Stock ticker being analysed.
            data: OHLCV DataFrame (minimum 20 bars recommended).

        Returns:
            Dict with keys: symbol, demand_zones, supply_zones,
            nearest_demand, nearest_supply, current_price, status, summary.
        """
        if data.empty or len(data) < _PIVOT_LOOKBACK * 2 + 1:
            self._result = {"error": "Insufficient data for demand/supply analysis."}
            self._status = "neutral"
            self._summary = "Not enough data."
            return self._result

        try:
            highs = data["High"].values
            lows = data["Low"].values
            closes = data["Close"].values
            current_price = float(closes[-1])

            supply_zones = self._find_supply_zones(highs, closes)
            demand_zones = self._find_demand_zones(lows, closes)

            nearest_supply = _nearest_above(supply_zones, current_price)
            nearest_demand = _nearest_below(demand_zones, current_price)

            # Determine status based on proximity to zones
            dist_supply = abs(nearest_supply["mid"] - current_price) / current_price if nearest_supply else float("inf")
            dist_demand = abs(nearest_demand["mid"] - current_price) / current_price if nearest_demand else float("inf")

            if nearest_demand and dist_demand < 0.02:
                status: Status = "bullish"
            elif nearest_supply and dist_supply < 0.02:
                status = "bearish"
            else:
                status = "neutral"

            summary = _build_summary(symbol, current_price, nearest_demand, nearest_supply, status)

            self._status = status
            self._summary = summary
            self._result = {
                "symbol": symbol,
                "current_price": current_price,
                "demand_zones": demand_zones,
                "supply_zones": supply_zones,
                "nearest_demand": nearest_demand,
                "nearest_supply": nearest_supply,
                "status": status,
                "summary": summary,
            }
        except Exception as exc:
            logger.error("DemandSupplyAnalysis failed for %s: %s", symbol, exc)
            self._result = {"error": str(exc)}
            self._status = "neutral"
            self._summary = "Analysis error."

        return self._result

    def get_status(self) -> Status:
        return self._status

    def get_summary(self) -> str:
        return self._summary

    def _find_supply_zones(
        self, highs: np.ndarray, closes: np.ndarray
    ) -> list[dict[str, float]]:
        """Identify supply zones at pivot highs where price reversed downward."""
        zones: list[dict[str, float]] = []
        n = len(highs)
        lb = _PIVOT_LOOKBACK
        for i in range(lb, n - lb):
            if highs[i] == max(highs[i - lb: i + lb + 1]):
                mid = float(highs[i])
                zones.append({
                    "mid": mid,
                    "top": round(mid * (1 + _ZONE_PCT), 2),
                    "bottom": round(mid * (1 - _ZONE_PCT), 2),
                    "bar_index": i,
                })
        # Keep the 5 most recent unique zones
        return _deduplicate_zones(zones)[-5:]

    def _find_demand_zones(
        self, lows: np.ndarray, closes: np.ndarray
    ) -> list[dict[str, float]]:
        """Identify demand zones at pivot lows where price reversed upward."""
        zones: list[dict[str, float]] = []
        n = len(lows)
        lb = _PIVOT_LOOKBACK
        for i in range(lb, n - lb):
            if lows[i] == min(lows[i - lb: i + lb + 1]):
                mid = float(lows[i])
                zones.append({
                    "mid": mid,
                    "top": round(mid * (1 + _ZONE_PCT), 2),
                    "bottom": round(mid * (1 - _ZONE_PCT), 2),
                    "bar_index": i,
                })
        return _deduplicate_zones(zones)[-5:]


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
    """Return the closest zone whose midpoint is above *price*."""
    above = [z for z in zones if z["mid"] > price]
    if not above:
        return None
    return min(above, key=lambda z: z["mid"] - price)


def _nearest_below(zones: list[dict], price: float) -> dict | None:
    """Return the closest zone whose midpoint is below *price*."""
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
) -> str:
    parts = [f"{symbol} @ ₹{price:.2f}"]
    if demand:
        parts.append(f"Demand: ₹{demand['bottom']:.2f}–{demand['top']:.2f}")
    if supply:
        parts.append(f"Supply: ₹{supply['bottom']:.2f}–{supply['top']:.2f}")
    bias = {"bullish": "near demand — watch for bounce", "bearish": "near supply — watch for reversal", "neutral": "between zones"}
    parts.append(bias[status])
    return " | ".join(parts)
