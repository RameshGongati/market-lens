"""Market Lens — Main Streamlit Entry Point."""

import streamlit as st

from config.credentials import load_credentials
from config.preferences import load_preferences
from config.settings import SUPPORTED_DATA_SOURCES
from config.trading_config import ENHANCERS, PRIMARY_STRATEGIES, TRADING_TYPES
from storage.database import (
    get_pattern_scan,
    init_db,
    load_latest_analysis_snapshot,
)
from ui.components.sidebar import render_sidebar
from ui.pages.alerts_page import render_alerts_page
from ui.pages.analysis_results import render_analysis_results
from ui.pages.dashboard import render_detail_view
from ui.pages.market_overview import render_market_overview
from ui.pages.market_heatmap import render_market_heatmap
from ui.pages.pattern_detail import render_pattern_detail
from ui.pages.pattern_results import render_pattern_results
from ui.pages.pattern_scanner import render_pattern_scanner
from ui.pages.placeholders import render_trade_journal_page
from ui.pages.gap_signals import render_signals_page
from ui.pages.options_trade_lab import render_options_trade_lab
from ui.pages.reports_page import render_reports_page
from ui.pages.research_page import render_research_page
from ui.pages.watchlist_manager import render_watchlist_manager
from ui.pages.settings import render_settings
from utils.logger import get_logger

logger = get_logger(__name__)


