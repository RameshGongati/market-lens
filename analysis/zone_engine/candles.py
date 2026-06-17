"""Candle classification for the demand/supply zone engine.

Implements the institutional "boring vs exciting" candle taxonomy that
underpins legin/base/legout pattern detection:

  * BORING / BASE candle   : body_pct < 0.50            (consolidation)
  * EXCITING candle        : body_pct >= 0.50           (directional momentum)
  * STRONG EXCITING candle : body_pct >= 0.80           (very strong momentum)

where ``body_pct = abs(close - open) / (high - low)``.

GTF teaches the exciting cutoff as "a body greater than roughly 50% of the
candle's range". 0.50 is therefore the boring/exciting split; there is no
separate indecisive band. This threshold is intentionally a single named
constant so it can be retuned after real-world testing.

Direction is determined purely by close vs open: BULLISH when the candle
closes above its open, BEARISH when it closes below, and DOJI when they are
equal (a doji carries no directional conviction and is treated as boring).
"""

from typing import TypedDict

# Rule: Candle Classification — body-to-range thresholds.
# Boring/exciting split at 0.50 (GTF "body > ~50%"); anything below 0.50 is
# boring (including dojis and zero-range bars), anything at or above is
# exciting. Retune here after real-world testing.
_EXCITING_THRESHOLD = 0.50   # body_pct >= 0.50 -> exciting
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
