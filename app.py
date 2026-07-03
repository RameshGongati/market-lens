"""Market Lens — Main Streamlit Entry Point."""

import streamlit as st

from config.credentials import load_credentials
from storage.database import init_db
from ui.components.sidebar import render_sidebar
from ui.pages.dashboard import render_dashboard
from ui.pages.watchlist_manager import render_watchlist_manager
from ui.pages.settings import render_settings
from utils.logger import get_logger

logger = get_logger(__name__)


def init_session_state() -> None:
    """Initialise all required Streamlit session state keys."""
    defaults: dict = {
        "active_page": "dashboard",
        "selected_watchlist_id": None,
        # Two-axis analysis model (Trading Type + Primary Strategy + Enhancers).
        # The sidebar seeds these from saved preferences via _init_two_axis_state;
        # they are listed here so the keys always exist before any page reads them.
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
