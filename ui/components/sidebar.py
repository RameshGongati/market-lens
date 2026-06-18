"""Sidebar with global controls — market status, data source, watchlist, alerts.

Stage B adds three grouped controls that replace the single "Analysis Type"
dropdown:

  * Trading Type (radio) — time horizon, drives data fetch in Stage C
  * Primary Strategy (radio) — the base method; options depend on trading type
  * ODD Enhancers (checkboxes) — optional layers, multi-select

The old ANALYSIS_TYPES selectbox is removed.  ``st.session_state["use_fibonacci"]``
is now *derived* from the Enhancers checkboxes so that the existing dashboard
code (which reads that key) keeps working without modification.
"""

import streamlit as st

from config.preferences import load_preferences, save_preferences, update_last_analysis_timestamp
from config.settings import CREDENTIALS_REQUIRED, SUPPORTED_DATA_SOURCES
from config.trading_config import (
    ENHANCERS,
    TRADING_TYPES,
    get_available_primaries,
    get_defaults,
)
from ui.components.alerts_toggle import render_alerts_toggle
from ui.components.credentials_form import render_credentials_form
from ui.components.notifications import render_notifications
from utils.helpers import format_timestamp
from utils.market_hours import get_current_ist_time, get_market_countdown, is_market_open, is_trading_day
from utils.helpers import load_predefined_watchlists
from watchlist.manager import get_all_watchlists


# ---------------------------------------------------------------------------
# Session-state key helpers for the new two-axis controls
# ---------------------------------------------------------------------------

def _enhancer_key(enhancer: str) -> str:
    """Return the sidebar session-state key for an individual enhancer checkbox.

    Uses a stable, slug-style key so that renaming an enhancer in
    TRADING_TYPES later doesn't silently clash with an old key.

    Example::

        >>> _enhancer_key("Fibonacci Confluence")
        'sidebar_enhancer_fibonacci_confluence'
    """
    return "sidebar_enhancer_" + enhancer.replace(" ", "_").replace("/", "_").lower()


def _init_two_axis_state() -> None:
    """Initialise the two-axis session state keys from saved preferences.

    Called once per session at the top of :func:`render_sidebar` before any
    widgets are defined.  Uses ``setdefault`` so it never overwrites values
    that are already in session state (e.g. from a previous rerun).

    Also back-fills individual enhancer checkbox keys from ``enhancers`` so
    that the ``st.checkbox`` calls below pick up the right initial state even
    on the very first render.
    """
    prefs = load_preferences()
    st.session_state.setdefault("trading_type", prefs.get("trading_type", "Short-term Trading"))
    st.session_state.setdefault("primary_strategy", prefs.get("primary_strategy", "Demand/Supply Zones"))
    st.session_state.setdefault("enhancers", prefs.get("enhancers", get_defaults("Short-term Trading")["enhancers"]))
    # Derive use_fibonacci from the persisted enhancers so the dashboard
    # doesn't see a stale False value before the user changes anything.
    st.session_state.setdefault(
        "use_fibonacci", "Fibonacci Confluence" in st.session_state["enhancers"]
    )
    # Pre-populate individual checkbox keys so the widgets show the correct
    # initial state (Streamlit ignores value= if the key already exists).
    for enhancer in ENHANCERS:
        st.session_state.setdefault(
            _enhancer_key(enhancer), enhancer in st.session_state["enhancers"]
        )


# ---------------------------------------------------------------------------
# Widget callbacks (fire before the script reruns)
# ---------------------------------------------------------------------------

