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
from ui.components.credentials_form import render_credentials_form
from ui.components.notifications import render_notifications
from utils.helpers import format_timestamp
from utils.market_hours import get_current_ist_time, get_market_countdown, is_market_open
from data.nse_indices import refresh_all_watchlists
from utils.helpers import get_nse_stock_batches, load_predefined_watchlists
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


# Each sidebar card carries a colour so a section can be found by scanning
# rather than by reading every label — the Screener in particular used to be
# an unlabelled grey chevron sitting directly below an identical one for
# credentials. Blue/teal/purple/amber are chosen to stay clear of the
# green-and-red that already mean demand, supply and zone strength.
_SECTION_COLOURS: dict[str, str] = {
    "data": "#378ADD",
    "watchlist": "#1D9E75",
    "screener": "#7F77DD",
    "strategy": "#EF9F27",
}

# Material Symbols ligature names for each section chip. Streamlit already
# loads "Material Symbols Rounded" for its own icon support, so inline HTML
# can reference the family directly — the sanitiser strips class and id but
# keeps style, and a font-family in a style attribute survives.
_SECTION_ICONS: dict[str, str] = {
    "data": "database",
    "watchlist": "bookmarks",
    "screener": "filter_alt",
    "strategy": "track_changes",
}


def _count_active_screener_filters() -> int:
    """How many screener filters are currently narrowing the results.

    Drives the badge on the Screener card so its state is visible without
    opening it — "All" and an empty multiselect both mean no filtering.
    """
    active = 0
    confirm = st.session_state.get("screener_confirmation", False)
    if confirm:
        active += 1
        # Proximity is disabled in confirmation mode, so a stale value left
        # over from the other mode must not be counted as active.
        if st.session_state.get("screener_confirm_score", "All") != "All":
            active += 1
    else:
        if st.session_state.get("screener_proximity", "All") != "All":
            active += 1
        if st.session_state.get("screener_min_score", "All") != "All":
            active += 1
    if st.session_state.get("screener_zone_strength"):
        active += 1
    return active


def _alert_badge_count() -> int:
    """Number shown on the Alerts nav item.

    Counts stocks currently near a zone in the last scan, falling back to
    unread rows in the alerts table when no scan has run. Both come from
    ``ui.pages.alerts_page`` so the badge and the page it opens can never
    report different numbers.
    """
    try:
        from ui.pages.alerts_page import unread_alert_count, zone_alert_matches
        near = len(zone_alert_matches())
        return near if near else unread_alert_count()
    except Exception:
        return 0


def _render_secondary_nav() -> None:
    """Alerts / Reports / Trade Journal / Settings, one row each."""
    current = st.session_state.get("active_page", "dashboard")
    count = _alert_badge_count()

    items = (
        ("Alerts", "alerts", ":material/notifications:"),
        ("Reports", "reports", ":material/description:"),
        ("Trade Journal", "trade_journal", ":material/menu_book:"),
        ("Settings", "settings", ":material/settings:"),
    )
    for label, page, icon in items:
        # The Alerts count is drawn as a red pill by CSS rather than put in
        # the label. Streamlit buttons have no badge slot, and BOTH markdown
        # routes fail here: this build silently drops :red-badge[] and :red[]
        # from a button label, rendering a plain "Alerts 17" — verified in the
        # DOM. So a marker element is emitted immediately before the button
        # and app.py hangs a ::after on the next sibling, with the count baked
        # into the rule because CSS cannot read application state.
        if page == "alerts" and count:
            st.markdown(
                "<div style='letter-spacing:0.13px;height:0;margin:0;'></div>"
                "<style>"
                "section[data-testid=\"stSidebar\"] "
                "[data-testid=\"stElementContainer\"]:has("
                "[style*=\"letter-spacing: 0.13px\"]) + "
                "[data-testid=\"stElementContainer\"] .stButton button p::after"
                "{content:\"" + str(count) + "\";background:#B3261E;"
                "color:#FFFFFF;border-radius:10px;padding:1px 7px;"
                "margin-left:8px;font-size:0.7rem;font-weight:700;"
                "vertical-align:middle;}"
                "</style>",
                unsafe_allow_html=True,
            )
        text = label
        if st.button(
            text,
            icon=icon,
            use_container_width=True,
            type="primary" if page == current else "secondary",
            key=f"nav2_{page}",
        ):
            st.session_state.active_page = page
            st.rerun()


