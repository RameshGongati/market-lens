"""Scan engine and shared helpers for the analysis pages.

The watchlist scan used to live inside ``render_dashboard`` alongside the
results grid. The grid moved to ``ui/pages/analysis_results.py`` and the
dashboard became a market overview (``ui/pages/market_overview.py``), so what
remains here is the scan itself plus the helpers both pages share: the
screener predicate, the exports, the per-stock detail view, and the
single-stock analysis used by "View" deep links.
"""

import math

import pandas as pd
import yfinance as yf
import streamlit as st

from alerts.manager import check_and_trigger_alerts
from alerts.zone_alert_checker import check_zone_alerts
from analysis.base import BaseAnalysis
from analysis.demand_supply import DemandSupplyAnalysis
from analysis.trend_following import TrendFollowingAnalysis
from config.alert_settings import load_alert_config
from config.trading_config import get_timeframe
from data.manager import (
    DataSourceManager,
    FetchMeta,
    fetch_for_trading_type,
    interval_display_label,
)
from storage.database import get_all_alerts, save_analysis_result
from ui.components.panels import scan_progress
from ui.components.stock_card import render_stock_card
from ui.components.stock_detail import render_stock_detail
from utils.export import export_to_excel, export_to_pdf
from utils.logger import get_logger
from types import SimpleNamespace

from utils.helpers import get_nse_batch_stocks, load_predefined_watchlists
from watchlist.manager import get_all_watchlists, get_stocks

logger = get_logger(__name__)

_STATUS_ORDER = {"bullish": 0, "neutral": 1, "bearish": 2}
_STRENGTH_ORDER = {"Strong": 0, "Medium": 1, "Weak": 2}
_PROXIMITY_PCT = {"≤3%": 3.0, "≤5%": 5.0, "≤10%": 10.0}
_SCORE_THRESHOLD = {"7": 7.0, "6+": 6.0, "5+": 5.0}
# Confirmed zones sit on a different ladder: freshness is pinned at 1.5 once
# a zone has been tested, so the only reachable totals are 2.5 / 3.5 / 4.5 /
# 5.5. The usual 7 / 6+ / 5+ thresholds would match almost nothing.
_CONFIRM_SCORE_THRESHOLD = {"5.5": 5.5, "4.5+": 4.5, "3.5+": 3.5}


def _nearest_zones(result: dict) -> list[dict]:
    """Return the nearest demand/supply zone dicts that exist."""
    zones = []
    for key in ("nearest_demand", "nearest_supply"):
        z = result.get(key)
        if z and z.get("proximal"):
            zones.append(z)
    return zones


def _passes_screener(result: dict) -> bool:
    """Return True if *result* passes all active screener filters.

    Reads screener state from session state. A stock passes if ANY of its
    nearest zones (demand or supply) satisfies all active criteria.
    """
    strength_filter: list[str] = st.session_state.get("screener_zone_strength", [])

    # Zone confirmation is a separate mode, not an extra condition. It reads
    # its own pre-selected list (see demand_supply.confirmation_zones) because
    # a confirmed zone can score below the 5.0 display cutoff and so may not
    # appear in demand_zones/supply_zones at all.
    if st.session_state.get("screener_confirmation", False):
        confirmed = result.get("confirmation_zones") or []
        if not confirmed:
            return False
        score_min = _CONFIRM_SCORE_THRESHOLD.get(
            st.session_state.get("screener_confirm_score", "All")
        )
        for zone in confirmed:
            if score_min is not None and zone.get("odd_score", 0) < score_min:
                continue
            if strength_filter and zone.get("zone_strength", "Normal") not in strength_filter:
                continue
            return True
        return False

    proximity = st.session_state.get("screener_proximity", "All")
    min_score = st.session_state.get("screener_min_score", "All")

    if proximity == "All" and min_score == "All" and not strength_filter:
        return True

    price = result.get("current_price", 0.0)
    if price <= 0:
        return False

    zones = _nearest_zones(result)
    if not zones:
        return False

    pct = _PROXIMITY_PCT.get(proximity)
    inside_only = proximity == "Inside Zone"
    score_min = _SCORE_THRESHOLD.get(min_score)

    for zone in zones:
        top = max(zone["proximal"], zone["distal"])
        bottom = min(zone["proximal"], zone["distal"])
        inside = bottom <= price <= top
        if inside_only:
            if not inside:
                continue
        elif pct is not None:
            if not inside:
                distance = abs(price - zone["proximal"]) / price * 100
                if distance > pct:
                    continue
        if score_min is not None:
            if zone.get("odd_score", 0) < score_min:
                continue
        if strength_filter:
            if zone.get("zone_strength", "Normal") not in strength_filter:
                continue
        return True

    return False


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