def _on_trading_type_change() -> None:
    """Callback: reset primary strategy and enhancers to the new type's defaults.

    When the user picks a different Trading Type, it would be confusing if the
    Primary Strategy and Enhancers stayed at values they had for the *old* type
    (e.g. Long-term defaults to Trend Following, but switching to Options should
    snap back to Demand/Supply Zones + Fibonacci).  After the reset the user can
    freely override both — this callback only fires on an explicit type change.
    """
    new_type: str = st.session_state.get("sidebar_trading_type", "Short-term Trading")
    defaults = get_defaults(new_type)
    new_primary: str = defaults["primary"]  # type: ignore[assignment]
    new_enhancers: list[str] = list(defaults["enhancers"])  # type: ignore[arg-type]

    # Sync canonical session-state keys
    st.session_state["trading_type"] = new_type
    st.session_state["primary_strategy"] = new_primary
    st.session_state["enhancers"] = new_enhancers
    st.session_state["use_fibonacci"] = "Fibonacci Confluence" in new_enhancers

    # Also sync the individual widget keys so each checkbox reflects the new
    # defaults on the very next render (Streamlit uses the key value, not
    # value=, once a key exists in session state).
    st.session_state["sidebar_primary_strategy"] = new_primary
    for enhancer in ENHANCERS:
        st.session_state[_enhancer_key(enhancer)] = enhancer in new_enhancers

    save_preferences({
        "trading_type": new_type,
        "primary_strategy": new_primary,
        "enhancers": new_enhancers,
    })


def _on_primary_strategy_change() -> None:
    """Callback: persist a user-driven primary-strategy change."""
    new_primary: str = st.session_state.get("sidebar_primary_strategy", "Demand/Supply Zones")
    st.session_state["primary_strategy"] = new_primary
    save_preferences({"primary_strategy": new_primary})


def _on_enhancer_change() -> None:
    """Callback: rebuild the ``enhancers`` list from individual checkbox states
    and keep ``use_fibonacci`` in sync for backward-compatible callers.
    """
    new_enhancers = [e for e in ENHANCERS if st.session_state.get(_enhancer_key(e), False)]
    st.session_state["enhancers"] = new_enhancers
    st.session_state["use_fibonacci"] = "Fibonacci Confluence" in new_enhancers
    save_preferences({"enhancers": new_enhancers})


