"""Main dashboard page — watchlist analysis grid."""

import math

import pandas as pd
import yfinance as yf
import streamlit as st

from alerts.manager import check_and_trigger_alerts
from analysis.base import BaseAnalysis
from analysis.demand_supply import DemandSupplyAnalysis
from analysis.intraday import IntradayAnalysis
from analysis.long_term import LongTermAnalysis
from analysis.short_term import ShortTermAnalysis
from analysis.trend_following import TrendFollowingAnalysis
from config.trading_config import get_timeframe
from data.manager import DataSourceManager, FetchMeta, fetch_for_trading_type, interval_display_label
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


def get_analyzer_for_primary(primary_strategy: str) -> BaseAnalysis:
    """Return the correct :class:`BaseAnalysis` instance for *primary_strategy*.

    Stage D real routing — instantiates the correct analyzer class rather than
    mapping to a legacy string key as the now-removed Stage B bridge did.

    Args:
        primary_strategy: One of ``config.trading_config.PRIMARY_STRATEGIES``.

    Returns:
        A fresh analyzer instance. Falls back to :class:`DemandSupplyAnalysis`
        for any unknown value so the app always produces a result.

    Example::

        >>> get_analyzer_for_primary("Trend Following (SMA50/EMA20)")
        TrendFollowingAnalysis()
        >>> get_analyzer_for_primary("Demand/Supply Zones")
        DemandSupplyAnalysis()
    """
    if primary_strategy == "Trend Following (SMA50/EMA20)":
        return TrendFollowingAnalysis()
    return DemandSupplyAnalysis()


def _valid_price(raw: object) -> float | None:
    """Return *raw* as a positive finite float, or ``None`` if it is invalid,
    zero, NaN, or infinite.

    Used to guard the price-selection step so that a NaN from the last OHLCV
    row (a partial/empty intraday candle) can never propagate to the card —
    note that ``NaN`` is *truthy* in Python, so the plain ``x or fallback``
    idiom silently keeps the NaN instead of falling back.
    """
    try:
        v = float(raw)  # type: ignore[arg-type]
        return v if math.isfinite(v) and v > 0 else None
    except (TypeError, ValueError):
        return None


