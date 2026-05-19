"""Intraday trading analysis using VWAP, RSI, and volume."""

from typing import Any

import numpy as np
import pandas as pd

from analysis.base import BaseAnalysis, Status, Strength
from utils.logger import get_logger

logger = get_logger(__name__)


def _compute_vwap(data: pd.DataFrame) -> float:
    """Compute VWAP for the given OHLCV data."""
    typical_price = (data["High"] + data["Low"] + data["Close"]) / 3
    cumulative_tpv = (typical_price * data["Volume"]).cumsum()
    cumulative_vol = data["Volume"].cumsum()
    vwap_series = cumulative_tpv / cumulative_vol.replace(0, np.nan)
    return float(vwap_series.iloc[-1]) if not vwap_series.empty else 0.0


def _compute_vwap_series(data: pd.DataFrame) -> pd.Series:
    """Return the full VWAP series for plotting."""
    typical_price = (data["High"] + data["Low"] + data["Close"]) / 3
    cumulative_tpv = (typical_price * data["Volume"]).cumsum()
    cumulative_vol = data["Volume"].cumsum()
    return cumulative_tpv / cumulative_vol.replace(0, np.nan)


def _compute_rsi(closes: pd.Series, period: int = 14) -> float:
    """Compute RSI for the most recent bar."""
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    return float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0


def _support_resistance(data: pd.DataFrame) -> tuple[float, float]:
    """Estimate simple intraday support and resistance from the session data."""
    resistance = float(data["High"].rolling(5).max().iloc[-1])
    support = float(data["Low"].rolling(5).min().iloc[-1])
    return support, resistance


class IntradayAnalysis(BaseAnalysis):
    """Analyses intraday trading opportunities using VWAP, RSI, and volume."""

    def __init__(self) -> None:
        self._result: dict[str, Any] = {}
        self._status: Status = "neutral"
        self._strength: Strength = "Weak"
        self._summary: str = "No analysis run yet."

    def analyse(self, symbol: str, data: pd.DataFrame) -> dict[str, Any]:
        """Run intraday analysis on 15-minute OHLCV data.

        Returns:
            Dict with: symbol, current_price, vwap, rsi, avg_volume,
            current_volume, volume_ratio, intraday_trend, support,
            resistance, recommendation, status, strength, summary.
        """
        if data.empty or len(data) < 5:
            self._result = {"error": "Insufficient data for intraday analysis (need 5+ bars)."}
            self._status = "neutral"
            self._strength = "Weak"
            self._summary = "Not enough data."
            return self._result

        try:
            closes = data["Close"]
            current_price = float(closes.iloc[-1])

            vwap = _compute_vwap(data)
            rsi = _compute_rsi(closes, period=min(14, len(closes) - 1))

            avg_volume = float(data["Volume"].mean())
            current_volume = float(data["Volume"].iloc[-1])
            volume_ratio = (current_volume / avg_volume) if avg_volume > 0 else 1.0

            support, resistance = _support_resistance(data)

            if current_price > vwap * 1.001:
                intraday_trend = "above VWAP — bullish bias"
                status: Status = "bullish"
            elif current_price < vwap * 0.999:
                intraday_trend = "below VWAP — bearish bias"
                status = "bearish"
            else:
                intraday_trend = "at VWAP — neutral"
                status = "neutral"

            if status == "bullish" and rsi > 75:
                status = "neutral"
                intraday_trend += " (RSI overbought)"
            elif status == "bearish" and rsi < 25:
                status = "neutral"
                intraday_trend += " (RSI oversold — possible bounce)"

            strength = _compute_strength(status, current_price, vwap, volume_ratio, rsi)

            recommendation = _build_recommendation(
                symbol, current_price, vwap, rsi, volume_ratio, support, resistance
            )
            summary = (
                f"{symbol}: {intraday_trend} | VWAP ₹{vwap:.2f} | "
                f"RSI {rsi:.0f} | Vol {volume_ratio:.1f}x avg [{strength}]"
            )

            self._status = status
            self._strength = strength
            self._summary = summary
            self._result = {
                "symbol": symbol,
                "current_price": current_price,
                "vwap": round(vwap, 2),
                "rsi": round(rsi, 1),
                "avg_volume": int(avg_volume),
                "current_volume": int(current_volume),
                "volume_ratio": round(volume_ratio, 2),
                "intraday_trend": intraday_trend,
                "support": round(support, 2),
                "resistance": round(resistance, 2),
                "recommendation": recommendation,
                "status": status,
                "strength": strength,
                "summary": summary,
            }
        except Exception as exc:
            logger.error("IntradayAnalysis failed for %s: %s", symbol, exc)
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
    price: float,
    vwap: float,
    volume_ratio: float,
    rsi: float,
) -> Strength:
    """Rate intraday strength from VWAP distance, volume, and RSI alignment.

    Strong: price clearly above/below VWAP + high volume + RSI 45-55.
    Medium: 1-2 signals aligned.
    Weak: signals against direction.
    """
    if status == "bullish":
        signals = sum([
            price > vwap * 1.005,   # Meaningfully above VWAP
            volume_ratio > 1.3,      # Higher than average volume
            45 <= rsi <= 65,         # RSI in healthy bullish zone
        ])
    elif status == "bearish":
        signals = sum([
            price < vwap * 0.995,
            volume_ratio > 1.3,
            35 <= rsi <= 55,
        ])
    else:
        return "Weak"

    if signals == 3:
        return "Strong"
    if signals == 2:
        return "Medium"
    return "Weak"


def _build_recommendation(
    symbol: str, price: float, vwap: float, rsi: float,
    vol_ratio: float, support: float, resistance: float,
) -> str:
    lines = [f"Intraday Analysis for {symbol}"]
    if price > vwap:
        lines.append(f"Price ₹{price:.2f} is above VWAP (₹{vwap:.2f}). Intraday bias is long.")
        lines.append(f"Potential long entry near VWAP ₹{vwap:.2f}, stop below support ₹{support:.2f}.")
    else:
        lines.append(f"Price ₹{price:.2f} is below VWAP (₹{vwap:.2f}). Intraday bias is short.")
        lines.append(f"Potential short entry near VWAP ₹{vwap:.2f}, stop above resistance ₹{resistance:.2f}.")
    if vol_ratio > 1.5:
        lines.append(f"Volume is {vol_ratio:.1f}x average — strong conviction in current move.")
    elif vol_ratio < 0.5:
        lines.append("Volume is below average — treat current move with caution.")
    if rsi > 70:
        lines.append(f"RSI {rsi:.0f} — overbought. Intraday longs should be cautious.")
    elif rsi < 30:
        lines.append(f"RSI {rsi:.0f} — oversold. Intraday shorts should be cautious.")
    lines.append(f"Intraday range: Support ₹{support:.2f} | Resistance ₹{resistance:.2f}")
    return "\n".join(lines)
