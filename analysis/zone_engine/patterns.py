"""Legin-base-legout pattern detection and zone boundary marking.

A "zone" forms when price makes an exciting (high-conviction) move away from
a level — the LEGIN — then pauses in a tight range of BORING/BASE candles,
then makes another exciting move away from that range — the LEGOUT. The
directions of the legin and legout together determine which of the four
documented institutional patterns formed:

    legin \\ legout |   bullish    |   bearish
    ----------------+--------------+--------------
       bearish      | DBR (demand) | DBD (supply)
       bullish      | RBR (demand) | RBD (supply)

Drop-Base-Rally and Rally-Base-Rally leave behind DEMAND zones (price is
expected to rally again from there); Rally-Base-Drop and Drop-Base-Drop
leave behind SUPPLY zones (price is expected to drop again from there).
"""

from typing import Sequence

import numpy as np
import pandas as pd

from analysis.zone_engine.candles import CandleInfo, classify_candle
from analysis.zone_engine.models import Zone
from analysis.zone_engine.scoring import score_zone

# Rule: BASE — a valid base is 1 to 6 consecutive boring candles; the
# scanner keeps extending the run up to 10 candles (anything longer than 6
# still forms a recognisable zone, it simply scores 0 on time-at-base).
_MAX_SCAN_BASE_CANDLES = 10

# Cap on how many consecutive same-direction exciting candles can extend a
# legin/legout run, so the scan stays bounded on strongly trending data.
_MAX_LEG_RUN = 6

# Rule: Pattern identity — (legin direction, legout direction) -> (zone_type, category)
_PATTERN_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("bearish", "bullish"): ("DBR", "demand"),   # Drop-Base-Rally
    ("bullish", "bullish"): ("RBR", "demand"),   # Rally-Base-Rally
    ("bullish", "bearish"): ("RBD", "supply"),   # Rally-Base-Drop
    ("bearish", "bearish"): ("DBD", "supply"),   # Drop-Base-Drop
}


def _classify_all(df: pd.DataFrame) -> list[CandleInfo]:
    """Classify every candle in the dataframe in a single pass."""
    return [
        classify_candle(float(o), float(h), float(l), float(c))
        for o, h, l, c in zip(df["Open"], df["High"], df["Low"], df["Close"])
    ]


def _extend_run(candles: Sequence[CandleInfo], anchor: int, step: int, limit: int, bound: int) -> int:
    """Extend a same-direction run of exciting candles away from *anchor*.

    Walks in *step* (-1 to extend a legin backwards, +1 to extend a legout
    forwards) while the next candle is exciting and shares the anchor
    candle's direction — implementing the spec's "one or more exciting
    candles" allowance for both legs — stopping at the array edge
    (*bound*) or after *limit* additional candles.

    Returns:
        The index of the furthest candle still part of the run.
    """
    direction = candles[anchor]["direction"]
    end = anchor
    steps = 0
    while steps < limit:
        nxt = end + step
        if nxt < 0 or nxt >= bound:
            break
        if not candles[nxt]["is_exciting"] or candles[nxt]["direction"] != direction:
            break
        end = nxt
        steps += 1
    return end


def _legout_clears_base(df: pd.DataFrame, direction: str, base_start: int, base_end: int, legout_idx: int) -> bool:
    """Rule: Legout validation — the legout candle must be an EXCITING
    candle whose CLOSE clears the base's high/low range in the legout
    direction (i.e. it represents a decisive move away from the base, not
    just a large wick back into it)."""
    base_high = float(df["High"].iloc[base_start: base_end + 1].max())
    base_low = float(df["Low"].iloc[base_start: base_end + 1].min())
    legout_close = float(df["Close"].iloc[legout_idx])
    if direction == "bullish":
        return legout_close > base_high
    if direction == "bearish":
        return legout_close < base_low
    return False


