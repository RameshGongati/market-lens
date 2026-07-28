"""Market Lens — Main Streamlit Entry Point."""

import streamlit as st

from config.credentials import load_credentials
from config.settings import SUPPORTED_DATA_SOURCES
from config.trading_config import ENHANCERS, PRIMARY_STRATEGIES, TRADING_TYPES
from storage.database import init_db
from ui.components.sidebar import render_sidebar
from ui.pages.dashboard import render_dashboard
from ui.pages.watchlist_manager import render_watchlist_manager
from ui.pages.settings import render_settings
from utils.logger import get_logger

logger = get_logger(__name__)


def init_session_state() -> None:
    """Initialise all required Streamlit session state keys.

    These are deliberately fixed defaults, not the saved preferences: every
    fresh app launch starts from a known baseline. A stock opened in a new
    browser tab is the one case that must NOT use these — it adopts the
    opening tab's selections from the URL instead (see the ?stock= handler
    in :func:`main`), because a new tab is a separate Streamlit session with
    empty state and would otherwise silently analyse against different
    settings than the ones on screen.
    """
    defaults: dict = {
        "active_page": "dashboard",
        "selected_watchlist_id": None,
        # Two-axis analysis model (Trading Type + Primary Strategy + Enhancers).
        "trading_type": "Options Trading",
        "primary_strategy": "Demand/Supply Zones",
        "enhancers": ["Fibonacci Confluence", "EMA 20 Confluence"],
        "selected_data_source": "Yahoo Finance",
        "alerts_on": False,
        "credentials": {},
        "analysing": False,
        "selected_stock_symbol": None,
        "analysis_results": {},
        "notifications": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def main() -> None:
    """Application entry point."""
    st.set_page_config(
        page_title="Market Lens",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items={
            "Get Help": None,
            "Report a bug": None,
            "About": "Market Lens v0.1.0 — Local Stock Market Analysis",
        },
    )

    # Force light theme via custom CSS
    st.markdown(
        """
        <style>
            [data-testid="stAppViewContainer"] { background-color: #ffffff; }
            [data-testid="stSidebar"] { background-color: #f8f9fa; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    init_session_state()

    # Open-in-new-tab support: if URL has ?stock=SYMBOL, jump straight to
    # stock detail view so a new browser tab shows the chart directly.
    _qp_stock = st.query_params.get("stock")
    if _qp_stock and not st.session_state.get("_qp_handled"):
        st.session_state.selected_stock_symbol = _qp_stock
        st.session_state.selected_stock_id = 0
        st.session_state.active_page = "stock_detail"
        _qp_exchange = st.query_params.get("exchange", "NSE")
        st.session_state["_qp_exchange"] = _qp_exchange

        # Adopt the opening tab's analysis context (see stock_card.py). This
        # must happen before render_sidebar() below so the sidebar widgets and
        # the detail chart both use the settings the user actually ran the
        # analysis with, rather than the launch defaults set above.
        _qp_src = st.query_params.get("src")
        if _qp_src in SUPPORTED_DATA_SOURCES:
            st.session_state["selected_data_source"] = _qp_src
        _qp_tt = st.query_params.get("tt")
        if _qp_tt in TRADING_TYPES:
            st.session_state["trading_type"] = _qp_tt
        _qp_ps = st.query_params.get("ps")
        if _qp_ps in PRIMARY_STRATEGIES:
            st.session_state["primary_strategy"] = _qp_ps
        _qp_enh = st.query_params.get("enh")
        if _qp_enh is not None:
            # Empty string legitimately means "no enhancers selected".
            _adopted = [e for e in _qp_enh.split(",") if e in ENHANCERS]
            st.session_state["enhancers"] = _adopted
            st.session_state["use_fibonacci"] = "Fibonacci Confluence" in _adopted

        st.session_state["_qp_handled"] = True

    try:
        init_db()
    except Exception as exc:
        st.error(f"Database initialisation failed: {exc}")
        logger.exception("Database init error")

    try:
        saved = load_credentials()
        if saved:
            st.session_state.credentials = saved
    except Exception as exc:
        logger.warning("Could not load saved credentials: %s", exc)

    render_sidebar()

    page = st.session_state.active_page
    if page == "dashboard":
        render_dashboard()
    elif page == "watchlist_manager":
        render_watchlist_manager()
    elif page == "settings":
        render_settings()
    else:
        render_dashboard()


if __name__ == "__main__":
    main()
