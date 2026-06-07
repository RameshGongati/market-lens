"""Sidebar with global controls — market status, data source, watchlist, alerts."""

import streamlit as st

from config.preferences import load_preferences, save_preferences, update_last_analysis_timestamp
from config.settings import ANALYSIS_TYPES, CREDENTIALS_REQUIRED, SUPPORTED_DATA_SOURCES
from ui.components.alerts_toggle import render_alerts_toggle
from ui.components.credentials_form import render_credentials_form
from ui.components.notifications import render_notifications
from utils.helpers import format_timestamp
from utils.market_hours import get_current_ist_time, get_market_countdown, is_market_open, is_trading_day
from watchlist.manager import get_all_watchlists


def render_sidebar() -> None:
    """Render the full sidebar including market status and all controls."""
    with st.sidebar:
        st.title("📈 Market Lens")

        # ---------- Market Status ----------
        _render_market_status()
        st.markdown("---")

        # ---------- Navigation ----------
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

        # ---------- Data Source ----------
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
            save_preferences({"selected_data_source": selected_source})

        _render_connection_status(selected_source)

        if CREDENTIALS_REQUIRED.get(selected_source):
            with st.expander("Configure Credentials", expanded=False):
                render_credentials_form(selected_source)

        st.markdown("---")

        # ---------- Watchlist ----------
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
            save_preferences({"selected_watchlist_id": selected_wl_id})
        else:
            st.caption("No watchlists yet. Create one in Watchlists.")
            st.session_state.selected_watchlist_id = None

        st.markdown("---")

        # ---------- Analysis Type ----------
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
        if selected_analysis != current_analysis:
            st.session_state.selected_analysis_type = selected_analysis
            save_preferences({"selected_analysis_type": selected_analysis})

        # ---------- Stage 3: Fibonacci confluence (opt-in) ----------
        # Only relevant to Demand/Supply Zones — hidden for every other
        # analysis type. Mirrors the selectbox pattern above: read the
        # current app-state value, compare against the widget's return, and
        # persist + save_preferences() only on a real change.
        if selected_analysis == "Demand/Supply Zones":
            current_use_fib = st.session_state.get("use_fibonacci", False)
            use_fibonacci = st.checkbox(
                "Enhance with Fibonacci Confluence",
                value=current_use_fib,
                key="sidebar_use_fibonacci",
            )
            if use_fibonacci != current_use_fib:
                st.session_state.use_fibonacci = use_fibonacci
                save_preferences({"use_fibonacci": use_fibonacci})

            # Disabled placeholder for a future feature — no logic, just a
            # visible "coming soon" marker directly below the Fibonacci
            # toggle.
            st.checkbox(
                "Options Trading (coming soon)",
                value=False,
                disabled=True,
                key="sidebar_options_trading_placeholder",
            )

        st.markdown("---")

        # ---------- Run Analysis ----------
        analysing = st.session_state.get("analysing", False)
        if analysing:
            st.button("⏳ Analysing…", disabled=True, use_container_width=True, type="primary")
        else:
            if st.button("▶ Run Analysis", type="primary", use_container_width=True):
                if not st.session_state.get("selected_watchlist_id"):
                    st.warning("Please select a watchlist first.")
                else:
                    st.session_state.active_page = "dashboard"
                    st.session_state.analysing = True
                    update_last_analysis_timestamp()
                    save_preferences({"alerts_on": st.session_state.get("alerts_on", False)})
                    st.rerun()

        # Re-run last analysis button
        prefs = load_preferences()
        last_ts = prefs.get("last_analysis_timestamp")
        if last_ts:
            st.caption(f"Last run: {format_timestamp(last_ts)}")
            if st.button("↺ Re-run Last", use_container_width=True):
                if st.session_state.get("selected_watchlist_id"):
                    st.session_state.active_page = "dashboard"
                    st.session_state.analysing = True
                    update_last_analysis_timestamp()
                    st.rerun()
                else:
                    st.warning("Select a watchlist first.")

        st.markdown("---")

        # ---------- Alerts Toggle ----------
        render_alerts_toggle()

        st.markdown("---")

        # ---------- Notifications ----------
        render_notifications()


def _render_market_status() -> None:
    """Display a prominent market open/closed indicator with countdown."""
    now = get_current_ist_time()
    open_ = is_market_open(now)
    trading = is_trading_day(now)
    countdown = get_market_countdown(now)

    if open_:
        st.markdown(
            "<div style='background:#d4edda;border-radius:8px;padding:10px 14px;"
            "border-left:4px solid #28a745;'>"
            "<span style='color:#155724;font-size:1rem;font-weight:700;'>🟢 Market Open</span><br>"
            f"<span style='color:#155724;font-size:0.8rem;'>{countdown}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='background:#f8d7da;border-radius:8px;padding:10px 14px;"
            "border-left:4px solid #dc3545;'>"
            "<span style='color:#721c24;font-size:1rem;font-weight:700;'>🔴 Market Closed</span><br>"
            f"<span style='color:#721c24;font-size:0.8rem;'>{countdown}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    st.caption(f"🕐 IST: {now.strftime('%d %b %Y  %H:%M')}")


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
