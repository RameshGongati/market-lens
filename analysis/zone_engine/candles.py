"""Candle classification for the demand/supply zone engine.

Implements the institutional "boring vs exciting" candle taxonomy that
underpins legin/base/legout pattern detection:

  * BORING / BASE candle   : body_pct <= 0.50           (consolidation)
  * indecisive candle      : 0.50 < body_pct < 0.60 -> treated as BORING
  * EXCITING candle        : body_pct >= 0.60           (directional momentum)
  * STRONG EXCITING candle : body_pct >= 0.80           (very strong momentum)

where ``body_pct = abs(close - open) / (high - low)``.

Direction is determined purely by close vs open: BULLISH when the candle
closes above its open, BEARISH when it closes below, and DOJI when they are
equal (a doji carries no directional conviction and is treated as boring).
"""

from typing import TypedDict

# Rule: Candle Classification — body-to-range thresholds.
# The 0.50-0.60 band is explicitly "neutral/indecisive ... treat as boring",
# so the boring/exciting split collapses to a single 0.60 cut here: anything
# below it is boring (including the indecisive band and dojis), anything at
# or above it is exciting.
_EXCITING_THRESHOLD = 0.60   # body_pct >= 0.60 -> exciting
_STRONG_THRESHOLD = 0.80     # body_pct >= 0.80 -> strong exciting


class CandleInfo(TypedDict):
    """Classification result for a single OHLC candle."""

    is_boring: bool
    is_exciting: bool
    is_strong: bool
    direction: str        # "bullish" | "bearish" | "doji"
    body_pct: float


def classify_candle(open_: float, high: float, low: float, close: float) -> CandleInfo:
    """Classify a single OHLC candle for legin/base/legout detection.

    Args:
        open_: Candle open price.
        high: Candle high price.
        low: Candle low price.
        close: Candle close price.

    Returns:
        A ``CandleInfo`` dict with ``is_boring``, ``is_exciting``,
        ``is_strong``, ``direction`` and the underlying ``body_pct`` ratio
        (exposed for diagnostics/tests).
    """
    total_range = high - low
    body = abs(close - open_)

    # Rule: guard against zero-range candles (e.g. illiquid/limit-locked
    # bars) — treat them as having no directional conviction.
    body_pct = (body / total_range) if total_range > 0 else 0.0

    # Rule: Direction — close vs open; equal values are a DOJI.
    if close > open_:
        direction = "bullish"
    elif close < open_:
        direction = "bearish"
    else:
        direction = "doji"  # DOJI candles carry no conviction -> boring

    # Rule: Candle Classification thresholds (see module docstring).
    is_exciting = body_pct >= _EXCITING_THRESHOLD
    is_strong = body_pct >= _STRONG_THRESHOLD
    is_boring = not is_exciting

    return CandleInfo(
        is_boring=is_boring,
        is_exciting=is_exciting,
        is_strong=is_strong,
        direction=direction,
        body_pct=body_pct,
    )
