"""Watchlist management UI panel component."""

from datetime import datetime

import yfinance as yf
import streamlit as st

from config.settings import EXCHANGES, MAX_STOCKS_PER_WATCHLIST, MAX_WATCHLISTS
from data.manager import DataSourceManager
from utils.helpers import format_currency, search_stocks
from utils.logger import get_logger
from watchlist.manager import (
    add_stock,
    create_watchlist,
    delete_watchlist,
    get_all_watchlists,
    get_stocks,
    remove_stock,
)

logger = get_logger(__name__)

# Price cache TTL in seconds (5 minutes)
_PRICE_CACHE_TTL = 300


# ---------------------------------------------------------------------------
# Search callback (module-level so Streamlit can identify it across reruns)
# ---------------------------------------------------------------------------

def _on_stock_selected() -> None:
    """on_change callback for the search results selectbox.

    Parses the selected option string (``"SYMBOL — Name (EXCHANGE)"``),
    pre-fills the symbol and exchange session-state keys so the input
    widgets below reflect the selection immediately, then clears the
    search query so the results dropdown closes.
    """
    chosen: str = st.session_state.get("wl_search_result", "") or ""
    if not chosen:
        return

    # Parse "SYMBOL — Company Name (NSE)" → symbol and exchange
    sym_part = chosen.split(" — ", 1)[0].strip()
    exchange_part = "BSE" if chosen.strip().endswith("(BSE)") else "NSE"

    st.session_state.wl_symbol_field = sym_part
    st.session_state.wl_exchange_field = exchange_part
    # Delete the search key (not assign) so Streamlit doesn't raise
    # StreamlitAPIException for a key bound to an active widget.
    if "wl_search_query" in st.session_state:
        del st.session_state["wl_search_query"]


# ---------------------------------------------------------------------------
# Price fetching helpers
# ---------------------------------------------------------------------------

def _yf_ticker(symbol: str, exchange: str) -> str:
    """Return the Yahoo Finance ticker string for a given symbol and exchange."""
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    return f"{symbol}{suffix}"


def _source_ticker(symbol: str, exchange: str, source_name: str) -> str:
    """Return the ticker string formatted for the active data source."""
    if source_name == "Yahoo Finance":
        return _yf_ticker(symbol, exchange)
    if source_name == "TradingView":
        return f"{exchange.upper()}:{symbol}"
    return symbol


def _is_cache_valid(entry: dict) -> bool:
    """Return True if a price cache entry is less than 5 minutes old."""
    ts = entry.get("timestamp")
    if not isinstance(ts, datetime):
        return False
    return (datetime.now() - ts).total_seconds() < _PRICE_CACHE_TTL


def _fetch_price_yf_direct(symbol: str, exchange: str) -> dict | None:
    """Fetch a quote directly from yfinance, bypassing DataSourceManager.

    Used as a fallback when the primary data source is unavailable or
    returns a zero price.

    Args:
        symbol: Raw stock symbol (e.g. "RELIANCE").
        exchange: "NSE" or "BSE".

    Returns:
        Dict with price, change, change_pct, timestamp, or None on failure.
    """
    try:
        ticker = yf.Ticker(_yf_ticker(symbol, exchange))
        info = ticker.fast_info
        price = float(getattr(info, "last_price", 0) or 0)
        prev_close = float(getattr(info, "previous_close", price) or price)
        if price <= 0:
            return None
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0
        return {
            "price": price,
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "timestamp": datetime.now(),
        }
    except Exception as exc:
        logger.warning("yfinance direct fetch failed for %s: %s", symbol, exc)
        return None