def render_sidebar() -> None:
    """Render the full sidebar including market status and all controls.

    Initialises the two-axis session state keys from saved preferences on the
    first call of the session, then renders:

    1. Market status (IST clock + open/closed badge)
    2. Navigation buttons
    3. Data source selector
    4. Watchlist selector
    5. **Trading Type** radio (new — Stage B)
    6. **Primary Strategy** radio (new — Stage B)
    7. **ODD Enhancers** checkboxes (new — Stage B; replaces old Fib checkbox)
    8. Run Analysis / Re-run Last buttons
    9. Alerts toggle
    10. Notifications
    """
    # Initialise two-axis state once per session before any widget renders.
    _init_two_axis_state()

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
        st.session_state.setdefault("watchlist_source", "My Watchlists")
        wl_source = st.radio(
            "Watchlist source",
            ["My Watchlists", "Index Watchlists"],
            index=["My Watchlists", "Index Watchlists"].index(
                st.session_state.get("watchlist_source", "My Watchlists")
            ),
            key="sidebar_wl_source",
            horizontal=True,
            label_visibility="collapsed",
        )
        st.session_state["watchlist_source"] = wl_source

        if wl_source == "My Watchlists":
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
        else:
            predefined = load_predefined_watchlists()
            if predefined:
                pd_names = [w["name"] for w in predefined]
                current_pd = st.session_state.get("selected_predefined_watchlist", pd_names[0])
                pd_idx = pd_names.index(current_pd) if current_pd in pd_names else 0
                selected_pd = st.selectbox(
                    "Select index watchlist",
                    pd_names,
                    index=pd_idx,
                    key="sidebar_predefined_select",
                    label_visibility="collapsed",
                )
                st.session_state["selected_predefined_watchlist"] = selected_pd
                wl_data = predefined[pd_names.index(selected_pd)]
                st.caption(f"{wl_data['description']} ({len(wl_data['symbols'])} stocks)")
            else:
                st.caption("No predefined watchlists available.")

        # ---------- Screener ----------
        with st.expander("Screener", expanded=False):
            _PROXIMITY_OPTIONS = ["All", "≤3%", "≤5%", "≤10%"]
            st.session_state.setdefault("screener_proximity", "All")
            _sp = st.session_state.get("screener_proximity", "All")
            _sp_idx = _PROXIMITY_OPTIONS.index(_sp) if _sp in _PROXIMITY_OPTIONS else 0
            st.selectbox(
                "Proximity to Zone",
                _PROXIMITY_OPTIONS,
                index=_sp_idx,
                key="sidebar_screener_proximity",
            )
            st.session_state["screener_proximity"] = st.session_state["sidebar_screener_proximity"]

            _SCORE_OPTIONS = ["All", "7", "6+", "5+"]
            st.session_state.setdefault("screener_min_score", "All")
            _ss = st.session_state.get("screener_min_score", "All")
            _ss_idx = _SCORE_OPTIONS.index(_ss) if _ss in _SCORE_OPTIONS else 0
            st.selectbox(
                "Min ODD Score",
                _SCORE_OPTIONS,
                index=_ss_idx,
                key="sidebar_screener_score",
            )
            st.session_state["screener_min_score"] = st.session_state["sidebar_screener_score"]

            _STRENGTH_OPTIONS = ["Normal", "Strong", "Very Strong"]
            st.session_state.setdefault("screener_zone_strength", [])
            st.multiselect(
                "Zone Strength",
                _STRENGTH_OPTIONS,
                key="sidebar_screener_strength",
                placeholder="All strengths",
            )
            st.session_state["screener_zone_strength"] = st.session_state["sidebar_screener_strength"]

        st.markdown("---")

        # ---------- Trading Type (axis 1) ----------
        st.markdown("**Trading Type**")
        _tt = st.session_state.get("trading_type", "Short-term Trading")
        _tt_idx = TRADING_TYPES.index(_tt) if _tt in TRADING_TYPES else 0
        st.radio(
            "Trading Type",
            TRADING_TYPES,
            index=_tt_idx,
            key="sidebar_trading_type",
            label_visibility="collapsed",
            on_change=_on_trading_type_change,
        )
        # Keep the canonical key in sync with the widget key on every render.
        st.session_state["trading_type"] = st.session_state["sidebar_trading_type"]

        # ---------- Primary Strategy (axis 2) ----------
        st.markdown("**Primary Strategy**")
        _selected_tt = st.session_state["trading_type"]
        _available = get_available_primaries(_selected_tt)
        _ps = st.session_state.get("primary_strategy", _available[0])
        # Guard: if the stored primary is no longer available for the current
        # trading type (e.g. after a type change mid-session), snap to the
        # first available option so the radio never shows a stale value.
        if _ps not in _available:
            _ps = _available[0]
            st.session_state["primary_strategy"] = _ps
        _ps_idx = _available.index(_ps)
        st.radio(
            "Primary Strategy",
            _available,
            index=_ps_idx,
            key="sidebar_primary_strategy",
            label_visibility="collapsed",
            on_change=_on_primary_strategy_change,
        )
        st.session_state["primary_strategy"] = st.session_state["sidebar_primary_strategy"]

        # ---------- ODD Enhancers (axis 2 — multi-select) ----------
        st.markdown("**ODD Enhancers**")
        for _enhancer in ENHANCERS:
            st.checkbox(
                _enhancer,
                key=_enhancer_key(_enhancer),
                on_change=_on_enhancer_change,
            )
        # After rendering, rebuild the canonical enhancers list and derive
        # use_fibonacci for backward-compatible code that reads that key.
        st.session_state["enhancers"] = [
            e for e in ENHANCERS if st.session_state.get(_enhancer_key(e), False)
        ]
        st.session_state["use_fibonacci"] = (
            "Fibonacci Confluence" in st.session_state["enhancers"]
        )

        st.markdown("---")

        # ---------- Run Analysis ----------
        analysing = st.session_state.get("analysing", False)
        if analysing:
            st.button("⏳ Analysing…", disabled=True, use_container_width=True, type="primary")
        else:
            if st.button("▶ Run Analysis", type="primary", use_container_width=True):
                _wl_src = st.session_state.get("watchlist_source", "My Watchlists")
                _has_wl = (
                    st.session_state.get("selected_watchlist_id")
                    if _wl_src == "My Watchlists"
                    else st.session_state.get("selected_predefined_watchlist")
                )
                if not _has_wl:
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
                _wl_src2 = st.session_state.get("watchlist_source", "My Watchlists")
                _has_wl2 = (
                    st.session_state.get("selected_watchlist_id")
                    if _wl_src2 == "My Watchlists"
                    else st.session_state.get("selected_predefined_watchlist")
                )
                if _has_wl2:
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