def _has_gap(df: pd.DataFrame, category: str, base_end: int, legout_start: int, legout_end: int) -> bool:
    """Rule: Strength/gap check — a gap exists when ANY candle in the legout
    sequence opens beyond the previous candle's range in the departure
    direction.

    Two patterns both count:
      1. base → gap → legout  (gap between last base candle and first legout)
      2. base → legout → gap  (gap between consecutive legout candles)

    For demand: gap up = next open > previous high.
    For supply: gap down = next open < previous low.
    """
    prev_idx = base_end
    for lo_idx in range(legout_start, legout_end + 1):
        prev_high = float(df["High"].iloc[prev_idx])
        prev_low = float(df["Low"].iloc[prev_idx])
        lo_open = float(df["Open"].iloc[lo_idx])
        if category == "demand" and lo_open > prev_high:
            return True
        if category == "supply" and lo_open < prev_low:
            return True
        prev_idx = lo_idx
    return False


def _normal_marking(df: pd.DataFrame, category: str, base_start: int, base_end: int) -> tuple[float, float]:
    """Rule: NORMAL zone boundary marking.

      * DEMAND (DBR, RBR): proximal = highest BODY top of the base
        (``max(open, close)`` per candle, then the max of those); distal =
        lowest WICK of the base (the lowest low).
      * SUPPLY (RBD, DBD): proximal = lowest BODY bottom of the base
        (``min(open, close)`` per candle, then the min of those); distal =
        highest WICK of the base (the highest high).
    """
    base = df.iloc[base_start: base_end + 1]
    body_tops = np.maximum(base["Open"].to_numpy(dtype=float), base["Close"].to_numpy(dtype=float))
    body_bottoms = np.minimum(base["Open"].to_numpy(dtype=float), base["Close"].to_numpy(dtype=float))

    if category == "demand":
        proximal = float(body_tops.max())
        distal = float(base["Low"].min())
    else:
        proximal = float(body_bottoms.min())
        distal = float(base["High"].max())
    return proximal, distal


# M13: Wick-to-wick vs body-to-wick proximal marking.
# If wick-to-wick zone width exceeds body-to-wick width by more than this
# ratio, body-to-wick is used to keep the zone tradeable (R:R concern).
_WICK_TO_BODY_ZONE_RATIO_THRESHOLD = 1.5

# M13: Doji detection — body smaller than this fraction of the candle range
# is treated as a doji for marking purposes (wicks = noise, not boundaries).
_DOJI_BODY_THRESHOLD = 0.10


def _wick_to_wick_marking(
    df: pd.DataFrame, category: str, base_start: int, base_end: int,
) -> tuple[float, float]:
    """Rule: WICK-TO-WICK zone boundary marking (M13 alternate).

      * DEMAND: proximal = highest HIGH of the base; distal = lowest LOW.
      * SUPPLY: proximal = lowest LOW of the base; distal = highest HIGH.

    Both edges use wick extremes, creating a tighter zone when wicks are small.
    """
    base = df.iloc[base_start: base_end + 1]
    if category == "demand":
        return float(base["High"].max()), float(base["Low"].min())
    return float(base["Low"].min()), float(base["High"].max())


def _has_doji_in_base(df: pd.DataFrame, base_start: int, base_end: int) -> bool:
    """Return True if any base candle is a doji (body < 10% of range)."""
    for idx in range(base_start, base_end + 1):
        o = float(df["Open"].iloc[idx])
        h = float(df["High"].iloc[idx])
        l = float(df["Low"].iloc[idx])
        c = float(df["Close"].iloc[idx])
        rng = h - l
        if rng <= 0:
            continue
        body_pct = abs(c - o) / rng
        if body_pct < _DOJI_BODY_THRESHOLD:
            return True
    return False


def _m13_proximal_marking(
    df: pd.DataFrame,
    category: str,
    base_start: int,
    base_end: int,
    has_gap: bool,
    num_exciting_legout: int,
    btw_proximal: float,
    btw_distal: float,
) -> tuple[float, str]:
    """M13: Choose between wick-to-wick and body-to-wick proximal.

    Priority chain:
      P1 — Explosive leg-out (2+ total leg-out units) → wick-to-wick.
           Each exciting candle = 1 unit, each gap = 1 unit.
      P2 — Doji in base → body-to-wick.
      P3 — Width ratio > 1.5 → body-to-wick; else wick-to-wick.

    Returns (proximal, proximal_marking) where proximal_marking is
    ``"Wick-to-Wick"`` or ``"Body-to-Wick"``.
    """
    wtw_proximal, _wtw_distal = _wick_to_wick_marking(df, category, base_start, base_end)

    # P1: Explosive leg-out override — 2+ total units → wick-to-wick.
    total_legout_units = num_exciting_legout + (1 if has_gap else 0)
    if total_legout_units >= 2:
        return wtw_proximal, "Wick-to-Wick"

    # P2: Doji in base — always body-to-wick.
    if _has_doji_in_base(df, base_start, base_end):
        return btw_proximal, "Body-to-Wick"

    # P3: Width ratio.
    btw_width = abs(btw_proximal - btw_distal)
    wtw_width = abs(wtw_proximal - _wtw_distal)
    if btw_width > 0 and wtw_width / btw_width > _WICK_TO_BODY_ZONE_RATIO_THRESHOLD:
        return btw_proximal, "Body-to-Wick"

    return wtw_proximal, "Wick-to-Wick"


