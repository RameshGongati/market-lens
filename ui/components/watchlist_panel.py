"""Watchlist management UI panel component."""

import streamlit as st

from watchlist.manager import (
    add_stock,
    create_watchlist,
    delete_watchlist,
    get_all_watchlists,
    get_stocks,
    remove_stock,
)
from config.settings import EXCHANGES, MAX_STOCKS_PER_WATCHLIST, MAX_WATCHLISTS


def render_watchlist_panel() -> None:
    """Render the full watchlist management UI panel."""
    st.subheader("Manage Watchlists")

    watchlists = get_all_watchlists()
    wl_count = len(watchlists)

    # Create watchlist
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

    # Select watchlist to manage
    wl_names = {w.name: w for w in watchlists}
    selected_name = st.selectbox("Select watchlist to manage", list(wl_names.keys()))
    selected_wl = wl_names[selected_name]

    col1, col2 = st.columns([4, 1])
    with col2:
        if st.button("🗑 Delete", key=f"del_wl_{selected_wl.id}", type="secondary"):
            delete_watchlist(selected_wl.id)
            st.success(f"Deleted watchlist '{selected_wl.name}'.")
            st.rerun()

    # Stocks in selected watchlist
    stocks = get_stocks(selected_wl.id)
    stock_count = len(stocks)
    st.markdown(f"**Stocks: {stock_count}/{MAX_STOCKS_PER_WATCHLIST}**")

    if stocks:
        for stock in stocks:
            scol1, scol2 = st.columns([5, 1])
            with scol1:
                st.write(f"**{stock.symbol}** ({stock.exchange})")
            with scol2:
                if st.button("✕", key=f"rem_stock_{stock.id}"):
                    remove_stock(selected_wl.id, stock.id)
                    st.rerun()
    else:
        st.caption("No stocks in this watchlist yet.")

    st.markdown("---")

    # Add stock form
    st.markdown("**Add Stock**")
    with st.form("add_stock_form", clear_on_submit=True):
        sym_col, ex_col = st.columns([3, 1])
        with sym_col:
            new_symbol = st.text_input("Symbol (e.g. RELIANCE)", max_chars=20)
        with ex_col:
            new_exchange = st.selectbox("Exchange", EXCHANGES)
        if st.form_submit_button("Add Stock"):
            if new_symbol.strip():
                try:
                    add_stock(selected_wl.id, new_symbol.strip(), new_exchange)
                    st.success(f"Added {new_symbol.upper()} ({new_exchange}).")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            else:
                st.warning("Please enter a stock symbol.")
