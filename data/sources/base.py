"""Abstract base class for all data sources."""

from abc import ABC, abstractmethod

import pandas as pd

_OHLC = ("Open", "High", "Low", "Close")


def drop_incomplete_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Remove rows missing any OHLC value.

    A partially-formed bar is not a candle, and letting one through is worse
    than dropping it because nothing downstream raises. Every comparison
    against NaN is False, so ``classify_candle`` reports a NaN-close bar as a
    BORING DOJI — a plausible-looking base candle that can silently extend a
    base, shift ``base_end_idx`` or break a leg-out run, with no error
    anywhere. Observed on BAJAJHLDNG 2026-07-31, where Yahoo returned open,
    high, low and volume but no close.

    Volume is deliberately not checked: a genuine zero-volume session is a
    real (if illiquid) bar, and the sources already filter those separately.

    Args:
        df: OHLCV frame, or anything lacking those columns (returned as-is).

    Returns:
        The frame with incomplete rows removed.
    """
    if df is None or df.empty:
        return df
    present = [c for c in _OHLC if c in df.columns]
    if not present:
        return df
    return df.dropna(subset=present)


class DataSource(ABC):
    """Contract that every data source implementation must satisfy."""

    @abstractmethod
    def connect(self, credentials: dict[str, str] | None = None) -> None:
        """Establish a connection / authenticate with the data source.

        Args:
            credentials: Optional mapping of credential fields to values.
        """

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the source is ready to serve data."""

    @abstractmethod
    def validate_credentials(self, credentials: dict[str, str]) -> bool:
        """Check whether the given credentials are structurally valid.

        Args:
            credentials: Credential fields to validate.

        Returns:
            True if the credentials appear complete, False otherwise.
        """

    @abstractmethod
    def fetch_quote(self, symbol: str) -> dict:
        """Fetch a real-time (or delayed) quote for *symbol*.

        Args:
            symbol: Ticker symbol, e.g. "RELIANCE.NS".

        Returns:
            Dict with keys: symbol, current_price, open, high, low,
            volume, change, change_pct.
        """

    @abstractmethod
    def fetch_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV history for *symbol*.

        Args:
            symbol: Ticker symbol.
            period: Lookback period string (e.g. "1y", "6mo", "3mo").
            interval: Bar interval (e.g. "1d", "1wk", "15m").

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume.
        """
