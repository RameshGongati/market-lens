"""Jugaad Data source — fetches OHLCV history directly from NSE via jugaad-data.

Uses the jugaad-data library which scrapes NSE's official website.
No credentials required. Historical data is reliable; live quotes
may fail when the market is closed (NSE returns empty responses).
"""

import os
from datetime import date, timedelta

import pandas as pd

from data.sources.base import DataSource
from utils.logger import get_logger

logger = get_logger(__name__)

# Period string to days mapping for converting yfinance-style
# period strings to date ranges that jugaad-data expects.
_PERIOD_DAYS: dict[str, int] = {
    "1mo": 30,
    "3mo": 90,
    "6mo": 180,
    "1y": 365,
    "2y": 730,
    "5y": 1825,
    "10y": 3650,
    "max": 7300,
}


class JugaadDataSource(DataSource):
    """Fetches equity data from NSE via the jugaad-data library."""

    def __init__(self) -> None:
        self._connected = False

    def connect(self, credentials: dict[str, str] | None = None) -> None:
        """Verify jugaad-data is importable and prime its cache directory."""
        try:
            # Ensure cache directories exist to avoid FileExistsError
            # from jugaad-data's caching mechanism on first use.
            cache_dir = os.path.expanduser("~/.cache/nsehistory-stock")
            os.makedirs(cache_dir, exist_ok=True)
            from jugaad_data.nse import stock_df  # noqa: F401
            self._connected = True
            logger.info("Jugaad Data source initialised")
        except ImportError:
            logger.error("jugaad-data not installed: pip install jugaad-data")
            self._connected = False
        except Exception as exc:
            logger.warning("Jugaad Data init error: %s", exc)
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def validate_credentials(self, credentials: dict[str, str]) -> bool:
        """Always valid — no credentials needed."""
        return True

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch a live quote from NSE via jugaad-data.

        Falls back gracefully when the market is closed or the
        API returns an empty response.
        """
        # Strip .NS suffix if present (jugaad-data uses plain NSE symbols)
        clean_symbol = symbol.replace(".NS", "").upper()
        try:
            from jugaad_data.nse import NSELive
            n = NSELive()
            q = n.stock_quote(clean_symbol)
            price_info = q.get("priceInfo", {})
            current_price = float(price_info.get("lastPrice", 0))
            prev_close = float(price_info.get("previousClose", current_price))
            change = current_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
            intraday = price_info.get("intraDayHighLow", {})
            volume = (
                q.get("marketDeptOrderBook", {})
                .get("tradeInfo", {})
                .get("totalTradedVolume", 0)
            )
            return {
                "symbol": clean_symbol,
                "current_price": current_price,
                "open": float(price_info.get("open", 0)),
                "high": float(intraday.get("max", 0)),
                "low": float(intraday.get("min", 0)),
                "volume": int(volume),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            }
        except Exception as exc:
            logger.warning("Jugaad live quote failed for %s: %s", clean_symbol, exc)
            return {
                "symbol": clean_symbol,
                "current_price": 0.0,
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "volume": 0,
                "change": 0.0,
                "change_pct": 0.0,
                "error": f"Quote unavailable: {exc}",
            }

    def fetch_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch OHLCV history from NSE via jugaad-data.

        Converts the period string to a date range and renames columns
        to match the standard Open/High/Low/Close/Volume format.
        Only daily interval is supported; weekly/monthly are resampled.
        """
        clean_symbol = symbol.replace(".NS", "").upper()
        days = _PERIOD_DAYS.get(period, 365)
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        try:
            from jugaad_data.nse import stock_df
            raw = stock_df(
                symbol=clean_symbol,
                from_date=start_date,
                to_date=end_date,
                series="EQ",
            )
            if raw.empty:
                logger.warning("No data returned for %s", clean_symbol)
                return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

            # Rename jugaad-data columns to standard OHLCV format
            df = raw.rename(columns={
                "OPEN": "Open",
                "HIGH": "High",
                "LOW": "Low",
                "CLOSE": "Close",
                "VOLUME": "Volume",
                "DATE": "Date",
            })

            # NSE reports timestamps in UTC (18:30 UTC = midnight IST next day),
            # so .date() returns the previous calendar day. Convert to IST
            # and normalize to midnight so dates align with actual trading days.
            df["Date"] = (
                pd.to_datetime(df["Date"])
                .dt.tz_localize("UTC")
                .dt.tz_convert("Asia/Kolkata")
                .dt.normalize()
            )
            df = df.sort_values("Date")
            df = df.set_index("Date")
            df = df[["Open", "High", "Low", "Close", "Volume"]]

            # Resample for weekly/monthly intervals if requested
            if interval == "1wk":
                df = df.resample("W").agg({
                    "Open": "first", "High": "max",
                    "Low": "min", "Close": "last", "Volume": "sum",
                }).dropna()
            elif interval == "1mo":
                df = df.resample("ME").agg({
                    "Open": "first", "High": "max",
                    "Low": "min", "Close": "last", "Volume": "sum",
                }).dropna()

            logger.info("Fetched %d bars for %s via Jugaad Data", len(df), clean_symbol)
            return df
        except Exception as exc:
            logger.error("Jugaad history fetch failed for %s: %s", clean_symbol, exc)
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
