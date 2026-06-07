"""Short-term investment analysis using daily indicators."""

from typing import Any

import numpy as np
import pandas as pd

from analysis.base import BaseAnalysis, Status, Strength
from utils.logger import get_logger

logger = get_logger(__name__)


def _compute_rsi(closes: pd.Series, period: int = 14) -> float:
    """Compute RSI for the most recent bar."""
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0


def _compute_macd(closes: pd.Series) -> tuple[float, float, float]:
    """Return (macd_line, signal_line, histogram) for the most recent bar."""
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal
    return float(macd_line.iloc[-1]), float(signal.iloc[-1]), float(hist.iloc[-1])


def _compute_rsi_series(closes: pd.Series, period: int = 14) -> pd.Series:
    """Return the full RSI series for plotting."""
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


class ShortTermAnalysis(BaseAnalysis):
    """Analyses short-term trading potential using SMA50, RSI, and MACD."""

    def __init__(self) -> None:
        self._result: dict[str, Any] = {}
        self._status: Status = "neutral"
        self._strength: Strength = "Weak"
        self._summary: str = "No analysis run yet."

    def analyse(self, symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        """Run short-term analysis on daily OHLCV data.

        Returns:
            Dict with: symbol, current_price, sma_50, rsi, macd_line,
            macd_signal, macd_hist, trend, momentum, key_levels,
            recommendation, status, strength, summary.
        """
        if data.empty or len(data) < 30:
            self._result = {"error": "Insufficient data for short-term analysis (need 30+ bars)."}
            self._status = "neutral"
            self._strength = "Weak"
            self._summary = "Not enough data."
            return self._result

        try:
            closes = data["Close"]
            current_price = float(closes.iloc[-1])

            sma_50 = float(closes.rolling(window=min(50, len(closes))).mean().iloc[-1])
            rsi = _compute_rsi(closes)
            macd_line, macd_signal, macd_hist = _compute_macd(closes)

            if current_price > sma_50:
                trend = "uptrend"
            elif current_price < sma_50:
                trend = "downtrend"
            else:
                trend = "sideways"

            if rsi >= 60:
                momentum = "strong"
            elif rsi >= 40:
                momentum = "moderate"
            else:
                momentum = "weak"

            bullish_signals = sum([
                current_price > sma_50,
                rsi > 50,
                macd_hist > 0,
            ])
            if bullish_signals >= 2:
                status: Status = "bullish"
            elif bullish_signals == 0:
                status = "bearish"
            else:
                status = "neutral"

            strength = _compute_strength(status, rsi, macd_hist, current_price, sma_50)

            key_levels = {
                "sma_50": round(sma_50, 2),
                "rsi": round(rsi, 1),
            }
            recommendation = _build_recommendation(
                symbol, current_price, sma_50, rsi, macd_line, macd_signal, macd_hist
            )
            summary = (
                f"{symbol}: {trend.capitalize()} | RSI {rsi:.0f} | "
                f"MACD {'▲' if macd_hist > 0 else '▼'} | SMA50: ₹{sma_50:.2f} [{strength}]"
            )

            self._status = status
            self._strength = strength
            self._summary = summary
            self._result = {
                "symbol": symbol,
                "current_price": current_price,
                "sma_50": round(sma_50, 2),
                "rsi": round(rsi, 1),
                "macd_line": round(macd_line, 4),
                "macd_signal": round(macd_signal, 4),
                "macd_hist": round(macd_hist, 4),
                "trend": trend,
                "momentum": momentum,
                "key_levels": key_levels,
                "recommendation": recommendation,
                "status": status,
                "strength": strength,
                "summary": summary,
            }
        except Exception as exc:
            logger.error("ShortTermAnalysis failed for %s: %s", symbol, exc)
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
    status: Status,
    rsi: float,
    macd_hist: float,
    price: float,
    sma50: float,
) -> Strength:
    """Rate strength from alignment of RSI, MACD, and SMA50.

    Strong: RSI 40-60 + MACD bullish + price above SMA50 (or inverse).
    Medium: 1-2 signals aligned.
    Weak: all signals against the stated direction.
    """
    if status == "bullish":
        signals = sum([
            40 <= rsi <= 65,     # RSI in healthy bullish zone (not overbought)
            macd_hist > 0,
            price > sma50,
        ])
    elif status == "bearish":
        signals = sum([
            35 <= rsi <= 60,
            macd_hist < 0,
            price < sma50,
        ])
    else:
        return "Weak"

    if signals == 3:
        return "Strong"
    if signals == 2:
        return "Medium"
    return "Weak"


def _build_recommendation(
    symbol: str, price: float, sma50: float, rsi: float,
    macd_line: float, macd_signal: float, macd_hist: float,
) -> str:
    lines = [f"Short-Term Analysis for {symbol}"]
    if price > sma50:
        lines.append(f"Price ₹{price:.2f} is above 50-day SMA (₹{sma50:.2f}) — short-term trend is up.")
    else:
        lines.append(f"Price ₹{price:.2f} is below 50-day SMA (₹{sma50:.2f}) — short-term trend is down.")
    if rsi > 70:
        lines.append(f"RSI {rsi:.0f} — overbought territory. Consider waiting for a pullback.")
    elif rsi < 30:
        lines.append(f"RSI {rsi:.0f} — oversold territory. Potential mean reversion opportunity.")
    else:
        lines.append(f"RSI {rsi:.0f} — neutral momentum zone.")
    if macd_hist > 0 and macd_line > macd_signal:
        lines.append("MACD is bullish — histogram positive and line above signal.")
    elif macd_hist < 0 and macd_line < macd_signal:
        lines.append("MACD is bearish — histogram negative and line below signal.")
    else:
        lines.append("MACD is at a crossover — watch for confirmation in the next few bars.")
    return "\n".join(lines)
