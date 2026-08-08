"""Index snapshots for the dashboard's market-overview strip.

Supplies the two headline indices (NIFTY 50, BANK NIFTY) with last price,
change, a short sparkline series and the 20-EMA relationship that drives the
MARKET BIAS card.

Deliberately narrow. Sector indices are NOT fetched here: Yahoo covers
``^NSEI`` and ``^NSEBANK`` reliably but its coverage of the sector indices
(Metal, Auto, PSU Bank, Realty) is patchy, and the sector panels belong to
Phase 7 anyway. Rather than half-fill them from an unreliable source, the
dashboard draws those boxes as explicit placeholders.

Kept free of Streamlit so it stays testable; the caller owns caching.
"""

from __future__ import annotations

from typing import TypedDict

import yfinance as yf

from data.sources.base import drop_incomplete_bars
from utils.logger import get_logger

logger = get_logger(__name__)

# Yahoo tickers for the two indices the design shows.
INDEX_TICKERS: dict[str, str] = {
    "NIFTY 50": "^NSEI",
    "BANK NIFTY": "^NSEBANK",
}

# Bars pulled for the sparkline and the EMA. 20-EMA needs at least 20 closes;
# 3 months of daily bars gives it room to settle without a large fetch.
_PERIOD = "3mo"
_INTERVAL = "1d"
_EMA_SPAN = 20
_SPARKLINE_BARS = 30


class IndexSnapshot(TypedDict):
    """One index's current state."""
    name: str
    ticker: str
    last: float
    change: float
    change_pct: float
    spark: list[float]
    ema20: float | None
    above_ema20: bool | None
    ok: bool


def _empty(name: str, ticker: str) -> IndexSnapshot:
    return IndexSnapshot(
        name=name, ticker=ticker, last=0.0, change=0.0, change_pct=0.0,
        spark=[], ema20=None, above_ema20=None, ok=False,
    )


def fetch_index_snapshot(name: str, ticker: str) -> IndexSnapshot:
    """Fetch one index. Never raises — a dead index must not break the page."""
    try:
        # auto_adjust=False for the same reason the equity sources use it: the
        # displayed level must match what the exchange quotes (see Gotcha 10).
        df = yf.Ticker(ticker).history(
            period=_PERIOD, interval=_INTERVAL, auto_adjust=False,
        )
        df = drop_incomplete_bars(df)
        if df is None or df.empty or "Close" not in df:
            return _empty(name, ticker)

        closes = df["Close"].astype(float)
        last = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) > 1 else last
        change = last - prev
        ema = float(closes.ewm(span=_EMA_SPAN, adjust=False).mean().iloc[-1])

        return IndexSnapshot(
            name=name,
            ticker=ticker,
            last=last,
            change=change,
            change_pct=(change / prev * 100) if prev else 0.0,
            spark=[float(v) for v in closes.tail(_SPARKLINE_BARS)],
            ema20=ema,
            above_ema20=last > ema,
            ok=True,
        )
    except Exception as exc:
        logger.warning("Index fetch failed for %s (%s): %s", name, ticker, exc)
        return _empty(name, ticker)


def fetch_all_indices() -> list[IndexSnapshot]:
    """Snapshots for every index in :data:`INDEX_TICKERS`, in display order."""
    return [fetch_index_snapshot(n, t) for n, t in INDEX_TICKERS.items()]


def market_bias(snapshots: list[IndexSnapshot]) -> tuple[str, str]:
    """Derive the MARKET BIAS card from NIFTY 50's position against its 20 EMA.

    Returns ``(bias, reason)``. ``bias`` is BULLISH / BEARISH / UNKNOWN — the
    last when the index could not be fetched, so the card says so rather than
    defaulting to a direction nobody computed.
    """
    nifty = next((s for s in snapshots if s["name"] == "NIFTY 50"), None)
    if not nifty or not nifty["ok"] or nifty["above_ema20"] is None:
        return "UNKNOWN", "Index data unavailable"
    if nifty["above_ema20"]:
        return "BULLISH", "Nifty above 20 EMA"
    return "BEARISH", "Nifty below 20 EMA"
