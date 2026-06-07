"""Upstox API data source scaffold."""

import pandas as pd

from data.sources.base import DataSource
from utils.logger import get_logger

logger = get_logger(__name__)

_REQUIRED_FIELDS = {"api_key", "api_secret", "access_token"}


class UpstoxSource(DataSource):
    """Fetches market data via the Upstox V2 API.

    Requires api_key, api_secret, and a valid access_token obtained
    through the Upstox OAuth2 flow.
    """

    def __init__(self) -> None:
        self._client = None
        self._connected = False

    def connect(self, credentials: dict[str, str] | None = None) -> None:
        """Authenticate with Upstox using provided credentials.

        Args:
            credentials: Must contain api_key, api_secret, access_token.

        Raises:
            ValueError: If required credential fields are missing.
            RuntimeError: If the upstox-python-sdk library is not installed.
        """
        if not credentials or not self.validate_credentials(credentials):
            raise ValueError(
                "Upstox API requires api_key, api_secret, and access_token. "
                "Generate an access_token via the Upstox OAuth2 login flow."
            )
        try:
            import upstox_client  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "upstox-python-sdk not installed. Run: pip install upstox-python-sdk"
            ) from exc

        try:
            configuration = upstox_client.Configuration()
            configuration.access_token = credentials["access_token"]
            api_client = upstox_client.ApiClient(configuration)
            self._client = upstox_client.MarketQuoteApi(api_client)
            self._connected = True
            logger.info("Upstox API connected")
        except Exception as exc:
            self._connected = False
            logger.error("Upstox connection failed: %s", exc)
            raise RuntimeError(f"Upstox authentication failed: {exc}") from exc

    def is_connected(self) -> bool:
        return self._connected

    def validate_credentials(self, credentials: dict[str, str]) -> bool:
        """Return True only if all required fields are present and non-empty."""
        return all(credentials.get(f) for f in _REQUIRED_FIELDS)

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch a live quote via Upstox.

        Args:
            symbol: Upstox instrument key (e.g. "NSE_EQ|INE002A01018").
        """
        if not self._connected or self._client is None:
            raise NotImplementedError(
                "Upstox API is not connected. "
                "Add your api_key, api_secret, and access_token in Settings → Data Sources."
            )
        try:
            import upstox_client  # type: ignore[import]
            response = self._client.get_full_market_quote(symbol, "2.0")
            data = response.data[symbol]
            return {
                "symbol": symbol,
                "current_price": data.last_price,
                "open": data.ohlc.open,
                "high": data.ohlc.high,
                "low": data.ohlc.low,
                "volume": data.volume,
                "change": data.net_change,
                "change_pct": data.net_change / data.ohlc.close * 100 if data.ohlc.close else 0.0,
            }
        except Exception as exc:
            logger.error("Upstox fetch_quote failed for %s: %s", symbol, exc)
            raise

    def fetch_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV history via Upstox.

        Args:
            symbol: Upstox instrument key.
            period: Lookback period string.
            interval: Bar interval string.
        """
        if not self._connected or self._client is None:
            raise NotImplementedError(
                "Upstox API is not connected. "
                "Configure credentials via Settings → Data Sources."
            )
        raise NotImplementedError(
            "Upstox historical data fetch not yet implemented. "
            "Instrument key mapping and date range handling are required."
        )