def _exceptional_distal(
    df: pd.DataFrame,
    zone_type: str,
    legin_start: int,
    legin_end: int,
    legout_start: int,
    legout_end: int,
) -> float:
    """Rule: EXCEPTIONAL zone marking — alternative distal lines that
    account for legin/legout wicks reaching further than the base itself:

      * DBR: distal = lowest wick of the legin OR the legout
      * RBD: distal = highest wick of the legin OR the legout
      * RBR: distal = lowest wick of the legout
      * DBD: distal = highest wick of the legout

    (No alternate proximal rule is documented, so ``proximal_exceptional``
    simply mirrors the NORMAL proximal — see ``detect_zones``.)
    """
    legin = df.iloc[legin_start: legin_end + 1]
    legout = df.iloc[legout_start: legout_end + 1]

    if zone_type == "DBR":
        return float(min(legin["Low"].min(), legout["Low"].min()))
    if zone_type == "RBD":
        return float(max(legin["High"].max(), legout["High"].max()))
    if zone_type == "RBR":
        return float(legout["Low"].min())
    # DBD
    return float(legout["High"].max())


def detect_zones(df: pd.DataFrame) -> list[Zone]:
    """Scan an OHLCV dataframe for DBR / RBR / RBD / DBD zone patterns.

    The scan walks forward looking for "exciting candle -> run of boring
    base candles -> exciting candle(s) that decisively leave the base" --
    the legin/base/legout structure -- and builds a fully scored ``Zone``
    for every valid occurrence found (see module docstring for the pattern
    matrix and ``scoring.py`` for the ODD trade score).

    Args:
        df: OHLCV DataFrame with Open/High/Low/Close columns in
            chronological order (oldest first).

    Returns:
        Detected zones in the order they formed. Returns an empty list for
        empty or too-short dataframes (need at least legin+base+legout).
    """
    n = len(df)
    if n < 3:
        return []

    candles = _classify_all(df)
    zones: list[Zone] = []

    i = 1
    while i < n - 1:
        legin_anchor = i - 1

        # Rule: LEGIN — at least one exciting candle immediately before the base.
        if not candles[legin_anchor]["is_exciting"]:
            i += 1
            continue

        # Rule: BASE — must start with a boring candle right after the legin.
        if not candles[i]["is_boring"]:
            i += 1
            continue

        base_start = i
        base_end = base_start
        gap_is_legout = False
        while (
            base_end + 1 < n
            and candles[base_end + 1]["is_boring"]
            and (base_end - base_start + 1) < _MAX_SCAN_BASE_CANDLES
        ):
            nxt_open = float(df["Open"].iloc[base_end + 1])
            cur_high = float(df["High"].iloc[base_end])
            cur_low = float(df["Low"].iloc[base_end])
            if nxt_open > cur_high or nxt_open < cur_low:
                gap_is_legout = True
                break
            base_end += 1
        num_base_candles = base_end - base_start + 1

        legout_start = base_end + 1
        if legout_start >= n:
            i = base_end + 1
            continue

        if gap_is_legout:
            # Rule: A gap between consecutive candles IS a leg-out departure.
            # The gap counts as the first leg-out "candle" — the zone forms
            # even if the candle after the gap is boring.
            gap_open = float(df["Open"].iloc[legout_start])
            if gap_open > float(df["High"].iloc[base_end]):
                legout_direction = "bullish"
            else:
                legout_direction = "bearish"
        else:
            if not candles[legout_start]["is_exciting"]:
                i = base_end + 1
                continue
            legout_direction = candles[legout_start]["direction"]
            if legout_direction == "doji":
                i = base_end + 1
                continue
            if not _legout_clears_base(df, legout_direction, base_start, base_end, legout_start):
                i = base_end + 1
                continue

        legin_direction = candles[legin_anchor]["direction"]
        pattern = _PATTERN_MAP.get((legin_direction, legout_direction))
        if pattern is None:
            i = base_end + 1
            continue
        zone_type, category = pattern

        legin_start = _extend_run(candles, legin_anchor, -1, _MAX_LEG_RUN, n)
        # Extend legout: if candle at legout_start is exciting in the
        # legout direction, extend the run; otherwise legout is just
        # the gap candle itself.
        if candles[legout_start]["is_exciting"] and candles[legout_start]["direction"] == legout_direction:
            legout_end = _extend_run(candles, legout_start, +1, _MAX_LEG_RUN, n)
        else:
            legout_end = legout_start

        proximal, distal = _normal_marking(df, category, base_start, base_end)

        # Trim legout: a candle that opens outside the zone and touches
        # back in is a zone test, not a legout continuation.  Use the
        # WTW proximal (widest zone boundary) so the check catches
        # candles that enter even the wick-based zone.
        wtw_prox, _ = _wick_to_wick_marking(df, category, base_start, base_end)
        for trim_idx in range(legout_start + 1, legout_end + 1):
            o = float(df["Open"].iloc[trim_idx])
            h = float(df["High"].iloc[trim_idx])
            l = float(df["Low"].iloc[trim_idx])
            if category == "supply" and o < wtw_prox and h >= wtw_prox:
                legout_end = trim_idx - 1
                break
            if category == "demand" and o > wtw_prox and l <= wtw_prox:
                legout_end = trim_idx - 1
                break
        proximal_exceptional = proximal
        distal_exceptional = _exceptional_distal(
            df, zone_type, legin_start, legin_anchor, legout_start, legout_end
        )

        # M2: auto-apply exceptional distal when leg wick exceeds base wick.
        marking = "Normal"
        if category == "demand" and distal_exceptional < distal:
            distal = distal_exceptional
            marking = "Exceptional"
        elif category == "supply" and distal_exceptional > distal:
            distal = distal_exceptional
            marking = "Exceptional"

        has_gap = _has_gap(df, category, base_end, legout_start, legout_end)
        legout_candles = candles[legout_start: legout_end + 1]
        num_exciting_legout = sum(1 for c in legout_candles if c["is_exciting"])

        # M13: wick-to-wick vs body-to-wick proximal marking.
        proximal, proximal_marking = _m13_proximal_marking(
            df, category, base_start, base_end,
            has_gap=has_gap,
            num_exciting_legout=num_exciting_legout,
            btw_proximal=proximal,
            btw_distal=distal,
        )

        score = score_zone(
            df=df,
            category=category,
            proximal=proximal,
            distal=distal,
            num_base_candles=num_base_candles,
            has_gap=has_gap,
            legout_candles=legout_candles,
            test_scan_start_idx=legout_end + 1,
        )

        if score["is_invalidated"]:
            i = legout_end + 1
            continue

        zones.append(
            Zone(
                zone_type=zone_type,
                category=category,
                proximal=proximal,
                distal=distal,
                proximal_exceptional=proximal_exceptional,
                distal_exceptional=distal_exceptional,
                marking=marking,
                proximal_marking=proximal_marking,
                base_start_idx=base_start,
                base_end_idx=base_end,
                legout_idx=legout_start,
                num_base_candles=num_base_candles,
                odd_score=score["odd_score"],
                freshness_points=score["freshness_points"],
                strength_points=score["strength_points"],
                time_points=score["time_points"],
                times_tested=score["times_tested"],
                zone_strength=score["zone_strength"],
                entry_recommendation=score["entry_recommendation"],
                created_at_index=legout_start,
                is_fresh=score["is_fresh"],
                activation_touch=score["activation_touch"],
            )
        )

        # Resume scanning right after this zone's legout run — a new legin
        # cannot start inside it.
        i = legout_end + 1

    return zones
