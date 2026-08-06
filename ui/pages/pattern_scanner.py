"""Pattern Scanner setup page."""

from __future__ import annotations

import html
from typing import Any

import streamlit as st

from analysis.pattern_scanner import (
    DETECTION_STAGES,
    PATTERN_FAMILIES,
    SCOPES,
    SUGGESTED_FILTERS,
    TIMEFRAMES,
    TRIANGLE_TYPES,
    default_settings,
)
from ui.components.panels import filter_chip, page_title, section_title, spacer, stat_card
from ui.pages.pattern_common import (
    pattern_counts,
    resolve_pattern_universe,
    serialise_settings,
)


def render_pattern_scanner() -> None:
    """Render the Pattern Scanner setup page."""
    _seed_setup_state()
    settings = _current_settings()
    universe_label, stocks = resolve_pattern_universe(settings["scope"])
    previous = st.session_state.get("pattern_scan_results", []) or []
    counts = pattern_counts(previous)

    _render_header()
    spacer(8)
    _render_summary_cards(settings, universe_label, len(stocks), counts)
    spacer(8)

    left, right = st.columns([1.4, 1])
    with left:
        with st.container(border=True):
            _render_pattern_selection(settings)
    with right:
        with st.container(border=True):
            _render_logic_preview()

    spacer(10)
    a, b, c, d = st.columns([1.05, 1.1, 1, 1])
    with a:
        with st.container(border=True):
            _render_watchlist_selection(universe_label, stocks)
    with b:
        with st.container(border=True):
            _render_suggested_filters()
    with c:
        with st.container(border=True):
            _render_pattern_notes()
    with d:
        with st.container(border=True):
            _render_after_scan()

    spacer(10)
    _render_action_area(universe_label, stocks)


def _seed_setup_state() -> None:
    saved = st.session_state.get("pattern_scan_settings") or default_settings()
    family = saved.get("pattern_family", "Triangle Patterns")
    ptype = saved.get("pattern_type", "All Triangle Patterns")
    timeframe = saved.get("timeframe", "Daily")
    scope = saved.get("scope", "Current Watchlist")
    stages = [s for s in saved.get("detection_stages", list(DETECTION_STAGES)) if s in DETECTION_STAGES]
    st.session_state.setdefault("ps_family", family if family in PATTERN_FAMILIES else "Triangle Patterns")
    st.session_state.setdefault("ps_type", ptype if ptype in TRIANGLE_TYPES else "All Triangle Patterns")
    st.session_state.setdefault("ps_stages", stages or list(DETECTION_STAGES))
    st.session_state.setdefault("ps_timeframe", timeframe if timeframe in TIMEFRAMES else "Daily")
    st.session_state.setdefault("ps_scope", scope if scope in SCOPES else "Current Watchlist")
    st.session_state.setdefault("ps_recent_only", saved.get("recent_only", True))
    st.session_state.setdefault("ps_freshness", saved.get("freshness_window", 5))
    st.session_state.setdefault(
        "ps_require_volatility", saved.get("require_volatility_contraction", False)
    )
    st.session_state.setdefault("ps_require_zone", saved.get("require_zone_context", False))
    selected = set(saved.get("selected_filters") or [])
    for label in SUGGESTED_FILTERS:
        st.session_state.setdefault(_filter_key(label), label in selected)


def _current_settings() -> dict[str, Any]:
    selected = [label for label in SUGGESTED_FILTERS if st.session_state.get(_filter_key(label))]
    return serialise_settings(
        {
            "pattern_family": st.session_state.get("ps_family", "Triangle Patterns"),
            "pattern_type": st.session_state.get("ps_type", "All Triangle Patterns"),
            "detection_stages": st.session_state.get("ps_stages", list(DETECTION_STAGES)),
            "timeframe": st.session_state.get("ps_timeframe", "Daily"),
            "scope": st.session_state.get("ps_scope", "Current Watchlist"),
            "recent_only": st.session_state.get("ps_recent_only", True),
            "freshness_window": st.session_state.get("ps_freshness", 5),
            "require_volatility_contraction": st.session_state.get(
                "ps_require_volatility", False
            ),
            "require_zone_context": st.session_state.get("ps_require_zone", False),
            "selected_filters": selected,
        }
    )


def _render_header() -> None:
    left, right = st.columns([2.2, 1.2])
    with left:
        page_title(
            "Pattern Scanner",
            "Scan watchlists for recent chart-pattern formations and breakouts.",
            icon="search",
        )
    with right:
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
        c1, c2 = st.columns([1, 1.15])
        with c1:
            st.caption(f"Last Scan: {st.session_state.get('_pattern_last_scan_label', 'Not run')}")
            st.toggle("Auto Refresh", key="pattern_auto_refresh")
        with c2:
            has_results = bool(st.session_state.get("pattern_scan_results"))
            if st.button(
                "Pattern Scan Results",
                icon=":material/table_view:",
                use_container_width=True,
                disabled=not has_results,
                help="Open the latest Pattern Scanner results.",
            ):
                st.session_state.active_page = "pattern_results"
                st.rerun()