def _fetch_all_prices(stocks: list, source_name: str) -> dict[str, dict | None]:
    """Fetch latest prices for every stock in the list.

    Prices are cached in session state for 5 minutes under the key
    ``wl_price_cache_{symbol}_{exchange}``.  On a cache miss the primary
    data source is tried first; if it returns a zero price or raises an
    exception the function falls back to a direct yfinance call.

    Args:
        stocks: List of Stock model objects.
        source_name: Name of the currently selected data source.

    Returns:
        Dict mapping stock symbol → price dict (or None if unavailable).
    """
    results: dict[str, dict | None] = {}

    # Set up the primary data source (DataSourceManager defaults to Yahoo Finance)
    ds_manager = DataSourceManager()
    source_ready = True  # YahooFinanceSource is always ready by default

    if source_name != "Yahoo Finance":
        creds = st.session_state.get("credentials", {}).get(source_name, {})
        try:
            ds_manager.switch_source(source_name, creds if creds else None)
        except Exception as exc:
            logger.warning(
                "Could not switch to %s for price fetch: %s — falling back to Yahoo Finance",
                source_name,
                exc,
            )
            source_ready = False  # Will use direct yfinance fallback only

    for stock in stocks:
        cache_key = f"wl_price_cache_{stock.symbol}_{stock.exchange}"
        cached = st.session_state.get(cache_key)

        # Return from cache if still fresh
        if cached and _is_cache_valid(cached):
            results[stock.symbol] = cached
            continue

        price_data: dict | None = None

        # Primary source attempt
        if source_ready:
            try:
                ticker_sym = _source_ticker(stock.symbol, stock.exchange, source_name)
                quote = ds_manager.get_quote(ticker_sym)
                current_price = quote.get("current_price", 0.0)
                if current_price and current_price > 0:
                    price_data = {
                        "price": current_price,
                        "change": quote.get("change", 0.0),
                        "change_pct": quote.get("change_pct", 0.0),
                        "timestamp": datetime.now(),
                    }
            except Exception as exc:
                logger.warning(
                    "Primary source quote failed for %s: %s", stock.symbol, exc
                )

        # Fallback: direct yfinance call (covers both zero-price and error cases)
        if price_data is None:
            price_data = _fetch_price_yf_direct(stock.symbol, stock.exchange)

        # Store in cache whether successful or not (None is stored too, which
        # prevents hammering the API on every rerun when a symbol is unavailable)
        if price_data:
            st.session_state[cache_key] = price_data

        results[stock.symbol] = price_data

    return results


# ---------------------------------------------------------------------------
# Public component
# ---------------------------------------------------------------------------

