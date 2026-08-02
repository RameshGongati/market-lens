"""Market Lens — Main Streamlit Entry Point."""

import streamlit as st

from config.credentials import load_credentials
from config.settings import SUPPORTED_DATA_SOURCES
from config.trading_config import ENHANCERS, PRIMARY_STRATEGIES, TRADING_TYPES
from storage.database import init_db
from ui.components.sidebar import render_sidebar
from ui.pages.dashboard import render_dashboard
from ui.pages.watchlist_manager import render_watchlist_manager
from ui.pages.settings import render_settings
from utils.logger import get_logger

logger = get_logger(__name__)


def init_session_state() -> None:
    """Initialise all required Streamlit session state keys.

    These are deliberately fixed defaults, not the saved preferences: every
    fresh app launch starts from a known baseline. A stock opened in a new
    browser tab is the one case that must NOT use these — it adopts the
    opening tab's selections from the URL instead (see the ?stock= handler
    in :func:`main`), because a new tab is a separate Streamlit session with
    empty state and would otherwise silently analyse against different
    settings than the ones on screen.
    """
    defaults: dict = {
        "active_page": "dashboard",
        "selected_watchlist_id": None,
        # Two-axis analysis model (Trading Type + Primary Strategy + Enhancers).
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
                background-color: #E4E1D4;
                border-color: #D0CDBC;
                border-radius: 14px;
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
            section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
                font-size: 0.84rem;
                color: #55555E;
            }
            /* Selectbox values. */
            section[data-testid="stSidebar"] [data-baseweb="select"] div {
                font-size: 0.92rem;
                color: #1E1E23;
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
        </style>
        """,
        unsafe_allow_html=True,
    )

    init_session_state()

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
