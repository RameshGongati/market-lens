"""Yahoo Finance data source — uses yfinance, no credentials required."""

import pandas as pd
import yfinance as yf

from data.sources.base import DataSource
from utils.logger import get_logger

logger = get_logger(__name__)


class YahooFinanceSource(DataSource):
    """Fetches market data from Yahoo Finance via yfinance."""

    def __init__(self) -> None:
        self._connected = True  # No auth needed; always ready

    def connect(self, credentials: dict[str, str] | None = None) -> None:
        """No-op — Yahoo Finance requires no authentication."""
        self._connected = True

    def is_connected(self) -> bool:
        return self._connected

    def validate_credentials(self, credentials: dict[str, str]) -> bool:
        """Always valid — Yahoo Finance needs no credentials."""
        return True

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch a real-time quote from Yahoo Finance.

        Args:
            symbol: Yahoo Finance ticker (e.g. "RELIANCE.NS", "AAPL").

        Returns:
            Dict with symbol, current_price, open, high, low, volume,
            change, change_pct.
        """
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.fast_info
            hist = ticker.history(period="2d", interval="1d")

            current_price = float(getattr(info, "last_price", 0) or 0)
            prev_close = float(getattr(info, "previous_close", current_price) or current_price)
            change = current_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0

            return {
                "symbol": symbol,
                "current_price": current_price,
                "open": float(getattr(info, "open", 0) or 0),
                "high": float(getattr(info, "day_high", 0) or 0),
                "low": float(getattr(info, "day_low", 0) or 0),
                "volume": int(getattr(info, "last_volume", 0) or 0),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            }
        except Exception as exc:
            logger.error("YahooFinance fetch_quote failed for %s: %s", symbol, exc)
            return {
                "symbol": symbol,
                "current_price": 0.0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0,
                "change": 0.0,
                "change_pct": 0.0,
                "error": str(exc),
            }

    def fetch_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV history from Yahoo Finance.

        Args:
            symbol: Yahoo Finance ticker.
            period: Lookback period (e.g. "1y", "6mo").
            interval: Bar size (e.g. "1d", "1wk", "15m").

        Returns:
            DataFrame with columns Open, High, Low, Close, Volume.
        """
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                logger.warning("No history returned for %s", symbol)
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception as exc:
            logger.error("YahooFinance fetch_history failed for %s: %s", symbol, exc)
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
