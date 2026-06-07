"""Overall market trend detection — the "50 SMA clock method".

This is Stage 2 *context*, layered on top of the Stage 1 zone engine: it
answers "which way is the market actually moving right now?" so zones can
be filtered for trend alignment (a demand zone is far less reliable in a
downtrend, and vice versa). It does not alter any Stage 1 detection or
scoring math — ``detect_trend`` only reads OHLCV closes.

The documented "clock method" reads the 50-period SMA like a clock hand:

  * a hand sweeping from 12 toward 3 o'clock is climbing — an UP trend
  * a hand sweeping from 3 toward 6 o'clock is falling — a DOWN trend
  * a hand that barely moves is flat — a SIDEWAYS trend

Concretely: take the SMA value "now" (the latest candle) and "then"
(``lookback`` candles earlier), express the change between them as a
percent slope, and convert that slope to an angle (via ``atan``, the
"clock hand" angle) measured in degrees from horizontal. A clearly
positive angle within the 12-to-3 o'clock arc (0° to +60°) with the SMA
higher now than before is an UP trend; a clearly negative angle within the
3-to-6 o'clock arc (0° to -60°) with the SMA lower now is a DOWN trend;
anything else — in particular a slope too small to call a direction — is
SIDEWAYS.
"""

import math
from typing import TypedDict

import pandas as pd

# Rule: 50 SMA clock method — default SMA period and lookback window (in
# candles) used to measure the SMA's slope/angle.
_DEFAULT_SMA_PERIOD = 50
_DEFAULT_LOOKBACK = 7

# Rule: "nearly flat" — a percent change in the SMA over the lookback
# window smaller than this magnitude is considered noise, not a trend, and
# is reported as SIDEWAYS regardless of its sign/angle.
_FLAT_SLOPE_THRESHOLD_PCT = 0.3   # +/- 0.3% over `lookback` candles

# Rule: clock arcs — UP requires an angle in the 12-to-3 o'clock arc
# (0 to +60 degrees); DOWN requires an angle in the 3-to-6 o'clock arc
# (0 to -60 degrees). Angles outside these arcs (a near-vertical SMA) are
# treated conservatively as SIDEWAYS rather than guessed at.
_CLOCK_ARC_DEGREES = 60.0


class TrendInfo(TypedDict):
    """Result of the 50 SMA clock method."""

    trend: str                  # "UP" | "DOWN" | "SIDEWAYS"
    sma_now: float | None       # SMA value at the latest candle
    sma_past: float | None      # SMA value `lookback` candles earlier
    slope: float | None         # percent change, sma_now vs sma_past (e.g. 0.045 = +4.5%)
    angle: float | None         # atan(slope) in degrees — the "clock hand" angle


def _insufficient_data() -> TrendInfo:
    """Conservative "can't tell yet" result for short/edge-case data —
    reported as SIDEWAYS with every numeric field ``None``."""
    return TrendInfo(trend="SIDEWAYS", sma_now=None, sma_past=None, slope=None, angle=None)


def detect_trend(
    df: pd.DataFrame,
    sma_period: int = _DEFAULT_SMA_PERIOD,
    lookback: int = _DEFAULT_LOOKBACK,
) -> TrendInfo:
    """Determine the overall trend using the documented 50 SMA clock method.

    Steps (each cited inline):
      1. Compute the ``sma_period``-period SMA on closing prices.
      2. ``sma_now``  — the SMA value at the latest candle.
      3. ``sma_past`` — the SMA value ``lookback`` candles earlier.
      4. ``slope`` — percent change from ``sma_past`` to ``sma_now``;
         ``angle`` — that slope expressed as a "clock hand" angle via
         ``atan(slope)``, in degrees.
      5. Clock logic (see module docstring for the full rationale):
           * UP       — angle in (0°, +60°] AND ``sma_now > sma_past``
           * DOWN     — angle in [-60°, 0°) AND ``sma_now < sma_past``
           * SIDEWAYS — ``|slope|`` within the flat threshold (default
             +/- 0.3% over the lookback window), or any angle outside the
             12-to-6 o'clock arcs above.

    Args:
        df: Full OHLCV DataFrame (chronological order, needs a ``Close``
            column).
        sma_period: Number of candles in the SMA window (default 50).
        lookback: How many candles back to compare the SMA against
            (default 7).

    Returns:
        A ``TrendInfo`` dict. When there isn't enough history to compute
        both ``sma_now`` and ``sma_past`` (fewer than
        ``sma_period + lookback`` candles, or the SMA window hasn't warmed
        up yet), returns ``SIDEWAYS`` with every numeric field ``None`` —
        the conservative "can't tell yet" answer.
    """
    min_candles = sma_period + lookback
    if df.empty or len(df) < min_candles:
        return _insufficient_data()

    sma_series = df["Close"].rolling(window=sma_period).mean()
    sma_now_raw = sma_series.iloc[-1]
    sma_past_raw = sma_series.iloc[-1 - lookback]

    if pd.isna(sma_now_raw) or pd.isna(sma_past_raw):
        return _insufficient_data()

    sma_now = float(sma_now_raw)
    sma_past = float(sma_past_raw)
    if sma_past == 0:
        return _insufficient_data()

    slope = (sma_now - sma_past) / sma_past
    angle = math.degrees(math.atan(slope))
    flat_threshold = _FLAT_SLOPE_THRESHOLD_PCT / 100.0

    if abs(slope) <= flat_threshold:
        trend = "SIDEWAYS"
    elif 0.0 < angle <= _CLOCK_ARC_DEGREES and sma_now > sma_past:
        trend = "UP"
    elif -_CLOCK_ARC_DEGREES <= angle < 0.0 and sma_now < sma_past:
        trend = "DOWN"
    else:
        trend = "SIDEWAYS"

    return TrendInfo(trend=trend, sma_now=sma_now, sma_past=sma_past, slope=slope, angle=angle)
