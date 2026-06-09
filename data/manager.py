"""Data source manager — switches active source and delegates calls.

Stage C adds :func:`fetch_for_trading_type`, a timeframe-aware fetch helper
that routes to the right ``period``/``interval`` for each trading type and
gracefully falls back to Daily data when intraday bars are unavailable.  The
actual network call is injectable via the ``fetch_fn`` parameter so unit tests
stay fully offline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

import pandas as pd

from config.trading_config import get_timeframe
from data.sources.base import DataSource
from data.sources.nse_india import NSEIndiaSource
from data.sources.tradingview import TradingViewSource
from data.sources.upstox import UpstoxSource
from data.sources.yahoo_finance import YahooFinanceSource
from data.sources.zerodha import ZerodhaSource
from utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stage C — timeframe-aware fetch helpers
# ---------------------------------------------------------------------------

# A fetch result must have at least this many rows to be considered usable.
# Below this threshold an intraday result is retried against daily data.
_MIN_SUFFICIENT_ROWS: int = 20

# Bar-size strings that are classified as "intraday".  These are the values
# that can legitimately return empty/short results when a broker restricts
# historical intraday depth, so they merit a daily fallback.  Daily ("1d"),
# weekly ("1wk"), and monthly ("1mo") are NOT in this set — a short result
# there is a genuine data shortage, not a brokerage restriction.
_INTRADAY_INTERVALS: frozenset[str] = frozenset({
    "1m", "2m", "3m", "5m", "10m", "15m", "30m",
    "60m", "75m", "90m", "1h",
})

# Human-readable display labels for the most common bar intervals.
_INTERVAL_LABELS: dict[str, str] = {
    "1d":  "Daily",
    "1wk": "Weekly",
    "1mo": "Monthly",
    "1m":  "1m (intraday)",
    "2m":  "2m (intraday)",
    "3m":  "3m (intraday)",
    "5m":  "5m (intraday)",
    "10m": "10m (intraday)",
    "15m": "15m (intraday)",
    "30m": "30m (intraday)",
    "60m": "60m (intraday)",
    "75m": "75m (intraday)",
    "90m": "90m (intraday)",
    "1h":  "1h (intraday)",
}


class FetchMeta(TypedDict):
    """Metadata returned alongside the OHLCV DataFrame by
    :func:`fetch_for_trading_type`.

    ``fell_back`` is True only when an intraday primary fetch returned
    insufficient data and a daily retry was used instead.  Callers can use
    this flag to show a non-intrusive note in the UI.
    """

    requested_interval: str  # interval the trading type asked for
    used_interval: str        # interval actually used (may differ after fallback)
    used_period: str          # period actually used
    fell_back: bool           # True when intraday fallback to daily fired
    message: str              # human-readable note; empty when everything is OK


def interval_display_label(interval: str, fell_back: bool = False) -> str:
    """Return a human-readable display label for *interval*.

    Args:
        interval: A bar-size string (e.g. ``"1d"``, ``"15m"``, ``"1wk"``).
        fell_back: When ``True``, appends ``" (intraday unavailable)"`` to
            indicate the effective interval differs from the requested one.

    Returns:
        A short label suitable for captions and headers, e.g.
        ``"Daily"``, ``"15m (intraday)"``,
        ``"Daily (intraday unavailable)"``.

    Example::

        >>> interval_display_label("15m")
        '15m (intraday)'
        >>> interval_display_label("1d", fell_back=True)
        'Daily (intraday unavailable)'
    """
    label = _INTERVAL_LABELS.get(interval, interval)
    if fell_back:
        label += " (intraday unavailable)"
    return label


def _is_intraday(interval: str) -> bool:
    """Return ``True`` if *interval* represents an intraday bar size.

    Used to decide whether a short or empty result should trigger the
    daily-data fallback — daily and weekly intervals are not retried even if
    the result is short (that would be a genuine data shortage, not a
    brokerage-imposed intraday restriction).

    Example::

        >>> _is_intraday("15m")
        True
        >>> _is_intraday("1d")
        False
    """
    return interval in _INTRADAY_INTERVALS


def _is_insufficient(df: pd.DataFrame | None) -> bool:
    """Return ``True`` when *df* is ``None``, empty, or has fewer than
    :data:`_MIN_SUFFICIENT_ROWS` rows."""
    if df is None:
        return True
    return df.empty or len(df) < _MIN_SUFFICIENT_ROWS


def _safe_fetch(
    symbol: str,
    period: str,
    interval: str,
    fetch_fn: Callable[[str, str, str], pd.DataFrame],
) -> pd.DataFrame:
    """Call *fetch_fn(symbol, period, interval)* and swallow exceptions.

    Returns an empty DataFrame on any error so callers never need to guard
    against a raised exception from the network layer.
    """
    try:
        result = fetch_fn(symbol, period, interval)
        return result if result is not None else pd.DataFrame(
            columns=["Open", "High", "Low", "Close", "Volume"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Fetch error for %s (period=%s interval=%s): %s",
            symbol, period, interval, exc,
        )
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def _default_fetch_fn(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Default yfinance-backed fetch, used when no *fetch_fn* is supplied to
    :func:`fetch_for_trading_type`.

    The import is lazy (inside the function body) so that test modules that
    never call this path don't incur a yfinance import at collection time.
    """
    import yfinance as yf  # lazy — keeps test-suite startup fast when mocked

    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval)
        if df.empty:
            return df
        available = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[available]
    except Exception as exc:  # noqa: BLE001
        logger.error("_default_fetch_fn failed for %s: %s", symbol, exc)
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def fetch_for_trading_type(
    symbol: str,
    trading_type: str,
    *,
    fetch_fn: Callable[[str, str, str], pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame | None, FetchMeta]:
    """Fetch OHLCV data appropriate for *trading_type* with graceful intraday
    fallback.

    This is the Stage C replacement for the ad-hoc ``_PERIOD_MAP`` lookup
    that the dashboard previously used.  The ``fetch_fn`` parameter makes
    the network call injectable so unit tests stay fully offline — pass a
    fake lambda; the production caller passes ``ds_manager.get_history``.

    Args:
        symbol: Ticker symbol already formatted for the active data source
            (e.g. ``"RELIANCE.NS"`` for Yahoo Finance).
        trading_type: One of :data:`config.trading_config.TRADING_TYPES`.
        fetch_fn: ``(symbol, period, interval) -> pd.DataFrame`` callable.
            Defaults to a direct yfinance call when ``None``.

    Returns:
        ``(dataframe, meta)`` where:

          * *dataframe* is ``None`` when no usable data could be retrieved,
            otherwise a valid OHLCV DataFrame with at least
            :data:`_MIN_SUFFICIENT_ROWS` rows.
          * *meta* is a :class:`FetchMeta` dict describing what was
            actually fetched and whether a fallback occurred — callers should
            display ``meta["message"]`` to the user when ``fell_back`` is
            ``True``.

    Fallback logic:
        1. Look up ``period`` / ``interval`` via
           :func:`config.trading_config.get_timeframe`.
        2. Attempt the primary fetch.
        3. If the interval is intraday **and** the result is insufficient
           (empty or < :data:`_MIN_SUFFICIENT_ROWS` rows), retry with
           ``period="1y", interval="1d"`` and set ``fell_back=True``.
        4. If the result is still insufficient, return ``(None, meta)``
           with a descriptive *message* — never raise to the caller.
    """
    if fetch_fn is None:
        fetch_fn = _default_fetch_fn

    tf = get_timeframe(trading_type)
    period: str = tf["period"]
    interval: str = tf["interval"]

    meta: FetchMeta = {
        "requested_interval": interval,
        "used_interval": interval,
        "used_period": period,
        "fell_back": False,
        "message": "",
    }

    df = _safe_fetch(symbol, period, interval, fetch_fn)

    # Intraday fallback — only for intraday intervals, where data availability
    # is commonly restricted by data providers.  Daily / weekly shortfalls are
    # not retried: a short result there is a real data gap, not a restriction.
    if _is_intraday(interval) and _is_insufficient(df):
        fallback_period, fallback_interval = "1y", "1d"
        logger.info(
            "Intraday fallback for %s: %s/%s returned %d rows — retrying %s/%s",
            symbol, period, interval,
            0 if df is None or df.empty else len(df),
            fallback_period, fallback_interval,
        )
        df = _safe_fetch(symbol, fallback_period, fallback_interval, fetch_fn)
        meta["fell_back"] = True
        meta["used_interval"] = fallback_interval
        meta["used_period"] = fallback_period
        meta["message"] = (
            f"Intraday data unavailable for {symbol}; showing Daily instead."
        )

    if _is_insufficient(df):
        logger.warning(
            "No usable data for %s (requested %s/%s)", symbol, period, interval
        )
        meta["message"] = meta["message"] or f"No data available for {symbol}."
        return None, meta

    return df, meta

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
