"""Reusable data structures for chart-pattern scanning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class PatternPoint:
    """One point on a detected pattern line."""

    index: int
    timestamp: Any
    price: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe point dict."""
        return {
            "index": self.index,
            "timestamp": _json_time(self.timestamp),
            "price": self.price,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatternPoint":
        """Build a point from persisted JSON data."""
        return cls(
            index=int(data.get("index", 0) or 0),
            timestamp=data.get("timestamp"),
            price=float(data.get("price", 0.0) or 0.0),
        )


@dataclass
class PatternMatch:
    """A chart-pattern match produced by a detector.

    The first group of fields mirrors the Pattern Scanner contract; the
    remaining fields are additive context used by the UI and exports.
    """

    symbol: str
    company_name: str
    exchange: str
    timeframe: str
    pattern_family: str
    pattern_type: str
    stage: str
    detected_at: datetime
    freshness_candles: int
    confidence_score: float
    apex_proximity: float
    breakout_bias: str
    zone_context: str
    volume_contraction: bool
    upper_trendline_points: list[PatternPoint]
    lower_trendline_points: list[PatternPoint]
    apex_point: PatternPoint
    breakout_level: float
    breakdown_level: float
    nearest_demand_zone: dict[str, Any] | None = None
    nearest_supply_zone: dict[str, Any] | None = None
    risk_reward: float | None = None
    notes: list[str] = field(default_factory=list)
    current_price: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    volume_contraction_ratio: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain JSON-safe dict suitable for exports/cache."""
        d = asdict(self)
        d["detected_at"] = self.detected_at.isoformat()
        d["upper_trendline_points"] = [p.to_dict() for p in self.upper_trendline_points]
        d["lower_trendline_points"] = [p.to_dict() for p in self.lower_trendline_points]
        d["apex_point"] = self.apex_point.to_dict()
        d["metadata"] = _json_metadata(self.metadata)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PatternMatch":
        """Build a match from persisted JSON data."""
        detected_raw = data.get("detected_at")
        try:
            detected_at = datetime.fromisoformat(str(detected_raw))
        except (TypeError, ValueError):
            detected_at = datetime.now()

        metadata = data.get("metadata") or {}
        for key in ("swing_highs", "swing_lows"):
            if isinstance(metadata.get(key), list):
                metadata[key] = [
                    PatternPoint.from_dict(p) if isinstance(p, dict) else p
                    for p in metadata[key]
                ]

        return cls(
            symbol=str(data.get("symbol", "")),
            company_name=str(data.get("company_name", "")),
            exchange=str(data.get("exchange", "NSE")),
            timeframe=str(data.get("timeframe", "Daily")),
            pattern_family=str(data.get("pattern_family", "Triangle Patterns")),
            pattern_type=str(data.get("pattern_type", "Symmetrical Triangle")),
            stage=str(data.get("stage", "Forming")),
            detected_at=detected_at,
            freshness_candles=int(data.get("freshness_candles", 0) or 0),
            confidence_score=float(data.get("confidence_score", 0.0) or 0.0),
            apex_proximity=float(data.get("apex_proximity", 0.0) or 0.0),
            breakout_bias=str(data.get("breakout_bias", "Neutral Until Break")),
            zone_context=str(data.get("zone_context", "Between Zones")),
            volume_contraction=bool(data.get("volume_contraction", False)),
            upper_trendline_points=[
                PatternPoint.from_dict(p)
                for p in data.get("upper_trendline_points", [])
                if isinstance(p, dict)
            ],
            lower_trendline_points=[
                PatternPoint.from_dict(p)
                for p in data.get("lower_trendline_points", [])
                if isinstance(p, dict)
            ],
            apex_point=PatternPoint.from_dict(data.get("apex_point") or {}),
            breakout_level=float(data.get("breakout_level", 0.0) or 0.0),
            breakdown_level=float(data.get("breakdown_level", 0.0) or 0.0),
            nearest_demand_zone=data.get("nearest_demand_zone"),
            nearest_supply_zone=data.get("nearest_supply_zone"),
            risk_reward=(
                float(data["risk_reward"])
                if data.get("risk_reward") not in (None, "")
                else None
            ),
            notes=list(data.get("notes") or []),
            current_price=float(data.get("current_price", 0.0) or 0.0),
            change=float(data.get("change", 0.0) or 0.0),
            change_pct=float(data.get("change_pct", 0.0) or 0.0),
            volume_contraction_ratio=float(
                data.get("volume_contraction_ratio", 1.0) or 1.0
            ),
            metadata=metadata,
        )


def _json_time(value: Any) -> Any:
    """Return an ISO-ish timestamp value when possible."""
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return value


def _json_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, PatternPoint):
            out[key] = value.to_dict()
        elif isinstance(value, list):
            out[key] = [
                item.to_dict() if isinstance(item, PatternPoint) else _json_time(item)
                for item in value
            ]
        elif isinstance(value, dict):
            out[key] = {
                str(k): _json_time(v)
                for k, v in value.items()
            }
        else:
            out[key] = _json_time(value)
    return out
