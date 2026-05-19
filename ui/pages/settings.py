"""App settings page."""

import streamlit as st

from config.credentials import clear_credentials
from config.settings import APP_VERSION, SUPPORTED_DATA_SOURCES
from storage.database import db_path


def render_settings() -> None:
    """Render the application settings page."""
    st.title("⚙️ Settings")

    # App info
    st.markdown("### About")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("App Version", f"v{APP_VERSION}")
    with col2:
        active_source = st.session_state.get("selected_data_source", "Yahoo Finance")
        st.metric("Active Data Source", active_source)

    st.markdown("---")

    # Database info
    st.markdown("### Storage")
    st.info(f"Database location: `{db_path()}`")

    st.markdown("---")

    # Credentials management
    st.markdown("### Credentials")
    st.warning("Clearing credentials will require re-entering API keys on next run.")

    col_a, col_b = st.columns(2)
    with col_a:
        source_to_clear = st.selectbox("Clear credentials for", ["All sources"] + SUPPORTED_DATA_SOURCES)
    with col_b:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Clear Credentials", type="secondary"):
            try:
                if source_to_clear == "All sources":
                    clear_credentials(source=None)
                    st.session_state.credentials = {}
                    st.success("All credentials cleared.")
                else:
                    clear_credentials(source=source_to_clear)
                    creds = st.session_state.get("credentials", {})
                    creds.pop(source_to_clear, None)
                    st.session_state.credentials = creds
                    st.success(f"Credentials for {source_to_clear} cleared.")
            except Exception as exc:
                st.error(f"Failed to clear credentials: {exc}")

    st.markdown("---")

    # Pending features roadmap
    st.markdown("### Pending Features Roadmap")
    pending = [
        "Real-time auto-refresh every 5 minutes during market hours",
        "Zerodha Kite Connect full integration (historical data + orders)",
        "Upstox API full integration (instrument key mapping)",
        "TradingView full historical data support",
        "NSE India historical data scraping",
        "Portfolio P&L tracking",
        "Email / push alert notifications",
        "Custom alert conditions (price triggers, RSI thresholds)",
        "Multi-timeframe analysis overlay",
        "Export analysis report to PDF",
        "Chart drawing tools (trend lines, Fibonacci)",
        "Sector-wise heatmap view",
    ]
    for item in pending:
        st.markdown(f"- {item}")
