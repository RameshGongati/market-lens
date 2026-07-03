"""App settings page."""

import streamlit as st

from config.credentials import clear_credentials
from config.preferences import load_preferences, reset_preferences, save_preferences
from config.settings import APP_VERSION, SUPPORTED_DATA_SOURCES
from storage.database import clear_all_analysis_history, clear_all_notes, db_path
from utils.export import exports_dir


def render_settings() -> None:
    """Render the application settings page."""
    st.title("⚙️ Settings")

    # ---------- About ----------
    st.markdown("### About")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("App Version", f"v{APP_VERSION}")
    with col2:
        active_source = st.session_state.get("selected_data_source", "Yahoo Finance")
        st.metric("Active Data Source", active_source)

    st.markdown("---")

    # ---------- Storage ----------
    st.markdown("### Storage")
    st.info(f"Database: `{db_path()}`")

    st.markdown("---")

    # ---------- User Preferences ----------
    st.markdown("### User Preferences")
    st.caption("Preferences are saved automatically when you change selections in the sidebar.")
    try:
        prefs = load_preferences()
    except Exception:
        prefs = {}

    if prefs:
        # Structured display of the two-axis selections — every read is
        # defensive (.get with a default) so a missing key never crashes
        # the page, even on a preferences file written by an older version.
        p1, p2 = st.columns(2)
        with p1:
            st.markdown(f"**Data Source:** {prefs.get('selected_data_source', 'Yahoo Finance')}")
            st.markdown(f"**Trading Type:** {prefs.get('trading_type', 'Options Trading')}")
            st.markdown(f"**Primary Strategy:** {prefs.get('primary_strategy', 'Demand/Supply Zones')}")
        with p2:
            _enh = prefs.get("enhancers") or []
            st.markdown(f"**Enhancers:** {', '.join(_enh) if _enh else 'None'}")
            st.markdown(f"**Alerts:** {'On' if prefs.get('alerts_on') else 'Off'}")
            st.markdown(f"**Theme:** {prefs.get('theme', 'Light (default)')}")
        with st.expander("Raw preferences (JSON)"):
            st.json(prefs, expanded=True)
    else:
        st.caption("No saved preferences found.")

    if st.button("Reset Preferences to Defaults", type="secondary", use_container_width=False):
        try:
            reset_preferences()
            st.success("Preferences reset to defaults.")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to reset preferences: {exc}")

    st.markdown("---")

    # ---------- Chart Settings ----------
    st.markdown("### Chart Settings")
    show_tooltip = st.toggle(
        "Show candle details tooltip",
        value=prefs.get("show_candle_tooltip", True),
        help="When ON, hovering over the chart shows a box with OHLC candle details. "
        "Turn OFF to hide the box and keep only the crosshair lines and price label.",
    )
    if show_tooltip != prefs.get("show_candle_tooltip", True):
        save_preferences({"show_candle_tooltip": show_tooltip})
        st.rerun()

    st.markdown("---")

    # ---------- Data Management ----------
    st.markdown("### Data Management")
    st.warning(
        "These actions are irreversible. Analysis history and notes will be permanently deleted."
    )

    dm1, dm2 = st.columns(2)
    with dm1:
        if st.button("Clear All Analysis History", type="secondary", use_container_width=True):
            try:
                clear_all_analysis_history()
                st.success("All analysis history cleared.")
            except Exception as exc:
                st.error(f"Failed to clear history: {exc}")
    with dm2:
        if st.button("Clear All Stock Notes", type="secondary", use_container_width=True):
            try:
                clear_all_notes()
                st.success("All stock notes cleared.")
            except Exception as exc:
                st.error(f"Failed to clear notes: {exc}")

    st.markdown("---")

    # ---------- Credentials ----------
    st.markdown("### Credentials")
    st.caption("Clearing credentials will require re-entering API keys on next run.")

    col_a, col_b = st.columns(2)
    with col_a:
        source_to_clear = st.selectbox(
            "Clear credentials for", ["All sources"] + SUPPORTED_DATA_SOURCES
        )
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

    # ---------- Exports ----------
    st.markdown("### Exports")
    export_path = exports_dir()
    st.info(f"Export files are saved to: `{export_path}`")
    st.caption("Navigate to that folder manually to access exported Excel and PDF files.")

    st.markdown("---")

    # ---------- Roadmap ----------
    st.markdown("### Pending Features Roadmap")
    roadmap = [
        "Dark theme toggle",
        "Telegram alert notifications",
        "Email alert notifications",
        "Live market news feed",
        "Multi-exchange global support (NYSE, NASDAQ, LSE)",
        "Backtesting engine with historical signal replay",
        "Docker containerisation for one-command setup",
        "TradingView full data integration (pending stable library)",
        "Increase watchlist limit beyond 10",
        "Run multiple primary strategies side-by-side",
        "Real-time auto-refresh every 5 minutes during market hours",
        "Zerodha Kite Connect order placement integration",
        "Upstox API instrument key mapping",
        "Portfolio P&L tracking",
        "Custom alert conditions (price triggers, RSI thresholds)",
        "RSI enhancer implementation (selectable today, not yet wired)",
        "Chart drawing tools (manual trend lines)",
        "Sector-wise heatmap view",
    ]
    for item in roadmap:
        st.markdown(f"- {item}")