def scan_context() -> dict | None:
    """Resolve everything a scan needs from session state.

    Split out of the old ``render_dashboard`` so the dashboard and the
    analysis-results page can both reason about the selected watchlist
    without duplicating the three-way source resolution. Returns ``None``
    and writes an ``st.info``/``st.warning`` when the selection is
    incomplete, which is the caller's cue to stop rendering.
    """
    wl_source = st.session_state.get("watchlist_source", "My Watchlists")
    watchlist_id = st.session_state.get("selected_watchlist_id")
    source_name = st.session_state.get("selected_data_source", "Yahoo Finance")

    # Stage D — read the two-axis selections; analysis_type IS primary_strategy
    # now that the Stage B temporary bridge is removed and real routing is live.
    # Stage C keeps driving timeframe via get_timeframe(trading_type).
    trading_type = st.session_state.get("trading_type", "Options Trading")
    primary_strategy = st.session_state.get("primary_strategy", "Demand/Supply Zones")
    enhancers: list[str] = st.session_state.get("enhancers", [])
    analysis_type = primary_strategy  # "Demand/Supply Zones" or "Trend Following (SMA50/EMA20)"
    # Stage C: effective timeframe label for display (requests the configured
    # interval; updated to show "unavailable" after analysis if fallback fired).
    _tf = get_timeframe(trading_type)
    _tf_label = interval_display_label(_tf["interval"])

    _is_predefined = wl_source == "Index Watchlists"
    _is_all_nse = wl_source == "All NSE Stocks"

    if _is_all_nse:
        _nse_batch = st.session_state.get("selected_nse_batch")
        if not _nse_batch:
            st.info("Select a stock range from the sidebar, then click **Run Analysis**.")
            return None
        wl_name = f"All NSE Stocks ({_nse_batch})"
    elif _is_predefined:
        _pd_name = st.session_state.get("selected_predefined_watchlist")
        if not _pd_name:
            st.info("Select an index watchlist from the sidebar, then click **Run Analysis**.")
            return None
        wl_name = _pd_name
    else:
        if watchlist_id is None:
            st.info("Select a watchlist from the sidebar, then click **Run Analysis**.")
            return None
        try:
            watchlists = get_all_watchlists()
            wl = next((w for w in watchlists if w.id == watchlist_id), None)
            wl_name = wl.name if wl else "Unknown"
        except Exception:
            wl_name = "Unknown"

    return {
        "wl_name": wl_name,
        "wl_source": wl_source,
        "watchlist_id": watchlist_id,
        "source_name": source_name,
        "trading_type": trading_type,
        "primary_strategy": primary_strategy,
        "analysis_type": analysis_type,
        "enhancers": enhancers,
        "tf": _tf,
        "tf_label": _tf_label,
        "is_all_nse": _is_all_nse,
        "is_predefined": _is_predefined,
    }


