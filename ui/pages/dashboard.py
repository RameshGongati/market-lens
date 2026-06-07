"""Main dashboard page — watchlist analysis grid."""

import yfinance as yf
import streamlit as st

from alerts.manager import check_and_trigger_alerts
from analysis.demand_supply import DemandSupplyAnalysis
from analysis.intraday import IntradayAnalysis
from analysis.long_term import LongTermAnalysis
from analysis.short_term import ShortTermAnalysis
from data.manager import DataSourceManager
from storage.database import get_all_alerts, save_analysis_result
from ui.components.stock_card import render_stock_card
from ui.components.stock_detail import render_stock_detail
from utils.export import export_to_excel, export_to_pdf
from utils.logger import get_logger
from watchlist.manager import get_all_watchlists, get_stocks

logger = get_logger(__name__)

_ANALYSIS_MAP = {
    "Demand/Supply Zones": DemandSupplyAnalysis,
    "Long Term Investment": LongTermAnalysis,
    "Short Term Investment": ShortTermAnalysis,
    "Intraday Trading": IntradayAnalysis,
}

_PERIOD_MAP = {
    "Demand/Supply Zones": ("1y", "1d"),
    "Long Term Investment": ("2y", "1d"),
    "Short Term Investment": ("6mo", "1d"),
    "Intraday Trading": ("5d", "15m"),
}

_STATUS_ORDER = {"bullish": 0, "neutral": 1, "bearish": 2}
_STRENGTH_ORDER = {"Strong": 0, "Medium": 1, "Weak": 2}


def render_dashboard() -> None:
    """Render the main dashboard page."""
    if st.session_state.get("active_page") == "stock_detail":
        _render_detail_view()
        return

    watchlist_id = st.session_state.get("selected_watchlist_id")
    analysis_type = st.session_state.get("selected_analysis_type", "Demand/Supply Zones")
    source_name = st.session_state.get("selected_data_source", "Yahoo Finance")

    st.title("📈 Market Lens — Dashboard")

    if watchlist_id is None:
        st.info("Select a watchlist from the sidebar, then click **Run Analysis**.")
        return

    try:
        watchlists = get_all_watchlists()
        wl = next((w for w in watchlists if w.id == watchlist_id), None)
        wl_name = wl.name if wl else "Unknown"
    except Exception:
        wl_name = "Unknown"

    st.subheader(f"{wl_name} — {analysis_type}")

    if not st.session_state.get("analysing"):
        cached = st.session_state.get("analysis_results", {})
        if cached:
            _render_filter_sort_bar(cached, analysis_type, wl_name)
        else:
            st.info("Click **▶ Run Analysis** in the sidebar to start.")
        return

    # Run analysis
    st.session_state.analysing = False
    stocks = get_stocks(watchlist_id)
    if not stocks:
        st.warning("This watchlist has no stocks. Add some in Watchlists.")
        return

    ds_manager = DataSourceManager()
    creds = st.session_state.get("credentials", {}).get(source_name, {})
    try:
        if creds:
            ds_manager.switch_source(source_name, creds)
        else:
            ds_manager.switch_source(source_name)
    except Exception as exc:
        st.error(f"Could not connect to {source_name}: {exc}")
        return

    period, interval = _PERIOD_MAP.get(analysis_type, ("1y", "1d"))
    results: dict[str, dict] = {}
    progress = st.progress(0, text="Analysing stocks…")
    alerts_on = st.session_state.get("alerts_on", False)

    for i, stock in enumerate(stocks):
        progress.progress((i + 1) / len(stocks), text=f"Analysing {stock.symbol}…")
        symbol = _make_symbol(stock.symbol, stock.exchange, source_name)
        try:
            quote = ds_manager.get_quote(symbol)
            hist = ds_manager.get_history(symbol, period, interval)
            analyser_cls = _ANALYSIS_MAP[analysis_type]
            analyser = analyser_cls()
            result = analyser.analyse(symbol, hist)
            current_price = result.get("current_price") or quote.get("current_price", 0.0)
            change_pct = quote.get("change_pct", 0.0)
            # Approximate absolute change from percentage
            change = round(current_price * change_pct / 100, 2)
            result.update({
                "current_price": current_price,
                "change_pct": change_pct,
                "change": change,
                "stock_id": stock.id,
                "exchange": stock.exchange,
            })
            results[stock.symbol] = result
            save_analysis_result(stock.id, analysis_type, result)
            check_and_trigger_alerts(stock, result, alerts_on)
        except Exception as exc:
            logger.error("Analysis error for %s: %s", stock.symbol, exc)
            results[stock.symbol] = {
                "symbol": stock.symbol,
                "exchange": stock.exchange,
                "status": "neutral",
                "summary": f"Error: {exc}",
                "current_price": 0.0,
                "change_pct": 0.0,
                "change": 0.0,
                "strength": "Weak",
                "stock_id": stock.id,
            }

    progress.empty()
    st.session_state.analysis_results = results
    _render_filter_sort_bar(results, analysis_type, wl_name)


