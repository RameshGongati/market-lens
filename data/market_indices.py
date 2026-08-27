"""Option-enabled index snapshots for the dashboard overview.

Supplies the supported NSE and BSE index-option underlyings with last price,
change, a short sparkline series and the 20-EMA relationship that drives the
MARKET BIAS card. Sector indices remain in ``data.market_heatmap``; keeping
them out of this module prevents the panel from becoming a second
sector-strength view.

Kept free of Streamlit so it stays testable; the caller owns caching.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict

import yfinance as yf

from data.sources.base import drop_incomplete_bars
from utils.logger import get_logger

logger = get_logger(__name__)

# NSE and BSE option underlyings, in dashboard display order. An empty Yahoo
# ticker means NSE can supply the current level/change but no reliable Yahoo
# history is available for the sparkline or 20 EMA.
INDEX_TICKERS: dict[str, str] = {
    "NIFTY 50": "^NSEI",
    "BANK NIFTY": "^NSEBANK",
    "FINNIFTY": "^CNXFIN",
    "MIDCPNIFTY": "",
    "NIFTY NEXT 50": "^NSMIDCP",
    "NIFTY INDIA FPI 150": "",
    "BSE SENSEX": "^BSESN",
    "BSE BANKEX": "BSE-BANK.BO",
}

INDEX_NSE_NAMES: dict[str, str] = {
    "NIFTY 50": "NIFTY 50",
    "BANK NIFTY": "NIFTY BANK",
    "FINNIFTY": "NIFTY FINANCIAL SERVICES",
    "MIDCPNIFTY": "NIFTY MIDCAP SELECT",
    "NIFTY NEXT 50": "NIFTY NEXT 50",
    "NIFTY INDIA FPI 150": "NIFTY INDIA FPI 150",
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
    if not ticker:
        return _empty(name, ticker)
    try:
        # auto_adjust=False for the same reason the equity sources use it: the
        # displayed level must match what the exchange quotes (see Gotcha 10).
        client = yf.Ticker(ticker)
        df = client.history(
            period=_PERIOD, interval=_INTERVAL, auto_adjust=False,
        )
        df = drop_incomplete_bars(df)
        if df is None or df.empty or "Close" not in df:
            return _empty(name, ticker)

        closes = df["Close"].astype(float)
        history_last = float(closes.iloc[-1])
        history_prev = float(closes.iloc[-2]) if len(closes) > 1 else history_last

        # Yahoo can expose a valid live quote while today's historical candle
        # is incomplete (SENSEX) or while it has only a current snapshot
        # (BANKEX). Prefer those quote fields for the headline movement, but
        # continue to calculate EMA/sparkline from completed daily candles.
        last, prev = history_last, history_prev
        try:
            fast_info = client.fast_info
            quote_last = float(fast_info.get("lastPrice") or 0.0)
            quote_prev = float(
                fast_info.get("previousClose")
                or fast_info.get("regularMarketPreviousClose")
                or 0.0
            )
            if quote_last > 0:
                last = quote_last
            if quote_prev > 0:
                prev = quote_prev
        except Exception as exc:
            logger.debug("Live index quote unavailable for %s: %s", ticker, exc)

        change = last - prev
        ema = (
            float(closes.ewm(span=_EMA_SPAN, adjust=False).mean().iloc[-1])
            if len(closes) >= _EMA_SPAN else None
        )

        return IndexSnapshot(
            name=name,
            ticker=ticker,
            last=last,
            change=change,
            change_pct=(change / prev * 100) if prev else 0.0,
            spark=[float(v) for v in closes.tail(_SPARKLINE_BARS)],
            ema20=ema,
            above_ema20=(last > ema if ema is not None else None),
            ok=True,
        )
    except Exception as exc:
        logger.warning("Index fetch failed for %s (%s): %s", name, ticker, exc)
        return _empty(name, ticker)


def fetch_nse_index_quotes() -> dict[str, dict]:
    """Current NSE levels for the option-enabled index underlyings."""
    try:
        from jugaad_data.nse import NSELive

        rows = NSELive().all_indices().get("data", [])
        wanted = set(INDEX_NSE_NAMES.values())
        return {
            str(row.get("index")): dict(row)
            for row in rows
            if row.get("index") in wanted
        }
    except Exception as exc:
        logger.warning("NSE option-index quote fetch failed: %s", exc)
        return {}


def _with_nse_quote(snapshot: IndexSnapshot, quote: dict | None) -> IndexSnapshot:
    """Overlay an NSE current quote while retaining Yahoo history context."""
    if not quote:
        return snapshot
    try:
        last = float(quote.get("last") or 0.0)
        if last <= 0:
            return snapshot
        change = float(quote.get("variation") or 0.0)
        pct = float(quote.get("percentChange") or 0.0)
        return IndexSnapshot(
            name=snapshot["name"],
            ticker=snapshot["ticker"],
            last=last,
            change=change,
            change_pct=pct,
            spark=snapshot["spark"],
            ema20=snapshot["ema20"],
            above_ema20=(last > snapshot["ema20"] if snapshot["ema20"] is not None else None),
            ok=True,
        )
    except (TypeError, ValueError):
        return snapshot


def fetch_all_indices() -> list[IndexSnapshot]:
    """Option-enabled index snapshots, fetched concurrently in display order."""
    items = list(INDEX_TICKERS.items())
    with ThreadPoolExecutor(max_workers=len(items) + 1) as executor:
        nse_future = executor.submit(fetch_nse_index_quotes)
        snapshots = list(executor.map(lambda item: fetch_index_snapshot(*item), items))
        nse_quotes = nse_future.result()
    return [
        _with_nse_quote(
            snapshot,
            nse_quotes.get(INDEX_NSE_NAMES[name]) if name in INDEX_NSE_NAMES else None,
        )
        for (name, _ticker), snapshot in zip(items, snapshots)
    ]


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