def _render_summary_cards(
    settings: dict[str, Any],
    universe_label: str,
    universe_size: int,
    counts: dict[str, int],
) -> None:
    cols = st.columns(6)
    cards = [
        ("Scan Universe", str(universe_size), universe_label, "layers", "bullish"),
        ("Pattern Family", settings["pattern_family"], "Group: Chart Patterns", "layers", "info"),
        ("Active Timeframe", settings["timeframe"], "Higher timeframe bias", "calendar", "purple"),
        ("Fresh Signals", str(counts["total"]), "Last pattern scan", "activity", "bullish"),
        ("Breakout Candidates", str(counts["breakout"]), "Breakout confirmed", "trend_up", "warning"),
        ("Near Apex Setups", str(counts["near_apex"]), "Approaching apex", "target", "purple"),
    ]
    for col, (label, value, sub, icon, tone) in zip(cols, cards):
        with col:
            stat_card(label, value, sub, icon, tone=tone)  # type: ignore[arg-type]


def _render_pattern_selection(settings: dict[str, Any]) -> None:
    section_title("Pattern Selection")
    top = st.columns([1.1, 1.1, 1.6])
    with top[0]:
        st.selectbox("Pattern Family", PATTERN_FAMILIES, key="ps_family")
    with top[1]:
        disabled = st.session_state.get("ps_family") != "Triangle Patterns"
        st.selectbox(
            "Pattern Type",
            TRIANGLE_TYPES,
            key="ps_type",
            disabled=disabled,
        )
    with top[2]:
        st.multiselect(
            "Detection Stage",
            DETECTION_STAGES,
            key="ps_stages",
        )

    mid = st.columns([1.1, 1.15, 1.15])
    with mid[0]:
        st.radio("Timeframe", TIMEFRAMES, horizontal=True, key="ps_timeframe")
    with mid[1]:
        st.selectbox("Scope", SCOPES, key="ps_scope")
    with mid[2]:
        st.toggle("Recent only", key="ps_recent_only")
        st.toggle("Require zone context", key="ps_require_zone")

    bottom = st.columns([1.2, 1])
    with bottom[0]:
        st.slider(
            "Freshness window: last candles",
            min_value=1,
            max_value=5,
            key="ps_freshness",
        )
    with bottom[1]:
        st.toggle("Require volatility contraction", key="ps_require_volatility")
        if st.session_state.get("ps_family") != "Triangle Patterns":
            st.info("Only Triangle Patterns are implemented in this first scanner release.")


def _render_logic_preview() -> None:
    section_title("Pattern Logic Preview")
    cols = st.columns(3)
    previews = [
        ("Symmetrical Triangle", "Lower highs + higher lows", "sym"),
        ("Ascending Triangle", "Flat top + rising lows", "asc"),
        ("Descending Triangle", "Falling highs + flat base", "desc"),
    ]
    for col, (title, caption, kind) in zip(cols, previews):
        with col:
            _logic_card(title, caption, kind)
    st.markdown(
        "<div style='border-top:1px solid #EEF0F3;margin-top:10px;padding-top:10px;"
        "text-align:center;font-size:0.82rem;color:#2F5FE0;font-weight:600;'>"
        "View all triangle patterns logic -></div>",
        unsafe_allow_html=True,
    )


def _render_watchlist_selection(universe_label: str, stocks: list[Any]) -> None:
    section_title("Watchlist Selection")
    filter_chip("Selected Universe", universe_label, icon="layers")
    total = len(stocks)
    c1, c2, c3 = st.columns(3)
    c1.metric("Total", total)
    c2.metric("Active", total)
    c3.metric("On Watch", len(st.session_state.get("pattern_watch_symbols", set())))
    if st.button("Manage Watchlists", icon=":material/arrow_forward:", key="ps_manage_wl"):
        st.session_state.active_page = "watchlist_manager"
        st.rerun()


def _render_suggested_filters() -> None:
    section_title("Suggested Filters")
    cols = st.columns(2)
    for idx, label in enumerate(SUGGESTED_FILTERS):
        with cols[idx % 2]:
            st.checkbox(label, key=_filter_key(label))


def _render_pattern_notes() -> None:
    section_title("Pattern Notes")
    notes = [
        "Triangles show contraction, not guaranteed direction.",
        "Symmetrical Triangle = lower highs + higher lows.",
        "Ascending Triangle = flat top + rising lows.",
        "Descending Triangle = falling highs + flat base.",
        "Best used with volume contraction and demand/supply zone context.",
        "Direction should be confirmed only after breakout or breakdown.",
    ]
    for note in notes:
        st.markdown(
            f"<div style='font-size:0.8rem;padding:3px 0;color:#4A5361;'>"
            f"<span style='color:#2F5FE0;'>&bull;</span> {html.escape(note)}</div>",
            unsafe_allow_html=True,
        )


