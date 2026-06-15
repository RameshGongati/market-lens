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

# Rule: Relative-size gate — an exciting candle whose range (high-low) is
# less than this fraction of the local rolling-median range is downgraded to
# boring. Prevents tiny-range candles with a full body from acting as
# legin/legout when they are clearly consolidation relative to their neighbours.
_RELATIVE_SIZE_THRESHOLD = 0.75
_RELATIVE_SIZE_WINDOW = 20

# Rule: Pattern identity — (legin direction, legout direction) -> (zone_type, category)
_PATTERN_MAP: dict[tuple[str, str], tuple[str, str]] = {
    ("bearish", "bullish"): ("DBR", "demand"),   # Drop-Base-Rally
    ("bullish", "bullish"): ("RBR", "demand"),   # Rally-Base-Rally
    ("bullish", "bearish"): ("RBD", "supply"),   # Rally-Base-Drop
    ("bearish", "bearish"): ("DBD", "supply"),   # Drop-Base-Drop
}


def _classify_all(df: pd.DataFrame) -> list[CandleInfo]:
    """Classify every candle in the dataframe.

    Two-pass approach:
      1. Classify each candle individually by body_pct.
      2. Downgrade exciting candles whose range is too small relative to their
         neighbours (rolling-median gate), so tiny-bodied consolidation candles
         don't act as legin/legout.
    """
    candles = [
        classify_candle(float(o), float(h), float(l), float(c))
        for o, h, l, c in zip(df["Open"], df["High"], df["Low"], df["Close"])
    ]

    ranges = (df["High"].to_numpy(dtype=float) - df["Low"].to_numpy(dtype=float))
    n = len(ranges)
    if n == 0:
        return candles

    half_w = _RELATIVE_SIZE_WINDOW // 2
    for i, c in enumerate(candles):
        if not c["is_exciting"]:
            continue
        r = ranges[i]
        window = ranges[max(0, i - half_w): min(n, i + half_w)]
        positive = window[window > 0]
        if len(positive) == 0:
            continue
        local_median = float(np.median(positive))
        if local_median > 0 and r / local_median < _RELATIVE_SIZE_THRESHOLD:
            candles[i] = CandleInfo(
                is_boring=True,
                is_exciting=False,
                is_strong=False,
                direction=c["direction"],
                body_pct=c["body_pct"],
            )

    return candles


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


def _has_gap(df: pd.DataFrame, category: str, base_end: int, legout_start: int) -> bool:
    """Rule: Strength/gap check — a (breakaway) gap exists when the legout
    candle opens beyond the final base candle's range, in the legout
    direction (price "jumped" away from the base rather than grinding out
    of it)."""
    base_high = float(df["High"].iloc[base_end])
    base_low = float(df["Low"].iloc[base_end])
    legout_open = float(df["Open"].iloc[legout_start])
    if category == "demand":
        return legout_open > base_high
    return legout_open < base_low


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
        while (
            base_end + 1 < n
            and candles[base_end + 1]["is_boring"]
            and (base_end - base_start + 1) < _MAX_SCAN_BASE_CANDLES
        ):
            base_end += 1
        num_base_candles = base_end - base_start + 1

        legout_start = base_end + 1
        if legout_start >= n or not candles[legout_start]["is_exciting"]:
            # No exciting candle right after this base -> no legout here.
            # Resume scanning from the candle after the base; it may itself
            # anchor the next legin.
            i = base_end + 1
            continue

        legout_direction = candles[legout_start]["direction"]
        if legout_direction == "doji":
            i = base_end + 1
            continue

        # Rule: Legout validation — must clear the base range (by close) in
        # its own direction, i.e. a genuinely decisive move away.
        if not _legout_clears_base(df, legout_direction, base_start, base_end, legout_start):
            i = base_end + 1
            continue

        legin_direction = candles[legin_anchor]["direction"]
        pattern = _PATTERN_MAP.get((legin_direction, legout_direction))
        if pattern is None:
            # A doji legin (or any other non-decisive combination) does not
            # form one of the four documented patterns.
            i = base_end + 1
            continue
        zone_type, category = pattern

        # Rule: LEGIN/LEGOUT — "one or more exciting candles"; extend each
        # leg to capture multi-candle runs that share the same direction.
        legin_start = _extend_run(candles, legin_anchor, -1, _MAX_LEG_RUN, n)
        legout_end = _extend_run(candles, legout_start, +1, _MAX_LEG_RUN, n)

        proximal, distal = _normal_marking(df, category, base_start, base_end)
        # No alternate proximal rule is documented for EXCEPTIONAL marking —
        # it mirrors the NORMAL proximal line.
        proximal_exceptional = proximal
        distal_exceptional = _exceptional_distal(
            df, zone_type, legin_start, legin_anchor, legout_start, legout_end
        )

        has_gap = _has_gap(df, category, base_end, legout_start)
        legout_candles = candles[legout_start: legout_end + 1]

        score = score_zone(
            df=df,
            category=category,
            proximal=proximal,
            num_base_candles=num_base_candles,
            has_gap=has_gap,
            legout_candles=legout_candles,
            test_scan_start_idx=legout_end + 1,
        )

        zones.append(
            Zone(
                zone_type=zone_type,
                category=category,
                proximal=proximal,
                distal=distal,
                proximal_exceptional=proximal_exceptional,
                distal_exceptional=distal_exceptional,
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
            )
        )

        # Resume scanning right after this zone's legout run — a new legin
        # cannot start inside it.
        i = legout_end + 1

    return zones
