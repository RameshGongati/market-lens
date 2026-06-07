"""TradingView data source scaffold — uses tvdatafeed."""

import pandas as pd

from data.sources.base import DataSource
from utils.logger import get_logger

logger = get_logger(__name__)

_REQUIRED_FIELDS = {"username", "password"}

# Mapping from generic interval strings to tvdatafeed Interval enum names
_INTERVAL_MAP = {
    "1m": "in_1_minute",
    "5m": "in_5_minute",
    "15m": "in_15_minute",
    "30m": "in_30_minute",
    "1h": "in_1_hour",
    "2h": "in_2_hour",
    "4h": "in_4_hour",
    "1d": "in_daily",
    "1wk": "in_weekly",
    "1mo": "in_monthly",
}


class TradingViewSource(DataSource):
    """Fetches market data via TradingView using tvdatafeed.

    Requires a TradingView account (username and password).
    """

    def __init__(self) -> None:
        self._tv = None
        self._connected = False

    def connect(self, credentials: dict[str, str] | None = None) -> None:
        """Authenticate with TradingView.

        Args:
            credentials: Must contain username and password.

        Raises:
            ValueError: If required credential fields are missing.
            RuntimeError: If tvdatafeed library is not installed.
        """
        if not credentials or not self.validate_credentials(credentials):
            raise ValueError(
                "TradingView requires username and password. "
                "Use your TradingView account credentials."
            )
        try:
            from tvdatafeed import TvDatafeed  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "tvdatafeed not installed. Run: pip install tvdatafeed"
            ) from exc

        try:
            self._tv = TvDatafeed(
                username=credentials["username"],
                password=credentials["password"],
            )
            self._connected = True
            logger.info("TradingView connected for user %s", credentials["username"])
        except Exception as exc:
            self._connected = False
            logger.error("TradingView connection failed: %s", exc)
            raise RuntimeError(f"TradingView authentication failed: {exc}") from exc

    def is_connected(self) -> bool:
        return self._connected

    def validate_credentials(self, credentials: dict[str, str]) -> bool:
        """Return True only if username and password are present and non-empty."""
        return all(credentials.get(f) for f in _REQUIRED_FIELDS)

    def fetch_quote(self, symbol: str) -> dict:
        """Return the latest bar as a pseudo-quote.

        TradingView does not provide a standalone real-time quote endpoint;
        this fetches the most recent daily bar as a proxy.

        Args:
            symbol: Ticker in "EXCHANGE:SYMBOL" format (e.g. "NSE:RELIANCE").
        """
        if not self._connected or self._tv is None:
            raise NotImplementedError(
                "TradingView is not connected. "
                "Add your username and password in Settings → Data Sources."
            )
        try:
            parts = symbol.split(":", 1)
            exchange, sym = (parts[0], parts[1]) if len(parts) == 2 else ("NSE", symbol)
            from tvdatafeed import Interval  # type: ignore[import]
            df = self._tv.get_hist(sym, exchange, interval=Interval.in_daily, n_bars=2)
            if df is None or df.empty:
                raise ValueError(f"No data returned for {symbol}")
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            change = latest["close"] - prev["close"]
            change_pct = (change / prev["close"] * 100) if prev["close"] else 0.0
            return {
                "symbol": symbol,
                "current_price": float(latest["close"]),
                "open": float(latest["open"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
                "volume": int(latest["volume"]),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            }
        except Exception as exc:
            logger.error("TradingView fetch_quote failed for %s: %s", symbol, exc)
            raise

    def fetch_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV history from TradingView.

        Args:
            symbol: "EXCHANGE:SYMBOL" format ticker.
            period: Lookback period (used to estimate bar count).
            interval: Bar interval string mapped to tvdatafeed Interval.
        """
        if not self._connected or self._tv is None:
            raise NotImplementedError(
                "TradingView is not connected. "
                "Configure credentials via Settings → Data Sources."
            )
        try:
            from tvdatafeed import Interval  # type: ignore[import]
            tv_interval_name = _INTERVAL_MAP.get(interval, "in_daily")
            tv_interval = getattr(Interval, tv_interval_name)
            # Estimate n_bars from period string
            n_bars = _period_to_bars(period, interval)
            parts = symbol.split(":", 1)
            exchange, sym = (parts[0], parts[1]) if len(parts) == 2 else ("NSE", symbol)
            df = self._tv.get_hist(sym, exchange, interval=tv_interval, n_bars=n_bars)
            if df is None or df.empty:
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            df = df.rename(columns={
                "open": "Open", "high": "High",
                "low": "Low", "close": "Close", "volume": "Volume",
            })
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception as exc:
            logger.error("TradingView fetch_history failed for %s: %s", symbol, exc)
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def _period_to_bars(period: str, interval: str) -> int:
    """Rough estimation of bar count from period and interval strings."""
    period_days = {"1d": 1, "5d": 5, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730, "5y": 1825}
    bars_per_day = {"1m": 375, "5m": 75, "15m": 25, "30m": 12, "1h": 6, "2h": 3, "4h": 2, "1d": 1, "1wk": 0.14, "1mo": 0.033}
    days = period_days.get(period, 365)
    bpd = bars_per_day.get(interval, 1)
    return max(50, int(days * bpd))
