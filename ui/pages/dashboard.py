"""Main dashboard page — watchlist analysis grid."""

import streamlit as st

from alerts.manager import check_and_trigger_alerts
from analysis.demand_supply import DemandSupplyAnalysis
from analysis.intraday import IntradayAnalysis
from analysis.long_term import LongTermAnalysis
from analysis.short_term import ShortTermAnalysis
from data.manager import DataSourceManager
from storage.database import save_analysis_result
from ui.components.stock_card import render_stock_card
from ui.components.stock_detail import render_stock_detail
from watchlist.manager import get_all_watchlists, get_stocks
from utils.logger import get_logger

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


def render_dashboard() -> None:
    """Render the main dashboard page."""
    # If a stock card was clicked, show detail view instead
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

    # Resolve watchlist name
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
            _render_results_grid(cached, analysis_type)
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
        elif source_name not in ("Yahoo Finance", "NSE India"):
            ds_manager.switch_source(source_name)
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
            # Merge quote data into result for display
            result["current_price"] = result.get("current_price") or quote.get("current_price", 0.0)
            result["change_pct"] = quote.get("change_pct", 0.0)
            results[stock.symbol] = result
            # Persist to database
            save_analysis_result(stock.id, analysis_type, result)
            check_and_trigger_alerts(stock, result, alerts_on)
        except Exception as exc:
            logger.error("Analysis error for %s: %s", stock.symbol, exc)
            results[stock.symbol] = {
                "symbol": stock.symbol,
                "status": "neutral",
                "summary": f"Error: {exc}",
                "current_price": 0.0,
                "change_pct": 0.0,
                "error": str(exc),
            }

    progress.empty()
    st.session_state.analysis_results = results
    _render_results_grid(results, analysis_type)


def _render_results_grid(results: dict[str, dict], analysis_type: str) -> None:
    """Render a 3-column grid of stock cards."""
    cols = st.columns(3)
    for idx, (symbol, result) in enumerate(results.items()):
        with cols[idx % 3]:
            render_stock_card(
                symbol=symbol,
                exchange=result.get("exchange", ""),
                status=result.get("status", "neutral"),
                summary=result.get("summary", ""),
                current_price=result.get("current_price", 0.0),
                change_pct=result.get("change_pct", 0.0),
                stock_id=result.get("stock_id", idx),
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

    render_stock_detail(
        symbol=symbol,
        exchange=exchange,
        analysis_type=analysis_type,
        result=result,
        history_df=None,  # Chart uses cached data; future: pass full df
    )


def _make_symbol(symbol: str, exchange: str, source: str) -> str:
    """Format a ticker symbol appropriately for the active data source."""
    if source == "Yahoo Finance":
        suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
        return f"{symbol}{suffix}"
    if source == "TradingView":
        return f"{exchange.upper()}:{symbol}"
    return symbol
