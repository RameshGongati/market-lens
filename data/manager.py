"""Data source manager — switches active source and delegates calls."""

import pandas as pd

from data.sources.base import DataSource
from data.sources.nse_india import NSEIndiaSource
from data.sources.tradingview import TradingViewSource
from data.sources.upstox import UpstoxSource
from data.sources.yahoo_finance import YahooFinanceSource
from data.sources.zerodha import ZerodhaSource
from utils.logger import get_logger

logger = get_logger(__name__)

_SOURCE_REGISTRY: dict[str, type[DataSource]] = {
    "Yahoo Finance": YahooFinanceSource,
    "NSE India": NSEIndiaSource,
    "Zerodha Kite Connect": ZerodhaSource,
    "Upstox API": UpstoxSource,
    "TradingView": TradingViewSource,
}


class DataSourceManager:
    """Manages the active data source and delegates data fetch calls."""

    def __init__(self) -> None:
        self._active_source_name: str = "Yahoo Finance"
        self._active_source: DataSource = YahooFinanceSource()
        self._active_source.connect()

    @property
    def active_source_name(self) -> str:
        """Name of the currently active data source."""
        return self._active_source_name

    @property
    def active_source(self) -> DataSource:
        """The active DataSource instance."""
        return self._active_source

    def switch_source(
        self,
        source_name: str,
        credentials: dict[str, str] | None = None,
    ) -> None:
        """Switch to a different data source.

        Args:
            source_name: One of the SUPPORTED_DATA_SOURCES strings.
            credentials: Optional credential fields for the new source.

        Raises:
            ValueError: If *source_name* is not recognised.
            RuntimeError: If the new source fails to connect.
        """
        if source_name not in _SOURCE_REGISTRY:
            raise ValueError(
                f"Unknown data source: '{source_name}'. "
                f"Choose one of: {list(_SOURCE_REGISTRY)}"
            )
        source_cls = _SOURCE_REGISTRY[source_name]
        new_source = source_cls()
        try:
            new_source.connect(credentials)
        except (ValueError, RuntimeError, NotImplementedError) as exc:
            raise RuntimeError(f"Failed to connect to {source_name}: {exc}") from exc

        self._active_source = new_source
        self._active_source_name = source_name
        logger.info("Active data source switched to: %s", source_name)

    def is_connected(self) -> bool:
        """Return True if the active source reports a live connection."""
        return self._active_source.is_connected()

    def get_quote(self, symbol: str) -> dict:
        """Fetch a real-time quote from the active data source.

        Args:
            symbol: Ticker symbol appropriate for the active source.

        Returns:
            Quote dict with symbol, current_price, open, high, low,
            volume, change, change_pct.
        """
        if not self._active_source.is_connected():
            return {
                "symbol": symbol,
                "current_price": 0.0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0,
                "change": 0.0,
                "change_pct": 0.0,
                "error": f"{self._active_source_name} is not connected.",
            }
        return self._active_source.fetch_quote(symbol)

    def get_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV history from the active data source.

        Args:
            symbol: Ticker symbol.
            period: Lookback period string.
            interval: Bar interval string.

        Returns:
            DataFrame with columns Open, High, Low, Close, Volume.
        """
        if not self._active_source.is_connected():
            logger.warning("Data source %s not connected", self._active_source_name)
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return self._active_source.fetch_history(symbol, period, interval)
