"""Yahoo Finance data source — uses yfinance, no credentials required."""

import pandas as pd
import yfinance as yf

from data.nse_bhavcopy import fetch_eod_ohlc
from data.sources.base import (
    DataSource,
    drop_incomplete_bars,
    fill_missing_sessions,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def _intraday_close(symbol: str, bar_date) -> float | None:
    """Last intraday close for *symbol* on *bar_date*, or None.

    Second line of repair when NSE's bhavcopy is unavailable. Deliberately
    hourly rather than minute data: it covers enough days to reach the
    requested session while staying a small response.
    """
    try:
        intraday = yf.Ticker(symbol).history(
            period="5d", interval="1h", auto_adjust=False,
        )
        if intraday.empty or "Close" not in intraday.columns:
            return None
        same_day = intraday[intraday.index.date == bar_date]
        same_day = same_day[same_day["Close"].notna()]
        if same_day.empty:
            return None
        return float(same_day["Close"].iloc[-1])
    except Exception as exc:  # noqa: BLE001 — a failed repair must not fail the fetch
        logger.warning("Intraday fallback failed for %s: %s", symbol, exc)
        return None


def _repair_last_bar(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Fill a missing close on the most recent daily bar from NSE's bhavcopy.

    Yahoo occasionally returns a final bar with open, high, low and volume
    but no close — the session's candle cannot be drawn and, left alone, the
    row would be read downstream as a boring doji. NSE publishes the
    authoritative close for that date, so the bar is completed rather than
    discarded.

    Only the LAST row is considered — this repairs a bar that is PRESENT but
    unfinished. A session Yahoo omitted entirely is a different failure and is
    handled by ``fill_missing_sessions``, which identifies the absent dates
    from the NSE trading calendar and rebuilds them from the same bhavcopy.

    Returns the frame unchanged when the close is already present, the index
    is not dated, or the bhavcopy has nothing for that symbol and date.
    """
    if df.empty or "Close" not in df.columns:
        return df
    if not pd.isna(df["Close"].iloc[-1]):
        return df
    try:
        bar_date = df.index[-1].date()
    except AttributeError:
        return df

    # Yahoo's own intraday feed is tried FIRST. It comes from a different
    # pipeline than the daily bar and is routinely complete while the daily
    # one is still unsettled — BAJAJHLDNG's 15:15 hourly close matched NSE's
    # official close exactly. It costs ~200ms against ~5s for the bhavcopy,
    # and NSE rate-limits, so a bhavcopy-first order made every chart open
    # wait seconds and sometimes pay that cost only to fail.
    #
    # The bhavcopy stays as the backup: Yahoo keeps only ~60 days of hourly
    # data, so intraday cannot answer for an older session, and NSE remains
    # the authority when the two ever disagree.
    close = _intraday_close(symbol, bar_date)
    if close is not None:
        df = df.copy()
        df.iloc[-1, df.columns.get_loc("Close")] = close
        logger.info(
            "Completed %s bar for %s from Yahoo intraday (close %.2f)",
            bar_date, symbol, close,
        )
        return df

    eod = fetch_eod_ohlc(symbol, bar_date)
    if not eod:
        logger.warning(
            "%s has no close for %s from intraday or NSE — dropping the bar",
            symbol, bar_date,
        )
        return df

    df = df.copy()
    # Only fill what is actually missing: the values Yahoo did return are
    # the ones the rest of the history is consistent with.
    for col, key in (("Open", "open"), ("High", "high"), ("Low", "low"), ("Close", "close")):
        if col in df.columns and pd.isna(df[col].iloc[-1]):
            df.iloc[-1, df.columns.get_loc(col)] = eod[key]
    logger.info(
        "Completed %s bar for %s from NSE bhavcopy (close %.2f)",
        bar_date, symbol, eod["close"],
    )
    return df


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
            # fast_info supplies every field returned below. A
            # ticker.history(period="2d") call used to sit here whose result
            # was assigned and never read — a second HTTP round trip per
            # quote, measured at 85ms, or 4.2s across a 50-stock scan.
            # fast_info reports live traded values and is not dividend
            # adjusted, so the unadjusted-price guarantee that history needed
            # auto_adjust=False for (see the module docstring and
            # manager._default_fetch_fn) does not apply to this path.
            info = ticker.fast_info

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
            # Use unadjusted prices so chart levels and zone boundaries
            # match actual traded prices (consistent with TradingView,
            # TraderTiger, Zerodha Kite). Adjusted prices shift all
            # pre-dividend candles down by the dividend amount, misplacing
            # demand/supply zones relative to current price.
            df = ticker.history(
                period=period, interval=interval, auto_adjust=False,
            )
            if df.empty:
                logger.warning("No history returned for %s", symbol)
            # Drop the "Adj Close" column added when auto_adjust=False
            df = df[["Open", "High", "Low", "Close", "Volume"]]
            if interval not in ("1wk", "1mo"):
                df = df[df["Volume"].fillna(0) > 0]
            # Repair before dropping: a bar Yahoo left unfinished can often
            # be completed from NSE's own end-of-day file, and completing it
            # is better than losing the most recent session.
            if interval == "1d":
                df = _repair_last_bar(df, symbol)
            df = drop_incomplete_bars(df)

            # _repair_last_bar fixes a bar that is PRESENT but unfinished.
            # Yahoo also omits whole sessions for individual symbols (Gotcha
            # 9) — BAJAJHLDNG 2026-07-31 is absent while 07-30 and 08-03 are
            # both there. Refetching Yahoo cannot help: the session is
            # missing from every window it serves. NSE has it, so the bar is
            # rebuilt from the bhavcopy.
            if interval == "1d":
                df = fill_missing_sessions(df, symbol)
            return df
        except Exception as exc:
            logger.error("YahooFinance fetch_history failed for %s: %s", symbol, exc)
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
