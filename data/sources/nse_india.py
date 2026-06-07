"""NSE India data source — scrapes the NSE website, no credentials required."""

import re

import pandas as pd
import requests
from bs4 import BeautifulSoup

from data.sources.base import DataSource
from utils.logger import get_logger

logger = get_logger(__name__)

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
_NSE_BASE = "https://www.nseindia.com"
_QUOTE_API = f"{_NSE_BASE}/api/quote-equity?symbol={{symbol}}"
_HIST_API = (
    f"{_NSE_BASE}/api/historical/cm/equity"
    "?symbol={symbol}&series=[%22EQ%22]&from={from_date}&to={to_date"
)


class NSEIndiaSource(DataSource):
    """Fetches equity data from the NSE India website."""

    def __init__(self) -> None:
        self._session: requests.Session | None = None
        self._connected = False

    def connect(self, credentials: dict[str, str] | None = None) -> None:
        """Create a session and fetch the NSE home page to prime cookies."""
        self._session = requests.Session()
        self._session.headers.update(_NSE_HEADERS)
        try:
            self._session.get(_NSE_BASE, timeout=10)
            self._connected = True
            logger.info("NSE India session initialised")
        except requests.RequestException as exc:
            logger.warning("NSE India connection warning: %s", exc)
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def validate_credentials(self, credentials: dict[str, str]) -> bool:
        """Always valid — NSE India needs no credentials."""
        return True

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch quote data from the NSE API.

        Args:
            symbol: NSE equity symbol (e.g. "RELIANCE").

        Returns:
            Quote dict with standard fields.
        """
        if not self._connected:
            self.connect()
        try:
            url = _QUOTE_API.format(symbol=symbol.upper())
            resp = self._session.get(url, timeout=10)  # type: ignore[union-attr]
            resp.raise_for_status()
            data = resp.json()
            price_info = data.get("priceInfo", {})
            current_price = float(price_info.get("lastPrice", 0))
            prev_close = float(price_info.get("previousClose", current_price))
            change = current_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
            return {
                "symbol": symbol.upper(),
                "current_price": current_price,
                "open": float(price_info.get("open", 0)),
                "high": float(price_info.get("intraDayHighLow", {}).get("max", 0)),
                "low": float(price_info.get("intraDayHighLow", {}).get("min", 0)),
                "volume": int(data.get("marketDeptOrderBook", {}).get("tradeInfo", {}).get("totalTradedVolume", 0)),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            }
        except Exception as exc:
            logger.error("NSE fetch_quote failed for %s: %s", symbol, exc)
            return {
                "symbol": symbol.upper(),
                "current_price": 0.0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0,
                "change": 0.0,
                "change_pct": 0.0,
                "error": f"NSE data unavailable: {exc}",
            }

    def fetch_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Return an empty DataFrame with a helpful message.

        NSE historical data requires date ranges; use Yahoo Finance for
        richer historical data until a full NSE history scraper is implemented.
        """
        logger.warning(
            "NSE India historical data not fully implemented. "
            "Consider using Yahoo Finance source for history."
        )
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
