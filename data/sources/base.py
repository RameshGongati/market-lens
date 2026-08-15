"""Abstract base class for all data sources."""

from abc import ABC, abstractmethod

import pandas as pd

from utils.logger import get_logger

logger = get_logger(__name__)

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


# How far back to look for holes. Recent sessions are what a chart is read
# for; a gap further back is history, and checking five years would turn
# every fetch into hundreds of calendar lookups.
_GAP_LOOKBACK_SESSIONS = 10


def fill_missing_sessions(
    df: pd.DataFrame,
    symbol: str,
    lookback: int = _GAP_LOOKBACK_SESSIONS,
) -> pd.DataFrame:
    """Rebuild sessions absent from the middle of a daily frame, from NSE.

    Yahoo omits whole trading days for individual symbols and then carries on
    — BAJAJHLDNG 2026-07-31 is absent while 2026-07-30 and 2026-08-03 are
    both present. That is a HOLE, not a short tail: any check that looks at
    the newest bar alone sees nothing wrong, because the newest bar is
    current. Refetching from the same source does not help either — the
    session is missing from every window Yahoo serves.

    NSE's bhavcopy has it. ``_repair_last_bar`` already reaches for the same
    file to complete a bar with a missing close; this reaches for it to
    supply a bar that is missing entirely, which is the same trade — an
    authoritative price beats a hole.

    Cheap in bulk: the bhavcopy is one file per DATE covering every symbol
    and is cached on disk, so the first symbol of a scan pays the download
    and the rest are dictionary lookups.

    Scoped to the last *lookback* sessions. A gap further back is history
    rather than something the user is about to trade, and walking five years
    of calendar would turn every fetch into hundreds of lookups.

    **Volume is set to 0** on a rebuilt bar — the bhavcopy parse keeps only
    OHLC. Nothing in zone detection reads volume (see
    ``drop_incomplete_bars``, which deliberately ignores it), but a volume
    chart will show a rebuilt session as an empty bar.

    Args:
        df: Daily OHLCV frame, DatetimeIndex.
        symbol: Ticker, with or without the ``.NS`` suffix.
        lookback: How many recent sessions to check.

    Returns:
        ``df`` with any recoverable missing sessions inserted, or unchanged.
    """
    from data.nse_bhavcopy import fetch_eod_ohlc
    from utils.market_hours import recent_trading_sessions

    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    try:
        expected = recent_trading_sessions(lookback)
        have = {ts.date() for ts in df.index}
        first = df.index[0].date()
        # Only sessions inside the frame's own span — a date before the
        # first bar is a listing boundary, not a gap.
        missing = [d for d in expected if d not in have and d >= first]
        if not missing:
            return df

        clean = symbol.replace(".NS", "").replace(".BO", "").upper()
        tz = df.index.tz
        rows, index = [], []
        for day in missing:
            bar = fetch_eod_ohlc(clean, day)
            if not bar:
                continue
            rows.append({
                "Open": bar["open"], "High": bar["high"],
                "Low": bar["low"], "Close": bar["close"], "Volume": 0,
            })
            stamp = pd.Timestamp(day)
            index.append(stamp.tz_localize(tz) if tz is not None else stamp)

        if not rows:
            logger.info(
                "%s: %d session(s) missing (%s) and NSE had none of them",
                clean, len(missing), ", ".join(str(d) for d in missing),
            )
            return df

        logger.info(
            "%s: rebuilt %d missing session(s) from NSE bhavcopy: %s",
            clean, len(rows), ", ".join(str(i.date()) for i in index),
        )
        patch = pd.DataFrame(rows, index=pd.DatetimeIndex(index))
        return pd.concat([df, patch]).sort_index()
    except Exception as exc:
        # A fetch that already succeeded must survive a failed repair.
        logger.warning("Missing-session fill failed for %s: %s", symbol, exc)
        return df


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
