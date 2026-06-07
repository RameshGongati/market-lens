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

``analysis.demand_supply.DemandSupplyAnalysis`` is a thin orchestrator on
top of this package; it owns the public ``BaseAnalysis`` contract and
result-shape concerns (including backward compatibility with the existing
UI), while all detection/scoring/filtering logic lives here.
"""

from analysis.zone_engine.candles import CandleInfo, classify_candle
from analysis.zone_engine.filters import filter_zones
from analysis.zone_engine.models import Zone
from analysis.zone_engine.patterns import detect_zones
from analysis.zone_engine.scoring import ZoneScore, score_zone

__all__ = [
    "CandleInfo",
    "classify_candle",
    "filter_zones",
    "Zone",
    "detect_zones",
    "ZoneScore",
    "score_zone",
]
