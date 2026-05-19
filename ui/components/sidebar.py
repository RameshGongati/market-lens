"""Sidebar with global controls — data source, watchlist, analysis type, alerts."""

import streamlit as st

from config.settings import ANALYSIS_TYPES, CREDENTIALS_REQUIRED, SUPPORTED_DATA_SOURCES
from ui.components.alerts_toggle import render_alerts_toggle
from ui.components.credentials_form import render_credentials_form
from ui.components.notifications import render_notifications
from watchlist.manager import get_all_watchlists


def render_sidebar() -> None:
    """Render the full sidebar including all global controls."""
    with st.sidebar:
        st.title("📈 Market Lens")
        st.markdown("---")

        # Navigation
        st.markdown("**Navigation**")
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("Dashboard", use_container_width=True):
                st.session_state.active_page = "dashboard"
                st.rerun()
        with col2:
            if st.button("Watchlists", use_container_width=True):
                st.session_state.active_page = "watchlist_manager"
                st.rerun()
        with col3:
            if st.button("Settings", use_container_width=True):
                st.session_state.active_page = "settings"
                st.rerun()

        st.markdown("---")

        # Data source selector
        st.markdown("**Data Source**")
        current_source = st.session_state.get("selected_data_source", "Yahoo Finance")
        source_idx = SUPPORTED_DATA_SOURCES.index(current_source) if current_source in SUPPORTED_DATA_SOURCES else 0
        selected_source = st.selectbox(
            "Select data source",
            SUPPORTED_DATA_SOURCES,
            index=source_idx,
            key="sidebar_source_select",
            label_visibility="collapsed",
        )

        if selected_source != current_source:
            st.session_state.selected_data_source = selected_source

        # Connection status indicator
        _render_connection_status(selected_source)

        # Credentials form if required
        if CREDENTIALS_REQUIRED.get(selected_source):
            with st.expander("Configure Credentials", expanded=False):
                render_credentials_form(selected_source)

        st.markdown("---")

        # Watchlist selector
        st.markdown("**Watchlist**")
        try:
            watchlists = get_all_watchlists()
        except Exception:
            watchlists = []

        if watchlists:
            wl_names = [w.name for w in watchlists]
            wl_ids = [w.id for w in watchlists]
            current_wl_id = st.session_state.get("selected_watchlist_id")
            try:
                wl_idx = wl_ids.index(current_wl_id) if current_wl_id in wl_ids else 0
            except (ValueError, TypeError):
                wl_idx = 0
            selected_wl_name = st.selectbox(
                "Select watchlist",
                wl_names,
                index=wl_idx,
                key="sidebar_watchlist_select",
                label_visibility="collapsed",
            )
            selected_wl_id = wl_ids[wl_names.index(selected_wl_name)]
            st.session_state.selected_watchlist_id = selected_wl_id
        else:
            st.caption("No watchlists yet. Create one in Watchlists.")
            st.session_state.selected_watchlist_id = None

        st.markdown("---")

        # Analysis type selector
        st.markdown("**Analysis Type**")
        current_analysis = st.session_state.get("selected_analysis_type", ANALYSIS_TYPES[0])
        analysis_idx = ANALYSIS_TYPES.index(current_analysis) if current_analysis in ANALYSIS_TYPES else 0
        selected_analysis = st.selectbox(
            "Select analysis",
            ANALYSIS_TYPES,
            index=analysis_idx,
            key="sidebar_analysis_select",
            label_visibility="collapsed",
        )
        st.session_state.selected_analysis_type = selected_analysis

        st.markdown("---")

        # Run analysis button
        if st.button("▶ Run Analysis", type="primary", use_container_width=True):
            if not st.session_state.get("selected_watchlist_id"):
                st.warning("Please select a watchlist first.")
            else:
                st.session_state.active_page = "dashboard"
                st.session_state.analysing = True
                st.rerun()

        st.markdown("---")

        # Alerts toggle
        render_alerts_toggle()

        st.markdown("---")

        # Notifications badge
        render_notifications()


def _render_connection_status(source_name: str) -> None:
    """Show a small connection status indicator for the selected source."""
    required = CREDENTIALS_REQUIRED.get(source_name, [])
    if not required:
        st.markdown(
            "<span style='color:green;font-size:0.8rem;'>● Connected (no auth needed)</span>",
            unsafe_allow_html=True,
        )
        return

    creds = st.session_state.get("credentials", {}).get(source_name, {})
    if all(creds.get(f) for f in required):
        st.markdown(
            "<span style='color:green;font-size:0.8rem;'>● Credentials saved</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<span style='color:orange;font-size:0.8rem;'>● Credentials required</span>",
            unsafe_allow_html=True,
        )
