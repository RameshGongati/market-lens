"""Long-term investment analysis using weekly/monthly indicators."""

from typing import Any

import pandas as pd

from analysis.base import BaseAnalysis, Status, Strength
from utils.logger import get_logger

logger = get_logger(__name__)


class LongTermAnalysis(BaseAnalysis):
    """Analyses long-term investment potential using 200-day SMA and 52-week levels."""

    def __init__(self) -> None:
        self._result: dict[str, Any] = {}
        self._status: Status = "neutral"
        self._strength: Strength = "Weak"
        self._summary: str = "No analysis run yet."

    def analyse(self, symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        """Run long-term analysis on daily or weekly OHLCV data.

        Returns:
            Dict with: symbol, current_price, sma_200, trend, high_52w,
            low_52w, position_pct, recommendation, status, strength, summary.
        """
        if data.empty or len(data) < 50:
            self._result = {"error": "Insufficient data for long-term analysis (need 50+ bars)."}
            self._status = "neutral"
            self._strength = "Weak"
            self._summary = "Not enough data."
            return self._result

        try:
            closes = data["Close"]
            current_price = float(closes.iloc[-1])

            sma_200 = float(closes.rolling(window=min(200, len(closes))).mean().iloc[-1])

            window_52w = min(252, len(closes))
            high_52w = float(data["High"].iloc[-window_52w:].max())
            low_52w = float(data["Low"].iloc[-window_52w:].min())

            rng = high_52w - low_52w
            position_pct = ((current_price - low_52w) / rng * 100) if rng > 0 else 50.0

            if current_price > sma_200 * 1.02:
                trend = "uptrend"
                status: Status = "bullish"
            elif current_price < sma_200 * 0.98:
                trend = "downtrend"
                status = "bearish"
            else:
                trend = "sideways"
                status = "neutral"

            strength = _compute_strength(status, current_price, sma_200, position_pct)

            key_levels = {
                "sma_200": round(sma_200, 2),
                "high_52w": round(high_52w, 2),
                "low_52w": round(low_52w, 2),
            }
            recommendation = _build_recommendation(symbol, current_price, sma_200, trend, position_pct)
            summary = (
                f"{symbol}: {trend.capitalize()} | {position_pct:.0f}% of 52w range | "
                f"SMA200: ₹{sma_200:.2f} [{strength}]"
            )

            self._status = status
            self._strength = strength
            self._summary = summary
            self._result = {
                "symbol": symbol,
                "current_price": current_price,
                "sma_200": round(sma_200, 2),
                "trend": trend,
                "high_52w": round(high_52w, 2),
                "low_52w": round(low_52w, 2),
                "position_pct": round(position_pct, 1),
                "key_levels": key_levels,
                "recommendation": recommendation,
                "status": status,
                "strength": strength,
                "summary": summary,
            }
        except Exception as exc:
            logger.error("LongTermAnalysis failed for %s: %s", symbol, exc)
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


def _compute_strength(
    status: Status, price: float, sma200: float, position_pct: float
) -> Strength:
    """Derive strength from the alignment of long-term signals.

    Strong: price clearly above/below SMA200 AND near 52w high/low.
    Medium: mixed signals.
    Weak: price below SMA200 in downtrend or contradictory signals.
    """
    if status == "bullish":
        # Strong: well above SMA + high in 52w range
        if price > sma200 * 1.05 and position_pct > 70:
            return "Strong"
        if price > sma200 * 1.02:
            return "Medium"
        return "Weak"
    if status == "bearish":
        # Strong: well below SMA + low in 52w range
        if price < sma200 * 0.95 and position_pct < 30:
            return "Strong"
        if price < sma200 * 0.98:
            return "Medium"
        return "Weak"
    # Neutral: near SMA — weak by definition
    return "Weak"


def _build_recommendation(
    symbol: str, price: float, sma200: float, trend: str, position_pct: float
) -> str:
    lines = [f"Long-Term Analysis for {symbol}"]
    if trend == "uptrend":
        lines.append(
            f"Price ₹{price:.2f} is above the 200-day SMA (₹{sma200:.2f}), "
            "indicating a sustained long-term uptrend."
        )
        if position_pct > 80:
            lines.append(
                "Caution: Price is near its 52-week high — consider waiting "
                "for a pullback before adding positions."
            )
        else:
            lines.append(
                "Price has room to run within the 52-week range. "
                "Suitable for long-term accumulation on dips."
            )
    elif trend == "downtrend":
        lines.append(
            f"Price ₹{price:.2f} is below the 200-day SMA (₹{sma200:.2f}), "
            "suggesting a long-term downtrend. Avoid fresh long positions."
        )
        if position_pct < 20:
            lines.append(
                "Price is near its 52-week low — watch for base formation "
                "before considering entry."
            )
    else:
        lines.append(
            f"Price ₹{price:.2f} is near the 200-day SMA (₹{sma200:.2f}), "
            "showing consolidation. Wait for a decisive breakout in either direction."
        )
    return "\n".join(lines)
