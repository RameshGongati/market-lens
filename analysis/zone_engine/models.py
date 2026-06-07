"""Data models for the demand/supply zone engine."""

from dataclasses import asdict, dataclass, field
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
    * Stage 2 context — additive fields layered on top of the Stage 1
      detection/scoring above, never folded into ``odd_score``:
      ``trend_at_zone`` (the overall 50-SMA-clock trend when the zone was
      evaluated, see ``analysis.zone_engine.trend``), ``ema20_enhancer``
      (the EMA 20 confluence "high probability" bonus flag, see
      ``analysis.zone_engine.enhancers``), and ``is_tradeable``/
      ``trade_warning`` (the trend-alignment safety verdict — a demand
      zone is only considered tradeable in an uptrend, a supply zone only
      in a downtrend; sideways markets make every zone untradeable).
    * Stage 3 context — an OPT-IN ("Enhance with Fibonacci Confluence"
      checkbox) layer on top of everything above, also never folded into
      ``odd_score``: ``fib_confluence``/``fib_levels_in_zone``/
      ``fib_strongest`` (whether/which retracement levels of the most
      recent swing line up with the zone, see
      ``analysis.zone_engine.fibonacci.fib_confluence``) and
      ``confluence_score``/``confluence_label`` (the combined EMA20+Fib
      confluence scorecard — a SEPARATE rating from ``odd_score``, see
      ``analysis.zone_engine.scoring.confluence_rating``). When the
      Fibonacci enhancer is switched off, every one of these fields stays
      at its conservative default below — byte-for-byte identical to
      Stage 2 behaviour.
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

    # --- Stage 2 context (additive — never folded into odd_score) --------
    trend_at_zone: str = ""         # "UP" | "DOWN" | "SIDEWAYS" at evaluation time
    ema20_enhancer: bool = False    # True when EMA 20 is in/near the zone (confluence bonus)
    is_tradeable: bool = True       # per the trend-alignment safety rule
    trade_warning: str = ""         # explanation when is_tradeable is False

    # --- Stage 3 context (opt-in, additive — never folded into odd_score) -
    fib_confluence: bool = False                    # True when a Fib level is in/near the zone
    fib_levels_in_zone: list = field(default_factory=list)  # ratios whose price falls inside the zone
    fib_strongest: float | None = None              # strongest Fib ratio in/near the zone (0.618 first)
    confluence_score: int = 0                       # combined EMA20+Fib bonus score (separate from odd_score)
    confluence_label: str = "None"                  # "None" | "Moderate" | "High"

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict representation (for caching/serialising/UI)."""
        return asdict(self)
