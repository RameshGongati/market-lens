"""Institutional demand/supply zone detection engine.

This package implements a documented institutional zone-trading
methodology as a set of small, independently testable pieces:

* ``candles``  — boring/exciting candle classification (the building block
  for recognising legin/base/legout structures).
* ``patterns`` — legin-base-legout scanning, the four zone patterns
  (DBR/RBR/RBD/DBD) and NORMAL/EXCEPTIONAL boundary marking.
* ``scoring``  — the ODD (freshness/strength/time-at-base) trade score,
  zone-testing detection, zone-strength labelling and entry guidance.
* ``models``   — the ``Zone`` dataclass tying it all together.
* ``filters``  — display-oriented filtering/merging/ranking that reduces
  the raw, full-history zone list down to a small, chart-friendly subset
  without touching any detection or scoring math.
* ``trend``    — Stage 2: overall market trend via the documented "50 SMA
  clock method" (UP/DOWN/SIDEWAYS), used to flag whether a zone is
  trend-aligned and therefore tradeable.
* ``enhancers``— Stage 2: the EMA 20 confluence "high probability" bonus
  flag — additive context, never folded into the ODD ``odd_score``.

``analysis.demand_supply.DemandSupplyAnalysis`` is a thin orchestrator on
top of this package; it owns the public ``BaseAnalysis`` contract and
result-shape concerns (including backward compatibility with the existing
UI), while all detection/scoring/filtering/context logic lives here.
"""

from analysis.zone_engine.candles import CandleInfo, classify_candle
from analysis.zone_engine.enhancers import EmaConfluence, ema20_confluence
from analysis.zone_engine.filters import filter_zones
from analysis.zone_engine.models import Zone
from analysis.zone_engine.patterns import detect_zones
from analysis.zone_engine.scoring import ZoneScore, score_zone
from analysis.zone_engine.trend import TrendInfo, detect_trend

__all__ = [
    "CandleInfo",
    "classify_candle",
    "EmaConfluence",
    "ema20_confluence",
    "filter_zones",
    "Zone",
    "detect_zones",
    "ZoneScore",
    "score_zone",
    "TrendInfo",
    "detect_trend",
]