def _render_after_scan() -> None:
    section_title("What happens after scan?")
    steps = [
        ("Select pattern", "Choose family, type and detection stage"),
        ("Scan watchlist", "Search recent appearances"),
        ("Review recent matches", "Filter and validate setups"),
        ("Open stock detail", "Inspect chart and levels"),
    ]
    for idx, (title, caption) in enumerate(steps, start=1):
        st.markdown(
            f"<div style='display:flex;gap:10px;padding:4px 0;'>"
            f"<span style='display:inline-flex;align-items:center;justify-content:center;"
            f"width:24px;height:24px;border-radius:50%;background:#EEF4FF;"
            f"color:#2F5FE0;font-size:0.78rem;font-weight:800;flex:0 0 24px;'>{idx}</span>"
            f"<span><b style='font-size:0.82rem;color:#26313F;'>{html.escape(title)}</b>"
            f"<div style='font-size:0.74rem;color:#7A7F87;'>{html.escape(caption)}</div></span>"
            f"</div>",
            unsafe_allow_html=True,
        )


def _render_action_area(universe_label: str, stocks: list[Any]) -> None:
    settings = _current_settings()
    with st.container(border=True):
        left, right = st.columns([2.3, 1.6])
        with left:
            st.markdown(
                "<div style='display:flex;gap:14px;align-items:center;'>"
                "<span style='display:inline-flex;align-items:center;justify-content:center;"
                "width:46px;height:46px;border-radius:12px;background:#F4F7FE;"
                "border:1px solid #DCE6FA;color:#2F5FE0;font-weight:800;'>&#10003;</span>"
                "<span><b style='font-size:1.05rem;color:#16233A;'>Ready to scan for triangle patterns?</b>"
                f"<div style='font-size:0.8rem;color:#6B7280;'>"
                f"{html.escape(universe_label)} &middot; {len(stocks)} symbols &middot; "
                f"{html.escape(settings['timeframe'])} &middot; last {settings['freshness_window']} candles</div></span>"
                "</div>",
                unsafe_allow_html=True,
            )
        with right:
            b1, b2 = st.columns([1, 1.35])
            with b1:
                if st.button("Save Template", icon=":material/bookmark:", use_container_width=True):
                    st.session_state["pattern_saved_template"] = settings
                    st.success("Template saved for this session.")
            with b2:
                disabled = settings["pattern_family"] != "Triangle Patterns" or not stocks
                if st.button(
                    "Start Pattern Scan",
                    icon=":material/play_arrow:",
                    type="primary",
                    use_container_width=True,
                    disabled=disabled,
                ):
                    st.session_state["pattern_scan_settings"] = settings
                    st.session_state["pattern_scan_universe_label"] = universe_label
                    st.session_state["pattern_scan_id"] = ""
                    st.session_state["selected_pattern_symbol"] = None
                    st.session_state["pattern_scanning"] = True
                    st.session_state.active_page = "pattern_results"
                    st.rerun()
        if not stocks:
            st.warning("Select a non-empty watchlist or universe before starting the scan.")


def _logic_card(title: str, caption: str, kind: str) -> None:
    svg = _triangle_svg(kind)
    st.markdown(
        f"<div style='border:1px solid #E7E9ED;border-radius:10px;padding:12px;"
        f"min-height:156px;background:#FFFFFF;text-align:center;'>"
        f"<div style='font-size:0.82rem;font-weight:700;color:#26313F;margin-bottom:8px;'>"
        f"{html.escape(title)}</div>{svg}"
        f"<div style='font-size:0.74rem;color:#6B7280;margin-top:6px;'>"
        f"{html.escape(caption)}</div></div>",
        unsafe_allow_html=True,
    )


def _triangle_svg(kind: str) -> str:
    if kind == "asc":
        upper = "M20 24 L112 24"
        lower = "M20 82 L112 36"
    elif kind == "desc":
        upper = "M20 24 L112 82"
        lower = "M20 82 L112 82"
    else:
        upper = "M20 24 L112 56"
        lower = "M20 82 L112 56"
    return (
        "<svg width='132' height='92' viewBox='0 0 132 92' fill='none' "
        "xmlns='http://www.w3.org/2000/svg' style='display:block;margin:0 auto;'>"
        "<polyline points='24,80 36,24 48,78 60,34 72,72 84,43 96,66 108,54' "
        "stroke='#5B6472' stroke-width='2' fill='none'/>"
        f"<path d='{upper}' stroke='#2F5FE0' stroke-width='2' stroke-dasharray='5 4'/>"
        f"<path d='{lower}' stroke='#22A55B' stroke-width='2' stroke-dasharray='5 4'/>"
        "</svg>"
    )


def _filter_key(label: str) -> str:
    return "ps_filter_" + label.lower().replace(" ", "_").replace("/", "_").replace("&", "and")