def _panel_marker() -> None:
    """Emit an invisible marker identifying a container as a sidebar PANEL.

    The sidebar has two kinds of bordered container: the two outer panels
    (menu, controls) which take the cream surface, and the section cards
    inside the second panel which take white. CSS cannot tell them apart by
    element type — both are ``stVerticalBlock`` — so each is tagged by a
    distinctive inline style that ``app.py`` selects on.

    A class would read better, but Streamlit's markdown sanitiser strips
    class and id while preserving style, so the letter-spacing value is the
    hook. Keep it in step with the selector in ``app.py``.
    """
    st.markdown(
        "<div style='letter-spacing:0.11px;height:0;margin:0;'></div>",
        unsafe_allow_html=True,
    )


def _screener_summary() -> str:
    """One-line description of the active screener filters, for the card face.

    Lets the Screener card show what it is doing while the controls stay
    folded away in a popover — the point of promoting it out of the expander
    was that its state was invisible.
    """
    parts: list[str] = []
    if st.session_state.get("screener_confirmation", False):
        parts.append("Confirmed ≤8%")
        score = st.session_state.get("screener_confirm_score", "All")
        if score != "All":
            parts.append(f"Score {score}")
    else:
        prox = st.session_state.get("screener_proximity", "All")
        if prox != "All":
            parts.append("Inside zone" if prox == "Inside Zone" else f"Within {prox}")
        score = st.session_state.get("screener_min_score", "All")
        if score != "All":
            parts.append(f"Score {score}")
    strengths = st.session_state.get("screener_zone_strength") or []
    if strengths:
        parts.append(", ".join(strengths))
    return " · ".join(parts) if parts else "No filters — showing every stock"


def _strategy_summary() -> tuple[str, str]:
    """Return (trading type, "primary · enhancers") for the Strategy card face."""
    trading_type = st.session_state.get("trading_type", "Options Trading")
    primary = st.session_state.get("primary_strategy", "Demand/Supply Zones")
    # Trim the parenthetical from "Trend Following (SMA50/EMA20)" so the line
    # fits a 300px rail without wrapping.
    primary_short = primary.split(" (")[0]
    enhancers = st.session_state.get("enhancers") or []
    short = [e.replace(" Confluence", "") for e in enhancers]
    detail = f"{primary_short} · {', '.join(short)}" if short else primary_short
    return trading_type, detail


def _card_line(text: str, colour: str = "#1E1E23", size: str = "0.92rem") -> None:
    """Render a plain summary line inside a card (no widget chrome)."""
    st.markdown(
        f"<div style='font-size:{size};color:{colour};line-height:1.45;'>{text}</div>",
        unsafe_allow_html=True,
    )