def run_scan(ctx: dict) -> dict[str, dict] | None:
    """Execute the watchlist scan and store the results in session state.

    Lifted verbatim out of ``render_dashboard`` when the results moved to
    their own page — the scan is now triggered from there, not from the
    dashboard, but nothing about how it scans has changed.
    """
    wl_name = ctx["wl_name"]
    watchlist_id = ctx["watchlist_id"]
    source_name = ctx["source_name"]
    trading_type = ctx["trading_type"]
    primary_strategy = ctx["primary_strategy"]
    analysis_type = ctx["analysis_type"]
    _tf = ctx["tf"]
    _is_all_nse = ctx["is_all_nse"]
    _is_predefined = ctx["is_predefined"]

    st.session_state.analysing = False

    if _is_all_nse:
        _batch_start = st.session_state.get("selected_nse_batch_start", 0)
        _batch_end = st.session_state.get("selected_nse_batch_end", 200)
        _batch_stocks = get_nse_batch_stocks(_batch_start, _batch_end)
        stocks = [
            SimpleNamespace(symbol=s["symbol"], exchange="NSE", id=0)
            for s in _batch_stocks
        ]
    elif _is_predefined:
        _pd_wl = next(
            (w for w in load_predefined_watchlists() if w["name"] == wl_name), None
        )
        if not _pd_wl or not _pd_wl["symbols"]:
            st.warning("This index watchlist has no stocks.")
            return None
        stocks = [
            SimpleNamespace(symbol=sym, exchange="NSE", id=0)
            for sym in _pd_wl["symbols"]
        ]
    else:
        stocks = get_stocks(watchlist_id)

    if not stocks:
        st.warning("This watchlist has no stocks. Add some in Watchlists.")
        return None

    ds_manager = DataSourceManager()
    creds = st.session_state.get("credentials", {}).get(source_name, {})
    try:
        if creds:
            ds_manager.switch_source(source_name, creds)
        else:
            ds_manager.switch_source(source_name)
    except Exception as exc:
        st.error(f"Could not connect to {source_name}: {exc}")
        return None

    # Fetch timeframe is driven entirely by the trading type via
    # get_timeframe(trading_type) / fetch_for_trading_type (see the loop below).
    results: dict[str, dict] = {}
    fallback_symbols: list[str] = []   # tracks stocks where intraday fell back
    # A standalone progress card rather than st.progress: the built-in bar is
    # a thin strip inserted into whatever page is on screen, which made the
    # scan look like it was running on the page the user just left. This
    # placeholder owns the viewport until the scan finishes.
    progress = st.empty()
    alerts_on = st.session_state.get("alerts_on", False)

    for i, stock in enumerate(stocks):
        progress.markdown(
            scan_progress(wl_name, stock.symbol, i + 1, len(stocks)),
            unsafe_allow_html=True,
        )
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
            if stock.id:
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
    return results


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

    # -- Zone proximity alert banner --
    try:
        _alert_cfg = load_alert_config()
        if _alert_cfg.get("enabled"):
            _matches = check_zone_alerts(results, _alert_cfg)
            if _matches:
                with st.expander(
                    f"🔔 {len(_matches)} stock{'s' if len(_matches) != 1 else ''} near zones",
                    expanded=False,
                ):
                    for m in _matches:
                        _cat = m.zone.get("category", "demand")
                        _icon = "📈" if _cat == "demand" else "📉"
                        _score = m.zone.get("odd_score", 0)
                        st.markdown(
                            f"{_icon} **{m.symbol}** ₹{m.current_price:,.2f} — "
                            f"{m.distance_pct:.1f}% from {_cat} "
                            f"(Score {_score})"
                        )
    except Exception as exc:
        logger.warning("Alert banner check failed: %s", exc)

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

    # Screener filters (set in sidebar expander)
    filtered = [(sym, r) for sym, r in filtered if _passes_screener(r)]

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

    # Mirrors sidebar._count_active_screener_filters — proximity and the
    # normal score ladder are inactive in confirmation mode, so counting a
    # value left over from the other mode would overstate what is filtering.
    if st.session_state.get("screener_confirmation", False):
        _active_screeners = sum([
            True,  # the confirmation mode itself
            st.session_state.get("screener_confirm_score", "All") != "All",
            bool(st.session_state.get("screener_zone_strength", [])),
        ])
    else:
        _active_screeners = sum([
            st.session_state.get("screener_proximity", "All") != "All",
            st.session_state.get("screener_min_score", "All") != "All",
            bool(st.session_state.get("screener_zone_strength", [])),
        ])
    _scr_note = f" | {_active_screeners} screener filter{'s' if _active_screeners != 1 else ''} active" if _active_screeners else ""
    st.caption(f"Showing {len(filtered)} of {total} stocks{_scr_note}")

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
        path = export_to_excel(
            results,
            wl_name,
            analysis_type,
            alerts,
            trading_type=st.session_state.get("trading_type", ""),
            primary_strategy=st.session_state.get("primary_strategy", analysis_type),
            enhancers=st.session_state.get("enhancers", []),
        )
        st.success(f"Exported to: `{path}`")
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
        path = export_to_pdf(
            results,
            wl_name,
            analysis_type,
            trading_type=st.session_state.get("trading_type", ""),
            primary_strategy=st.session_state.get("primary_strategy", analysis_type),
            enhancers=st.session_state.get("enhancers", []),
        )
        st.success(f"Exported to: `{path}`")
        with open(path, "rb") as fh:
            st.download_button(
                label="📥 Download PDF",
                data=fh.read(),
                file_name=path.name,
                mime="application/pdf",
            )
    except Exception as exc:
        st.error(f"PDF export failed: {exc}")