def init_session_state() -> None:
    """Initialise all required Streamlit session state keys.

    User-facing sidebar selections begin from saved preferences. This matters
    for pages reached through a URL, including a clickable heatmap tile: that
    navigation can create a fresh Streamlit session, so temporary session
    state would otherwise reset the selected F&O/watchlist to its default.

    A stock opened in a new browser tab still adopts the originating analysis
    context from the URL afterwards (see the ``?stock=`` handler in
    :func:`main`), which remains the authoritative detail-chart context.
    """
    prefs = load_preferences()
    defaults: dict = {
        "active_page": "dashboard",
        "selected_watchlist_id": prefs.get("selected_watchlist_id"),
        "watchlist_source": prefs.get("watchlist_source", "Index Watchlists"),
        "selected_predefined_watchlist": prefs.get(
            "selected_predefined_watchlist", "Nifty 50"
        ),
        "selected_nse_batch": prefs.get("selected_nse_batch", ""),
        # Two-axis analysis model (Trading Type + Primary Strategy + Enhancers).
        "trading_type": prefs.get("trading_type", "Options Trading"),
        "primary_strategy": prefs.get("primary_strategy", "Demand/Supply Zones"),
        "enhancers": list(prefs.get("enhancers", [
            "Fibonacci Confluence", "EMA 20 Confluence",
        ])),
        "selected_data_source": prefs.get("selected_data_source", "Yahoo Finance"),
        "alerts_on": prefs.get("alerts_on", False),
        "credentials": {},
        "analysing": False,
        "selected_stock_symbol": None,
        "analysis_results": {},
        "pattern_scan_settings": {},
        "pattern_scan_id": "",
        "pattern_scan_source_name": "",
        "pattern_scan_results": [],
        "pattern_chart_data": {},
        "pattern_zone_results": {},
        "pattern_scan_errors": {},
        "pattern_scan_fallback_symbols": [],
        "pattern_scanning": False,
        "selected_pattern_symbol": None,
        "pattern_watch_symbols": set(),
        "pattern_reviewed_symbols": set(),
        "heatmap_selected_group": "banks",
        "heatmap_stock_universe": "group:banks",
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

    # Surface styling that the theme file cannot express.
    #
    # Colours themselves live in .streamlit/config.toml; what is here is the
    # layering the theme has no vocabulary for — turning each bordered
    # container in the sidebar into a white card that sits above the tinted
    # sidebar surface. That contrast is what makes the sections read as
    # separate groups rather than one undifferentiated column.
    #
    # These selectors target Streamlit's internal test ids, which can change
    # between releases. If a future upgrade flattens the sidebar cards, this
    # block is the first place to look — the app still works without it, it
    # just loses the card treatment.
    st.markdown(
        """
        <style>
            /* Two outer PANELS — the menu (identity + page nav) and the
               controls below it — take the cream surface. Three tones stack:
               the sidebar rail (darkest), these panels, then the white cards
               inside the second one, which is what makes the cards read as
               raised rather than as outlines on a flat field.

               Panels are identified by the invisible marker that
               sidebar._panel_marker emits as their first child; cards by
               their section header. Both hooks are inline-style values
               because Streamlit's sanitiser strips class and id. */
            section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(
                > [data-testid="stElementContainer"]:first-child
                  [style*="letter-spacing: 0.11px"]
            ) {
                background: linear-gradient(180deg, #F8FBFF 0%, #F5F8FC 100%);
                border-color: #DCE6F2;
                border-radius: 14px;
                box-shadow: 0 7px 22px rgba(31, 63, 104, 0.06);
            }

            /* Sidebar cards: a bordered container whose FIRST child holds a
               section header (see sidebar._section_header). Matching the
               header rather than the container alone is what stops this rule
               hitting every vertical block — the outer blocks contain the
               headers too, but not as a direct child.

               The hook is the header's letter-spacing, not a class, because
               Streamlit's markdown sanitiser strips class and id attributes
               while preserving inline style. Keep the 0.6px value in
               sidebar._section_header and this selector in step; if the cards
               ever go flat, that pair is what came apart. */
            section[data-testid="stSidebar"] [data-testid="stVerticalBlock"]:has(
                > [data-testid="stElementContainer"]:first-child
                  [style*="letter-spacing: 0.6px"]
            ) {
                background-color: #FFFFFF;
                border-color: #E7E7E1;
                border-radius: 10px;
            }

            /* "Dashboard" wraps to two lines in a three-column sidebar row. */
            section[data-testid="stSidebar"] .stButton button {
                white-space: nowrap;
                min-height: 40px;
                border-radius: 8px;
                border-color: #E0E7F0;
                background-color: #FFFFFF;
                box-shadow: 0 2px 7px rgba(36, 67, 104, 0.05);
            }
            section[data-testid="stSidebar"] .stButton button:hover {
                border-color: #9BBEFF;
                background-color: #F6FAFF;
            }
            /* The illustrated sidebar rows are native Streamlit buttons with
               custom faces. Streamlit wraps every button in its own element
               container; if those wrappers keep their default spacing, the
               nav rows look like separate blocks with huge gaps. Keep wrapper
               spacing flat and let the button CSS own the rhythm. */
            section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(
                > [data-testid="stButton"]
            ),
            section[data-testid="stSidebar"] [data-testid="stButton"] {
                margin-top: 0 !important;
                margin-bottom: 0 !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
            }
            section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
                gap: 0.45rem !important;
            }

            /* Streamlit's sidebar defaults run small and low-contrast — its
               captions in particular sit at roughly 0.8rem in a light grey.
               These bring the widget text up to match the hand-rendered
               markup above it, and darken the greys so nothing reads as
               disabled when it is not. */
            section[data-testid="stSidebar"] .stButton button p,
            section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
            section[data-testid="stSidebar"] label p {
                font-size: 0.94rem;
                color: #1E1E23;
            }
            /* Primary buttons (active nav page, Run analysis) are filled with
               the slate accent, so their label must stay white — the darkening
               rule above would otherwise put near-black text on a dark fill. */
            section[data-testid="stSidebar"]
            button[data-testid="stBaseButton-primary"] p {
                color: #FFFFFF;
            }
            section[data-testid="stSidebar"]
            button[data-testid="stBaseButton-primary"] {
                background: linear-gradient(135deg, #176BFF 0%, #1859D9 100%);
                border-color: #176BFF;
                box-shadow: 0 5px 12px rgba(23, 91, 217, 0.22);
            }

            section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
                font-size: 0.84rem;
                color: #55555E;
            }
            /* Selectbox values.

               Hooked on .react-aria-ComboBox, which is what this Streamlit
               build renders. An earlier [data-baseweb="select"] selector
               matched NOTHING here — BaseWeb is not in this DOM at all — so
               the rule silently did nothing. If the dropdowns ever lose their
               styling after a Streamlit upgrade, check that class first. */
            section[data-testid="stSidebar"] .react-aria-ComboBox input {
                font-size: 0.92rem;
                color: #1E1E23;
            }
            /* Make every select control's edge visible. Streamlit already
               draws a 1px border, but coloured WHITE — against the white
               section cards, the white main canvas and the cream popover
               surface alike it measures a contrast ratio of 1.08-1.00, i.e.
               invisible, so the dropdowns read as plain text rather than as
               controls you can open. Only the COLOUR changes; the box, its
               radius and its metrics stay Streamlit's own, so no control
               shifts by a pixel.

               Deliberately unscoped. A sidebar-scoped rule reached neither
               the screener popover — st.popover renders its body in a portal
               outside the sidebar element, however it looks on screen — nor
               the main filter bar's Status / Strength / Sort by. Scoping this
               three ways would leave the same defect one click away each
               time. */
            .react-aria-ComboBox > div {
                border-color: #C6C4BC;
            }
            .react-aria-ComboBox > div:hover,
            .react-aria-ComboBox > div:focus-within {
                border-color: #4A5361;
            }

            /* Watchlist segmented control.

               Streamlit's defaults invert the emphasis: unselected segments
               get the solid rail colour while the selected one gets a 10%
               tint plus an outline, so the chosen option looks like the
               hollow one. Selected now takes the WATCHLIST card's teal as a
               filled tint, and unselected drops to transparent.

               Targeted via aria-checked and data-variant rather than the
               emotion class, which changes between Streamlit builds. */
            /* Three separate pills with gaps, not segments welded into one
               track: with a shared track the unselected options had no edge
               of their own and read as empty space rather than as choices. */
            section[data-testid="stSidebar"] [data-testid="stButtonGroup"] {
                background-color: transparent;
                padding: 0;
            }
            /* The flex row is an inner div, not stButtonGroup itself — the
               gap has to go on the actual flex parent of the buttons. */
            section[data-testid="stSidebar"]
            [data-testid="stButtonGroup"] > div {
                gap: 6px !important;
            }
            section[data-testid="stSidebar"]
            button[data-variant="segmented_control"] {
                flex: 1;
                /* Streamlit sets margin-right:-1px to weld adjacent segments
                   into one track, which cancels any gap. */
                margin: 0 !important;
                background-color: #FCFCFA !important;
                border: 1px solid #E0DFD8 !important;
                border-radius: 8px !important;
            }
            section[data-testid="stSidebar"]
            button[data-variant="segmented_control"] p {
                color: #55555E !important;
                font-size: 0.9rem;
                font-weight: 400;
            }
            section[data-testid="stSidebar"]
            button[data-variant="segmented_control"]:hover {
                border-color: #C9C8BF !important;
            }
            section[data-testid="stSidebar"]
            button[data-variant="segmented_control"][aria-checked="true"] {
                background-color: #E1F5EE !important;
                border-color: #5DCAA5 !important;
            }
            section[data-testid="stSidebar"]
            button[data-variant="segmented_control"][aria-checked="true"] p {
                color: #085041 !important;
                font-weight: 600;
            }

            /* ---- Main-area layout: dashboard + analysis results ----------
               Streamlit's default column gap is ~1rem and its vertical block
               gap is larger, so a row of summary cards sat tight horizontally
               while stacking loosely against the row below it — the opposite
               of the design, where cards breathe sideways and sections sit
               close. These even that out. */
            [data-testid="stMain"] [data-testid="stHorizontalBlock"] {
                gap: 0.85rem;
            }
            [data-testid="stMain"] [data-testid="stVerticalBlock"] {
                gap: 0.7rem;
            }
            /* Bordered containers are the section panels. Match the card
               radius and border so a panel and a card read as one family. */
            [data-testid="stMain"] [data-testid="stVerticalBlockBorderWrapper"] {
                border-radius: 12px;
                border-color: #E4E6E9;
                background: #FFFFFF;
            }
            /* The page is content-dense; wide mode otherwise stretches the
               tables across an entire ultrawide monitor. */
            [data-testid="stMainBlockContainer"] {
                max-width: 1600px;
                padding-top: 2.2rem;
            }
            /* Buttons in the page header rows should match the card height
               rhythm rather than Streamlit's default chunky padding. */
            [data-testid="stMain"] .stButton button {
                border-radius: 9px;
                border-color: #E4E6E9;
            }

            /* Pagination row — hooked on the marker emitted by
               panels.pagination_bar. Streamlit's default button is sized for
               a text label, so at page-number size the strip read as a row of
               large plain boxes overlapping the table's scrollbar. Keep the
               0.09px value in step with that marker. */
            [data-testid="stMain"] [data-testid="stVerticalBlock"]:has(
                > [data-testid="stElementContainer"]
                  [style*="letter-spacing: 0.09px"]
            ) .stButton button {
                min-height: 32px;
                height: 32px;
                padding: 0 2px;
                font-size: 0.82rem;
                border-radius: 7px;
                border-color: #DFE3E8;
                color: #4A5361;
            }
            /* The current page is the one filled control in the strip, so it
               reads as position rather than as an action.

               The label colour MUST be restated here. The rule above sets a
               dark grey on every button in the strip, primary included, which
               put dark text on a dark fill and made the selected page number
               invisible. */
            [data-testid="stMain"] [data-testid="stVerticalBlock"]:has(
                > [data-testid="stElementContainer"]
                  [style*="letter-spacing: 0.09px"]
            ) button[data-testid="stBaseButton-primary"] {
                background-color: #17509E;
                border-color: #17509E;
                color: #FFFFFF;
            }
            [data-testid="stMain"] [data-testid="stVerticalBlock"]:has(
                > [data-testid="stElementContainer"]
                  [style*="letter-spacing: 0.09px"]
            ) button[data-testid="stBaseButton-primary"] p {
                color: #FFFFFF;
            }
            [data-testid="stMain"] [data-testid="stVerticalBlock"]:has(
                > [data-testid="stElementContainer"]
                  [style*="letter-spacing: 0.09px"]
            ) button[data-testid="stBaseButton-primary"]:hover {
                background-color: #123E7C;
                border-color: #123E7C;
                color: #FFFFFF;
            }
            [data-testid="stMain"] [data-testid="stVerticalBlock"]:has(
                > [data-testid="stElementContainer"]
                  [style*="letter-spacing: 0.09px"]
            ) .stButton button:hover {
                border-color: #2F80ED;
                color: #2F80ED;
            }

            /* Streamlit's own ⋮ menu (Rerun / Clear cache / Print / Record /
               About). config.toml's toolbarMode="minimal" removes the Deploy
               button but leaves this, and it is framework chrome rather than
               anything Market Lens offers. Delete this rule to get it back. */
            [data-testid="stMainMenu"] {
                display: none;
            }
            /* With the toolbar emptied, Streamlit's header is a blank ~3.6rem
               strip that still floats above the canvas and clipped the page
               title and the header buttons underneath it. Collapse it to zero
               and drop its background rather than display:none, so the
               sidebar's collapse control keeps working. */
            [data-testid="stHeader"] {
                height: 0;
                min-height: 0;
                background: transparent;
                pointer-events: none;
            }
            /* Float the toolbar clear of the zero-height header so Streamlit's
               native STOP control stays reachable during a scan. A custom
               in-page Stop button cannot work: the scan loop blocks the
               script, so the page never processes the click. Streamlit's own
               stop signals the script runner directly, which does. With
               toolbarMode="minimal" and the ⋮ menu hidden, this is the only
               thing left in the toolbar.

               width/height MUST be pinned to auto. Streamlit styles this
               element at 100% x 100%; making it position:fixed without
               overriding that turned it into a full-viewport invisible sheet
               at z-index 1000 that swallowed every click and scroll on the
               page. pointer-events stays none on the box and is re-enabled
               only on its children, so the container can never intercept
               input again even if its size rule changes. */
            [data-testid="stToolbar"] {
                position: fixed;
                top: 8px;
                right: 14px;
                left: auto;
                bottom: auto;
                width: auto;
                height: auto;
                min-height: 0;
                z-index: 1000;
                pointer-events: none;
            }
            [data-testid="stToolbar"] > * {
                pointer-events: auto;
            }
            /* Reclaim the space the header used to reserve. */
            [data-testid="stMainBlockContainer"] {
                padding-top: 1.6rem;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )

    init_session_state()

    # Main-navigation links use a same-tab ``?nav=`` route so their custom
    # HTML can retain the exact layout and illustrated-icon treatment the
    # sidebar needs. Process each value only once: retaining it in the URL
    # must never override a later in-app navigation action.
    _qp_nav = st.query_params.get("nav")
    _nav_pages = {
        "dashboard",
        "watchlist_manager",
        "pattern_scanner",
        "signals",
        "alerts",
        "reports",
        "trade_journal",
        "settings",
    }
    if _qp_nav in _nav_pages and st.session_state.get("_nav_qp_last") != _qp_nav:
        st.session_state.active_page = _qp_nav
        st.session_state["_nav_qp_last"] = _qp_nav

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
        # Zone confirmation mode — carried so the new tab draws the same zones
        # the opening tab's screener matched on. Only "0"/"1" are accepted;
        # anything else leaves the default alone, as with the params above.
        _qp_cf = st.query_params.get("cf")
        if _qp_cf in ("0", "1"):
            st.session_state["screener_confirmation"] = _qp_cf == "1"
            # The sidebar checkbox reads its own widget key, so seed that too
            # or the box would render unticked while the mode is active.
            st.session_state["sidebar_screener_confirmation"] = _qp_cf == "1"

        st.session_state["_qp_handled"] = True

    # Pattern Detail new-tab support. Pattern results live in session_state in
    # the opening tab, so a fresh tab restores them from the local scan cache
    # using the compact scan id carried in the URL.
    _qp_pattern_scan = st.query_params.get("pattern_scan")
    _qp_pattern_symbol = st.query_params.get("pattern_symbol")
    if _qp_pattern_scan and _qp_pattern_symbol and not st.session_state.get("_qp_pattern_handled"):
        st.session_state["_qp_pattern_scan_id"] = _qp_pattern_scan
        st.session_state["pattern_scan_id"] = _qp_pattern_scan
        st.session_state.selected_pattern_symbol = _qp_pattern_symbol
        st.session_state.active_page = "pattern_detail"
        st.session_state["_qp_pattern_handled"] = True

    # Heatmap tile links use query params so the tile itself can be clickable
    # without a separate Streamlit button under it.
    _qp_heatmap_group = st.query_params.get("heatmap_group")
    _qp_heatmap_view = st.query_params.get("heatmap_view", "Group Stocks")
    _hm_query = (_qp_heatmap_group, _qp_heatmap_view)
    if _qp_heatmap_group and st.session_state.get("_hm_qp_last") != _hm_query:
        st.session_state["heatmap_selected_group"] = _qp_heatmap_group
        st.session_state["heatmap_stock_universe"] = f"group:{_qp_heatmap_group}"
        st.session_state["hm_stock_universe_select"] = f"group:{_qp_heatmap_group}"
        if _qp_heatmap_view in {"Heatmap", "Group Stocks"}:
            st.session_state["hm_view_widget"] = _qp_heatmap_view
        st.session_state.active_page = "market_heatmap"
        st.session_state["_hm_qp_last"] = _hm_query

    # Research Engine new-tab support. The sidebar's "Research ↗" item is an
    # <a target="_blank"> (native buttons cannot open tabs), so a fresh session
    # lands here with ?research=1. One-shot, like ?stock: the flag routes the
    # first run only, leaving in-tab navigation away from the page free.
    if st.query_params.get("research") == "1" and not st.session_state.get("_qp_research_handled"):
        st.session_state.active_page = "research"
        st.session_state["_qp_research_handled"] = True

    # Gap Signals new-tab support: the cached scan id rides the URL so a fresh
    # session can restore the results table (same pattern as ?pattern_scan).
    _qp_gap_scan = st.query_params.get("gap_scan")
    if _qp_gap_scan and not st.session_state.get("_qp_gap_handled"):
        st.session_state["_qp_gap_scan_id"] = _qp_gap_scan
        st.session_state.active_page = "signals"
        st.session_state["_qp_gap_handled"] = True

    # Options Trade Lab deep link (Research Engine sub-page), one-shot.
    if st.query_params.get("options_trade_lab") == "1" \
            and not st.session_state.get("_qp_otl_handled"):
        st.session_state.active_page = "options_trade_lab"
        st.session_state["_qp_otl_handled"] = True

    try:
        init_db()
    except Exception as exc:
        st.error(f"Database initialisation failed: {exc}")
        logger.exception("Database init error")

    # A normal URL navigation (such as selecting a clickable heatmap tile)
    # can open a fresh Streamlit session. Restore the latest completed scan so
    # Analysis Results does not appear empty after that navigation.
    if not st.session_state.get("analysis_results"):
        try:
            snapshot = load_latest_analysis_snapshot()
        except Exception as exc:
            logger.warning("Could not restore latest analysis snapshot: %s", exc)
            snapshot = None
        if snapshot and snapshot.get("results"):
            st.session_state["analysis_results"] = snapshot["results"]
            metadata = snapshot.get("metadata", {}) or {}
            st.session_state["_last_scan_label"] = metadata.get(
                "last_scan_label", snapshot.get("created_at", "")
            )
            st.session_state["_used_tf_label"] = metadata.get("used_tf_label", "")
            st.session_state["_fetch_fallback_symbols"] = metadata.get(
                "fallback_symbols", []
            )

    _scan_id = st.session_state.get("_qp_pattern_scan_id")
    if _scan_id and not st.session_state.get("_qp_pattern_loaded"):
        try:
            cached_scan = get_pattern_scan(str(_scan_id))
        except Exception as exc:
            logger.warning("Could not restore pattern scan %s: %s", _scan_id, exc)
            cached_scan = None
        if cached_scan:
            st.session_state["pattern_scan_settings"] = cached_scan.get("settings", {})
            st.session_state["pattern_scan_results"] = cached_scan.get("matches", [])
            st.session_state["pattern_scan_universe_label"] = cached_scan.get(
                "universe_label", ""
            )
            st.session_state["_pattern_last_scan_label"] = cached_scan.get(
                "created_at", ""
            )
            source_name = cached_scan.get("source_name")
            if source_name in SUPPORTED_DATA_SOURCES:
                st.session_state["selected_data_source"] = source_name
                st.session_state["pattern_scan_source_name"] = source_name
        st.session_state["_qp_pattern_loaded"] = True

    try:
        saved = load_credentials()
        if saved:
            st.session_state.credentials = saved
    except Exception as exc:
        logger.warning("Could not load saved credentials: %s", exc)

    render_sidebar()

    # Routing. "dashboard" is the market overview landing page; scan results
    # live on their own page, and the per-stock chart on a third. The detail
    # view is dispatched here rather than from inside a page, so any page can
    # navigate to it by setting active_page alone.
    page = st.session_state.active_page
    if page == "stock_detail":
        render_detail_view()
    elif page == "pattern_scanner":
        render_pattern_scanner()
    elif page == "pattern_results":
        render_pattern_results()
    elif page == "pattern_detail":
        render_pattern_detail()
    elif page == "analysis_results":
        render_analysis_results()
    elif page == "market_heatmap":
        render_market_heatmap()
    elif page == "alerts":
        render_alerts_page()
    elif page == "reports":
        render_reports_page()
    elif page == "trade_journal":
        render_trade_journal_page()
    elif page == "watchlist_manager":
        render_watchlist_manager()
    elif page == "settings":
        render_settings()
    elif page == "research":
        render_research_page()
    elif page == "signals":
        render_signals_page()
    elif page == "options_trade_lab":
        render_options_trade_lab()
    else:
        render_market_overview()


if __name__ == "__main__":
    main()
