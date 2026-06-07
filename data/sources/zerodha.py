"""Zerodha Kite Connect data source scaffold."""

import pandas as pd

from data.sources.base import DataSource
from utils.logger import get_logger

logger = get_logger(__name__)

_REQUIRED_FIELDS = {"api_key", "api_secret", "access_token"}


class ZerodhaSource(DataSource):
    """Fetches market data via the Zerodha Kite Connect API.

    Requires api_key, api_secret, and a valid access_token obtained
    through the Kite Connect OAuth flow.
    """

    def __init__(self) -> None:
        self._kite = None
        self._connected = False

    def connect(self, credentials: dict[str, str] | None = None) -> None:
        """Authenticate with Kite Connect using provided credentials.

        Args:
            credentials: Must contain api_key, api_secret, access_token.

        Raises:
            ValueError: If required credential fields are missing.
            RuntimeError: If the kiteconnect library is not installed.
        """
        if not credentials or not self.validate_credentials(credentials):
            raise ValueError(
                "Zerodha Kite Connect requires api_key, api_secret, and access_token. "
                "Generate an access_token via the Kite Connect login flow."
            )
        try:
            from kiteconnect import KiteConnect  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "kiteconnect library not installed. Run: pip install kiteconnect"
            ) from exc

        try:
            self._kite = KiteConnect(api_key=credentials["api_key"])
            self._kite.set_access_token(credentials["access_token"])
            self._connected = True
            logger.info("Zerodha Kite Connect connected")
        except Exception as exc:
            self._connected = False
            logger.error("Zerodha connection failed: %s", exc)
            raise RuntimeError(f"Zerodha authentication failed: {exc}") from exc

    def is_connected(self) -> bool:
        return self._connected

    def validate_credentials(self, credentials: dict[str, str]) -> bool:
        """Return True only if all required fields are present and non-empty."""
        return all(credentials.get(f) for f in _REQUIRED_FIELDS)

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch a live quote via Kite Connect.

        Args:
            symbol: Kite instrument symbol (e.g. "NSE:RELIANCE").
        """
        if not self._connected or self._kite is None:
            raise NotImplementedError(
                "Zerodha Kite Connect is not connected. "
                "Add your api_key, api_secret, and access_token in Settings → Data Sources."
            )
        try:
            quote = self._kite.quote([symbol])[symbol]
            return {
                "symbol": symbol,
                "current_price": quote["last_price"],
                "open": quote["ohlc"]["open"],
                "high": quote["ohlc"]["high"],
                "low": quote["ohlc"]["low"],
                "volume": quote["volume"],
                "change": quote["net_change"],
                "change_pct": quote["change"],
            }
        except Exception as exc:
            logger.error("Zerodha fetch_quote failed for %s: %s", symbol, exc)
            raise

    def fetch_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV history via Kite Connect.

        Args:
            symbol: Kite instrument symbol.
            period: Lookback period (mapped to Kite date ranges).
            interval: Bar interval (mapped to Kite interval strings).
        """
        if not self._connected or self._kite is None:
            raise NotImplementedError(
                "Zerodha Kite Connect is not connected. "
                "Configure credentials via Settings → Data Sources."
            )
        raise NotImplementedError(
            "Zerodha historical data fetch not yet implemented. "
            "Instrument token lookup and date range mapping are required."
        )