def render_dashboard() -> None:
    """Render the main dashboard page."""
    if st.session_state.get("active_page") == "stock_detail":
        _render_detail_view()
        return

    watchlist_id = st.session_state.get("selected_watchlist_id")
    source_name = st.session_state.get("selected_data_source", "Yahoo Finance")

    # Stage D — read the two-axis selections; analysis_type IS primary_strategy
    # now that the Stage B temporary bridge is removed and real routing is live.
    # Stage C keeps driving timeframe via get_timeframe(trading_type).
    trading_type = st.session_state.get("trading_type", "Short-term Trading")
    primary_strategy = st.session_state.get("primary_strategy", "Demand/Supply Zones")
    enhancers: list[str] = st.session_state.get("enhancers", [])
    analysis_type = primary_strategy  # "Demand/Supply Zones" or "Trend Following (SMA50/EMA20)"
    # Stage C: effective timeframe label for display (requests the configured
    # interval; updated to show "unavailable" after analysis if fallback fired).
    _tf = get_timeframe(trading_type)
    _tf_label = interval_display_label(_tf["interval"])

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

    # Show the two-axis selection and effective timeframe in the header.
    _enhancer_label = ", ".join(enhancers) if enhancers else "None"
    st.subheader(f"{wl_name} | {trading_type} | {primary_strategy} | Enhancers: {_enhancer_label}")
    # Timeframe caption — read from session state so it persists across reruns
    # (e.g. the user filters/sorts without re-running analysis).
    _used_tf_label = st.session_state.get("_used_tf_label", _tf_label)
    st.caption(f"Timeframe: {_used_tf_label}")

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

    # Stage C: _PERIOD_MAP is kept for reference but is no longer used for
    # fetching — get_timeframe(trading_type) drives period/interval instead.
    results: dict[str, dict] = {}
    fallback_symbols: list[str] = []   # tracks stocks where intraday fell back
    progress = st.progress(0, text="Analysing stocks…")
    alerts_on = st.session_state.get("alerts_on", False)

    for i, stock in enumerate(stocks):
        progress.progress((i + 1) / len(stocks), text=f"Analysing {stock.symbol}…")
        symbol = _make_symbol(stock.symbol, stock.exchange, source_name)
        try:
            quote = ds_manager.get_quote(symbol)
            # Stage C: fetch with trading-type-aware timeframe + intraday fallback.
            hist, fetch_meta = fetch_for_trading_type(
                symbol, trading_type, fetch_fn=ds_manager.get_history
            )
            if fetch_meta["fell_back"]:
                fallback_symbols.append(stock.symbol)
            # If no data at all, give analyse() an empty df — it will return a
            # graceful "insufficient data" error dict via its own guard.
            if hist is None:
                hist = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
            # Stage D: real routing — get_analyzer_for_primary() instantiates
            # the correct engine class; DemandSupplyAnalysis accepts the opt-in
            # use_fibonacci kwarg, TrendFollowingAnalysis takes only symbol+data.
            analyser = get_analyzer_for_primary(primary_strategy)
            if isinstance(analyser, DemandSupplyAnalysis):
                result = analyser.analyse(
                    symbol, hist,
                    use_fibonacci=st.session_state.get("use_fibonacci", False),
                )
            else:
                result = analyser.analyse(symbol, hist)
            # Rule: prefer a live, finite quote price; fall back to the last
            # valid close that analyse() stored (which itself guards against
            # NaN — see demand_supply.py). Do NOT use plain `or` — NaN is
            # truthy in Python and would silently bypass the fallback.
            _quote_p = _valid_price(quote.get("current_price"))
            _result_p = _valid_price(result.get("current_price"))
            current_price = _quote_p if _quote_p is not None else (_result_p or 0.0)
            change_pct = float(quote.get("change_pct") or 0.0)
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

    # Stage C: persist the effective timeframe label so the header caption
    # stays accurate on subsequent reruns (filter/sort interactions).
    any_fallback = bool(fallback_symbols)
    st.session_state["_fetch_fallback_symbols"] = fallback_symbols
    st.session_state["_used_tf_label"] = interval_display_label(
        _tf["interval"], fell_back=any_fallback
    )

    _render_filter_sort_bar(results, analysis_type, wl_name)


def _render_filter_sort_bar(
    results: dict[str, dict], analysis_type: str, wl_name: str
) -> None:
    """Render filter/sort controls, export buttons, and the results grid."""
    total = len(results)

    # Stage C: show a non-intrusive note when any stock fell back from
    # intraday to daily data.  Stored in session state so it persists across
    # filter/sort reruns without re-running the analysis.
    _fallback = st.session_state.get("_fetch_fallback_symbols", [])
    if _fallback:
        st.info(
            "ℹ️ Intraday data unavailable for some stocks — Daily data used instead."
            f"  Affected: {', '.join(_fallback[:5])}"
            + (" …" if len(_fallback) > 5 else "")
        )

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
                result=result,
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
    # Stage D: analysis_type IS primary_strategy (bridge removed)
    primary_strategy = st.session_state.get("primary_strategy", "Demand/Supply Zones")
    analysis_type = primary_strategy
    exchange = result.get("exchange", "NSE")
    stock_id = result.get("stock_id") or st.session_state.get("selected_stock_id")

    # Stage C: the chart data must match the analysis timeframe so that zone
    # overlays and Fibonacci lines land on the same bars as the analysis.
    # Cache key includes trading_type so switching type invalidates old cache.
    trading_type = st.session_state.get("trading_type", "Short-term Trading")
    cache_key = f"detail_hist_{symbol}_{trading_type.replace(' ', '_')}"
    history_df = st.session_state.get(cache_key)

    if history_df is None or getattr(history_df, "empty", True):
        try:
            suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
            full_symbol = f"{symbol}{suffix}"
            # Use fetch_for_trading_type so the chart matches analysis bars;
            # _default_fetch_fn (yfinance) is used since this is outside the
            # DataSourceManager's scope and yf is already imported in this file.
            history_df, _det_meta = fetch_for_trading_type(full_symbol, trading_type)
            if history_df is not None and not history_df.empty:
                st.session_state[cache_key] = history_df
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
