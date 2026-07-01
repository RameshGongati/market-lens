"""Demand and Supply zone detection — institutional legin/base/legout engine.

This module is a thin orchestrator: all detection/scoring/filtering/context
logic lives in ``analysis.zone_engine`` (candle classification,
legin-base-legout pattern matching, NORMAL/EXCEPTIONAL boundary marking, the
ODD trade score, display filtering, and — Stage 2 — overall trend detection
and EMA 20 confluence). ``DemandSupplyAnalysis`` wires that engine into the
app's ``BaseAnalysis`` contract, decides the overall market bias, builds the
headline summary, and shapes the result dict.

Stage 2 layers two pieces of *additive* context on top of the Stage 1
zones — neither changes detection or the documented 7-point ODD score:
  * ``trend`` — the overall market direction ("UP"/"DOWN"/"SIDEWAYS") from
    the 50 SMA clock method (``analysis.zone_engine.trend.detect_trend``).
  * ``ema20_enhancer`` — a "high probability" bonus flag when the 20-period
    EMA lines up with a zone (``analysis.zone_engine.enhancers``).
Both feed a trend-alignment safety check (``_apply_trend_alignment``) that
marks zones disagreeing with the trend as not tradeable, with an
explanatory ``trade_warning`` — see ``_enrich_zone``.

Stage 3 adds a further, OPT-IN layer: switch on ``use_fibonacci`` (the
"Enhance with Fibonacci Confluence" sidebar checkbox) and every zone also
gets ``fib_confluence``/``fib_levels_in_zone``/``fib_strongest`` (whether
retracement levels of the most recent swing line up with it — see
``analysis.zone_engine.fibonacci``) plus a combined ``confluence_score``/
``confluence_label`` (EMA20 + Fibonacci, a SEPARATE rating from
``odd_score`` — see ``analysis.zone_engine.scoring.confluence_rating``).
With the checkbox off (the default), ``analyse`` skips all of this work and
every zone keeps the Stage 2 defaults — byte-for-byte identical behaviour.

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

import datetime as dt
import zoneinfo
from dataclasses import replace
from typing import Any

import pandas as pd

from analysis.base import BaseAnalysis, Status, Strength
from analysis.zone_engine.enhancers import ema20_confluence
from analysis.zone_engine.fibonacci import calculate_fib_levels, fib_confluence, find_recent_swing
from analysis.zone_engine.filters import filter_zones
from analysis.zone_engine.models import Zone
from analysis.zone_engine.patterns import detect_zones
from analysis.zone_engine.scoring import confluence_rating
from analysis.zone_engine.trend import detect_trend
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

# Rule: Stage 2 trend-alignment safety — explanatory ``trade_warning``
# strings attached to a zone when it fails the alignment check (a demand
# zone is only reliable in an uptrend, a supply zone only in a downtrend;
# a sideways market makes every zone unreliable).
_DEMAND_IN_DOWNTREND_WARNING = "Demand zone in downtrend - risky per methodology"
_SUPPLY_IN_UPTREND_WARNING = "Supply zone in uptrend - risky per methodology"
_SIDEWAYS_WARNING = "Sideways trend - avoid"


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

    def analyse(self, symbol: str, data: pd.DataFrame, use_fibonacci: bool = False) -> dict[str, Any]:
        """Detect demand/supply zones and classify the current price position.

        Args:
            symbol: The stock ticker being analysed.
            data: OHLCV DataFrame with columns Open, High, Low, Close, Volume.
            use_fibonacci: Stage 3 OPT-IN switch — the "Enhance with
                Fibonacci Confluence" sidebar checkbox. ``False`` (the
                default) skips all Fibonacci work entirely and leaves every
                zone's ``fib_*``/``confluence_*`` fields at their Stage 2
                defaults, with no ``fib_swing``/``fib_levels`` keys in the
                result — byte-for-byte identical to Stage 2 behaviour. When
                ``True``, anchors retracement levels to the most recent
                swing (``analysis.zone_engine.fibonacci.find_recent_swing``),
                checks each display zone for confluence with them, and rates
                the combined EMA20+Fibonacci confluence — see
                ``_enrich_zone``/``_enrich_zone_with_fibonacci``. This never
                changes Stage 1 detection/scoring or Stage 2 trend/EMA20
                math; ``odd_score`` is identical either way.

        Returns a dict with (at minimum):
            * ``all_zones`` — every detected zone, as dicts (Zone.to_dict()
              plus legacy alias keys). This is the *filtered, display*
              list — see ``analysis.zone_engine.filters.filter_zones`` —
              not the raw, full-history scan. Each zone dict also carries
              the Stage 2 context fields below.
            * ``all_zones_count`` — total number of zones ``detect_zones``
              found before filtering, for the "(of N detected)" summary.
            * ``nearest_demand`` / ``nearest_supply`` — nearest zone (as a
              dict) below/above the current price, or ``None``.
            * ``current_price`` — latest close.
            * ``trend`` — overall market trend ("UP"/"DOWN"/"SIDEWAYS")
              from the documented 50 SMA clock method (see
              ``analysis.zone_engine.trend.detect_trend``).
            * ``trend_detail`` — the full ``TrendInfo`` dict (sma_now,
              sma_past, slope, angle) backing ``trend``.
            * ``status`` — "bullish" near a tradeable, fresh demand zone in
              an uptrend; "bearish" near a tradeable, fresh supply zone in
              a downtrend; otherwise "neutral".
            * ``summary`` — one-line human-readable summary, including the
              trend and (for the nearest zone) its EMA 20 confluence and
              tradeability flags.

            Plus the legacy keys ``demand_zones``, ``supply_zones`` and
            ``strength`` that the existing UI already depends on (see the
            module docstring).

            Stage 2 additions to every zone dict (additive context — never
            folded into the documented 7-point ``odd_score``):
            ``trend_at_zone`` (the overall trend when the zone was
            evaluated), ``ema20_enhancer`` (True when the 20-period EMA is
            in/near the zone — a "high probability" confluence bonus, see
            ``analysis.zone_engine.enhancers.ema20_confluence``), and
            ``is_tradeable``/``trade_warning`` (the trend-alignment safety
            verdict — see ``_apply_trend_alignment``).

            Stage 3 additions (only populated when ``use_fibonacci=True``;
            otherwise every zone keeps the conservative defaults below and
            neither ``fib_swing`` nor ``fib_levels`` appears in the result
            at all):
              * ``fib_swing`` — the ``SwingInfo`` anchoring the retracement
                (see ``analysis.zone_engine.fibonacci.find_recent_swing``).
              * ``fib_levels`` — ``{ratio: price}`` for the four documented
                retracement levels (0.382/0.5/0.618/0.786), see
                ``analysis.zone_engine.fibonacci.calculate_fib_levels``.
              * Per zone: ``fib_confluence``/``fib_levels_in_zone``/
                ``fib_strongest`` (whether/which levels line up with it,
                see ``analysis.zone_engine.fibonacci.fib_confluence``) and
                ``confluence_score``/``confluence_label`` (the combined
                EMA20+Fibonacci confluence rating — a SEPARATE scorecard
                from ``odd_score``, see
                ``analysis.zone_engine.scoring.confluence_rating``).
        """
        if data.empty or len(data) < _MIN_CANDLES:
            self._result = {"error": "Insufficient data for demand/supply analysis."}
            self._status = "neutral"
            self._strength = "Weak"
            self._summary = "Not enough data."
            return self._result

        try:
            # Rule: never return NaN as current_price — if the most recent
            # candle has a NaN close (e.g. a partial/empty intraday row from
            # the data source), fall back to the last *valid* close so that
            # the price displayed on the card is always a real number.
            _close_valid = data["Close"].dropna()
            current_price = float(_close_valid.iloc[-1]) if not _close_valid.empty else 0.0

            # --- Rule: scan all 4 patterns (DBR/RBR/RBD/DBD) -----------------
            # detect_zones() reports every structure across the full history —
            # keep that count for the summary, but never draw/expose the raw
            # list directly: it's far too noisy for a chart (20+ overlapping
            # zones). filter_zones() reduces it to the meaningful, tradeable
            # subset (fresh + scoring >=5, overlaps merged, nearest 3 per
            # side) that everything below — display, nearest zones, status —
            # is derived from. See analysis.zone_engine.filters.filter_zones.
            # Drop today's candle while the market is still open — its
            # OHLC values change intraday, so zone detection on an
            # incomplete candle produces unreliable zones.  After NSE
            # close (3:30 PM IST + 30 min buffer) the candle is final
            # and safe to use.  current_price above still uses the live
            # close for display regardless.
            zone_data = data
            if isinstance(data.index, pd.DatetimeIndex) and not data.empty:
                if data.index[-1].date() >= dt.date.today():
                    ist = zoneinfo.ZoneInfo("Asia/Kolkata")
                    if dt.datetime.now(ist).hour < 16:
                        zone_data = data.iloc[:-1]
            zones = detect_zones(zone_data)
            all_zones_count = len(zones)
            display_zones = filter_zones(zones, current_price)

            # --- Stage 2: trend + EMA 20 confluence context ------------------
            # Pure additive context layered on the already-filtered display
            # zones — neither changes detection/scoring math nor what
            # filter_zones chose to show (see _enrich_zone).
            trend_detail = detect_trend(data)
            trend = trend_detail["trend"]

            # --- Stage 3: OPT-IN Fibonacci confluence context ----------------
            # Only computed when the "Enhance with Fibonacci Confluence"
            # checkbox is on; when it's off, fib_swing/fib_levels are simply
            # never added to the result and every zone keeps its Stage 2
            # defaults (see _enrich_zone) — byte-for-byte identical to
            # Stage 2 behaviour. Anchored to the most recent significant
            # swing across the *full* history, not just the display zones —
            # see analysis.zone_engine.fibonacci.find_recent_swing.
            fib_swing = None
            fib_levels: dict[float, float] = {}
            if use_fibonacci:
                fib_swing = find_recent_swing(data)
                fib_levels = calculate_fib_levels(fib_swing)

            display_zones = [
                _enrich_zone(z, data, trend, use_fibonacci, fib_levels) for z in display_zones
            ]

            demand_zones = [z for z in display_zones if z.category == "demand"]
            supply_zones = [z for z in display_zones if z.category == "supply"]

            nearest_demand = _nearest_below(demand_zones, current_price)
            nearest_supply = _nearest_above(supply_zones, current_price)

            nd_dict = _zone_dict(nearest_demand) if nearest_demand else None
            ns_dict = _zone_dict(nearest_supply) if nearest_supply else None

            # --- Rule: status — bullish near a *tradeable*, fresh demand
            # zone in an uptrend; bearish near a tradeable, fresh supply
            # zone in a downtrend; otherwise neutral -------------------------
            status = _determine_status(current_price, nd_dict, ns_dict)
            active_zone = nd_dict if status == "bullish" else ns_dict if status == "bearish" else None
            strength = _legacy_strength(active_zone)

            summary = _build_summary(
                all_zones_count, demand_zones, supply_zones, nd_dict, ns_dict,
                current_price, status, trend, use_fibonacci,
            )

            self._status = status
            self._strength = strength
            self._summary = summary
            self._result = {
                "symbol": symbol,
                "current_price": current_price,
                # New Zone-spec shape — already filtered down to the
                # meaningful/tradeable subset (see filter_zones), enriched
                # with Stage 2 trend/EMA20 context and (when switched on)
                # Stage 3 Fibonacci confluence context (see _enrich_zone).
                "all_zones": [_zone_dict(z) for z in display_zones],
                "all_zones_count": all_zones_count,
                "nearest_demand": nd_dict,
                "nearest_supply": ns_dict,
                "status": status,
                "summary": summary,
                # Stage 2: overall trend (50 SMA clock method).
                "trend": trend,
                "trend_detail": trend_detail,
                # Legacy-compatible keys still read by ui/pages/dashboard.py
                # and ui/components/stock_detail.py — see module docstring.
                "demand_zones": [_zone_dict(z) for z in demand_zones],
                "supply_zones": [_zone_dict(z) for z in supply_zones],
                "strength": strength,
            }
            # Stage 3: only ever present when the Fibonacci enhancer was
            # switched on — their absence is how stock_detail.py detects
            # whether to draw the retracement lines (see _add_fibonacci_lines)
            # and the result stays byte-for-byte identical to Stage 2 when off.
            if use_fibonacci:
                self._result["fib_swing"] = fib_swing
                self._result["fib_levels"] = fib_levels
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


def _apply_trend_alignment(zone: Zone, trend: str) -> Zone:
    """Rule: Stage 2 trend-alignment safety — a zone is only "tradeable"
    when it agrees with the overall market direction:

      * a DEMAND zone is tradeable ONLY when the trend is "UP"
      * a SUPPLY zone is tradeable ONLY when the trend is "DOWN"
      * in a "SIDEWAYS" market, *no* zone is considered tradeable

    Returns a copy of *zone* with ``trend_at_zone``, ``is_tradeable`` and
    ``trade_warning`` set accordingly (every other field — including the
    Stage 1 ``odd_score`` — is left untouched).
    """
    if trend == "SIDEWAYS":
        return replace(zone, trend_at_zone=trend, is_tradeable=False, trade_warning=_SIDEWAYS_WARNING)

    if zone.category == "demand":
        if trend == "UP":
            return replace(zone, trend_at_zone=trend, is_tradeable=True, trade_warning="")
        return replace(zone, trend_at_zone=trend, is_tradeable=False, trade_warning=_DEMAND_IN_DOWNTREND_WARNING)

    # category == "supply"
    if trend == "DOWN":
        return replace(zone, trend_at_zone=trend, is_tradeable=True, trade_warning="")
    return replace(zone, trend_at_zone=trend, is_tradeable=False, trade_warning=_SUPPLY_IN_UPTREND_WARNING)


def _enrich_zone(
    zone: Zone,
    data: pd.DataFrame,
    trend: str,
    use_fibonacci: bool = False,
    fib_levels: dict[float, float] | None = None,
) -> Zone:
    """Attach Stage 2 (and, opt-in, Stage 3) *context* to a display zone —
    the EMA 20 confluence bonus flag, the trend-alignment tradeability
    verdict and (when ``use_fibonacci`` is on) the Fibonacci confluence
    rating — without touching any Stage 1 detection/scoring field
    (``odd_score`` and friends pass through ``dataclasses.replace``
    untouched throughout).

    See ``analysis.zone_engine.enhancers.ema20_confluence``,
    ``_apply_trend_alignment`` and ``_enrich_zone_with_fibonacci`` for the
    rules each piece of context encodes.

    Args:
        zone: The display zone to enrich.
        data: Full OHLCV DataFrame (for the EMA 20 confluence check).
        trend: The overall market trend ("UP"/"DOWN"/"SIDEWAYS").
        use_fibonacci: Stage 3 OPT-IN switch — when ``False`` (the
            default), the zone's ``fib_*``/``confluence_*`` fields are left
            at their Stage 2 defaults entirely untouched (byte-for-byte
            identical to Stage 2 behaviour).
        fib_levels: ``{ratio: price}`` retracement levels to check the zone
            against — only consulted when ``use_fibonacci`` is True and
            non-empty (graceful "nothing to anchor to" handling when swing
            detection couldn't find one — see ``find_recent_swing``).
    """
    confluence = ema20_confluence(data, zone)
    enriched = replace(zone, ema20_enhancer=confluence["is_enhancer"])
    enriched = _apply_trend_alignment(enriched, trend)

    if use_fibonacci and fib_levels:
        enriched = _enrich_zone_with_fibonacci(enriched, fib_levels)

    return enriched


def _enrich_zone_with_fibonacci(zone: Zone, fib_levels: dict[float, float]) -> Zone:
    """Stage 3 (opt-in): attach the Fibonacci confluence bonus to *zone* —
    purely additive context, layered on top of everything ``_enrich_zone``
    has already attached (including the just-set ``ema20_enhancer`` flag,
    which feeds the combined rating below) without touching ``odd_score``
    or any other Stage 1/2 field.

    Computes the per-zone Fibonacci confluence
    (``analysis.zone_engine.fibonacci.fib_confluence``) and combines it with
    the EMA 20 flag into a SEPARATE ``confluence_score``/``confluence_label``
    scorecard (``analysis.zone_engine.scoring.confluence_rating`` — never
    merged into ``odd_score``).
    """
    fib_result = fib_confluence(zone, fib_levels)
    rating = confluence_rating(zone.ema20_enhancer, fib_result)
    return replace(
        zone,
        fib_confluence=fib_result["has_confluence"],
        fib_levels_in_zone=fib_result["levels_in_zone"],
        fib_strongest=fib_result["strongest_level"],
        confluence_score=rating["confluence_score"],
        confluence_label=rating["confluence_label"],
    )


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
    """Rule: status (Stage 2: now trend-aware) — "bullish" when price sits
    near a *fresh*, *tradeable* demand zone (which, per the trend-alignment
    rule, means the overall trend is "UP"); "bearish" when it sits near a
    fresh, tradeable supply zone (trend "DOWN"); otherwise "neutral".

    Requiring ``is_tradeable`` is what makes this trend-aware: a fresh zone
    that disagrees with the prevailing trend (or a sideways market) can no
    longer drive the headline bias on its own — see
    ``_apply_trend_alignment``.
    """
    if (
        nearest_demand
        and nearest_demand.get("is_fresh")
        and nearest_demand.get("is_tradeable")
        and _is_near(price, nearest_demand)
    ):
        return "bullish"
    if (
        nearest_supply
        and nearest_supply.get("is_fresh")
        and nearest_supply.get("is_tradeable")
        and _is_near(price, nearest_supply)
    ):
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


def _zone_flags_suffix(zone: dict[str, Any]) -> str:
    """Stage 2: render a zone's additive context as a trailing
    ``", EMA20 confluence, TRADEABLE"``-style suffix for the summary
    headline (see ``_enrich_zone`` / ``_apply_trend_alignment``).

    EMA 20 confluence is reported only when present (it's a bonus, not
    always-on); the tradeability verdict is always reported, as either
    "TRADEABLE" or "AVOID".
    """
    flags = ", EMA20 confluence" if zone.get("ema20_enhancer") else ""
    flags += ", TRADEABLE" if zone.get("is_tradeable") else ", AVOID"
    return flags


def _fib_summary_parts(zone: dict[str, Any] | None, use_fibonacci: bool) -> list[str]:
    """Stage 3 (opt-in): render the headline zone's Fibonacci confluence
    context as extra summary segments, e.g. ``["Fib 0.618 in zone",
    "Confluence: High"]``.

    Only produces anything when ``use_fibonacci`` is on (the checkbox off
    means the zone carries nothing but Stage 2 defaults, and the summary
    must stay byte-for-byte identical to Stage 2's) — see ``_enrich_zone``/
    ``_enrich_zone_with_fibonacci`` for where these fields come from.

    Reports the strongest level only when it actually fell *inside* the
    zone (the higher-conviction case the "Fib X in zone" wording promises);
    the combined ``confluence_label`` is always surfaced so a zone whose
    only confluence is the EMA 20 still gets its rating shown.
    """
    if not use_fibonacci or not zone:
        return []

    parts: list[str] = []
    levels_in_zone = zone.get("fib_levels_in_zone") or []
    strongest = zone.get("fib_strongest")
    if strongest is not None and strongest in levels_in_zone:
        parts.append(f"Fib {strongest:g} in zone")
    parts.append(f"Confluence: {zone.get('confluence_label', 'None')}")
    return parts


def _build_summary(
    all_zones_count: int,
    demand_zones: list[Zone],
    supply_zones: list[Zone],
    nearest_demand: dict[str, Any] | None,
    nearest_supply: dict[str, Any] | None,
    price: float,
    status: Status,
    trend: str,
    use_fibonacci: bool = False,
) -> str:
    """Build a one-line summary, e.g.:

    "Trend: UP | Showing 4 key zones (of 23 detected) | Nearest demand
    1121-1178 (DBR, score 6, Strong, EMA20 confluence, TRADEABLE) | Fib
    0.618 in zone | Confluence: High | price near fresh demand"

    Stage 2 adds the leading "Trend: ..." headline (from the 50 SMA clock
    method) and appends each nearest zone's additive context — its EMA 20
    confluence bonus (when present) and trend-alignment tradeability
    verdict — to its descriptor. Stage 3 (opt-in — see ``use_fibonacci``)
    appends the headline zone's Fibonacci confluence context as further
    segments (see ``_fib_summary_parts``); with the checkbox off these are
    simply absent and the summary is byte-for-byte identical to Stage 2's.
    The "(of N detected)" / "price ..." parts from Stage 1's decluttering
    summary are preserved unchanged.
    """
    shown = len(demand_zones) + len(supply_zones)
    parts = [
        f"Trend: {trend}",
        f"Showing {shown} key zone{'s' if shown != 1 else ''} (of {all_zones_count} detected)",
    ]

    candidates = []
    if nearest_demand:
        candidates.append(("Nearest demand", nearest_demand))
    if nearest_supply:
        candidates.append(("Nearest supply", nearest_supply))

    headline_zone: dict[str, Any] | None = None
    if candidates:
        label, headline_zone = min(candidates, key=lambda lz: abs(_zone_mid(lz[1]) - price))
        parts.append(
            f"{label} {headline_zone['bottom']:.0f}-{headline_zone['top']:.0f} "
            f"({headline_zone['zone_type']}, score {_fmt_score(headline_zone['odd_score'])}, "
            f"{headline_zone['zone_strength']}{_zone_flags_suffix(headline_zone)})"
        )

    parts.extend(_fib_summary_parts(headline_zone, use_fibonacci))

    bias = {
        "bullish": "near fresh demand",
        "bearish": "near fresh supply",
        "neutral": "between zones",
    }
    parts.append(f"price {bias[status]}")

    return " | ".join(parts)