def _section_header(
    label: str, section: str, badge: str = "", badge_tone: str = "count"
) -> None:
    """Render a coloured marker + section label at the top of a sidebar card.

    Deliberately a coloured square rather than an icon: Streamlit only renders
    Material icons through its own ``:material/name:`` markdown directive,
    which is not processed inside raw HTML, and no icon webfont is guaranteed
    to be loaded. A plain coloured chip needs no font at all and cannot
    silently render as a blank box.

    Args:
        label: Section name, shown uppercase.
        section: Key into :data:`_SECTION_COLOURS`.
        badge: Optional short pill on the right (e.g. an active-filter count).
    """
    colour = _SECTION_COLOURS.get(section, "#6B6B73")
    icon = _SECTION_ICONS.get(section, "circle")
    # A count ("2 on") is a figure worth reading, so it takes the section
    # colour solid. A status ("live") is ambient — a soft green tint states
    # that things are fine without shouting it.
    if badge_tone == "status":
        badge_bg, badge_fg = "#EAF3DE", "#3B6D11"
    else:
        badge_bg, badge_fg = colour, "#ffffff"
    badge_html = (
        f"<span style='margin-left:auto;background:{badge_bg};color:{badge_fg};"
        f"font-size:0.72rem;font-weight:600;padding:2px 10px;"
        f"border-radius:999px;'>{badge}</span>"
        if badge
        else ""
    )
    # The label's letter-spacing below is load-bearing, not just typographic:
    # app.py's CSS finds sidebar cards by matching [style*="letter-spacing:
    # 0.6px"] on this header. A class would be cleaner but Streamlit's
    # markdown sanitiser strips class and id while keeping inline style.
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:9px;margin-bottom:6px;'>"
        f"<span style='width:24px;height:24px;border-radius:7px;background:{colour};"
        f"display:flex;align-items:center;justify-content:center;flex:none;'>"
        f"<span style=\"font-family:'Material Symbols Rounded';font-size:16px;"
        f"color:#ffffff;line-height:1;\">{icon}</span></span>"
        f"<span style='font-size:0.82rem;font-weight:600;letter-spacing:0.6px;"
        f"color:#4A4A52;'>{label}</span>{badge_html}</div>",
        unsafe_allow_html=True,
    )