def _card_summary(result: dict) -> str:
    """Card summary, with the confirmed zone put first in confirmation mode.

    The default summary leads with the nearest DISPLAY zone, which in this mode
    is the wrong headline: on ONGC it announced a supply zone 17% away while
    the zone that actually matched the screener sat 3% away and went unnamed.
    Confirmation zones score below the display floor, so they can never appear
    in the default text — the card has to say which zone it matched on.
    """
    summary = result.get("summary", "")
    # Live session state — the same read _passes_screener and the chart
    # overlays use, so all three agree on whether the mode is active.
    if not st.session_state.get("screener_confirmation", False):
        return summary

    zones = result.get("confirmation_zones") or []
    if not zones:
        return summary

    price = result.get("current_price", 0.0) or 0.0
    parts = []
    for z in zones:
        prox = z.get("proximal", 0.0)
        gap = f" {abs(price - prox) / price * 100:.1f}%" if price else ""
        parts.append(
            f"{z.get('category', '')} {prox:g} "
            f"({z.get('zone_type', '')}, score {z.get('odd_score', 0):g}{gap})"
        )
    return f"Confirmed: {' · '.join(parts)} | {summary}"


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
                summary=_card_summary(result),
                current_price=result.get("current_price", 0.0),
                change=result.get("change", 0.0),
                change_pct=result.get("change_pct", 0.0),
                stock_id=result.get("stock_id", idx),
                strength=result.get("strength", "Weak"),
                updated_at=result.get("updated_at"),
                result=result,
                serial_no=idx + 1,
            )


