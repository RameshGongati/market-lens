"""Watchlist management UI panel component."""

import streamlit as st

from config.settings import EXCHANGES, MAX_STOCKS_PER_WATCHLIST, MAX_WATCHLISTS
from utils.helpers import search_stocks
from watchlist.manager import (
    add_stock,
    create_watchlist,
    delete_watchlist,
    get_all_watchlists,
    get_stocks,
    remove_stock,
)


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
    # Clear the search so the dropdown closes after selection
    st.session_state.wl_search_query = ""


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
        for stock in stocks:
            sc1, sc2 = st.columns([5, 1])
            with sc1:
                st.write(f"**{stock.symbol}** ({stock.exchange})")
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
                # Reset all search and entry state for the next addition.
                st.session_state.wl_search_query = ""
                st.session_state.wl_symbol_field = ""
                st.session_state.wl_exchange_field = EXCHANGES[0]
                st.session_state.pop("wl_search_result", None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        else:
            st.warning("Please enter or select a stock symbol.")
