"""Data models for the demand/supply zone engine."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Zone:
    """A single detected demand or supply zone with its full ODD scorecard.

    Field groups mirror the institutional legin-base-legout methodology this
    engine implements:

    * Identity — ``zone_type`` (DBR/RBR/RBD/DBD) and ``category`` (demand or
      supply), derived from the legin/legout direction combination.
    * Boundaries — NORMAL ``proximal``/``distal`` lines (the default,
      conservative marking used everywhere else in the app) plus the
      EXCEPTIONAL variants (``proximal_exceptional``/``distal_exceptional``)
      retained for future stages that may want to offer the alternate
      marking style.
    * Structure — the row indices that produced the zone (base span and the
      triggering legout candle) and how many candles formed the base.
    * ODD trade score — ``odd_score`` (0-7) broken down into its three
      components (``freshness_points``, ``strength_points``,
      ``time_points``), plus the derived ``times_tested``, ``zone_strength``
      label, ``entry_recommendation`` text and ``is_fresh`` flag.
    """

    zone_type: str                  # "DBR" | "RBR" | "RBD" | "DBD"
    category: str                   # "demand" | "supply"
    proximal: float                 # NORMAL proximal line (edge nearest price)
    distal: float                   # NORMAL distal line (far edge of the zone)
    proximal_exceptional: float     # EXCEPTIONAL proximal line variant
    distal_exceptional: float       # EXCEPTIONAL distal line variant
    base_start_idx: int             # row index of the first base candle
    base_end_idx: int               # row index of the last base candle
    legout_idx: int                 # row index of the first legout candle
    num_base_candles: int           # number of candles forming the base
    odd_score: float                # Trade Score, 0-7 (freshness+strength+time)
    freshness_points: float         # 3 / 1.5 / 0
    strength_points: float          # 2 / 1
    time_points: float              # 2 / 1 / 0
    times_tested: int               # distinct re-entries into the zone
    zone_strength: str              # "Normal" | "Strong" | "Very Strong"
    entry_recommendation: str       # human-readable trade guidance
    created_at_index: int           # row index the zone became active (legout)
    is_fresh: bool                  # True when times_tested == 0

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation (for caching/serialising/UI)."""
        return asdict(self)
