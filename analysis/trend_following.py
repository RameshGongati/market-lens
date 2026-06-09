"""Trend Following strategy — 50 SMA / 200 SMA golden-cross / death-cross engine.

This is the Stage D primary strategy that provides genuine Trend Following
analysis as an alternative to the Demand/Supply zone engine.

Algorithm:
    1. Compute SMA_FAST (50-period) and SMA_SLOW (200-period) on closing prices.
    2. Scan for the most recent point where SMA_FAST crossed SMA_SLOW:
       * GOLDEN CROSS: SMA_FAST was below SMA_SLOW, now above → BUY.
       * DEATH CROSS:  SMA_FAST was above SMA_SLOW, now below → SELL.
    3. Determine the current signal from the live SMA relationship + 50 SMA
       clock-method trend context (reused from
       ``analysis.zone_engine.trend.detect_trend``).
    4. Grade strength from cross recency and trend clarity.

The 200 SMA requires at least ``SMA_SLOW + _BUFFER`` candles of history to be
meaningful. Results with fewer candles return a neutral / "HOLD" verdict with a
descriptive message rather than raising.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from analysis.base import BaseAnalysis, Status, Strength
from analysis.zone_engine.trend import detect_trend
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Public constants — easy to tune later without touching algorithm logic
# ---------------------------------------------------------------------------

SMA_FAST: int = 50     # Fast SMA period (short leg of cross pair)
SMA_SLOW: int = 200    # Slow SMA period (long leg of cross pair)

# ---------------------------------------------------------------------------
# Private tuning constants
# ---------------------------------------------------------------------------

# Minimum candles needed for a valid 200-period SMA.  The small buffer avoids
# acting on the very first SMA_SLOW values which average over incomplete windows.
_BUFFER: int = 10
_MIN_CANDLES: int = SMA_SLOW + _BUFFER   # 210

# A cross within this many candles from the latest bar is considered "recent"
# and earns the "Strong" rating; older crosses earn "Medium".
_RECENT_CROSS_CANDLES: int = 30


class TrendFollowingAnalysis(BaseAnalysis):
    """50/200 SMA golden-cross / death-cross strategy.

    Implements the ``BaseAnalysis`` contract so it plugs into the dashboard
    routing layer exactly like ``DemandSupplyAnalysis``.  The result dict uses
    a different shape (keyed by ``strategy: "Trend Following"``) which
    ``stock_card.py`` and ``stock_detail.py`` detect to render the correct UI.
    """

    def __init__(self) -> None:
        self._result: dict[str, Any] = {}
        self._status: Status = "neutral"
        self._strength: Strength = "Weak"
        self._summary: str = "No analysis run yet."

    # ------------------------------------------------------------------
    # BaseAnalysis contract
    # ------------------------------------------------------------------

    def analyse(self, symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        """Run the trend-following analysis and return a result dict.

        Args:
            symbol: The stock ticker being analysed.
            data: OHLCV DataFrame with at least a ``Close`` column.

        Returns:
            Dict with keys: ``strategy``, ``symbol``, ``current_price``,
            ``trend`` (UP/DOWN/SIDEWAYS), ``trend_detail``, ``signal``
            (BUY/SELL/HOLD), ``last_cross``, ``sma_fast_now``,
            ``sma_slow_now``, ``status``, ``strength``, ``summary``.

            When data is insufficient, ``error`` is also present and the
            signal/status default to safe "HOLD"/"neutral" values rather
            than raising.
        """
        if data.empty or len(data) < _MIN_CANDLES:
            return self._insufficient_data_result(symbol, len(data))

        try:
            close = data["Close"]
            _close_valid = close.dropna()
            current_price = float(_close_valid.iloc[-1]) if not _close_valid.empty else 0.0

            # --- Rolling SMAs -------------------------------------------------
            sma_fast_series = close.rolling(window=SMA_FAST).mean()
            sma_slow_series = close.rolling(window=SMA_SLOW).mean()

            sma_fast_now_raw = sma_fast_series.iloc[-1]
            sma_slow_now_raw = sma_slow_series.iloc[-1]

            if pd.isna(sma_fast_now_raw) or pd.isna(sma_slow_now_raw):
                raise ValueError("SMA computation yielded NaN — data may have gaps.")

            sma_fast_now = float(sma_fast_now_raw)
            sma_slow_now = float(sma_slow_now_raw)

            # --- Trend (50 SMA clock method — reused from zone_engine.trend) ---
            trend_info = detect_trend(data, sma_period=SMA_FAST)
            trend = trend_info["trend"]

            # --- Cross detection ----------------------------------------------
            last_cross = _find_last_cross(sma_fast_series, sma_slow_series, close)

            # --- Signal / status / strength / summary -------------------------
            signal = _determine_signal(sma_fast_now, sma_slow_now, trend)
            status: Status = (
                "bullish" if signal == "BUY"
                else "bearish" if signal == "SELL"
                else "neutral"
            )
            strength = _compute_strength(signal, last_cross)
            summary = _build_summary(signal, trend, last_cross, sma_fast_now, sma_slow_now)

            self._status = status
            self._strength = strength
            self._summary = summary
            self._result = {
                "strategy": "Trend Following",
                "symbol": symbol,
                "current_price": current_price,
                "trend": trend,
                "trend_detail": trend_info,
                "signal": signal,
                "last_cross": last_cross,
                "sma_fast_now": sma_fast_now,
                "sma_slow_now": sma_slow_now,
                "status": status,
                "strength": strength,
                "summary": summary,
            }

        except Exception as exc:
            logger.error("TrendFollowingAnalysis failed for %s: %s", symbol, exc)
            self._result = _error_result(symbol, str(exc))
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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _insufficient_data_result(self, symbol: str, n_rows: int) -> dict[str, Any]:
        msg = (
            f"Insufficient data: need >= {_MIN_CANDLES} candles for "
            f"{SMA_SLOW}-period SMA, got {n_rows}."
        )
        self._status = "neutral"
        self._strength = "Weak"
        self._summary = f"Trend Following | insufficient data for {SMA_SLOW} SMA"
        self._result = {
            **_neutral_skeleton(symbol),
            "error": msg,
            "summary": self._summary,
        }
        return self._result


# ---------------------------------------------------------------------------
# Pure helper functions — exported with underscore prefix so tests can import
# them directly for unit-level coverage without going through analyse().
# ---------------------------------------------------------------------------

def _neutral_skeleton(symbol: str) -> dict[str, Any]:
    """Return a safe, neutral result skeleton (no error field)."""
    return {
        "strategy": "Trend Following",
        "symbol": symbol,
        "current_price": 0.0,
        "trend": "SIDEWAYS",
        "trend_detail": None,
        "signal": "HOLD",
        "last_cross": {"type": None, "candles_ago": None, "price": None},
        "sma_fast_now": None,
        "sma_slow_now": None,
        "status": "neutral",
        "strength": "Weak",
        "summary": "Trend Following | neutral",
    }


def _error_result(symbol: str, message: str) -> dict[str, Any]:
    return {**_neutral_skeleton(symbol), "error": message, "summary": "Analysis error."}


def _find_last_cross(
    sma_fast: pd.Series,
    sma_slow: pd.Series,
    close: pd.Series,
) -> dict[str, Any]:
    """Find the most recent golden or death cross.

    A cross is detected when the sign of ``(sma_fast - sma_slow)`` flips
    between two consecutive candles where both SMAs are valid (non-NaN).

    Returns:
        ``{"type": "golden"/"death"/None, "candles_ago": int/None,
        "price": float/None}``

        *candles_ago* is measured from the last bar in *close* (0 = the
        cross happened on the latest bar).
        *price* is the closing price at the bar where the cross occurred.
    """
    diff = sma_fast - sma_slow
    valid = diff.dropna()

    if len(valid) < 2:
        return {"type": None, "candles_ago": None, "price": None}

    above = valid > 0                     # True where fast > slow
    prev_above = above.shift(1)           # previous bar's state
    both_valid = prev_above.notna()       # skip the first (shift produces NaN)
    sign_changed = above != prev_above
    cross_mask = both_valid & sign_changed
    cross_locs = valid.index[cross_mask]

    if len(cross_locs) == 0:
        return {"type": None, "candles_ago": None, "price": None}

    # Most recent cross
    last_ts = cross_locs[-1]
    cross_val = float(diff.loc[last_ts])
    cross_type = "golden" if cross_val > 0 else "death"

    # Distance from last bar in the original series
    positions = close.index.get_indexer([last_ts])
    pos = int(positions[0])
    candles_ago: int | None = (len(close) - 1 - pos) if pos >= 0 else None

    try:
        cross_price: float | None = float(close.iloc[pos]) if pos >= 0 else None
    except Exception:
        cross_price = None

    return {"type": cross_type, "candles_ago": candles_ago, "price": cross_price}


def _determine_signal(sma_fast_now: float, sma_slow_now: float, trend: str) -> str:
    """Return BUY / SELL / HOLD from the live SMA relationship and trend.

    Rules:
    * BUY  — SMA_FAST above SMA_SLOW (golden state) AND trend is UP.
    * SELL — SMA_FAST below SMA_SLOW (death state)  AND trend is DOWN.
    * HOLD — all other cases: sideways trend, or SMA direction conflicts
      with trend direction (mixed signals — better to stay out).

    This two-condition gate prevents false signals when the SMAs have just
    crossed but the 50 SMA clock method still reads the old direction.
    """
    if sma_fast_now > sma_slow_now and trend == "UP":
        return "BUY"
    if sma_fast_now < sma_slow_now and trend == "DOWN":
        return "SELL"
    return "HOLD"


def _compute_strength(signal: str, last_cross: dict[str, Any]) -> Strength:
    """Grade signal conviction from cross recency.

    * Strong — BUY or SELL with a cross within the last ``_RECENT_CROSS_CANDLES``.
    * Medium — BUY or SELL but the most recent cross is older (established trend).
    * Weak   — HOLD (sideways or conflicting signals).
    """
    if signal == "HOLD":
        return "Weak"

    candles_ago = last_cross.get("candles_ago")
    recent = (
        candles_ago is not None
        and isinstance(candles_ago, int)
        and candles_ago <= _RECENT_CROSS_CANDLES
    )
    return "Strong" if recent else "Medium"


def _build_summary(
    signal: str,
    trend: str,
    last_cross: dict[str, Any],
    sma_fast_now: float,
    sma_slow_now: float,
) -> str:
    """Build the one-line human-readable summary.

    Example output::

        "Trend Following | UP | BUY (golden cross 8 candles ago) | 50SMA above 200SMA"
        "Trend Following | DOWN | SELL (death cross 42 candles ago) | 50SMA below 200SMA"
        "Trend Following | SIDEWAYS | HOLD (no cross detected) | 50SMA below 200SMA"
    """
    cross_type = last_cross.get("type")
    candles_ago = last_cross.get("candles_ago")

    if cross_type and candles_ago is not None:
        cross_desc = f"{cross_type} cross {candles_ago} candles ago"
    elif cross_type:
        cross_desc = f"{cross_type} cross"
    else:
        cross_desc = "no cross detected"

    sma_rel = (
        f"{SMA_FAST}SMA above {SMA_SLOW}SMA"
        if sma_fast_now > sma_slow_now
        else f"{SMA_FAST}SMA below {SMA_SLOW}SMA"
    )

    return f"Trend Following | {trend} | {signal} ({cross_desc}) | {sma_rel}"