def render_watchlist_panel() -> None:
    """Render the full watchlist management UI panel."""
    st.subheader("Manage Watchlists")

    watchlists = get_all_watchlists()
    wl_count = len(watchlists)

    st.markdown(f"**Watchlists: {wl_count}/{MAX_WATCHLISTS}**")
    with st.form("create_watchlist_form", clear_on_submit=True):
        new_name = st.text_input("New watchlist name", max_chars=50)
        if st.form_submit_button("Create Watchlist"):
            if new_name.strip():
                try:
                    create_watchlist(new_name.strip())
                    st.success(f"Watchlist '{new_name}' created.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            else:
                st.warning("Please enter a watchlist name.")

    st.markdown("---")

    if not watchlists:
        st.info("No watchlists yet. Create your first one above.")
        return

    wl_names = {w.name: w for w in watchlists}
    selected_name = st.selectbox("Select watchlist to manage", list(wl_names.keys()))
    selected_wl = wl_names[selected_name]

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🗑 Delete", key=f"del_wl_{selected_wl.id}", type="secondary"):
            delete_watchlist(selected_wl.id)
            st.success(f"Deleted watchlist '{selected_wl.name}'.")
            st.rerun()

    stocks = get_stocks(selected_wl.id)
    st.markdown(f"**Stocks: {len(stocks)}/{MAX_STOCKS_PER_WATCHLIST}**")

    if stocks:
        source_name: str = st.session_state.get("selected_data_source", "Yahoo Finance")

        # Fetch all prices at once; show spinner only while actual network calls happen
        with st.spinner("Fetching prices…"):
            prices = _fetch_all_prices(stocks, source_name)

        for stock in stocks:
            price_data = prices.get(stock.symbol)
            sc1, sc2 = st.columns([5, 1])

            with sc1:
                if price_data and price_data.get("price", 0) > 0:
                    price = price_data["price"]
                    change = price_data["change"]
                    change_pct = price_data["change_pct"]
                    color = "#26a69a" if change >= 0 else "#ef5350"
                    sign = "+" if change >= 0 else ""
                    price_str = format_currency(price)
                    change_str = (
                        f"{sign}₹{abs(change):.2f}"
                        f" ({sign}{change_pct:.2f}%)"
                    )
                    st.markdown(
                        f"**{stock.symbol}**"
                        f" <span style='color:#666;font-size:0.82rem;'>({stock.exchange})</span><br>"
                        f"<span style='font-size:0.92rem;font-weight:600;'>{price_str}</span>"
                        f"&nbsp;"
                        f"<span style='color:{color};font-size:0.8rem;font-weight:600;'>"
                        f"{change_str}</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"**{stock.symbol}** ({stock.exchange})<br>"
                        f"<span style='color:#999;font-size:0.8rem;'>Price unavailable</span>",
                        unsafe_allow_html=True,
                    )

            with sc2:
                if st.button("✕", key=f"rem_stock_{stock.id}"):
                    remove_stock(selected_wl.id, stock.id)
                    st.rerun()
    else:
        st.caption("No stocks in this watchlist yet.")

    st.markdown("---")
    st.markdown("**Add Stock**")
    _render_add_stock_section(selected_wl.id)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _render_add_stock_section(watchlist_id: int) -> None:
    """Live-search autocomplete + symbol/exchange inputs + Add button.

    All widgets are plain (no form wrapper) so that:
    - The search input triggers an immediate rerun on each keystroke.
    - The ``on_change`` callback on the results selectbox can write back
      to the symbol and exchange keys before those widgets render.
    """
    # Initialise session-state keys with defaults on first render.
    st.session_state.setdefault("wl_search_query", "")
    st.session_state.setdefault("wl_symbol_field", "")
    st.session_state.setdefault("wl_exchange_field", EXCHANGES[0])

    # ── Search input ──────────────────────────────────────────────────────
    st.text_input(
        "Search symbol or company name",
        key="wl_search_query",
        placeholder="e.g. RELIANCE or Reliance Industries",
        help="Results update instantly as you type. Selecting a stock fills the fields below.",
    )
    search_query: str = st.session_state.get("wl_search_query", "")

    # ── Results dropdown ──────────────────────────────────────────────────
    if search_query:
        matches = search_stocks(search_query, limit=20)
        if matches:
            count = len(matches)
            st.caption(
                f"Found **{count}** stock{'s' if count != 1 else ''} matching '{search_query}'"
            )
            options = [
                f"{s['symbol']} — {s['name']} ({s['exchange']})" for s in matches
            ]
            # If the stored selection is no longer valid for the current
            # options (query changed), drop it so the box resets cleanly.
            if st.session_state.get("wl_search_result") not in options:
                st.session_state.pop("wl_search_result", None)

            st.selectbox(
                "Select stock from results",
                options,
                key="wl_search_result",
                on_change=_on_stock_selected,
                label_visibility="collapsed",
            )
        else:
            st.caption(f"No stocks found for: **'{search_query}'**")
    else:
        st.caption("Type to search stocks…")

    # ── Symbol / Exchange / Add ───────────────────────────────────────────
    c1, c2, c3 = st.columns([3, 1, 2])
    with c1:
        symbol: str = st.text_input(
            "Symbol",
            key="wl_symbol_field",
            max_chars=20,
            placeholder="e.g. RELIANCE",
        )
    with c2:
        exchange: str = st.selectbox(  # type: ignore[assignment]
            "Exchange",
            EXCHANGES,
            key="wl_exchange_field",
        )
    with c3:
        st.markdown("<br>", unsafe_allow_html=True)  # visual alignment
        add_clicked = st.button(
            "➕ Add Stock", type="primary", use_container_width=True
        )

    if add_clicked:
        sym = symbol.strip().upper()
        if sym:
            try:
                add_stock(watchlist_id, sym, exchange)
                st.success(f"Added **{sym}** ({exchange}).")
                # Delete widget-bound keys rather than assigning to them —
                # Streamlit raises StreamlitAPIException if you set (=) a key
                # that is already bound to a rendered widget in this run.
                # Deletion is always safe and causes the widget to reset to
                # its default on the next rerun, which also refreshes the
                # stock list displayed above.
                for _key in (
                    "wl_search_query",
                    "wl_search_result",
                    "wl_symbol_field",
                    "wl_exchange_field",
                ):
                    if _key in st.session_state:
                        del st.session_state[_key]
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        else:
            st.warning("Please enter or select a stock symbol.")
