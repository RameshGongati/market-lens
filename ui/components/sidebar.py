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

from config.preferences import save_preferences, update_last_analysis_timestamp
from config.settings import CREDENTIALS_REQUIRED, SUPPORTED_DATA_SOURCES
from config.trading_config import (
    ENHANCERS,
    TRADING_TYPES,
    get_available_primaries,
    get_defaults,
)
from ui.components.credentials_form import render_credentials_form
from ui.components.notifications import render_notifications
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


def _go_to_page(page: str) -> None:
    """Route within the current Streamlit session."""
    st.session_state.active_page = page


def _sidebar_nav_button_css(
    page: str,
    active: bool,
    icon_name: str | None = None,
    material_icon: str | None = None,
    icon_colour: str = "#1974FF",
    icon_size: int = 40,
    badge: int | None = None,
    min_height: int = 56,
) -> str:
    """CSS for one keyed sidebar nav button."""
    button_class = f"st-key-nav_card_{page}"
    background = "linear-gradient(135deg,#FFFFFF 0%,#EEF6FF 100%)" if active else "#FFFFFF"
    border = "#8FBBFF" if active else "#E7EBF2"
    text_colour = "#155AE8" if active else "#17233B"
    shadow = (
        "inset 4px 0 0 #1974FF,0 6px 14px rgba(30,101,218,0.17)"
        if active else "0 4px 11px rgba(24,54,97,0.09)"
    )
    if icon_name:
        before = (
            f"content:\"\";background:url('/app/static/icons/{icon_name}') center / contain no-repeat;"
            f"width:{icon_size}px;height:{icon_size}px;"
        )
    else:
        before = (
            f"content:\"{material_icon or 'circle'}\";font-family:'Material Symbols Rounded';"
            f"font-size:{icon_size}px;color:{icon_colour};line-height:1;text-align:center;"
            "font-variation-settings:'FILL' 1,'wght' 700,'GRAD' 100;"
            f"filter:drop-shadow(0 2px 1px {icon_colour}33);"
        )
    badge_rule = ""
    if badge:
        badge_rule = (
            f"section[data-testid=\"stSidebar\"] .{button_class} button::after {{"
            f"content:\"{badge}\";position:absolute;right:14px;top:50%;transform:translateY(-50%);"
            "min-width:28px;padding:4px 8px;text-align:center;border-radius:999px;"
            "background:linear-gradient(145deg,#FF5555,#DE2020);"
            "box-shadow:inset 0 1px 1px rgba(255,255,255,0.45),0 2px 5px rgba(186,28,28,0.24);"
            "color:#FFFFFF;font-size:0.74rem;font-weight:750;line-height:1;box-sizing:border-box;"
            "}"
        )
    return (
        f"section[data-testid=\"stSidebar\"] .{button_class} {{"
        "margin:0 !important;padding:0 !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} [data-testid=\"stButton\"] {{"
        "margin:0 !important;padding:0 !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button {{"
        "position:relative;display:flex;align-items:center;justify-content:flex-start;"
        f"width:100% !important;min-height:{min_height}px !important;"
        f"padding:0 {54 if badge else 16}px 0 78px !important;margin:0 !important;"
        f"background:{background} !important;border:1px solid {border} !important;"
        f"border-radius:10px !important;box-shadow:{shadow} !important;"
        "box-sizing:border-box;text-align:left !important;overflow:hidden;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button:hover {{"
        f"border-color:{border} !important;box-shadow:{shadow} !important;transform:translateY(-1px);"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button [data-testid=\"stMarkdownContainer\"] {{"
        "position:absolute !important;left:78px !important;right:16px !important;top:50% !important;"
        "transform:translateY(-50%) !important;width:auto !important;text-align:left !important;"
        "display:block !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button p {{"
        f"color:{text_colour} !important;font-size:1rem !important;font-weight:650 !important;"
        "line-height:1.2 !important;text-align:left !important;margin:0 !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button::before {{"
        f"{before}position:absolute;left:18px;top:50%;transform:translateY(-50%);display:block;"
        "}"
        f"{badge_rule}"
    )


def _sidebar_nav_button(
    label: str,
    page: str,
    marker: str,
    active: bool,
    icon_name: str | None = None,
    material_icon: str | None = None,
    icon_colour: str = "#1974FF",
    icon_size: int = 40,
    badge: int | None = None,
    min_height: int = 56,
) -> None:
    """Render a styled sidebar nav button without browser navigation."""
    button_key = f"nav_card_{page}"
    st.button(
        label,
        key=button_key,
        use_container_width=True,
        on_click=_go_to_page,
        args=(page,),
    )


def _render_secondary_nav() -> None:
    """Render the compact, illustrated Tools navigation rows."""
    current = st.session_state.get("active_page", "dashboard")
    count = _alert_badge_count()

    items = (
        ("Alerts", "alerts", "alerts-bell.png"),
        ("Reports", "reports", "reports-bars.png"),
        ("Trade Journal", "trade_journal", "journal-book.png"),
        ("Settings", "settings", "settings-gear.png"),
    )
    markers = {
        "alerts": "0.120px",
        "reports": "0.121px",
        "trade_journal": "0.122px",
        "settings": "0.123px",
    }
    st.markdown(
        "<style>"
        + "".join(
            _sidebar_nav_button_css(
                page,
                page == current,
                icon_name=icon_name,
                icon_size=40,
                badge=count if page == "alerts" and count else None,
                min_height=54,
            )
            for _, page, icon_name in items
        )
        + "</style>",
        unsafe_allow_html=True,
    )
    for label, page, icon_name in items:
        _sidebar_nav_button(
            label,
            page,
            markers[page],
            page == current,
            icon_name=icon_name,
            icon_size=40,
            badge=count if page == "alerts" and count else None,
            min_height=54,
        )


def _render_primary_navigation(current_page: str) -> None:
    """Render precise, left-aligned buttons for the five primary destinations.

    Native Streamlit buttons centre their content and constrain icon styling.
    These buttons keep Streamlit routing in session state while CSS supplies
    the illustrated treatment.
    """
    pattern_active = current_page in {
        "pattern_scanner",
        "pattern_results",
        "pattern_detail",
    }
    items = (
        ("Dashboard", "dashboard", "home", "#1473FF", "#0754D5", current_page == "dashboard"),
        ("Watchlists", "watchlist_manager", "format_list_bulleted", "#2767DE", "#1748A7", current_page == "watchlist_manager"),
        ("Pattern Scanner", "pattern_scanner", "center_focus_strong", "#7549E6", "#4B20BB", pattern_active),
        ("Signals", "signals", "bolt", "#16C86B", "#078C49", current_page == "signals"),
    )
    markers = {
        "dashboard": "0.130px",
        "watchlist_manager": "0.131px",
        "pattern_scanner": "0.132px",
        "signals": "0.133px",
        "research": "0.134px",
    }
    st.markdown(
        "<div style='margin:14px 2px 0;font-size:0.84rem;font-weight:700;"
        "letter-spacing:0.55px;color:#66738A;'>MAIN NAVIGATION</div>",
        unsafe_allow_html=True,
    )
    nav_css: list[str] = []
    for label, page, icon, colour, shadow_colour, active in items:
        asset_icons = {
            "dashboard": ("dashboard-home.png", 54),
            "signals": ("signal-bolt.png", 42),
        }
        if page in asset_icons:
            asset_name, asset_size = asset_icons[page]
            nav_css.append(
                _sidebar_nav_button_css(
                    page,
                    active,
                    icon_name=asset_name,
                    icon_size=asset_size,
                    min_height=58,
                )
            )
        else:
            nav_css.append(
                _sidebar_nav_button_css(
                    page,
                    active,
                    material_icon=icon,
                    icon_colour=colour,
                    icon_size=31,
                    min_height=58,
                )
            )
    nav_css.append(
        _sidebar_nav_button_css(
            "research",
            current_page == "research",
            icon_name="research-flask.png",
            icon_size=42,
            min_height=48,
        )
    )
    st.markdown("<style>" + "".join(nav_css) + "</style>", unsafe_allow_html=True)
    for label, page, icon, colour, shadow_colour, active in items:
        asset_icons = {
            "dashboard": ("dashboard-home.png", 54),
            "signals": ("signal-bolt.png", 42),
        }
        if page in asset_icons:
            asset_name, asset_size = asset_icons[page]
            _sidebar_nav_button(
                label,
                page,
                markers[page],
                active,
                icon_name=asset_name,
                icon_size=asset_size,
                min_height=58,
            )
        else:
            _sidebar_nav_button(
                label,
                page,
                markers[page],
                active,
                material_icon=icon,
                icon_colour=colour,
                icon_size=31,
                min_height=58,
            )
    _sidebar_nav_button(
        "Research Engine",
        "research",
        markers["research"],
        current_page == "research",
        icon_name="research-flask.png",
        icon_size=42,
        min_height=48,
    )


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


def _nav_group_label(label: str) -> None:
    """Render the quiet uppercase label used to group sidebar destinations."""
    st.markdown(
        f"<div style='margin:14px 2px 7px;font-size:0.78rem;font-weight:700;"
        f"letter-spacing:0.72px;color:#66738A;'>{label}</div>",
        unsafe_allow_html=True,
    )


def _watchlist_setup_summary() -> str:
    """Short face text for the watchlist setup card."""
    source = st.session_state.get("watchlist_source", "Index Watchlists")
    if source == "My Watchlists":
        return "Custom watchlist"
    if source == "All NSE Stocks":
        batch = st.session_state.get("selected_nse_batch", "All NSE")
        return str(batch)

    selected = st.session_state.get("selected_predefined_watchlist", "Nifty 50")
    try:
        predefined = load_predefined_watchlists()
        match = next((w for w in predefined if w.get("name") == selected), None)
        if match:
            return f"{selected} • {len(match.get('symbols', []))}"
    except Exception:
        pass
    return str(selected)


def _toggle_setup_panel(key: str) -> None:
    """Open one Analysis Setup panel, or close it if it is already open."""
    current = st.session_state.get("sidebar_setup_panel", "")
    st.session_state["sidebar_setup_panel"] = "" if current == key else key


def _setup_card(
    key: str,
    title: str,
    detail: str,
    icon_name: str,
    tone: str,
    selected: bool,
) -> None:
    """Render one clickable Analysis Setup face without URL navigation."""
    button_key = f"setup_card_{key}"
    st.button(
        title,
        key=button_key,
        use_container_width=True,
        on_click=_toggle_setup_panel,
        args=(key,),
    )


def _setup_card_css(
    key: str,
    detail: str,
    icon_name: str,
    tone: str,
    selected: bool,
) -> str:
    """CSS for one Analysis Setup card button."""
    button_class = f"st-key-setup_card_{key}"
    palette = {
        "blue": ("#EFF7FF", "#80B6FF", "#0B58F0", "#D8ECFF"),
        "green": ("#EEFFF5", "#7DD7A7", "#079245", "#D9F8E5"),
        "purple": ("#F8F1FF", "#C6A5FF", "#612CE4", "#EEE1FF"),
        "orange": ("#FFF7ED", "#FFB577", "#EB5B12", "#FFE4C6"),
    }
    bg, border, accent, _ = palette.get(tone, palette["blue"])
    border_width = "1.5px" if selected else "1px"
    shadow = (
        f"inset 3px 0 0 {accent},0 7px 16px rgba(24,54,97,0.13)"
        if selected else "0 4px 12px rgba(24,54,97,0.08)"
    )
    detail_css = detail.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f"section[data-testid=\"stSidebar\"] .{button_class} {{"
        "margin:0 !important;padding:0 !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} [data-testid=\"stButton\"] {{"
        "margin:0 !important;padding:0 !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button {{"
        "position:relative;display:flex;align-items:center;justify-content:flex-start;"
        "width:100% !important;min-height:82px !important;"
        f"padding:12px 44px 12px 86px !important;margin:0 !important;background:{bg} !important;"
        f"border:{border_width} solid {border} !important;border-radius:10px !important;"
        f"box-shadow:{shadow} !important;box-sizing:border-box;color:#17233B !important;"
        "text-align:left !important;overflow:hidden;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button:hover {{"
        f"border-color:{border} !important;box-shadow:{shadow} !important;transform:translateY(-1px);"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button [data-testid=\"stMarkdownContainer\"] {{"
        "position:absolute !important;left:86px !important;right:44px !important;top:50% !important;"
        "transform:translateY(-50%) !important;width:auto !important;text-align:left !important;"
        "display:block !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button p {{"
        f"color:{accent} !important;font-size:1rem !important;font-weight:750 !important;"
        "line-height:1.18 !important;text-align:left !important;"
        "white-space:normal;margin:0 !important;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button p::after {{"
        f"content:\"{detail_css}\";display:block;margin-top:4px;color:#4C586D;"
        "font-size:0.84rem;font-weight:500;line-height:1.22;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button::before {{"
        f"content:\"\";position:absolute;left:14px;top:50%;width:56px;height:56px;"
        f"transform:translateY(-50%);background:url('/app/static/icons/{icon_name}') center / contain no-repeat;"
        "}"
        f"section[data-testid=\"stSidebar\"] .{button_class} button::after {{"
        f"content:\"›\";position:absolute;right:17px;top:50%;transform:translateY(-54%);"
        f"color:{accent};font-size:1.8rem;font-weight:700;line-height:1;"
        "}"
    )


def _setup_panel_header(
    title: str,
    detail: str,
    icon_name: str,
    tone: str,
    badge: str = "",
) -> None:
    """Render the opened Analysis Setup panel header in the same visual language."""
    palette = {
        "blue": ("#F6FBFF", "#D8EAFF", "#0B58F0"),
        "green": ("#F3FFF8", "#D7F3E1", "#079245"),
        "purple": ("#FBF7FF", "#E8D9FF", "#612CE4"),
        "orange": ("#FFF9F1", "#FFE0C2", "#EB5B12"),
    }
    bg, border, accent = palette.get(tone, palette["blue"])
    badge_html = (
        f"<span style='margin-left:auto;border-radius:999px;padding:3px 9px;"
        f"background:#FFFFFF;border:1px solid {border};color:{accent};"
        f"font-size:0.72rem;font-weight:700;line-height:1;'>{badge}</span>"
        if badge
        else ""
    )
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;margin:0 0 8px;"
        f"padding:9px 10px;background:{bg};border:1px solid {border};"
        "border-radius:10px;box-shadow:inset 0 1px 0 rgba(255,255,255,0.65);'>"
        "<span style='display:flex;align-items:center;justify-content:center;width:38px;flex:0 0 38px;'>"
        f"<img src='/app/static/icons/{icon_name}' alt='' "
        "style='display:block;width:36px;height:36px;object-fit:contain;' /></span>"
        "<span style='display:flex;flex-direction:column;gap:1px;min-width:0;'>"
        f"<span style='font-size:0.9rem;font-weight:750;color:{accent};line-height:1.15;'>{title}</span>"
        f"<span style='font-size:0.78rem;font-weight:500;color:#5D6676;line-height:1.2;'>{detail}</span>"
        f"</span>{badge_html}</div>",
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
    8. Run Analysis action
    9. Notifications
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

            _render_primary_navigation(st.session_state.get("active_page", "dashboard"))

        # ---------- Panel 2: tools and analysis controls ----------
        with st.container(border=True):
            _panel_marker()
            _nav_group_label("TOOLS")
            _render_secondary_nav()

            _nav_group_label("ANALYSIS SETUP")
            _setup_panel = st.session_state.get("sidebar_setup_panel", "")
            _setup_panel = _setup_panel if _setup_panel in {"data", "watchlist", "screener", "strategy"} else ""

            # ---------- Data Source ----------
            _src_now = st.session_state.get("selected_data_source", "Yahoo Finance")
            _watchlist_face = _watchlist_setup_summary()
            _active_filters = _count_active_screener_filters()
            _screener_face = _screener_summary()
            _tt_face, _detail_face = _strategy_summary()
            _setup_css_specs = (
                ("data", _src_now, "setup-data-source.png", "blue", _setup_panel == "data"),
                ("watchlist", _watchlist_face, "setup-watchlist.png", "green", _setup_panel == "watchlist"),
                ("screener", _screener_face, "setup-filter.png", "purple", _setup_panel == "screener"),
                ("strategy", f"{_tt_face} · {_detail_face}", "setup-strategy.png", "orange", _setup_panel == "strategy"),
            )
            st.markdown(
                "<style>"
                + "".join(_setup_card_css(*spec) for spec in _setup_css_specs)
                + "</style>",
                unsafe_allow_html=True,
            )
            _setup_card(
                "data",
                "Data Source",
                _src_now,
                "setup-data-source.png",
                "blue",
                _setup_panel == "data",
            )
            if _setup_panel == "data":
                # A source needing no credentials is always connected, so the
                # badge can be decided before the selectbox renders.
                _needs_creds = CREDENTIALS_REQUIRED.get(_src_now, [])
                _creds_now = st.session_state.get("credentials", {}).get(_src_now, {})
                _is_live = not _needs_creds or all(_creds_now.get(f) for f in _needs_creds)
                _setup_panel_header(
                    "Data Source",
                    _src_now,
                    "setup-data-source.png",
                    "blue",
                    badge="live" if _is_live else "setup",
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
            _setup_card(
                "watchlist",
                "Watchlist / Universe",
                _watchlist_face,
                "setup-watchlist.png",
                "green",
                _setup_panel == "watchlist",
            )
            if _setup_panel == "watchlist":
                _setup_panel_header(
                    "Watchlist / Universe",
                    _watchlist_setup_summary(),
                    "setup-watchlist.png",
                    "green",
                )
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
                if wl_source != _current_wl:
                    save_preferences({"watchlist_source": wl_source})

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
                        if selected_pd != current_pd:
                            save_preferences({"selected_predefined_watchlist": selected_pd})
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
                    if selected_batch != current_batch:
                        save_preferences({"selected_nse_batch": selected_batch})
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
            _setup_card(
                "screener",
                "Screener / Filters",
                _screener_face,
                "setup-filter.png",
                "purple",
                _setup_panel == "screener",
            )
            if _setup_panel == "screener":
                _setup_panel_header(
                    "Screener / Filters",
                    _screener_face,
                    "setup-filter.png",
                    "purple",
                    badge=f"{_active_filters} on" if _active_filters else "",
                )
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
            _setup_card(
                "strategy",
                "Strategy",
                f"{_tt_face} · {_detail_face}",
                "setup-strategy.png",
                "orange",
                _setup_panel == "strategy",
            )
            if _setup_panel == "strategy":
                _setup_panel_header(
                    "Strategy",
                    f"{_tt_face} · {_detail_face}",
                    "setup-strategy.png",
                    "orange",
                )
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

            # Small breathing space before the primary action, without a rule.
            st.markdown("<div style='height:4px;'></div>", unsafe_allow_html=True)

            # ---------- Run Analysis ----------
            st.markdown(
                """
                <style>
                section[data-testid="stSidebar"] .st-key-run_analysis_cta,
                section[data-testid="stSidebar"] .st-key-run_analysis_busy {
                    margin: 0 !important;
                    padding: 0 !important;
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_cta [data-testid="stButton"],
                section[data-testid="stSidebar"] .st-key-run_analysis_busy [data-testid="stButton"] {
                    margin: 0 !important;
                    padding: 0 !important;
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_cta button,
                section[data-testid="stSidebar"] .st-key-run_analysis_busy button {
                    position: relative;
                    width: 100% !important;
                    min-height: 60px !important;
                    padding: 0 48px 0 86px !important;
                    border: 0 !important;
                    border-radius: 10px !important;
                    background: linear-gradient(135deg, #258DFF 0%, #0758F4 48%, #0038D9 100%) !important;
                    box-shadow:
                        inset 0 1px 1px rgba(255,255,255,0.36),
                        inset 0 -2px 4px rgba(0,30,145,0.28),
                        0 8px 16px rgba(11,87,231,0.34) !important;
                    text-align: left !important;
                    overflow: hidden;
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_cta button:hover {
                    transform: translateY(-1px);
                    box-shadow:
                        inset 0 1px 1px rgba(255,255,255,0.42),
                        inset 0 -2px 4px rgba(0,30,145,0.26),
                        0 10px 18px rgba(11,87,231,0.38) !important;
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_cta button::before,
                section[data-testid="stSidebar"] .st-key-run_analysis_busy button::before {
                    content: "🚀";
                    position: absolute;
                    left: 22px;
                    top: 50%;
                    transform: translateY(-52%);
                    font-size: 36px;
                    line-height: 1;
                    filter: drop-shadow(0 4px 4px rgba(0,31,117,0.30));
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_cta button::after,
                section[data-testid="stSidebar"] .st-key-run_analysis_busy button::after {
                    content: "›";
                    position: absolute;
                    right: 22px;
                    top: 50%;
                    transform: translateY(-54%);
                    color: #FFFFFF;
                    font-size: 2rem;
                    font-weight: 800;
                    line-height: 1;
                    opacity: 0.95;
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_cta button [data-testid="stMarkdownContainer"],
                section[data-testid="stSidebar"] .st-key-run_analysis_busy button [data-testid="stMarkdownContainer"] {
                    position: absolute !important;
                    left: 86px !important;
                    right: 48px !important;
                    top: 50% !important;
                    transform: translateY(-50%) !important;
                    width: auto !important;
                    display: block !important;
                    text-align: left !important;
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_cta button p,
                section[data-testid="stSidebar"] .st-key-run_analysis_busy button p {
                    color: #FFFFFF !important;
                    font-size: 1.05rem !important;
                    font-weight: 750 !important;
                    line-height: 1.1 !important;
                    margin: 0 !important;
                    text-align: left !important;
                }
                section[data-testid="stSidebar"] .st-key-run_analysis_busy button {
                    opacity: 0.72;
                    cursor: wait;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            analysing = st.session_state.get("analysing", False)
            if analysing:
                st.button(
                    "Analysing...",
                    key="run_analysis_busy",
                    disabled=True,
                    use_container_width=True,
                    type="primary",
                )
            else:
                if st.button(
                    "Run Analysis",
                    key="run_analysis_cta",
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
    # red-means-supply on the charts.
    if open_:
        bg, border, fg, muted = "#EEFFF5", "#86D7A7", "#138A4A", "#24683F"
        title, glyph, accent = "Market Open", "light_mode", "#20B66E"
    else:
        bg, border, fg, muted = "#FFF9F0", "#FFB56F", "#F05B13", "#5E3030"
        title, glyph, accent = "Market Closed", "schedule", "#FF8A00"

    icon = "font-family:'Material Symbols Rounded';line-height:1;"
    status_detail = countdown or ("Market session in progress" if open_ else "Market closed")
    st.markdown(
        # Brand row: deliberately larger and more graphic than an ordinary
        # navigation label so the app identity is clear immediately.
        "<div style='display:flex;align-items:center;gap:14px;margin:4px 2px 19px;'>"
        "<span style='width:58px;height:58px;border-radius:13px;"
        "background:linear-gradient(145deg,#328BFF 0%,#0A39D9 78%);"
        "box-shadow:inset 0 1px 1px rgba(255,255,255,0.45),0 7px 14px rgba(18,72,210,0.28);"
        "display:flex;align-items:center;justify-content:center;flex:none;'>"
        f"<span style=\"{icon}font-size:36px;color:#FFFFFF;\">trending_up</span></span>"
        "<span style='font-size:1.75rem;font-weight:700;letter-spacing:0;color:#17233B;'>"
        "Market Lens</span></div>"
        # Status card uses a subtle bar-chart texture at the right edge. It is
        # purely decorative: the open/closed state and actual time remain text.
        f"<div style='position:relative;overflow:hidden;background:{bg};border:1px solid {border};"
        "border-radius:14px;padding:18px 16px;margin-bottom:16px;min-height:96px;'>"
        "<div style='position:absolute;right:-6px;bottom:0;width:48%;height:100%;opacity:0.13;"
        f"background:linear-gradient(135deg,transparent 25%,{accent} 100%);'></div>"
        f"<div style='position:absolute;right:12px;bottom:15px;display:flex;align-items:flex-end;gap:5px;height:54px;opacity:0.16;color:{accent};'>"
        "<span style='width:12px;height:17px;background:currentColor;border-radius:3px 3px 0 0;'></span>"
        "<span style='width:12px;height:27px;background:currentColor;border-radius:3px 3px 0 0;'></span>"
        "<span style='width:12px;height:37px;background:currentColor;border-radius:3px 3px 0 0;'></span>"
        "<span style='width:12px;height:48px;background:currentColor;border-radius:3px 3px 0 0;'></span></div>"
        "<div style='position:relative;z-index:1;display:flex;align-items:center;gap:14px;'>"
        f"<span style='width:58px;height:58px;border-radius:50%;background:radial-gradient(circle at 31% 28%,#FFD46B 0%,#FF9C12 44%,#E66700 73%,#B94A00 100%);"
        "box-shadow:inset 0 2px 3px rgba(255,255,255,0.55),inset 0 -3px 4px rgba(146,56,0,0.28),0 5px 9px rgba(178,83,7,0.22);"
        "display:flex;align-items:center;justify-content:center;flex:none;'>"
        f"<span style=\"{icon}font-size:36px;color:#FFFFFF;text-shadow:0 2px 2px rgba(115,52,0,0.36);\">{glyph}</span></span>"
        "<div style='min-width:0;'>"
        f"<div style='font-size:1.08rem;font-weight:700;color:{fg};line-height:1.2;'>{title}</div>"
        f"<div style='margin-top:5px;font-size:0.87rem;font-weight:600;color:{muted};'>{status_detail}</div>"
        f"<div style='margin-top:6px;display:flex;align-items:center;gap:6px;color:#5E6069;font-size:0.76rem;'>"
        f"<span style=\"{icon}font-size:16px;color:{accent};\">calendar_month</span>"
        f"{now.strftime('%d %b %Y · %H:%M')} IST</div></div></div>"
        "</div>",
        unsafe_allow_html=True,
    )
