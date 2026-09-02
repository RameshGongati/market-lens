"""Market heatmap group definitions and quote snapshots.

The dashboard needs a compact market map, while the full heatmap page needs
drill-down stock tiles.  This module owns the market/index/sector universe and
the data shaping so the Streamlit pages stay focused on rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import math
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from typing import Any

import pandas as pd

from utils.helpers import get_company_name

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = Path(__file__).parent / "predefined_watchlists.json"
_YAHOO_BATCH_SIZE = 180

_GROUP_TICKERS: dict[str, str] = {
    "nifty": "^NSEI",
    "nifty100": "^CNX100",
    "nifty200": "^CNX200",
    "nifty500": "^CRSLDX",
    "banks": "^NSEBANK",
    "financials": "^CNXFIN",
    "it": "^CNXIT",
    "auto": "^CNXAUTO",
    "pharma": "^CNXPHARMA",
    "fmcg": "^CNXFMCG",
    "energy": "^CNXENERGY",
    "metals": "^CNXMETAL",
    "consdurbl": "^CNXCONSUM",
    "realty": "^CNXREALTY",
    "psubank": "^CNXPSUBANK",
    "media": "^CNXMEDIA",
}

_MANUAL_GROUP_SYMBOLS: dict[str, list[str]] = {
    "financials": [
        "ABCAPITAL", "BAJFINANCE", "BAJAJFINSV", "BSE", "CAMS", "CDSL",
        "CHOLAFIN", "HDFCAMC", "ICICIGI", "ICICIPRULI", "JIOFIN",
        "KFINTECH", "LICHSGFIN", "LICI", "LTF", "MANAPPURAM", "MCX",
        "MFSL", "MOTILALOFS", "MUTHOOTFIN", "NAM-INDIA", "PFC",
        "PNBHOUSING", "POLICYBZR", "RECLTD", "SBICARD", "SHRIRAMFIN",
    ],
    "oilgas": [
        "AEGISLOG", "ATGL", "BPCL", "CASTROLIND", "GAIL", "HINDPETRO",
        "IGL", "IOC", "MGL", "OIL", "ONGC", "PETRONET", "RELIANCE",
    ],
    "consdurbl": [
        "AMBER", "BLUESTARCO", "CROMPTON", "DIXON", "HAVELLS",
        "KALYANKJIL", "PGEL", "POLYCAB", "TITAN", "VOLTAS",
    ],
    "realty": [
        "DLF", "GODREJPROP", "LODHA", "NBCC", "OBEROIRLTY",
        "PHOENIXLTD", "PRESTIGE",
    ],
    "psubank": [
        "BANKBARODA", "BANKINDIA", "CANBK", "INDIANB", "PNB", "SBIN",
        "UNIONBANK",
    ],
    "media": [
        "NETWORK18", "PVRINOX", "RADIOCITY", "SAREGAMA", "SUNTV",
        "TATACOMM", "ZEEL",
    ],
    "healthcare": [
        "APOLLOHOSP", "CIPLA", "DIVISLAB", "DRREDDY", "FORTIS",
        "GLENMARK", "LUPIN", "MANKIND", "MAXHEALTH", "SUNPHARMA",
        "TORNTPHARM", "ZYDUSLIFE",
    ],
}


@dataclass(frozen=True)
class HeatmapGroup:
    """One top-level market heatmap tile."""

    id: str
    label: str
    kind: str
    watchlists: tuple[str, ...] = ()
    manual_symbols: tuple[str, ...] = ()
    note: str = ""

    @property
    def ticker(self) -> str:
        return _GROUP_TICKERS.get(self.id, "")


GROUPS: tuple[HeatmapGroup, ...] = (
    HeatmapGroup("nifty", "NIFTY", "index", ("Nifty 50",)),
    HeatmapGroup("nifty_next_50", "NIFTNXT50", "index", ("Nifty Next 50",)),
    HeatmapGroup("nifty100", "NIFTY100", "index", ("Nifty 50", "Nifty Next 50")),
    HeatmapGroup("nifty200", "NIFTY200", "index", ("F&O Stocks",), note="F&O coverage"),
    HeatmapGroup("nifty500", "NIFTY500", "index", ("F&O Stocks",), note="F&O coverage"),
    HeatmapGroup("banks", "Banks", "sector", ("Nifty Bank",)),
    HeatmapGroup("financials", "Financials", "sector", manual_symbols=tuple(_MANUAL_GROUP_SYMBOLS["financials"])),
    HeatmapGroup("it", "IT", "sector", ("Nifty IT",)),
    HeatmapGroup("auto", "Auto", "sector", ("Nifty Auto",)),
    HeatmapGroup("pharma", "Pharma", "sector", ("Nifty Pharma",)),
    HeatmapGroup("fmcg", "FMCG", "sector", ("Nifty FMCG",)),
    HeatmapGroup("energy", "Energy", "sector", ("Nifty Energy",)),
    HeatmapGroup("oilgas", "OILANDGAS", "sector", manual_symbols=tuple(_MANUAL_GROUP_SYMBOLS["oilgas"])),
    HeatmapGroup("metals", "Metals", "sector", ("Nifty Metal",)),
    HeatmapGroup("consdurbl", "CONSRDURBL", "sector", manual_symbols=tuple(_MANUAL_GROUP_SYMBOLS["consdurbl"])),
    HeatmapGroup("realty", "Realty", "sector", manual_symbols=tuple(_MANUAL_GROUP_SYMBOLS["realty"])),
    HeatmapGroup("psubank", "PSU Bank", "sector", manual_symbols=tuple(_MANUAL_GROUP_SYMBOLS["psubank"])),
    HeatmapGroup("media", "Media", "sector", manual_symbols=tuple(_MANUAL_GROUP_SYMBOLS["media"])),
    HeatmapGroup("healthcare", "Healthcare", "sector", manual_symbols=tuple(_MANUAL_GROUP_SYMBOLS["healthcare"])),
)

QuoteMap = dict[str, dict[str, Any]]
QuoteFetcher = Callable[[Sequence[str]], QuoteMap]


def group_by_id(group_id: str) -> HeatmapGroup | None:
    """Return a configured group by id."""
    return next((g for g in GROUPS if g.id == group_id), None)


def predefined_watchlists() -> list[dict[str, Any]]:
    """Return shipped watchlist definitions used by the heatmap universes."""
    try:
        raw = json.loads(_WATCHLIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _watchlists_by_name() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for item in predefined_watchlists():
        name = str(item.get("name", ""))
        symbols = item.get("symbols") or []
        if name and isinstance(symbols, list):
            result[name] = [str(s).upper() for s in symbols if str(s).strip()]
    return result


def _dedupe(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for symbol in symbols:
        clean = symbol.replace(".NS", "").strip().upper()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def symbols_for_group(group: HeatmapGroup | str) -> list[str]:
    """Return stock symbols backing a group tile."""
    spec = group_by_id(group) if isinstance(group, str) else group
    if spec is None:
        return []
    symbols: list[str] = list(spec.manual_symbols)
    watchlists = _watchlists_by_name()
    for name in spec.watchlists:
        symbols.extend(watchlists.get(name, []))
    return _dedupe(symbols)


def symbols_for_watchlist(name: str) -> list[str]:
    """Return stock symbols for one shipped predefined watchlist."""
    return _dedupe(_watchlists_by_name().get(name, []))


def _format_yahoo_symbol(symbol: str) -> str:
    if symbol.startswith("^"):
        return symbol
    if symbol.endswith(".NS") or symbol.endswith(".BO"):
        return symbol
    return f"{symbol}.NS"


def _clean_yahoo_symbol(symbol: str) -> str:
    return symbol.replace(".NS", "").replace(".BO", "").upper()


def _empty_quote(symbol: str, error: str = "") -> dict[str, Any]:
    return {
        "symbol": _clean_yahoo_symbol(symbol),
        "price": 0.0,
        "change": 0.0,
        "change_pct": 0.0,
        "volume": 0,
        "ok": False,
        "error": error,
    }


def _stale_yahoo_daily_symbols(
    session_dates: dict[str, tuple[pd.Timestamp, pd.Timestamp]],
) -> set[str]:
    """Find symbols whose Yahoo daily bars are behind the market majority.

    Yahoo's multi-ticker daily response occasionally omits one symbol's prior
    session while returning the current session. Calculating change from the
    next available bar then turns a one-day move into a multi-day move. The
    dominant dates across the same batch provide a cheap, reliable way to flag
    only those outliers for an individual quote refresh.
    """
    if len(session_dates) < 2:
        return set()

    latest_counts = Counter(last for last, _prev in session_dates.values())
    market_latest, _ = latest_counts.most_common(1)[0]
    matching_latest = {
        symbol: prev
        for symbol, (last, prev) in session_dates.items()
        if last == market_latest
    }
    if len(matching_latest) < 2:
        return set()

    previous_counts = Counter(matching_latest.values())
    market_previous, _ = previous_counts.most_common(1)[0]
    return {
        symbol
        for symbol, (last, prev) in session_dates.items()
        if last < market_latest or (last == market_latest and prev < market_previous)
    }


def _fast_info_value(info: Any, key: str) -> Any:
    """Read one field from yfinance's FastInfo (or a plain dict in tests).

    FastInfo answers ``.get`` for camelCase keys but returns None — without
    raising — for the snake_case names used here, while exposing the same
    fields as snake_case ATTRIBUTES. So a None from ``.get`` must still fall
    through to ``getattr``, not only an exception.
    """
    if isinstance(info, dict):
        return info.get(key)
    value = None
    try:
        value = info.get(key)
    except Exception:
        value = None
    if value is None:
        value = getattr(info, key, None)
    return value


def _mover_yahoo_daily_symbols(quotes: QuoteMap, *, limit_per_side: int = 8) -> set[str]:
    """Find batch-calculated top movers worth validating against fast quotes."""
    gainers: list[tuple[float, str]] = []
    losers: list[tuple[float, str]] = []
    for symbol, quote in quotes.items():
        if not quote.get("ok"):
            continue
        try:
            change_pct = float(quote.get("change_pct", 0.0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(change_pct) or change_pct == 0:
            continue
        if change_pct > 0:
            gainers.append((change_pct, symbol))
        else:
            losers.append((change_pct, symbol))

    gainers.sort(reverse=True)
    losers.sort()
    return {
        symbol
        for _change_pct, symbol in gainers[:limit_per_side] + losers[:limit_per_side]
    }


def _quote_from_price_prev(
    symbol: str,
    price: float,
    prev_close: float,
    *,
    volume: int = 0,
    source: str = "",
) -> dict[str, Any] | None:
    if not (
        math.isfinite(price)
        and price > 0
        and math.isfinite(prev_close)
        and prev_close > 0
    ):
        return None
    change = price - prev_close
    return {
        "symbol": _clean_yahoo_symbol(symbol),
        "price": price,
        "change": change,
        "change_pct": change / prev_close * 100.0,
        "volume": volume,
        "ok": True,
        "source": source,
        "error": "",
    }


def _fetch_yahoo_fast_quote(yf: Any, symbol: str, ticker: str) -> dict[str, Any] | None:
    try:
        info = yf.Ticker(ticker).fast_info
        price = float(
            _fast_info_value(info, "last_price")
            or _fast_info_value(info, "regular_market_price")
            or 0
        )
        prev_close = float(
            _fast_info_value(info, "previous_close")
            or _fast_info_value(info, "regular_market_previous_close")
            or 0
        )
        volume = int(float(_fast_info_value(info, "last_volume") or _fast_info_value(info, "volume") or 0))
        return _quote_from_price_prev(
            symbol,
            price,
            prev_close,
            volume=volume,
            source="yahoo_fast",
        )
    except Exception as exc:
        logger.debug("Yahoo fast heatmap quote unavailable for %s: %s", symbol, exc)
        return None


def _fetch_yahoo_intraday_quote(
    yf: Any,
    symbol: str,
    ticker: str,
    prev_close: float,
    *,
    volume: int = 0,
) -> dict[str, Any] | None:
    for interval in ("1m", "5m", "15m"):
        try:
            raw = yf.download(
                ticker,
                period="1d",
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
            if raw.empty:
                continue
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(1):
                    continue
                frame = raw.xs(ticker, level=1, axis=1)
            else:
                frame = raw
            closes = frame["Close"].dropna().astype(float)
            if closes.empty:
                continue
            volumes = frame["Volume"].dropna().astype(float) if "Volume" in frame else pd.Series(dtype=float)
            intraday_volume = int(volumes.sum()) if not volumes.empty else volume
            return _quote_from_price_prev(
                symbol,
                float(closes.iloc[-1]),
                prev_close,
                volume=intraday_volume or volume,
                source="yahoo_intraday",
            )
        except Exception as exc:
            logger.debug("Yahoo %s intraday repair failed for %s: %s", interval, symbol, exc)
    return None


def _create_nse_quote_session():
    """A warmed jugaad NSELive client for NSE equity quotes, or None.

    A plain requests session gets a 403 from NSE's edge on /api/quote-equity
    (verified); jugaad's NSELive carries the header/cookie dance NSE accepts
    and is already this codebase's working NSE channel (options chain, index
    quotes). Returning None (offline, blocked, library missing) simply means
    the repair falls back to Yahoo's fast quote.
    """
    try:
        from jugaad_data.nse import NSELive

        return NSELive()
    except Exception as exc:  # noqa: BLE001 — repair is best-effort
        logger.debug("NSE quote session unavailable: %s", exc)
        return None


def _fetch_nse_equity_quote(symbol: str, session) -> dict[str, Any] | None:
    """NSE's own live quote for one equity, normalized, or None."""
    try:
        data = session.stock_quote(symbol)
        price_info = data.get("priceInfo") or {}
        price = float(price_info.get("lastPrice") or 0)
        prev_close = float(price_info.get("previousClose") or 0)
        traded = (data.get("securityWiseDP") or {}).get("quantityTraded") or 0
        return _quote_from_price_prev(
            symbol,
            price,
            prev_close,
            volume=int(float(traded)),
            source="nse",
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("NSE quote unavailable for %s: %s", symbol, exc)
        return None


def _repair_yahoo_quotes(
    quotes: QuoteMap,
    ticker_by_symbol: dict[str, str],
    repair_reasons: dict[str, str],
    yf: Any,
) -> None:
    """Re-check flagged quotes against live sources, updating in place.

    NSE's own quote is preferred — it is the exchange the batch is trying to
    describe, the same authority choice market_indices.py makes for index
    levels. Yahoo's fast quote is the fallback. A symbol whose repair returns
    nothing keeps its original quote: a possibly-stale number the log calls
    out beats a fabricated one.
    """
    if not repair_reasons:
        return
    session = _create_nse_quote_session()
    # NSE's per-equity quote endpoint is edge-blocked in some environments
    # (verified: /api/quote-equity 403s even through jugaad's session while
    # the index/chain endpoints work). Two consecutive failures mean the
    # session is blocked, not that two symbols are odd — stop paying a
    # round trip per symbol and let Yahoo's fast quote carry the repair.
    nse_failures = 0
    for plain, reason in repair_reasons.items():
        quote = None
        if session is not None and nse_failures < 2:
            quote = _fetch_nse_equity_quote(plain, session)
            nse_failures = 0 if quote is not None else nse_failures + 1
        if quote is None:
            ticker = ticker_by_symbol.get(plain) or _format_yahoo_symbol(plain)
            quote = _fetch_yahoo_fast_quote(yf, plain, ticker)
        if quote is None:
            logger.warning("Quote repair (%s) found no live source for %s",
                           reason, plain)
            continue
        old_pct = float(quotes.get(plain, {}).get("change_pct", 0.0) or 0.0)
        quotes.setdefault(plain, _empty_quote(plain)).update(quote)
        logger.info(
            "Repaired quote (%s) for %s via %s (%+.2f%% -> %+.2f%%)",
            reason, plain, quote.get("source", "?"),
            old_pct, float(quote.get("change_pct", 0.0) or 0.0),
        )


def fetch_yahoo_changes(symbols: Sequence[str]) -> QuoteMap:
    """Fetch latest close-to-close changes from Yahoo in one batch."""
    unique = _dedupe(symbols)
    if not unique:
        return {}
    if len(unique) > _YAHOO_BATCH_SIZE:
        out: QuoteMap = {}
        for start in range(0, len(unique), _YAHOO_BATCH_SIZE):
            out.update(fetch_yahoo_changes(unique[start:start + _YAHOO_BATCH_SIZE]))
        return out
    tickers = [_format_yahoo_symbol(s) for s in unique]
    try:
        import yfinance as yf

        raw = yf.download(
            tickers,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=False,
            progress=False,
            threads=True,
        )
    except Exception as exc:
        logger.warning("Heatmap Yahoo batch failed: %s", exc)
        return {s: _empty_quote(s, str(exc)) for s in unique}

    out: QuoteMap = {}
    session_dates: dict[str, tuple[pd.Timestamp, pd.Timestamp]] = {}
    incomplete_daily: dict[str, tuple[float, int]] = {}
    ticker_by_symbol = dict(zip(unique, tickers))
    for plain, ticker in zip(unique, tickers):
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                if ticker not in raw.columns.get_level_values(0):
                    out[plain] = _empty_quote(plain, "No data")
                    continue
                frame = raw[ticker]
            else:
                frame = raw
            close_series = frame["Close"].astype(float)
            closes = close_series.dropna()
            volumes = frame["Volume"].dropna().astype(float) if "Volume" in frame else pd.Series(dtype=float)
            latest_volume = int(volumes.iloc[-1]) if not volumes.empty else 0

            if not closes.empty:
                latest_date = pd.Timestamp(close_series.index[-1]).normalize()
                last_valid_date = pd.Timestamp(closes.index[-1]).normalize()
                if latest_date > last_valid_date:
                    prev_close = float(closes.iloc[-1])
                    out[plain] = _empty_quote(plain, "Latest daily close unavailable")
                    out[plain]["volume"] = latest_volume
                    incomplete_daily[plain] = (prev_close, latest_volume)
                    continue

            if len(closes) < 2:
                out[plain] = _empty_quote(plain, "Insufficient data")
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            session_dates[plain] = (
                pd.Timestamp(closes.index[-1]).normalize(),
                pd.Timestamp(closes.index[-2]).normalize(),
            )
            change = last - prev
            out[plain] = {
                "symbol": plain,
                "price": last,
                "change": change,
                "change_pct": (change / prev * 100.0) if prev else 0.0,
                "volume": latest_volume,
                "ok": math.isfinite(last) and last > 0,
                "error": "",
            }
        except Exception as exc:
            out[plain] = _empty_quote(plain, str(exc))

    for plain, (prev_close, volume) in incomplete_daily.items():
        quote = _fetch_yahoo_intraday_quote(
            yf,
            plain,
            ticker_by_symbol[plain],
            prev_close,
            volume=volume,
        )
        if quote is not None:
            out[plain].update(quote)
            logger.info(
                "Completed missing Yahoo daily close for %s from intraday quote (%+.2f%%)",
                plain,
                float(quote.get("change_pct", 0.0) or 0.0),
            )

    # Repair per-symbol holes in Yahoo's batch daily history through the same
    # NSE-first path the mover validation uses.
    repair_reasons = {
        plain: "stale daily row"
        for plain in _stale_yahoo_daily_symbols(session_dates)
    }
    _repair_yahoo_quotes(out, ticker_by_symbol, repair_reasons, yf)
    return out


def fetch_source_quotes(
    symbols: Sequence[str],
    source_name: str,
    credentials: dict[str, str] | None = None,
) -> QuoteMap:
    """Fetch stock quote changes from the selected app data source."""
    if source_name == "Yahoo Finance":
        return fetch_yahoo_changes(symbols)

    try:
        from data.manager import build_source_manager

        manager = build_source_manager(source_name, credentials or {})
    except Exception as exc:
        logger.warning("Heatmap source init failed for %s: %s", source_name, exc)
        return {s: _empty_quote(s, str(exc)) for s in _dedupe(symbols)}

    out: QuoteMap = {}
    for symbol in _dedupe(symbols):
        try:
            quote_symbol = symbol if source_name != "TradingView" else f"NSE:{symbol}"
            quote = manager.get_quote(quote_symbol)
            price = float(quote.get("current_price") or 0.0)
            change = float(quote.get("change") or 0.0)
            change_pct = float(quote.get("change_pct") or 0.0)
            out[symbol] = {
                "symbol": symbol,
                "price": price,
                "change": change,
                "change_pct": change_pct,
                "volume": int(float(quote.get("volume") or 0)),
                "ok": price > 0 and math.isfinite(price),
                "error": quote.get("error", ""),
            }
        except Exception as exc:
            out[symbol] = _empty_quote(symbol, str(exc))
    return out


def has_setup(result: dict[str, Any]) -> bool:
    """Return True when an analysis result contains a usable setup."""
    return bool(
        result.get("demand_zones")
        or result.get("supply_zones")
        or result.get("confirmation_zones")
    )


def setup_count(symbols: Sequence[str], results: dict[str, dict[str, Any]]) -> int:
    """Count symbols in a group that have a setup in the latest scan."""
    wanted = {s.upper() for s in symbols}
    return sum(1 for sym, res in results.items() if sym.upper() in wanted and has_setup(res))


def _average_quote(symbol: str, quotes: QuoteMap) -> dict[str, Any]:
    valid = [q for q in quotes.values() if q.get("ok")]
    if not valid:
        return _empty_quote(symbol, "No quote data")
    return {
        "symbol": symbol,
        "price": 0.0,
        "change": 0.0,
        "change_pct": sum(float(q.get("change_pct", 0.0)) for q in valid) / len(valid),
        "volume": sum(int(q.get("volume", 0) or 0) for q in valid),
        "ok": True,
        "source": "basket",
        "error": "",
    }


def group_tiles(
    results: dict[str, dict[str, Any]] | None = None,
    *,
    quote_fetcher: QuoteFetcher | None = None,
    allow_basket_fallback: bool = True,
    fallback_limit: int = 60,
) -> list[dict[str, Any]]:
    """Return top-level heatmap tiles for every configured group."""
    results = results or {}
    fetcher = quote_fetcher or fetch_yahoo_changes
    index_symbols = [g.ticker for g in GROUPS if g.ticker]
    direct = fetcher(index_symbols)

    fallback_symbols: list[str] = []
    if allow_basket_fallback:
        for group in GROUPS:
            q = direct.get(group.ticker) if group.ticker else None
            if not q or not q.get("ok"):
                fallback_symbols.extend(symbols_for_group(group)[:fallback_limit])
    fallback_quotes = fetcher(fallback_symbols) if fallback_symbols else {}

    tiles: list[dict[str, Any]] = []
    for group in GROUPS:
        symbols = symbols_for_group(group)
        quote = direct.get(group.ticker) if group.ticker else None
        source = "index"
        if not quote or not quote.get("ok"):
            basket = {s: fallback_quotes[s] for s in symbols[:fallback_limit] if s in fallback_quotes}
            quote = _average_quote(group.label, basket)
            source = "basket" if quote.get("ok") else "none"
        tiles.append({
            "id": group.id,
            "label": group.label,
            "kind": group.kind,
            "ticker": group.ticker,
            "change_pct": float(quote.get("change_pct", 0.0)) if quote else 0.0,
            "price": float(quote.get("price", 0.0)) if quote else 0.0,
            "volume": int(quote.get("volume", 0) or 0) if quote else 0,
            "ok": bool(quote and quote.get("ok")),
            "source": source,
            "setup_count": setup_count(symbols, results),
            "symbol_count": len(symbols),
            "note": group.note,
        })
    return tiles


def stock_tiles(
    group_id: str,
    *,
    results: dict[str, dict[str, Any]] | None = None,
    source_name: str = "Yahoo Finance",
    credentials: dict[str, str] | None = None,
    quote_fetcher: QuoteFetcher | None = None,
) -> list[dict[str, Any]]:
    """Return stock-level heatmap tiles for one group."""
    group = group_by_id(group_id)
    if group is None:
        return []
    return stock_tiles_for_symbols(
        symbols_for_group(group),
        results=results,
        source_name=source_name,
        credentials=credentials,
        quote_fetcher=quote_fetcher,
    )


def stock_tiles_for_symbols(
    symbols: Sequence[str],
    *,
    results: dict[str, dict[str, Any]] | None = None,
    source_name: str = "Yahoo Finance",
    credentials: dict[str, str] | None = None,
    quote_fetcher: QuoteFetcher | None = None,
) -> list[dict[str, Any]]:
    """Return stock-level heatmap tiles for an arbitrary symbol universe."""
    clean_symbols = _dedupe(symbols)
    if quote_fetcher is not None:
        quotes = quote_fetcher(clean_symbols)
    else:
        quotes = fetch_source_quotes(clean_symbols, source_name, credentials)
        if source_name == "Yahoo Finance":
            try:
                import yfinance as yf

                ticker_by_symbol = {symbol: _format_yahoo_symbol(symbol) for symbol in clean_symbols}
                repair_reasons = {
                    symbol: "mover validation"
                    for symbol in _mover_yahoo_daily_symbols(quotes, limit_per_side=10)
                }
                _repair_yahoo_quotes(quotes, ticker_by_symbol, repair_reasons, yf)
            except Exception as exc:
                logger.debug("Yahoo mover validation skipped: %s", exc)
    results = results or {}
    rows: list[dict[str, Any]] = []
    for symbol in clean_symbols:
        quote = quotes.get(symbol) or _empty_quote(symbol)
        res = results.get(symbol, {})
        rows.append({
            "symbol": symbol,
            "name": get_company_name(symbol),
            "price": float(quote.get("price", 0.0)),
            "change_pct": float(quote.get("change_pct", 0.0)),
            "change": float(quote.get("change", 0.0)),
            "volume": int(quote.get("volume", 0) or 0),
            "ok": bool(quote.get("ok")),
            "has_setup": has_setup(res),
            "setup": _setup_label(res),
        })
    return rows


def _setup_label(result: dict[str, Any]) -> str:
    if not result:
        return "No scan setup"
    if result.get("demand_zones"):
        return "Demand setup"
    if result.get("supply_zones"):
        return "Supply setup"
    if result.get("confirmation_zones"):
        return "Confirmed zone"
    return "No scan setup"


def filter_tiles(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Apply the full-page heatmap filter mode."""
    if mode == "Gainers":
        return [r for r in rows if r.get("ok") and r.get("change_pct", 0.0) > 0]
    if mode == "Losers":
        return [r for r in rows if r.get("ok") and r.get("change_pct", 0.0) < 0]
    if mode == "With Setups":
        return [r for r in rows if r.get("has_setup") or r.get("setup_count", 0) > 0]
    return rows


def sort_tiles(rows: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Sort heatmap tiles by the selected UI mode."""
    if mode == "% Change: High to Low":
        return sorted(rows, key=lambda r: (not r.get("ok"), -float(r.get("change_pct", 0.0))))
    if mode == "% Change: Low to High":
        return sorted(rows, key=lambda r: (not r.get("ok"), float(r.get("change_pct", 0.0))))
    if mode == "Setups First":
        return sorted(rows, key=lambda r: (-(int(r.get("setup_count", 0)) + int(bool(r.get("has_setup")))), r.get("label", r.get("symbol", ""))))
    return sorted(rows, key=lambda r: str(r.get("label", r.get("symbol", ""))))