def _render_filter_sort_bar(
    results: dict[str, dict], analysis_type: str, wl_name: str
) -> None:
    """Render filter/sort controls, export buttons, and the results grid."""
    total = len(results)

    # Initialise filter/sort state with defaults
    st.session_state.setdefault("dash_status_filter", [])
    st.session_state.setdefault("dash_strength_filter", [])
    st.session_state.setdefault("dash_sort_by", "Default")

    # Header row: title on left, export buttons on right
    _, xl_col, pdf_col = st.columns([5, 1, 1])
    with xl_col:
        xl_clicked = st.button(
            "📊 Excel", use_container_width=True, help="Export results to Excel"
        )
    with pdf_col:
        pdf_clicked = st.button(
            "📄 PDF", use_container_width=True, help="Export results to PDF"
        )

    # Filter/sort controls row
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        status_filter: list[str] = st.multiselect(
            "Status",
            ["Bullish", "Bearish", "Neutral"],
            key="dash_status_filter",
            placeholder="All statuses",
        )
    with fc2:
        strength_filter: list[str] = st.multiselect(
            "Strength",
            ["Strong", "Medium", "Weak"],
            key="dash_strength_filter",
            placeholder="All strengths",
        )
    with fc3:
        sort_by: str = st.selectbox(
            "Sort by",
            ["Default", "Status", "Strength", "Price Change %", "Alphabetical"],
            key="dash_sort_by",
        )  # type: ignore[assignment]

    # Apply filters
    filtered = list(results.items())
    if status_filter:
        lc_filter = {s.lower() for s in status_filter}
        filtered = [(sym, r) for sym, r in filtered if r.get("status", "neutral") in lc_filter]
    if strength_filter:
        filtered = [
            (sym, r) for sym, r in filtered if r.get("strength", "Weak") in strength_filter
        ]

    # Apply sorting
    if sort_by == "Status":
        filtered.sort(key=lambda x: _STATUS_ORDER.get(x[1].get("status", "neutral"), 1))
    elif sort_by == "Strength":
        filtered.sort(key=lambda x: _STRENGTH_ORDER.get(x[1].get("strength", "Weak"), 2))
    elif sort_by == "Price Change %":
        filtered.sort(key=lambda x: x[1].get("change_pct", 0.0), reverse=True)
    elif sort_by == "Alphabetical":
        filtered.sort(key=lambda x: x[0])

    st.caption(f"Showing {len(filtered)} of {total} stocks")

    # Handle export clicks — generate file then offer download
    if xl_clicked:
        _do_export_excel(results, wl_name, analysis_type)
    if pdf_clicked:
        _do_export_pdf(results, wl_name, analysis_type)

    _render_results_grid(dict(filtered), analysis_type)


def _do_export_excel(
    results: dict[str, dict], wl_name: str, analysis_type: str
) -> None:
    """Generate an Excel export and render a download button."""
    try:
        alerts = get_all_alerts()
        path = export_to_excel(list(results.values()), wl_name, analysis_type, alerts)
        with open(path, "rb") as fh:
            st.download_button(
                label="📥 Download Excel",
                data=fh.read(),
                file_name=path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    except Exception as exc:
        st.error(f"Excel export failed: {exc}")


def _do_export_pdf(
    results: dict[str, dict], wl_name: str, analysis_type: str
) -> None:
    """Generate a PDF export and render a download button."""
    try:
        path = export_to_pdf(list(results.values()), wl_name, analysis_type)
        with open(path, "rb") as fh:
            st.download_button(
                label="📥 Download PDF",
                data=fh.read(),
                file_name=path.name,
                mime="application/pdf",
            )
    except Exception as exc:
        st.error(f"PDF export failed: {exc}")


def _render_results_grid(results: dict[str, dict], analysis_type: str) -> None:
    """Render a 3-column grid of stock cards."""
    if not results:
        st.info("No stocks match the current filters.")
        return
    cols = st.columns(3)
    for idx, (symbol, result) in enumerate(results.items()):
        with cols[idx % 3]:
            render_stock_card(
                symbol=symbol,
                exchange=result.get("exchange", "NSE"),
                status=result.get("status", "neutral"),
                summary=result.get("summary", ""),
                current_price=result.get("current_price", 0.0),
                change=result.get("change", 0.0),
                change_pct=result.get("change_pct", 0.0),
                stock_id=result.get("stock_id", idx),
                strength=result.get("strength", "Weak"),
                updated_at=result.get("updated_at"),
            )


def _render_detail_view() -> None:
    """Render the detail view for the selected stock."""
    symbol = st.session_state.get("selected_stock_symbol")
    if not symbol:
        st.session_state.active_page = "dashboard"
        st.rerun()
        return

    results = st.session_state.get("analysis_results", {})
    result = results.get(symbol, {})
    analysis_type = st.session_state.get("selected_analysis_type", "Demand/Supply Zones")
    exchange = result.get("exchange", "NSE")
    stock_id = result.get("stock_id") or st.session_state.get("selected_stock_id")

    # Fetch 1-year daily OHLCV data for the chart.
    # Cached per symbol so reruns (radio buttons, notes, etc.) skip the fetch.
    cache_key = f"detail_hist_{symbol}"
    history_df = st.session_state.get(cache_key)

    if history_df is None or getattr(history_df, "empty", True):
        try:
            suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
            hist = yf.Ticker(f"{symbol}{suffix}").history(period="1y", interval="1d")
            if not hist.empty:
                st.session_state[cache_key] = hist
                history_df = hist
            else:
                history_df = None
        except Exception as exc:
            logger.warning("History prefetch failed for %s: %s", symbol, exc)
            history_df = None

    render_stock_detail(
        symbol=symbol,
        exchange=exchange,
        analysis_type=analysis_type,
        result=result,
        history_df=history_df,
        stock_id=stock_id,
    )


def _make_symbol(symbol: str, exchange: str, source: str) -> str:
    """Format a ticker symbol for the active data source."""
    if source == "Yahoo Finance":
        suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
        return f"{symbol}{suffix}"
    if source == "TradingView":
        return f"{exchange.upper()}:{symbol}"
    return symbol