def _init_two_axis_state() -> None:
    """Derive the session state keys that depend on ``enhancers``.

    Called once per session at the top of :func:`render_sidebar` before any
    widgets are defined.  Uses ``setdefault`` so it never overwrites values
    that are already in session state (e.g. from a previous rerun).

    ``trading_type``, ``primary_strategy`` and ``enhancers`` themselves are
    NOT set here.  ``app.init_session_state`` establishes them first — from
    fixed launch defaults, or from the deep-link query params when a stock is
    opened in a new tab — so a ``setdefault`` at this point could never do
    anything.  This function only fills in what depends on them.
    """
    enhancers = st.session_state.get("enhancers", [])
    # Derive use_fibonacci from the enhancer list so the dashboard doesn't
    # see a stale False value before the user changes anything.
    st.session_state.setdefault(
        "use_fibonacci", "Fibonacci Confluence" in enhancers
    )
    # Pre-populate individual checkbox keys so the widgets show the correct
    # initial state (Streamlit ignores value= if the key already exists).
    for enhancer in ENHANCERS:
        st.session_state.setdefault(_enhancer_key(enhancer), enhancer in enhancers)


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
    new_type: str = st.session_state.get("sidebar_trading_type", "Options Trading")
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
        # ---------- Panel 1: identity + page navigation ----------
        # Kept separate from the controls below because it answers a different
        # question — "where am I" rather than "what am I analysing".
        with st.container(border=True):
            _panel_marker()
            _render_brand_and_status()

            # The current page is rendered as a FILLED button and the others
            # as outlines, so the active page is visible at a glance —
            # previously all three were identical.
            #
            # Settings moved out of this row to the secondary nav group below
            # the analysis controls: this row is for the places you go while
            # working, and Settings is not one of them.
            _current_page = st.session_state.get("active_page", "dashboard")
            _nav = (
                ("Dashboard", "dashboard", ":material/dashboard:"),
                ("Watchlists", "watchlist_manager", ":material/list:"),
            )
            for _col, (_label, _page, _icon) in zip(st.columns(2), _nav):
                with _col:
                    if st.button(
                        _label,
                        icon=_icon,
                        use_container_width=True,
                        type="primary" if _page == _current_page else "secondary",
                        key=f"nav_{_page}",
                    ):
                        st.session_state.active_page = _page
                        st.rerun()
            _pattern_active = _current_page in {
                "pattern_scanner",
                "pattern_results",
                "pattern_detail",
            }
            if st.button(
                "Pattern Scanner",
                icon=":material/query_stats:",
                use_container_width=True,
                type="primary" if _pattern_active else "secondary",
                key="nav_pattern_scanner",
            ):
                st.session_state.active_page = "pattern_scanner"
                st.rerun()

        # ---------- Panel 2: analysis controls ----------
        with st.container(border=True):
            _panel_marker()
            # ---------- Data Source ----------
            with st.container(border=True):
                # A source needing no credentials is always connected, so the
                # badge can be decided before the selectbox renders.
                _src_now = st.session_state.get("selected_data_source", "Yahoo Finance")
                _needs_creds = CREDENTIALS_REQUIRED.get(_src_now, [])
                _creds_now = st.session_state.get("credentials", {}).get(_src_now, {})
                _is_live = not _needs_creds or all(_creds_now.get(f) for f in _needs_creds)
                _section_header(
                    "DATA SOURCE",
                    "data",
                    badge="live" if _is_live else "setup",
                    badge_tone="status",
                )
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

                # No separate "Connected" line — the live/setup badge in the
                # header already carries that, and repeating it wasted a row.
                if CREDENTIALS_REQUIRED.get(selected_source):
                    with st.expander("Configure Credentials", expanded=False):
                        render_credentials_form(selected_source)

            # ---------- Watchlist ----------
            with st.container(border=True):
                _section_header("WATCHLIST", "watchlist")
                _WL_SOURCES = ["My Watchlists", "Index Watchlists", "All NSE Stocks"]
                _WL_SHORT = {
                    "My Watchlists": "Custom",
                    "Index Watchlists": "Index",
                    "All NSE Stocks": "All NSE",
                }
                st.session_state.setdefault("watchlist_source", "Index Watchlists")
                _current_wl = st.session_state.get("watchlist_source", "Index Watchlists")
                if _current_wl not in _WL_SOURCES:
                    _current_wl = "Index Watchlists"

                source_col, list_col = st.columns([0.95, 1.75])
                with source_col:
                    wl_source = st.selectbox(
                        "Watchlist source",
                        _WL_SOURCES,
                        index=_WL_SOURCES.index(_current_wl),
                        format_func=lambda s: _WL_SHORT.get(s, s),
                        key="sidebar_wl_source",
                        label_visibility="collapsed",
                    )
                st.session_state["watchlist_source"] = wl_source

                _watchlist_caption = ""

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
                        with list_col:
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
                        with list_col:
                            st.caption("No custom watchlists yet.")
                        st.session_state.selected_watchlist_id = None
                elif wl_source == "Index Watchlists":
                    predefined = load_predefined_watchlists()
                    if predefined:
                        pd_names = [w["name"] for w in predefined]
                        current_pd = st.session_state.get("selected_predefined_watchlist", pd_names[0])
                        pd_idx = pd_names.index(current_pd) if current_pd in pd_names else 0
                        with list_col:
                            selected_pd = st.selectbox(
                                "Select index watchlist",
                                pd_names,
                                index=pd_idx,
                                key="sidebar_predefined_select",
                                label_visibility="collapsed",
                            )
                        st.session_state["selected_predefined_watchlist"] = selected_pd
                        wl_data = predefined[pd_names.index(selected_pd)]
                        _watchlist_caption = (
                            f"{wl_data['description']} ({len(wl_data['symbols'])} stocks)"
                        )

                        # Sits at the BOTTOM of the watchlist card rather than as
                        # a standalone full-width button, and keeps a text label —
                        # an icon-only control here would be as easy to miss as
                        # the Screener chevron was.
                        if _watchlist_caption:
                            st.markdown(
                                f"<div style='margin-top:-7px;margin-bottom:8px;"
                                f"font-size:0.84rem;color:#8A8F98;'>"
                                f"{_watchlist_caption}</div>",
                                unsafe_allow_html=True,
                            )
                        if st.button(
                            "Refresh from NSE",
                            icon=":material/refresh:",
                            key="refresh_nse_indices",
                            use_container_width=True,
                        ):
                            with st.spinner("Fetching latest lists from NSE..."):
                                result = refresh_all_watchlists()

                            # Show per-list change details: added/removed stocks
                            for name in result["updated"]:
                                changes = result["changes"][name]
                                parts = []
                                if changes["added"]:
                                    parts.append(f"Added: {', '.join(changes['added'])}")
                                if changes["removed"]:
                                    parts.append(f"Removed: {', '.join(changes['removed'])}")
                                st.success(f"**{name}** ({result['total_symbols'][name]} stocks) — {'; '.join(parts)}")

                            # Lists fetched successfully but no changes detected
                            if result["unchanged"]:
                                st.info(
                                    f"Already up to date: {', '.join(result['unchanged'])}"
                                )

                            # Lists that failed with reason
                            for fail_msg in result["failed"]:
                                st.error(f"Failed — {fail_msg}")

                            if result["updated"]:
                                st.rerun()
                    else:
                        with list_col:
                            st.caption("No index watchlists available.")
                else:
                    batches = get_nse_stock_batches()
                    batch_labels = [b["label"] for b in batches]
                    current_batch = st.session_state.get("selected_nse_batch", batch_labels[0])
                    batch_idx = batch_labels.index(current_batch) if current_batch in batch_labels else 0
                    with list_col:
                        selected_batch = st.selectbox(
                            "Select stock range",
                            batch_labels,
                            index=batch_idx,
                            key="sidebar_nse_batch_select",
                            label_visibility="collapsed",
                        )
                    st.session_state["selected_nse_batch"] = selected_batch
                    batch_data = batches[batch_labels.index(selected_batch)]
                    st.session_state["selected_nse_batch_start"] = batch_data["start"]
                    st.session_state["selected_nse_batch_end"] = batch_data["end"]
                    st.markdown(
                        f"<div style='margin-top:-7px;font-size:0.84rem;"
                        f"color:#8A8F98;'>"
                        f"{batch_data['end'] - batch_data['start']} stocks in this batch"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            # ---------- Screener ----------
            # Promoted out of a bare expander into its own coloured card. It used
            # to render as a grey chevron directly below the identical one for
            # credentials, with nothing indicating whether any filter was active —
            # so it was both hard to find and, once found, hard to read.
            _active_filters = _count_active_screener_filters()
            with st.container(border=True):
                _section_header(
                    "SCREENER",
                    "screener",
                    badge=f"{_active_filters} on" if _active_filters else "",
                )
                # The card face states what is filtering; the controls fold away
                # into a popover so four widgets do not occupy the rail for a
                # setting most sessions never touch.
                _card_line(
                    _screener_summary(),
                    colour="#3C3489" if _active_filters else "#5F5E5A",
                    size="0.88rem",
                )
                _screener_panel = st.popover(
                    "Edit filters", icon=":material/tune:", use_container_width=True
                )
            with _screener_panel:
                # Zone confirmation is a different question from the rest of
                # the screener. The others ask "is price approaching a zone";
                # this asks "has price already reacted to one and left". Since
                # a confirmed zone is by definition no longer being approached,
                # the proximity control is meaningless here and is disabled
                # rather than left to give misleading results.
                st.session_state.setdefault("screener_confirmation", False)
                st.checkbox(
                    "Zone confirmation",
                    key="sidebar_screener_confirmation",
                    help=(
                        "Show stocks where price entered a zone and closed back "
                        "out through the proximal — the zone reacted. Limited to "
                        "moves still within 8% of the proximal."
                    ),
                )
                st.session_state["screener_confirmation"] = st.session_state[
                    "sidebar_screener_confirmation"
                ]
                _confirm_on = st.session_state["screener_confirmation"]

                _PROXIMITY_OPTIONS = ["All", "Inside Zone", "≤3%", "≤5%", "≤10%"]
                st.session_state.setdefault("screener_proximity", "All")
                _sp = st.session_state.get("screener_proximity", "All")
                _sp_idx = _PROXIMITY_OPTIONS.index(_sp) if _sp in _PROXIMITY_OPTIONS else 0
                st.selectbox(
                    "Proximity to Zone",
                    _PROXIMITY_OPTIONS,
                    index=_sp_idx,
                    key="sidebar_screener_proximity",
                    disabled=_confirm_on,
                    help="Not used with zone confirmation — 8% is applied instead."
                    if _confirm_on else None,
                )
                st.session_state["screener_proximity"] = st.session_state["sidebar_screener_proximity"]

                # The score ladder changes with the mode. A confirmed zone has
                # freshness pinned at 1.5, so it can only ever total 2.5, 3.5,
                # 4.5 or 5.5 — the usual 7/6+/5+ options would match almost
                # nothing. These three are the reachable values.
                if _confirm_on:
                    _SCORE_OPTIONS = ["All", "5.5", "4.5+", "3.5+"]
                    _score_key = "screener_confirm_score"
                else:
                    _SCORE_OPTIONS = ["All", "7", "6+", "5+"]
                    _score_key = "screener_min_score"
                st.session_state.setdefault(_score_key, "All")
                _ss = st.session_state.get(_score_key, "All")
                _ss_idx = _SCORE_OPTIONS.index(_ss) if _ss in _SCORE_OPTIONS else 0
                # Widget key includes the mode so switching modes does not
                # carry "6+" into a ladder that has no such option.
                st.selectbox(
                    "Min ODD Score",
                    _SCORE_OPTIONS,
                    index=_ss_idx,
                    key=f"sidebar_{_score_key}",
                )
                st.session_state[_score_key] = st.session_state[f"sidebar_{_score_key}"]

                _STRENGTH_OPTIONS = ["Normal", "Strong", "Very Strong"]
                st.session_state.setdefault("screener_zone_strength", [])
                st.multiselect(
                    "Zone Strength",
                    _STRENGTH_OPTIONS,
                    key="sidebar_screener_strength",
                    placeholder="All strengths",
                )
                st.session_state["screener_zone_strength"] = st.session_state["sidebar_screener_strength"]

            # No rule between cards — each card's own border already separates
            # it from the next, so a divider only adds a stray line.
            with st.container(border=True):
                _section_header("STRATEGY", "strategy")
                # Face shows the current configuration; the three axes fold into a
                # popover. Trading type, primary strategy and enhancers are set
                # once per session far more often than they are toggled, and as
                # nine stacked widgets they dominated the rail.
                _tt_face, _detail_face = _strategy_summary()
                _card_line(_tt_face)
                _card_line(_detail_face, colour="#55555E", size="0.83rem")
                _strategy_panel = st.popover(
                    "Change strategy", icon=":material/tune:", use_container_width=True
                )
            with _strategy_panel:
                # ---------- Trading Type (axis 1) ----------
                st.markdown("**Trading Type**")
                _tt = st.session_state.get("trading_type", "Options Trading")
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

            # ---------- Secondary nav ----------
            # Alerts / Reports / Trade Journal / Settings sit below the
            # analysis controls: they are destinations rather than scan
            # settings. Full-width rows rather than a column grid so the
            # labels never wrap and the Alerts badge has somewhere to sit.
            _render_secondary_nav()

            # Small breathing space before the primary action, without a rule.
            st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

            # ---------- Run Analysis ----------
            analysing = st.session_state.get("analysing", False)
            if analysing:
                st.button(
                    "Analysing…",
                    icon=":material/hourglass_top:",
                    disabled=True,
                    use_container_width=True,
                    type="primary",
                )
            else:
                if st.button(
                    "Run analysis",
                    icon=":material/play_arrow:",
                    type="primary",
                    use_container_width=True,
                ):
                    _wl_src = st.session_state.get("watchlist_source", "My Watchlists")
                    if _wl_src == "All NSE Stocks":
                        _has_wl = st.session_state.get("selected_nse_batch")
                    elif _wl_src == "My Watchlists":
                        _has_wl = st.session_state.get("selected_watchlist_id")
                    else:
                        _has_wl = st.session_state.get("selected_predefined_watchlist")
                    if not _has_wl:
                        st.warning("Please select a watchlist first.")
                    else:
                        st.session_state.active_page = "analysis_results"
                        st.session_state.analysing = True
                        update_last_analysis_timestamp()
                        save_preferences({"alerts_on": st.session_state.get("alerts_on", False)})
                        st.rerun()

            # Re-run last analysis button
            prefs = load_preferences()
            last_ts = prefs.get("last_analysis_timestamp")
            if last_ts:
                st.caption(f"Last run: {format_timestamp(last_ts)}")
                if st.button(
                    "Re-run last",
                    icon=":material/replay:",
                    use_container_width=True,
                ):
                    _wl_src2 = st.session_state.get("watchlist_source", "My Watchlists")
                    if _wl_src2 == "All NSE Stocks":
                        _has_wl2 = st.session_state.get("selected_nse_batch")
                    elif _wl_src2 == "My Watchlists":
                        _has_wl2 = st.session_state.get("selected_watchlist_id")
                    else:
                        _has_wl2 = st.session_state.get("selected_predefined_watchlist")
                    if _has_wl2:
                        st.session_state.active_page = "analysis_results"
                        st.session_state.analysing = True
                        update_last_analysis_timestamp()
                        st.rerun()
                    else:
                        st.warning("Select a watchlist first.")

            # ---------- Notifications ----------
            render_notifications()


def _render_brand_and_status() -> None:
    """Render the brand mark and the market status pill.

    One HTML block rather than two Streamlit elements so the pair stays
    visually joined. It carries no surface of its own — the whole sidebar
    sits inside a single panel (see the CSS in ``app.py``), so a box here
    would nest a box inside a box.
    """
    now = get_current_ist_time()
    open_ = is_market_open(now)
    countdown = get_market_countdown(now)

    # Closed is AMBER, not red. A closed market is a normal state, not an
    # error, and alarm red both overstates it and collides with
    # red-means-supply on the charts. The glyph follows the same logic —
    # a sun while the session runs, a moon once it has ended.
    if open_:
        bg, border, fg, muted = "#EAF3DE", "#97C459", "#27500A", "#3B6D11"
        title, glyph = "Market open", "light_mode"
    else:
        bg, border, fg, muted = "#FAEEDA", "#EF9F27", "#633806", "#854F0B"
        title, glyph = "Market closed", "bedtime"

    icon = (
        "font-family:'Material Symbols Rounded';line-height:1;"
    )
    st.markdown(
        # Brand row
        "<div style='display:flex;align-items:center;gap:10px;margin-bottom:11px;'>"
        "<span style='width:32px;height:32px;border-radius:9px;background:#4A5361;"
        "display:flex;align-items:center;justify-content:center;flex:none;'>"
        f"<span style=\"{icon}font-size:20px;color:#ffffff;\">query_stats</span></span>"
        "<span style='font-size:1.3rem;font-weight:600;color:#1A1A1F;'>"
        "Market Lens</span></div>"
        # Status pill
        # margin-bottom keeps the nav buttons from butting up against the pill.
        f"<div style='background:{bg};border:1px solid {border};border-radius:9px;"
        f"padding:9px 11px;margin-bottom:14px;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center;gap:8px;'>"
        f"<span style='display:flex;align-items:center;gap:6px;color:{fg};"
        f"font-size:0.98rem;font-weight:600;'>"
        f"<span style=\"{icon}font-size:18px;\">{glyph}</span>{title}</span>"
        f"<span style='color:{muted};font-size:0.82rem;'>{countdown}</span></div>"
        f"<div style='color:{muted};font-size:0.78rem;margin-top:4px;'>"
        f"{now.strftime('%d %b %Y · %H:%M')} IST</div>"
        "</div>",
        unsafe_allow_html=True,
    )