def _run_single_stock_analysis(symbol: str) -> dict:
    """Run analysis for a single stock (new-tab / deep-link scenario).

    When the user opens a stock in a new browser tab via ?stock=SYMBOL there
    are no cached analysis results.  This fetches data and runs the configured
    analysis strategy so the detail view renders correctly.
    """
    exchange = st.session_state.get("_qp_exchange", "NSE")
    source_name = st.session_state.get("selected_data_source", "Yahoo Finance")
    trading_type = st.session_state.get("trading_type", "Options Trading")
    primary_strategy = st.session_state.get("primary_strategy", "Demand/Supply Zones")

    full_symbol = _make_symbol(symbol, exchange, source_name)
    ds_manager = DataSourceManager()
    creds = st.session_state.get("credentials", {}).get(source_name, {})
    try:
        if creds:
            ds_manager.switch_source(source_name, creds)
        else:
            ds_manager.switch_source(source_name)
    except Exception as exc:
        logger.error("Data source init failed for %s: %s", symbol, exc)
        return {}

    try:
        quote = ds_manager.get_quote(full_symbol)
        hist, _meta = fetch_for_trading_type(
            full_symbol, trading_type, fetch_fn=ds_manager.get_history
        )
        if hist is None:
            hist = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        analyser = get_analyzer_for_primary(primary_strategy)
        if isinstance(analyser, DemandSupplyAnalysis):
            result = analyser.analyse(
                full_symbol, hist,
                use_fibonacci=st.session_state.get("use_fibonacci", False),
            )
        else:
            result = analyser.analyse(full_symbol, hist)

        _quote_p = _valid_price(quote.get("current_price"))
        _result_p = _valid_price(result.get("current_price"))
        current_price = _quote_p if _quote_p is not None else (_result_p or 0.0)
        change_pct = float(quote.get("change_pct") or 0.0)
        change = round(current_price * change_pct / 100, 2)
        result.update({
            "current_price": current_price,
            "change_pct": change_pct,
            "change": change,
            "stock_id": 0,
            "exchange": exchange,
        })
        return result
    except Exception as exc:
        logger.error("Single-stock analysis error for %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "exchange": exchange,
            "status": "neutral",
            "summary": f"Error: {exc}",
            "current_price": 0.0,
            "change_pct": 0.0,
            "change": 0.0,
            "strength": "Weak",
            "stock_id": 0,
        }


def render_detail_view() -> None:
    """Render the detail view for the selected stock."""
    symbol = st.session_state.get("selected_stock_symbol")
    if not symbol:
        st.session_state.active_page = "dashboard"
        st.rerun()
        return

    results = st.session_state.get("analysis_results", {})
    result = results.get(symbol, {})

    # New-tab support: when opened via ?stock=SYMBOL there are no cached
    # analysis results.  Run analysis on-the-fly for this single stock.
    if not result:
        result = _run_single_stock_analysis(symbol)
        if result:
            results[symbol] = result
            st.session_state.analysis_results = results

    # Stage D: analysis_type IS primary_strategy (bridge removed)
    primary_strategy = st.session_state.get("primary_strategy", "Demand/Supply Zones")
    analysis_type = primary_strategy
    exchange = result.get("exchange") or st.session_state.get("_qp_exchange", "NSE")
    stock_id = result.get("stock_id") or st.session_state.get("selected_stock_id")

    # No OHLCV prefetch here: render_stock_detail fetches its own bars via
    # fetch_by_interval for the candle interval the user picks. A prefetch used
    # to run for a cache-priming step in that function, but the priming key
    # could never match the interval cache's key, so the frame was fetched and
    # then dropped — one wasted network round trip per detail view. Priming was
    # removed rather than repaired: the dashboard fetches the trading type's
    # timeframe (1y for Options Trading) while the chart fetches the interval's
    # (5y for Daily), so a working prime would have shown a different history
    # on first render than on every render after.
    render_stock_detail(
        symbol=symbol,
        exchange=exchange,
        analysis_type=analysis_type,
        result=result,
        stock_id=stock_id,
    )


def _make_symbol(symbol: str, exchange: str, source: str) -> str:
    """Format a ticker symbol for the active data source.

    Index tickers arrive already fully qualified (``^NSEI``, ``BSE-BANK.BO``)
    from the dashboard's index-tile deep links; suffixing those would produce
    ``^NSEI.NS``, which Yahoo does not recognise. Keep in step with
    ``stock_detail._source_symbol`` — they are separate code paths (Gotcha 10).
    """
    if source == "Yahoo Finance":
        if symbol.startswith("^") or symbol.upper().endswith((".NS", ".BO")):
            return symbol
        suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
        return f"{symbol}{suffix}"
    if source == "TradingView":
        return f"{exchange.upper()}:{symbol}"
    return symbol
