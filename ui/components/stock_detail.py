"""Full detailed stock analysis view with chart toggle, history, and notes."""

import html
import math
from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as st_components

from analysis.demand_supply import DemandSupplyAnalysis
from analysis.trend_following import TrendFollowingAnalysis
from analysis.zone_engine.patterns import WIDE_BASE_THRESHOLD_PCT
from config.preferences import load_preferences
from data.manager import (
    INTERVAL_OPTIONS,
    build_source_manager,
    default_interval_label,
    fetch_by_interval,
)
from data.market_indices import market_bias
from storage.database import (
    compare_analysis_results,
    delete_note,
    get_notes,
    save_note,
)
from ui.components.panels import bias_pill, kv_row, section_title
from ui.components.tradingview_chart import get_tradingview_url, render_tradingview_chart
from ui.pages.market_overview import indices_cached
from utils.helpers import format_timestamp, get_company_name
from utils.logger import get_logger
from utils.market_hours import get_current_ist_time, is_market_open

logger = get_logger(__name__)

_CHART_UP_COLOR = "#16794A"
_CHART_DOWN_COLOR = "#C23B33"
_CHART_BLUE = "#2F80ED"
_CHART_ORANGE = "#EF9F27"
_CHART_PURPLE = "#7B61E3"
_CHART_NEUTRAL = "#6B7280"
_CHART_GRID = "#E8EDF4"
_CHART_AXIS = "#4A5361"
_CHART_SPIKE = "#8A8F98"

_STATUS_COLOR = {
    "bullish": _CHART_UP_COLOR,
    "bearish": _CHART_DOWN_COLOR,
    "neutral": _CHART_ORANGE,
}

def _crosshair_js(show_date: bool) -> str:
    """Build JS for crosshair labels: price on y-axis always, date at top when tooltip is off.

    Uses a MutationObserver to re-bind listeners after Plotly fullscreen
    transitions which recreate the drag overlay elements.
    """
    show_date_flag = "true" if show_date else "false"
    return (
        "<script>\n"
        "(function() {\n"
        "    var doc = window.parent.document;\n"
        "    var showDate = " + show_date_flag + ";\n"
        "\n"
        "    function bind(plot) {\n"
        "        var drags = plot.querySelectorAll('.nsewdrag');\n"
        "        if (!drags.length) return;\n"
        "        var drag = drags[0];\n"
        "        if (drag._crosshairBound) return;\n"
        "        drag._crosshairBound = true;\n"
        "\n"
        "        plot.style.position = 'relative';\n"
        "        var badge = 'background:#787b86;color:#fff;font-size:11px;padding:1px 5px;'\n"
        "                  + 'pointer-events:none;display:none;z-index:1000;font-family:monospace;'\n"
        "                  + 'border-radius:2px;white-space:nowrap;position:absolute;';\n"
        "\n"
        "        plot.querySelectorAll('.y-price-label,.x-date-label')\n"
        "           .forEach(function(el) { el.remove(); });\n"
        "\n"
        "        var priceLabel = doc.createElement('div');\n"
        "        priceLabel.className = 'y-price-label';\n"
        "        priceLabel.style.cssText = badge + 'left:0;transform:translateY(-50%)';\n"
        "        plot.appendChild(priceLabel);\n"
        "\n"
        "        var dateLabel = null;\n"
        "        if (showDate) {\n"
        "            dateLabel = doc.createElement('div');\n"
        "            dateLabel.className = 'x-date-label';\n"
        "            dateLabel.style.cssText = badge + 'top:5px;transform:translateX(-50%)';\n"
        "            plot.appendChild(dateLabel);\n"
        "        }\n"
        "\n"
        "        drag.addEventListener('mousemove', function(e) {\n"
        "            var ya = plot._fullLayout.yaxis;\n"
        "            if (!ya || !ya.range) return;\n"
        "            var r = drag.getBoundingClientRect();\n"
        "            var pr = plot.getBoundingClientRect();\n"
        "            var frac = (e.clientY - r.top) / r.height;\n"
        "            var price = ya.range[1] - frac * (ya.range[1] - ya.range[0]);\n"
        "            priceLabel.textContent = price.toFixed(2);\n"
        "            priceLabel.style.top = (e.clientY - pr.top) + 'px';\n"
        "            priceLabel.style.display = 'block';\n"
        "        });\n"
        "\n"
        "        if (dateLabel) {\n"
        "            var M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];\n"
        "            plot.on('plotly_hover', function(data) {\n"
        "                if (!data.points || !data.points.length) return;\n"
        "                var d = new Date(data.points[0].x);\n"
        "                dateLabel.textContent = d.getDate()+' '+M[d.getMonth()]+' '+d.getFullYear();\n"
        "                try {\n"
        "                    var xa = plot._fullLayout.xaxis;\n"
        "                    dateLabel.style.left = (xa._offset+xa.l2p(xa.d2l(d.getTime())))+'px';\n"
        "                } catch(ex) {\n"
        "                    var pr2 = plot.getBoundingClientRect();\n"
        "                    dateLabel.style.left = (data.event.clientX-pr2.left)+'px';\n"
        "                }\n"
        "                dateLabel.style.display = 'block';\n"
        "            });\n"
        "            plot.on('plotly_unhover', function() {\n"
        "                dateLabel.style.display = 'none';\n"
        "            });\n"
        "        }\n"
        "\n"
        "        drag.addEventListener('mouseleave', function() {\n"
        "            priceLabel.style.display = 'none';\n"
        "            if (dateLabel) dateLabel.style.display = 'none';\n"
        "        });\n"
        "    }\n"
        "\n"
        "    function init(n) {\n"
        "        if (n > 15) return;\n"
        "        var plots = doc.querySelectorAll('.js-plotly-plot');\n"
        "        if (!plots.length) { setTimeout(function(){ init(n+1); }, 300); return; }\n"
        "        var plot = plots[plots.length - 1];\n"
        "        var drags = plot.querySelectorAll('.nsewdrag');\n"
        "        if (!drags.length) { setTimeout(function(){ init(n+1); }, 300); return; }\n"
        "\n"
        "        bind(plot);\n"
        "\n"
        "        var observer = new MutationObserver(function() {\n"
        "            var newDrags = plot.querySelectorAll('.nsewdrag');\n"
        "            if (newDrags.length && !newDrags[0]._crosshairBound) {\n"
        "                bind(plot);\n"
        "            }\n"
        "        });\n"
        "        observer.observe(plot, { childList: true, subtree: true });\n"
        "    }\n"
        "    init(0);\n"
        "})();\n"
        "</script>\n"
    )

# Lookback windows (calendar days) for the period selector buttons
_PERIOD_DAYS = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365, "2Y": 730, "3Y": 1095, "4Y": 1460, "5Y": 1825}

# Interval-selector labels list — stable order for the radio widget.
_INTERVAL_LABELS: list[str] = list(INTERVAL_OPTIONS.keys())


def _source_symbol(symbol: str, exchange: str, source_name: str) -> str:
    """Format a chart fetch symbol for the selected data source."""
    if source_name == "Yahoo Finance":
        suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
        return f"{symbol}{suffix}"
    if source_name == "TradingView":
        return f"{exchange.upper()}:{symbol}"
    return symbol


def _make_analyser_for_chart(primary_strategy: str):
    """Instantiate the correct analyser for a chart re-analysis.

    Local copy of the dashboard routing logic — avoids a circular import
    (dashboard imports stock_detail; stock_detail must not import dashboard).

    Args:
        primary_strategy: One of ``config.trading_config.PRIMARY_STRATEGIES``.

    Returns:
        A fresh :class:`~analysis.base.BaseAnalysis` instance.
    """
    if primary_strategy == "Trend Following (SMA50/EMA20)":
        return TrendFollowingAnalysis()
    return DemandSupplyAnalysis()


def render_stock_detail(
    symbol: str,
    exchange: str,
    analysis_type: str,
    result: dict[str, Any],
    stock_id: int | None = None,
) -> None:
    """Render the full detailed analysis view for a single stock.

    Chart data is fetched here via ``fetch_by_interval`` for whichever candle
    interval the user selects, so no OHLCV frame is passed in — the caller
    used to supply one for a cache-priming step that could never take effect
    (its key lacked the interval cache's ``use_fib`` component) and which
    would have served the wrong bar count anyway, since the dashboard fetches
    the trading type's timeframe while the chart fetches the interval's.

    Args:
        symbol: Stock ticker.
        exchange: Exchange (NSE/BSE).
        analysis_type: The analysis type run.
        result: Analysis result dict from the analysis module.
        stock_id: Database stock id for history/notes lookup.
    """
    _render_detail_header(symbol, exchange, analysis_type, result)

    if "error" in result:
        st.error(result["error"])
        return

    # Chart is FIRST because st.tabs has no way to preselect a tab — it always
    # opens the first one. This page is reached by clicking "View" on a stock,
    # so landing anywhere but the chart means the thing the user asked for is
    # off screen behind a tab.
    tabs = st.tabs([
        "Chart", "Overview", "Analysis", "Zones", "Confluence",
        "Trade Plan", "Notes",
    ])

    # Streamlit executes every tab body on each run regardless of which one is
    # on screen, so running the chart panel here makes chart_result -- the
    # re-analysis at the selected candle interval -- available to every other
    # tab without analysing twice.
    with tabs[0]:
        chart_df, chart_result = _render_chart_panel(
            symbol, exchange, analysis_type, result
        )

    with tabs[1]:
        _render_overview_tab(symbol, chart_result, chart_df, analysis_type)

    with tabs[2]:
        _render_metrics(chart_result, analysis_type)
        _recommendation = (
            chart_result.get("recommendation")
            or chart_result.get("summary", "")
        )
        if _recommendation:
            st.markdown("#### Recommendation")
            for _line in _recommendation.split("\n"):
                if _line.strip():
                    st.markdown(_line.strip())
        if stock_id is not None:
            st.markdown("---")
            _render_history_section(stock_id, analysis_type)

    with tabs[3]:
        if analysis_type == "Demand/Supply Zones":
            _render_zones_tab(chart_result)
        else:
            st.caption("Zone detail applies to the Demand/Supply strategy.")

    with tabs[4]:
        _render_confluence_tab(chart_result)

    with tabs[5]:
        _render_trade_plan(full=True)

    with tabs[6]:
        if stock_id is not None:
            _render_notes_section(stock_id)
        else:
            st.caption(
                "Notes are saved per watchlist stock. Open this stock from a "
                "watchlist to add notes."
            )


def _render_chart_panel(
    symbol: str, exchange: str, analysis_type: str, result: dict[str, Any],
) -> tuple[pd.DataFrame | None, dict[str, Any]]:
    """Chart controls, interval re-analysis and the chart itself.

    Lifted out of ``render_stock_detail`` unchanged when the page gained tabs
    -- the body below is the original chart section verbatim. It returns the
    interval-specific frame and analysis result because the other tabs and the
    Setup Summary rail all read from that re-analysis, not from the dashboard
    scan's result.
    """
    # ---------- Chart section ----------
    # -----------------------------------------------------------------------
    # Interval selector — lets the user pick candle size independently of
    # the trading-type default.  Changing it re-fetches data AND re-runs
    # analysis at the new interval so chart overlays stay consistent.
    # -----------------------------------------------------------------------
    _trading_type = st.session_state.get("trading_type", "Options Trading")
    _default_label = default_interval_label(_trading_type)
    _iv_key = f"detail_interval_radio_{symbol}"
    # Initialise to the trading-type default on first open for this stock.
    st.session_state.setdefault(_iv_key, _default_label)

    # The data source participates in the chart cache key below, so switching
    # source refetches rather than replaying the previous source's bars.
    _src_name = st.session_state.get("selected_data_source", "Yahoo Finance")
    _cache_src_key = _src_name.replace(" ", "_")

    # Chart controls: Chart Type | Candle Interval
    ct_col, iv_col = st.columns([2, 5])
    with ct_col:
        chart_type = st.radio(
            "Chart Type",
            ["Candlestick", "Line", "TradingView"],
            horizontal=True,
            key="chart_type_radio",
        )

    # Interval selector — hidden for TradingView (TV has its own controls)
    if chart_type != "TradingView":
        with iv_col:
            _cur_label = st.session_state.get(_iv_key, _default_label)
            _cur_idx = (
                _INTERVAL_LABELS.index(_cur_label)
                if _cur_label in _INTERVAL_LABELS else 0
            )
            interval_label: str = st.radio(
                "Candle Interval",
                _INTERVAL_LABELS,
                index=_cur_idx,
                horizontal=True,
                key=_iv_key,
            )
    else:
        interval_label = st.session_state.get(_iv_key, _default_label)

    # Period range — zoom/window on the fetched data (does not re-fetch)
    selected_period = "1Y"
    if chart_type != "TradingView":
        _period_options = list(_PERIOD_DAYS.keys())
        selected_period = st.segmented_control(
            "Period",
            _period_options,
            default="1Y",
            key="chart_period_segmented",
            width="stretch",
        ) or "1Y"

    # -----------------------------------------------------------------------
    # Fetch + re-analyse for the selected interval (with per-stock caching).
    # Cache key: f"detail_cache_{symbol}_{interval_label}" — switching back
    # to a previously viewed interval reuses the cached (df, result) pair
    # without an additional network call.
    # -----------------------------------------------------------------------
    _use_fib = st.session_state.get("use_fibonacci", False)
    # The data source belongs in the key: without it, switching Yahoo -> Jugaad
    # hits this cache and returns the old Yahoo dataframe, skipping the
    # source-aware fetch below entirely. The zones are then recomputed from
    # that stale data too, so the whole detail view silently shows the wrong
    # source (and, for symbols where Yahoo drops a trading day, a gap).
    # The confirmation mode is deliberately NOT part of this key. What is
    # cached is (dataframe, analysis result) — not the figure, which is rebuilt
    # from them on every rerun. ``confirmation_zones`` is always present in the
    # result regardless of the checkbox, and the overlay decides at draw time
    # whether to use it, so the cached value is mode-independent. Keying on the
    # mode would only force a redundant refetch and re-analysis on every toggle.
    _chart_cache_key = (
        f"detail_cache_{symbol}_{interval_label}_{_use_fib}_{_cache_src_key}"
    )
    if st.session_state.get(_chart_cache_key) is None:
        # Need a fresh fetch at this interval.
        # Fetch through the data source the user actually selected. Without an
        # explicit fetch_fn, fetch_by_interval falls back to _default_fetch_fn,
        # which is hard-wired to Yahoo Finance — the chart would silently ignore
        # a Jugaad/NSE selection and show different prices than the analysis.
        full_symbol = _source_symbol(symbol, exchange, _src_name)
        _src_creds = st.session_state.get("credentials", {}).get(_src_name, {})
        with st.spinner(f"Fetching {interval_label} data for {symbol}…"):
            try:
                _chart_fetch_fn = build_source_manager(_src_name, _src_creds).get_history
            except Exception as exc:
                # Surface the failure instead of quietly charting Yahoo data.
                st.warning(f"Could not use {_src_name} ({exc}); showing Yahoo Finance data.")
                _chart_fetch_fn = None
                full_symbol = _source_symbol(symbol, exchange, "Yahoo Finance")
            _chart_df, _fetch_meta = fetch_by_interval(
                full_symbol, interval_label, fetch_fn=_chart_fetch_fn,
            )

        if _chart_df is not None and not _chart_df.empty:
            # Re-run the same primary strategy on the interval-specific data.
            _primary = st.session_state.get("primary_strategy", "Demand/Supply Zones")
            _analyser = _make_analyser_for_chart(_primary)
            try:
                if isinstance(_analyser, DemandSupplyAnalysis):
                    _chart_result = _analyser.analyse(
                        symbol, _chart_df, use_fibonacci=_use_fib
                    )
                else:
                    _chart_result = _analyser.analyse(symbol, _chart_df)
            except Exception as exc:
                logger.warning(
                    "Interval re-analysis failed for %s at %s: %s",
                    symbol, interval_label, exc,
                )
                _chart_result = result  # safe fallback — use dashboard result
            # Preserve the live price from the dashboard quote so the header
            # caption stays accurate regardless of what the resampled close is.
            _live_price = result.get("current_price", 0.0)
            if _live_price and _live_price > 0:
                _chart_result["current_price"] = _live_price
            for _k in ("change_pct", "change", "exchange", "stock_id"):
                if _k in result:
                    _chart_result[_k] = result[_k]
        else:
            _chart_df = None
            _chart_result = result  # safe fallback

        _cache_msg = _fetch_meta.get("message", "")
        st.session_state[_chart_cache_key] = (_chart_df, _chart_result, _cache_msg)

    chart_df, chart_result, chart_meta_msg = st.session_state[_chart_cache_key]

    # Caption: shows which interval the chart uses and any fallback note.
    _interval_caption = f"Candles: **{interval_label}** | Analysis recomputed at this interval"
    if chart_meta_msg:
        _interval_caption += f" | ⚠️ {chart_meta_msg}"
    st.caption(_interval_caption)

    # Rebuild trend/signal badges from the chart_result (reflects the chosen interval)
    _chart_trend = chart_result.get("trend")
    _chart_is_tf = chart_result.get("strategy") == "Trend Following"
    chart_header = "### Price Chart"
    if _chart_trend:
        _tc = _TREND_BADGE_COLORS.get(_chart_trend, _CHART_NEUTRAL)
        chart_header += (
            f" <span style='color:{_tc};font-size:0.65em;background:{_tc}22;"
            f"padding:2px 8px;border-radius:8px;border:1px solid {_tc};'>"
            f"Trend: {_chart_trend}</span>"
        )
    if _chart_is_tf:
        _chart_signal = chart_result.get("signal", "HOLD")
        _sc = {"BUY": _CHART_UP_COLOR, "SELL": _CHART_DOWN_COLOR}.get(
            _chart_signal, _CHART_NEUTRAL
        )
        chart_header += (
            f" <span style='color:{_sc};font-size:0.65em;background:{_sc}22;"
            f"padding:2px 8px;border-radius:8px;border:1px solid {_sc};'>"
            f"Signal: {_chart_signal}</span>"
        )
    st.markdown(chart_header, unsafe_allow_html=True)

    # -----------------------------------------------------------------------
    # Render the chart
    # -----------------------------------------------------------------------
    if chart_type == "TradingView":
        link_col, hint_col = st.columns([1, 3])
        with link_col:
            st.link_button(
                "🔗 Open in TradingView →",
                url=get_tradingview_url(symbol, exchange),
            )
        with hint_col:
            st.caption(
                "Opens candlestick chart in TradingView. "
                "Log in to TradingView for full access."
            )
        render_tradingview_chart(
            symbol=symbol,
            exchange=exchange,
            height=600,
            default_interval="D",
            compact=False,
            theme="light",
        )
    elif chart_df is not None and not chart_df.empty:
        # _filter_by_period slices the fetched data for the Period range zoom.
        # Guard: if the slice would be empty (e.g. 1W window on Monthly candles),
        # _filter_by_period already falls back to the full dataset — no extra
        # handling needed here.
        df_view = _filter_by_period(chart_df, selected_period)
        fig = _build_chart(symbol, df_view, chart_result, analysis_type, chart_type, full_df=chart_df, interval_label=interval_label)
        if analysis_type == "Demand/Supply Zones":
            st.caption(
                "Showing nearest fresh zones (score >= 5). "
                "Tested/used-up zones hidden."
            )
        elif analysis_type == "Trend Following (SMA50/EMA20)":
            st.caption(
                "50 SMA (orange) and 200 SMA (blue) — cross marker shown "
                "if within the displayed window."
            )
        show_tooltip = load_preferences().get("show_candle_tooltip", True)
        if not show_tooltip:
            fig.update_layout(hovermode="closest")
            fig.update_traces(hoverinfo="none")
        st.plotly_chart(fig, use_container_width=True, key=f"plotly_{symbol}_{selected_period}", config={"scrollZoom": True})
        _js_bust = f"<!-- {symbol}_{selected_period}_{interval_label} -->"
        st_components.html(_js_bust + _crosshair_js(show_date=not show_tooltip), height=0)
    else:
        st.warning(
            "Unable to load chart data for the selected interval. "
            "Try a different interval or check your internet connection."
        )

    return chart_df, chart_result


# ---------------------------------------------------------------------------
# Page header and the Setup Summary / Trade Plan rail
# ---------------------------------------------------------------------------

def _market_status_text() -> str:
    """"Market Open/Closed" plus today's IST date, for the price block."""
    try:
        now = get_current_ist_time()
        state = "Market Open" if is_market_open(now) else "Market Closed"
        return f"{state} · {now.strftime('%d %b %Y')}"
    except Exception:
        return ""


def _render_detail_header(
    symbol: str, exchange: str, analysis_type: str, result: dict[str, Any],
) -> None:
    """Symbol, verdict badge, live price and the page actions.

    The badge is derived from the best zone's category, not from
    ``result["status"]`` — status is "neutral" for most stocks and would put a
    WAIT badge on a page whose chart clearly shows a fresh demand zone.
    """
    price = result.get("current_price", 0.0) or 0.0
    change = float(result.get("change", 0.0) or 0.0)
    change_pct = float(result.get("change_pct", 0.0) or 0.0)
    company = get_company_name(symbol)

    zones = [*(result.get("demand_zones") or []),
             *(result.get("supply_zones") or [])]
    best = max(zones, key=lambda z: z.get("odd_score", 0)) if zones else {}
    if best.get("category") == "demand":
        action, tone = "BUY", "bullish"
    elif best.get("category") == "supply":
        action, tone = "SELL", "bearish"
    else:
        action, tone = "WAIT", "muted"
    if best and not best.get("is_tradeable"):
        action, tone = "WAIT", "warning"

    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            "<div style='font-size:0.78rem;color:#9AA0A8;margin-bottom:2px;'>"
            "Dashboard &nbsp;&rsaquo;&nbsp; Analysis Results &nbsp;&rsaquo;&nbsp; "
            f"<span style='color:#4A5361;font-weight:600;'>{html.escape(symbol)}"
            "</span></div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:10px;'>"
            f"<span style='font-size:1.75rem;font-weight:800;color:#16233A;"
            f"letter-spacing:-0.2px;'>{html.escape(symbol)}</span>"
            f"{bias_pill(action, tone)}</div>"
            f"<div style='font-size:0.86rem;color:#6B7280;margin-top:1px;'>"
            f"{html.escape(company)} &middot; {html.escape(exchange)} &middot; "
            f"{html.escape(analysis_type)}</div>",
            unsafe_allow_html=True,
        )
    with right:
        up = change >= 0
        colour = "#16794A" if up else "#C23B33"
        st.markdown(
            f"<div style='text-align:right;'>"
            f"<div style='font-size:1.6rem;font-weight:800;color:#16233A;'>"
            f"&#8377;{price:,.2f}</div>"
            f"<div style='font-size:0.86rem;color:{colour};font-weight:600;'>"
            f"{change:+,.2f} ({change_pct:+.2f}%)</div>"
            f"<div style='font-size:0.74rem;color:#9AA0A8;'>"
            f"{html.escape(_market_status_text())}</div></div>",
            unsafe_allow_html=True,
        )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Back to Results", icon=":material/arrow_back:",
                         use_container_width=True, key="sd_back_results"):
                st.session_state.active_page = "analysis_results"
                st.session_state.selected_stock_symbol = None
                st.query_params.clear()
                st.rerun()
        with b2:
            if st.button("Dashboard", icon=":material/dashboard:",
                         use_container_width=True, key="sd_back_dash"):
                st.session_state.active_page = "dashboard"
                st.session_state.selected_stock_symbol = None
                st.query_params.clear()
                st.rerun()
    st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)


def _ma_position(df: "pd.DataFrame | None", price: float, span: int,
                 exponential: bool) -> tuple[str, str]:
    """Where price sits against a moving average, as (text, colour).

    Computed here from the chart's own frame rather than read off a zone flag:
    ``ema20_enhancer`` records whether a ZONE had EMA confluence, which is a
    different question from where price is right now.
    """
    try:
        if df is None or df.empty or "Close" not in df or price <= 0:
            return "\u2014", "#A8A8A0"
        closes = df["Close"].astype(float)
        if len(closes) < span:
            return "\u2014", "#A8A8A0"
        ma = (closes.ewm(span=span, adjust=False).mean() if exponential
              else closes.rolling(span).mean()).iloc[-1]
        if pd.isna(ma):
            return "\u2014", "#A8A8A0"
        above = price > float(ma)
        return ("Above price" if above else "Below price",
                "#16794A" if above else "#C23B33")
    except Exception:
        return "\u2014", "#A8A8A0"


def _render_setup_rail(result: dict[str, Any], df: "pd.DataFrame | None") -> None:
    """Setup Summary -- the read-out beside the chart."""
    zones = [*(result.get("demand_zones") or []),
             *(result.get("supply_zones") or [])]
    best = max(zones, key=lambda z: z.get("odd_score", 0)) if zones else {}
    price = float(result.get("current_price") or 0.0)

    with st.container(border=True):
        section_title("Setup Summary")
        trend = (result.get("trend") or "\u2014").upper()
        kv_row("Trend", trend,
               tone={"UP": "#16794A", "DOWN": "#C23B33"}.get(trend, "#8A8F98"))
        if best:
            kv_row("Zone", f"{best.get('zone_type', '')} "
                           f"({'Fresh' if best.get('is_fresh') else 'Tested'})")
            kv_row("Zone Strength", str(best.get("zone_strength", "\u2014")))
            # Scale is 7, not 10 -- the ODD score has seven points available
            # (freshness 3 + strength 2 + time 2). Showing /10 would misstate
            # how good a 6.0 actually is.
            kv_row("ODD Score", f"{best.get('odd_score', 0):.1f} / 7")
        else:
            kv_row("Zone", "No tradeable zone")
        kv_row("RR (Min)", pending="p2")

        ema_txt, ema_col = _ma_position(df, price, 20, True)
        sma_txt, sma_col = _ma_position(df, price, 50, False)
        kv_row("EMA 20", ema_txt, tone=ema_col)
        # The engine's trend clock uses a 50 SMA, not an EMA -- labelled for
        # what is actually computed.
        kv_row("SMA 50", sma_txt, tone=sma_col)

        kv_row("Sector", pending="p7")
        try:
            bias, _reason = market_bias(indices_cached())
            kv_row("Market", bias.title(),
                   tone={"BULLISH": "#16794A",
                         "BEARISH": "#C23B33"}.get(bias, "#8A8F98"))
        except Exception:
            kv_row("Market", "\u2014")
        kv_row("Status", str(best.get("entry_recommendation", "\u2014"))
               if best else "\u2014")


def _render_trade_plan(full: bool = False) -> None:
    """Quick Trade Plan -- every field is Phase 2 (M1).

    Rendered with real labels and explicit pending markers rather than
    omitted, so the layout is settled and the missing inputs stay visible.
    Nothing here is estimated: an invented entry or stop is worse than a
    blank one.
    """
    with st.container(border=True):
        section_title("Trade Plan" if full else "Quick Trade Plan")
        for label in ("Entry Zone", "Stop Loss", "Target 1", "Target 2",
                      "Risk / Reward", "Position Size"):
            kv_row(label, pending="p2")
        if full:
            st.caption(
                "Entry, stop, targets and position sizing are Phase 2 (M1) "
                "and Phase 6 (M31). No values are estimated here."
            )


def _render_overview_tab(
    symbol: str, result: dict[str, Any], df: "pd.DataFrame | None",
    analysis_type: str,
) -> None:
    """Setup summary, trade plan and key levels, without the chart."""
    a, b, c = st.columns(3)
    with a:
        _render_setup_rail(result, df)
    with b:
        _render_trade_plan()
    with c:
        with st.container(border=True):
            section_title("Key Levels")
            nd, ns = result.get("nearest_demand"), result.get("nearest_supply")
            # Straight off the nearest zone boundaries: the proximal is the
            # level price meets first, the distal the one that invalidates it.
            kv_row("Support 1", f"{nd['proximal']:,.2f}" if nd else "\u2014")
            kv_row("Support 2", f"{nd['distal']:,.2f}" if nd else "\u2014")
            kv_row("Resistance 1", f"{ns['proximal']:,.2f}" if ns else "\u2014")
            kv_row("Resistance 2", f"{ns['distal']:,.2f}" if ns else "\u2014")


def _render_zones_tab(result: dict[str, Any]) -> None:
    """Every drawn zone with its boundaries and score."""
    zones = [*(result.get("demand_zones") or []),
             *(result.get("supply_zones") or [])]
    if not zones:
        st.caption(
            "No zones passed the display filter for this interval. "
            f"{result.get('all_zones_count', 0)} were detected in total."
        )
        return
    rows = ""
    for z in sorted(zones, key=lambda x: x.get("odd_score", 0), reverse=True):
        tone = "bullish" if z.get("category") == "demand" else "bearish"
        rows += (
            "<tr>"
            f"<td style='padding:6px 8px;'>{bias_pill(z.get('zone_type', ''), tone)}</td>"
            f"<td style='padding:6px 8px;'>{z.get('proximal', 0):,.2f}</td>"
            f"<td style='padding:6px 8px;'>{z.get('distal', 0):,.2f}</td>"
            f"<td style='padding:6px 8px;'><b>{z.get('odd_score', 0):.1f}</b></td>"
            f"<td style='padding:6px 8px;'>{html.escape(str(z.get('zone_strength', '')))}</td>"
            f"<td style='padding:6px 8px;'>{z.get('times_tested', 0)}</td>"
            f"<td style='padding:6px 8px;'>{z.get('base_width_pct', 0):.1f}%</td>"
            "</tr>"
        )
    head = "".join(
        f"<th style='text-align:left;padding:6px 8px;font-size:0.7rem;"
        f"color:#8A8F98;border-bottom:1px solid #E7E9ED;'>{h}</th>"
        for h in ["Type", "Proximal", "Distal", "ODD", "Strength",
                  "Tested", "Base Width"]
    )
    st.markdown(
        f"<div style='overflow-x:auto;'><table style='width:100%;"
        f"border-collapse:collapse;font-size:0.8rem;'><tr>{head}</tr>"
        f"{rows}</table></div>",
        unsafe_allow_html=True,
    )
    _render_zone_widths(result)


def _render_confluence_tab(result: dict[str, Any]) -> None:
    """EMA 20 and Fibonacci confluence, per zone."""
    zones = [*(result.get("demand_zones") or []),
             *(result.get("supply_zones") or [])]
    if not zones:
        st.caption("No zones to report confluence for.")
        return
    fib_on = bool(result.get("fib_levels"))
    if not fib_on:
        st.caption(
            "Fibonacci confluence is opt-in \u2014 enable it in the sidebar "
            "enhancers to populate the Fib columns."
        )
    for z in sorted(zones, key=lambda x: x.get("odd_score", 0), reverse=True):
        with st.container(border=True):
            section_title(
                f"{z.get('zone_type', '')} @ {z.get('proximal', 0):,.2f}",
                hint=f"ODD {z.get('odd_score', 0):.1f}",
            )
            kv_row("EMA 20 confluence",
                   "Yes" if z.get("ema20_enhancer") else "No",
                   tone="#16794A" if z.get("ema20_enhancer") else "#8A8F98")
            if fib_on:
                kv_row("Fibonacci confluence",
                       "Yes" if z.get("fib_confluence") else "No",
                       tone="#16794A" if z.get("fib_confluence") else "#8A8F98")
                kv_row("Strongest level", str(z.get("fib_strongest") or "\u2014"))
                kv_row("Combined rating", str(z.get("confluence_label") or "None"))
            else:
                kv_row("Fibonacci confluence", "Not enabled")
            kv_row("Closing quality", str(z.get("closing_quality", "unchecked")).title())
            kv_row("Zone quality", str(z.get("zone_quality") or "Clean"))



# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _render_metrics(result: dict[str, Any], analysis_type: str) -> None:
    """Render a row of key metric chips based on analysis type."""
    cols = st.columns(4)
    metrics: list[tuple[str, str]] = []

    if analysis_type == "Demand/Supply Zones":
        nd = result.get("nearest_demand")
        ns = result.get("nearest_supply")
        metrics = [
            ("Current Price", f"₹{result.get('current_price', 0):,.2f}"),
            ("Nearest Demand", f"₹{nd['mid']:,.2f}" if nd else "—"),
            ("Nearest Supply", f"₹{ns['mid']:,.2f}" if ns else "—"),
            ("Strength", result.get("strength", "—")),
        ]
    elif analysis_type == "Trend Following (SMA50/EMA20)":
        _last_cross = result.get("last_cross") or {}
        _cross_t = _last_cross.get("type")
        _cross_ago = _last_cross.get("candles_ago")
        _cross_str = (
            f"{_cross_t.capitalize()} ({_cross_ago}c ago)"
            if _cross_t and _cross_ago is not None
            else ("—" if not _cross_t else _cross_t.capitalize())
        )
        _sma_fast = result.get("sma_fast_now")
        _sma_slow = result.get("sma_slow_now")
        metrics = [
            ("Current Price", f"₹{result.get('current_price', 0):,.2f}"),
            ("Signal", result.get("signal", "—")),
            ("SMA 50", f"₹{_sma_fast:,.2f}" if _sma_fast is not None else "—"),
            ("Last Cross", _cross_str),
        ]
    elif analysis_type == "Long Term Investment":
        metrics = [
            ("Current Price", f"₹{result.get('current_price', 0):,.2f}"),
            ("SMA 200", f"₹{result.get('sma_200', 0):,.2f}"),
            ("52W High", f"₹{result.get('high_52w', 0):,.2f}"),
            ("52W Low", f"₹{result.get('low_52w', 0):,.2f}"),
        ]
    elif analysis_type == "Short Term Investment":
        metrics = [
            ("Current Price", f"₹{result.get('current_price', 0):,.2f}"),
            ("SMA 50", f"₹{result.get('sma_50', 0):,.2f}"),
            ("RSI", f"{result.get('rsi', 0):.1f}"),
            ("MACD Hist", f"{result.get('macd_hist', 0):.4f}"),
        ]
    elif analysis_type == "Intraday Trading":
        metrics = [
            ("Current Price", f"₹{result.get('current_price', 0):,.2f}"),
            ("VWAP", f"₹{result.get('vwap', 0):,.2f}"),
            ("RSI", f"{result.get('rsi', 0):.1f}"),
            ("Vol Ratio", f"{result.get('volume_ratio', 0):.1f}x"),
        ]

    for col, (label, value) in zip(cols, metrics):
        with col:
            st.metric(label, value)


def _render_zone_widths(result: dict[str, Any]) -> None:
    """M12: show every displayed zone's base width next to its boundaries.

    The chart label only flags bases wider than
    :data:`WIDE_BASE_THRESHOLD_PCT`, so a tight base leaves no trace there.
    This panel always prints the raw number — when sizing a trade, knowing a
    base is 0.8% wide matters as much as knowing another is 5%.
    """
    zones = [*result.get("demand_zones", []), *result.get("supply_zones", [])]
    if not zones:
        return

    with st.expander("Zone base widths", expanded=False):
        st.caption(
            "Base width is the full base range (highest high to lowest low) "
            f"as a percentage of the proximal. Above {WIDE_BASE_THRESHOLD_PCT:.0f}% "
            "the zone is flagged \"Wide Base\" on the chart — the stop has to "
            "sit far from entry, which hurts R:R."
        )
        rows = []
        for z in zones:
            width = float(z.get("base_width_pct", 0.0) or 0.0)
            rows.append({
                "Zone": z.get("zone_type", "—"),
                "Proximal": f"₹{z.get('proximal', 0):,.2f}",
                "Distal": f"₹{z.get('distal', 0):,.2f}",
                "Score": _fmt_zone_score(z.get("odd_score", 0)),
                "Base width": f"{width:.1f}%",
                "Flag": "Wide Base" if width > WIDE_BASE_THRESHOLD_PCT else "",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Period filter helper
# ---------------------------------------------------------------------------

def _filter_by_period(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Return the slice of *df* covering the requested lookback period.

    Handles both tz-aware and tz-naive DatetimeIndex so the comparison
    never raises a mixed-timezone TypeError.  Falls back to the full
    DataFrame if the sliced result would be empty.

    Args:
        df: OHLCV DataFrame with a DatetimeIndex.
        period: One of "1W", "1M", "3M", "6M", "1Y", "2Y", "3Y", "4Y", "5Y".

    Returns:
        Sliced (or original) DataFrame.
    """
    days = _PERIOD_DAYS.get(period, 365)
    tz = df.index.tz                              # None for tz-naive index
    now = pd.Timestamp.now(tz=tz)
    cutoff = now - pd.Timedelta(days=days)
    sliced = df[df.index >= cutoff]
    return sliced if not sliced.empty else df


# ---------------------------------------------------------------------------
# Chart builder
# ---------------------------------------------------------------------------

def _build_chart(
    symbol: str,
    df: pd.DataFrame,
    result: dict[str, Any],
    analysis_type: str,
    chart_type: str,
    full_df: pd.DataFrame | None = None,
    interval_label: str = "Daily",
) -> go.Figure:
    """Build an interactive Plotly chart with volume subplot and overlays."""
    show_rsi = analysis_type == "Short Term Investment"

    # Row heights: price + volume (+ optional RSI)
    if show_rsi:
        row_heights = [0.55, 0.2, 0.25]
        rows = 3
        specs = [[{"type": "xy"}], [{"type": "xy"}], [{"type": "xy"}]]
        subplot_titles = (symbol, "Volume", "RSI")
    else:
        row_heights = [0.7, 0.3]
        rows = 2
        specs = [[{"type": "xy"}], [{"type": "xy"}]]
        subplot_titles = (symbol, "Volume")

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=row_heights,
        specs=specs,
        subplot_titles=subplot_titles,
    )

    # --- Price trace ---
    if chart_type == "Candlestick":
        fig.add_trace(
            go.Candlestick(
                x=df.index,
                open=df["Open"],
                high=df["High"],
                low=df["Low"],
                close=df["Close"],
                name=symbol,
                increasing_line_color=_CHART_UP_COLOR,
                decreasing_line_color=_CHART_DOWN_COLOR,
                showlegend=False,
            ),
            row=1, col=1,
        )
    else:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Close"],
                name="Close",
                line={"color": _CHART_BLUE, "width": 2.2},
                fill="tozeroy",
                fillcolor="rgba(47,128,237,0.10)",
                showlegend=False,
            ),
            row=1, col=1,
        )

    # --- Analysis overlays ---
    if analysis_type == "Demand/Supply Zones":
        _add_trend_context_lines(fig, df)
        _add_zone_overlays(fig, result, df, full_df=full_df)
        # Stage 3 (opt-in) — only draws anything when the Fibonacci
        # confluence checkbox was on (detected via result["fib_levels"]).
        _add_fibonacci_lines(fig, result, df)
    elif analysis_type == "Trend Following (SMA50/EMA20)":
        # Prominent 50 SMA + 200 SMA lines plus a cross marker — no zone
        # rectangles (there are none in a Trend Following result).
        _add_tf_sma_lines(fig, df)
        _add_tf_cross_marker(fig, df, result)
    elif analysis_type == "Long Term Investment":
        _add_sma_line(fig, df, result.get("sma_200"), "SMA 200", _CHART_BLUE)
    elif analysis_type == "Short Term Investment":
        _add_sma_line(fig, df, result.get("sma_50"), "SMA 50", _CHART_ORANGE)
    elif analysis_type == "Intraday Trading":
        _add_vwap_line(fig, df, result.get("vwap"))

    # --- Volume bars ---
    vol_colors = [
        _CHART_UP_COLOR if c >= o else _CHART_DOWN_COLOR
        for c, o in zip(df["Close"], df["Open"])
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=vol_colors,
            showlegend=False,
            opacity=0.62,
        ),
        row=2, col=1,
    )

    # --- RSI subplot (Short Term only) ---
    if show_rsi:
        rsi_val = result.get("rsi", 50)
        # Build a simple RSI series for visual reference using the result value
        # (full RSI series computation is available in analysis/short_term.py)
        import numpy as np
        from analysis.short_term import _compute_rsi as _rt
        closes = df["Close"]
        delta = closes.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_series = 100 - (100 / (1 + rs))

        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=rsi_series,
                name="RSI",
                line={"color": _CHART_PURPLE, "width": 1.5},
                showlegend=False,
            ),
            row=3, col=1,
        )
        fig.add_hline(y=70, line_dash="dot", line_color=_CHART_DOWN_COLOR, row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color=_CHART_UP_COLOR, row=3, col=1)

    fig.update_layout(
        height=560 + (120 if show_rsi else 0),
        template="plotly_white",
        margin={"t": 40, "b": 20, "l": 55, "r": 80},
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font={"color": _CHART_AXIS},
        xaxis=dict(
            rangeslider=dict(visible=False),
        ),
        yaxis=dict(side="left"),
    )
    # Place the rangeslider at the very bottom of the chart (below the last subplot)
    bottom_xaxis = f"xaxis{rows}"   # "xaxis2" for 2-row, "xaxis3" for 3-row
    fig.update_layout(
        **{bottom_xaxis: {"rangeslider": {"visible": True, "thickness": 0.04}}}
    )
    if interval_label not in ("Weekly", "Monthly"):
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_xaxes(
        showgrid=True,
        gridcolor=_CHART_GRID,
        zeroline=False,
        showline=True,
        linecolor="#CDD6E3",
        tickfont={"color": _CHART_AXIS},
        showspikes=True,
        spikemode="across",
        spikethickness=1,
        spikecolor=_CHART_SPIKE,
        spikedash="dash",
        spikesnap="data",
    )
    fig.update_yaxes(
        showgrid=True,
        gridcolor=_CHART_GRID,
        zeroline=False,
        showline=True,
        linecolor="#CDD6E3",
        tickfont={"color": _CHART_AXIS},
        showspikes=True,
        spikemode="across",
        spikethickness=1,
        spikecolor=_CHART_SPIKE,
        spikedash="dash",
        spikesnap="cursor",
    )
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    if show_rsi:
        fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])

    return fig


# Rule: zone styling — semi-transparent fills (demand=green, supply=red),
# subtle boundary-line colors that echo the fill, and readable label colors
# that match the brighter Pattern Detail chart palette.
_ZONE_FILL_COLORS = {
    "demand": "rgba(34,165,91,0.14)",
    "supply": "rgba(235,87,87,0.13)",
}
_ZONE_LINE_COLORS = {
    "demand": "rgba(34,165,91,0.50)",
    "supply": "rgba(235,87,87,0.50)",
}
_ZONE_TEXT_COLORS = {"demand": _CHART_UP_COLOR, "supply": _CHART_DOWN_COLOR}

# Stage 2: trend-context moving-average reference lines.
_SMA50_LINE_COLOR = _CHART_ORANGE  # thin orange — the 50 SMA "clock method" input
_EMA20_LINE_COLOR = _CHART_BLUE    # thin blue — the EMA 20 confluence input

# Stage 2: zone-label flag colors — TRADEABLE/AVOID echo the trend badge's
# bullish-green / cautionary-orange palette so they read at a glance.
_TRADEABLE_FLAG_COLOR = _CHART_UP_COLOR
_AVOID_FLAG_COLOR = "#EF7A1A"
# Confirmation zones are drawn under a lower score bar than everything else on
# the chart, so their flag uses a distinct blue rather than the green/orange
# verdict palette — it states a different kind of fact.
_CONFIRMED_FLAG_COLOR = _CHART_BLUE

# Stage 2: trend badge palette — UP green, DOWN red, SIDEWAYS neutral grey.
_TREND_BADGE_COLORS = {
    "UP": _CHART_UP_COLOR,
    "DOWN": _CHART_DOWN_COLOR,
    "SIDEWAYS": _CHART_NEUTRAL,
}

# Stage 3 (opt-in): Fibonacci retracement line styling — per the documented
# importance ranking, the golden ratio (0.618) is drawn solid and slightly
# thicker than the others (which are dashed) so it stands out as the most
# important retracement level on the chart.
_FIB_LINE_STYLES: dict[float, dict[str, Any]] = {
    0.382: {"color": "#56CCF2", "dash": "dash", "width": 1},    # light blue dashed
    0.5:   {"color": _CHART_ORANGE, "dash": "dash", "width": 1},  # orange dashed
    0.618: {"color": "#D4AF37", "dash": "solid", "width": 2},   # gold solid, thicker (most important)
    0.786: {"color": _CHART_PURPLE, "dash": "dash", "width": 1},  # purple dashed
}
_ZONE_LABEL_XSHIFT = 18


def _fmt_zone_score(score: float) -> str:
    """Format an ODD score without a trailing ``.0`` for whole numbers
    (e.g. ``6`` rather than ``6.0``) — mirrors analysis/demand_supply.py."""
    return f"{score:g}"


def _stagger_label_positions(zones: list[dict[str, Any]], min_gap: float) -> list[float]:
    """Compute right-edge label y-positions for *zones*, nudging any whose
    natural (price-aligned) positions sit closer than *min_gap* apart so
    overlapping zone labels stay readable.

    Walks zones from lowest to highest price, keeping each label at its
    natural midpoint unless that would place it within *min_gap* of the
    previous (lower) label — in which case it gets pushed up just far
    enough to clear it. Returns positions in the same order as *zones*.
    """
    if not zones:
        return []

    order = sorted(range(len(zones)), key=lambda i: zones[i]["mid"])
    positions = [0.0] * len(zones)
    prev_pos: float | None = None
    for i in order:
        natural = zones[i]["mid"]
        pos = natural if prev_pos is None else max(natural, prev_pos + min_gap)
        positions[i] = pos
        prev_pos = pos
    return positions


def _add_zone_overlays(fig: go.Figure, result: dict[str, Any], df: pd.DataFrame, full_df: pd.DataFrame | None = None) -> None:
    """Draw the filtered demand/supply zones as decluttered chart overlays.

    ``result["demand_zones"]``/``result["supply_zones"]`` are already the
    filtered, ranked subset produced by ``filter_zones`` (at most 3 + 3 —
    see analysis/zone_engine/filters.py and analysis/demand_supply.py), so
    this never has to reason about the raw, noisy full-history zone list —
    it only has to draw what's already been chosen well.

    Each zone gets:
      * a semi-transparent rectangle (green=demand, red=supply) spanning
        the full visible chart width, from its distal to proximal line;
      * a SOLID line on the proximal edge (the tradeable boundary) and a
        DOTTED line on the distal edge (the invalidation boundary);
      * a label at the right edge of the chart — "{TYPE} | Score {score} |
        {strength}", with the Stage 2 context flags appended: "| EMA20"
        when ``ema20_enhancer`` is set (a confluence bonus — see
        analysis.zone_engine.enhancers), Stage 3's "| Fib" / "| Confluence:
        {label}" (opt-in — only when the Fibonacci checkbox was on, see
        below), and a colored "| TRADEABLE" (green) or "| AVOID" (orange)
        verdict from the trend-alignment safety rule (see
        analysis.demand_supply._apply_trend_alignment). The base label is
        dark green/red; vertical positions are staggered so labels for
        zones close in price don't overlap.

    Stage 3 (opt-in): when the Fibonacci confluence checkbox was on for
    this analysis — detected, like ``_add_fibonacci_lines``, via the
    presence of ``result["fib_levels"]`` — each zone's label additionally
    gets "| Fib" (only when ``fib_confluence`` is set on that zone) and
    "| Confluence: {confluence_label}" (the combined EMA20+Fib rating, see
    analysis.zone_engine.scoring.confluence_rating). With the checkbox off
    neither is shown — the label is byte-for-byte identical to Stage 2's.
    """
    zones = [*result.get("demand_zones", []), *result.get("supply_zones", [])]

    # Zone confirmation: draw the confirmed zones too, whenever the checkbox is
    # set — the same live session-state read the screener filter uses, so the
    # list and the chart can never disagree about which mode is active.
    #
    # These are exactly the zones filter_zones refuses to draw: scoring 3.5-5.0
    # and/or tested more than once. Without this the screener could list a
    # stock on a zone the chart never showed — observed on LUPIN, whose
    # confirmed zone 1.7% from price was invisible while three drawn demand
    # zones sat 34-45% away.
    if st.session_state.get("screener_confirmation", False):
        _drawn = {round(float(z["proximal"]), 4) for z in zones}
        for _cz in result.get("confirmation_zones", []):
            if round(float(_cz["proximal"]), 4) not in _drawn:
                zones.append({**_cz, "confirmed_zone": True})

    if not zones or df.empty:
        return

    fib_active = bool(result.get("fib_levels"))
    x1 = df.index[-1]

    # Minimum vertical spacing between labels, scaled to the chart's price
    # range so it "just works" across very different stocks/price levels.
    price_span = float(df["High"].max() - df["Low"].min()) or 1.0
    min_gap = price_span * 0.035
    label_positions = _stagger_label_positions(zones, min_gap)

    for zone, label_y in zip(zones, label_positions):
        category = zone.get("category", "demand")
        fill_color = _ZONE_FILL_COLORS.get(category, _ZONE_FILL_COLORS["demand"])
        line_color = _ZONE_LINE_COLORS.get(category, _ZONE_LINE_COLORS["demand"])
        text_color = _ZONE_TEXT_COLORS.get(category, _ZONE_TEXT_COLORS["demand"])
        proximal, distal = zone["proximal"], zone["distal"]
        top, bottom = zone["top"], zone["bottom"]

        # Start the zone from where it formed (base_start_idx), not the chart edge.
        _src = full_df if full_df is not None else df
        zone_start_idx = zone.get("base_start_idx", 0)
        zone_x0 = _src.index[min(zone_start_idx, len(_src) - 1)]
        if zone_x0 < df.index[0]:
            zone_x0 = df.index[0]

        # Shaded zone rectangle from zone formation to chart right edge.
        fig.add_shape(
            type="rect",
            xref="x", yref="y",
            x0=zone_x0, x1=x1, y0=bottom, y1=top,
            fillcolor=fill_color,
            line_width=0,
            layer="below",
            row=1, col=1,
        )
        # Proximal boundary (the tradeable edge nearest price) — SOLID.
        fig.add_shape(
            type="line",
            xref="x", yref="y",
            x0=zone_x0, x1=x1, y0=proximal, y1=proximal,
            line={"color": line_color, "width": 1.25, "dash": "solid"},
            layer="below",
            row=1, col=1,
        )
        # Distal boundary (the far/invalidation edge) — DOTTED.
        fig.add_shape(
            type="line",
            xref="x", yref="y",
            x0=zone_x0, x1=x1, y0=distal, y1=distal,
            line={"color": line_color, "width": 1, "dash": "dot"},
            layer="below",
            row=1, col=1,
        )
        # Stage 2 context flags appended to the label: an "| EMA20"
        # confluence bonus (when present) and a colored TRADEABLE/AVOID
        # verdict from the trend-alignment safety rule. Plotly annotation
        # text supports inline <span style="color:..."> for exactly this
        # kind of "mostly one color, one bit highlighted" label.
        flags = ""
        # Mark the confirmation-only zones so they are never mistaken for the
        # ordinary 5.0+ set — they are drawn under a lower bar and the label
        # has to say so.
        if zone.get("confirmed_zone"):
            flags += f" | <span style='color:{_CONFIRMED_FLAG_COLOR}'>CONFIRMED</span>"
        if zone.get("marking") == "Exceptional":
            flags += " | Exceptional"
        # M8: closing concept — show Strong/Weak Close, hide Unchecked.
        _cq = zone.get("closing_quality", "unchecked")
        if _cq == "strong":
            flags += " | Strong Close"
        elif _cq == "weak":
            flags += " | Weak Close"
        # M10: achievement ratio — show Weak Departure, hide Clean.
        if zone.get("zone_quality") == "Weak Departure":
            flags += " | Weak Departure"
        # M12: base width — flag only a notably wide base; a tight one is
        # the normal case and would just clutter the label.
        # Missing-base zones (M17) are excluded: their "base" is the single
        # turning-point candle, which is EXCITING by definition (M5 wants its
        # body >= 1.3% of price and >= 50% of its range), so its full range
        # sits near the threshold structurally. An instant reversal has no
        # base to be sloppy about, so "Wide Base" would read as a criticism
        # of something that isn't there. The width still shows in the detail
        # panel — see _render_zone_widths.
        _bw = float(zone.get("base_width_pct", 0.0) or 0.0)
        if _bw > WIDE_BASE_THRESHOLD_PCT and int(zone.get("num_base_candles", 0) or 0) > 0:
            flags += f" | Wide Base {_bw:.1f}%"
        if zone.get("ema20_enhancer"):
            flags += " | EMA20"
        # Stage 3 (opt-in): only when the Fibonacci checkbox was on for
        # this analysis — otherwise the label stays byte-for-byte identical
        # to Stage 2's (see fib_active / module docstring above).
        if fib_active:
            if zone.get("fib_confluence"):
                flags += " | Fib"
            flags += f" | Confluence: {zone.get('confluence_label', 'None')}"
        if zone.get("is_tradeable"):
            flags += f" | <span style='color:{_TRADEABLE_FLAG_COLOR}'>TRADEABLE</span>"
        else:
            flags += f" | <span style='color:{_AVOID_FLAG_COLOR}'>AVOID</span>"

        # Right-edge label, vertically staggered to avoid overlap.
        fig.add_annotation(
            x=x1, y=label_y,
            xref="x", yref="y",
            xanchor="left", yanchor="middle",
            xshift=_ZONE_LABEL_XSHIFT,
            text=(
                f"{zone['zone_type']} | Score {_fmt_zone_score(zone['odd_score'])} "
                f"| {zone['zone_strength']}{flags}"
            ),
            showarrow=False,
            align="left",
            font={"color": text_color, "size": 11},
            bgcolor="rgba(255,255,255,0.75)",
            row=1, col=1,
        )


def _add_trend_context_lines(fig: go.Figure, df: pd.DataFrame) -> None:
    """Draw the Stage 2 trend-context moving averages as thin reference
    lines on the price chart:

      * 50 SMA (thin grey) — the input to the "50 SMA clock method" trend
        detector (see ``analysis.zone_engine.trend.detect_trend``);
      * EMA 20 (thin blue) — the input to the EMA 20 confluence enhancer
        (see ``analysis.zone_engine.enhancers.ema20_confluence``).

    Purely visual context — these mirror (but recompute, for the visible
    window) the same rolling/exponential averages the analysis already
    used; they don't feed back into any score or filter.
    """
    if df.empty:
        return
    sma_period = min(50, len(df))
    ema_period = min(20, len(df))
    sma_series = df["Close"].rolling(window=sma_period).mean()
    ema_series = df["Close"].ewm(span=ema_period, adjust=False).mean()

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=sma_series,
            name="SMA 50",
            line={"color": _SMA50_LINE_COLOR, "width": 1},
            showlegend=True,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=ema_series,
            name="EMA 20",
            line={"color": _EMA20_LINE_COLOR, "width": 1},
            showlegend=True,
        ),
        row=1, col=1,
    )


def _add_tf_sma_lines(fig: go.Figure, df: pd.DataFrame) -> None:
    """Draw the 50 SMA (orange, prominent) and 200 SMA (blue, prominent) for
    a Trend Following chart.

    Both are computed fresh on the visible ``df`` window.  They are drawn
    more prominently (width 2.0) than the thin reference lines used on
    Demand/Supply charts (width 1.0) because they are the primary signal
    inputs for the Trend Following strategy, not just context decoration.
    """
    if df.empty:
        return

    from analysis.trend_following import SMA_FAST, SMA_SLOW

    sma_fast_series = df["Close"].rolling(window=min(SMA_FAST, len(df))).mean()
    sma_slow_series = df["Close"].rolling(window=min(SMA_SLOW, len(df))).mean()

    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=sma_fast_series,
            name=f"SMA {SMA_FAST}",
            line={"color": _CHART_ORANGE, "width": 2.0},
            showlegend=True,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=sma_slow_series,
            name=f"SMA {SMA_SLOW}",
            line={"color": _CHART_BLUE, "width": 2.0},
            showlegend=True,
        ),
        row=1, col=1,
    )


def _add_tf_cross_marker(
    fig: go.Figure, df: pd.DataFrame, result: dict[str, Any]
) -> None:
    """Mark the most recent golden/death cross on the chart, if visible.

    Reads ``result["last_cross"]`` (set by ``TrendFollowingAnalysis``).
    The cross is only annotated when it falls within the displayed ``df``
    window (``candles_ago`` < ``len(df)``); stale crosses that predate
    the visible range are silently skipped — they would be off the x-axis.

    Marker style:
    * Golden cross — green upward triangle + "Golden Cross" annotation above.
    * Death cross  — red downward triangle + "Death Cross" annotation below.
    """
    if df.empty:
        return

    last_cross = result.get("last_cross") or {}
    cross_type = last_cross.get("type")
    candles_ago = last_cross.get("candles_ago")

    if cross_type is None or candles_ago is None:
        return
    if not isinstance(candles_ago, int) or candles_ago >= len(df):
        return   # cross is older than the visible window

    cross_bar_pos = len(df) - 1 - candles_ago
    cross_x = df.index[cross_bar_pos]
    try:
        cross_y = float(df["Close"].iloc[cross_bar_pos])
    except Exception:
        return

    is_golden = cross_type == "golden"
    marker_color = _CHART_UP_COLOR if is_golden else _CHART_DOWN_COLOR
    marker_symbol = "triangle-up" if is_golden else "triangle-down"
    label = "Golden Cross" if is_golden else "Death Cross"
    text_pos = "top center" if is_golden else "bottom center"
    ay = -40 if is_golden else 40

    # Scatter marker at the cross candle
    fig.add_trace(
        go.Scatter(
            x=[cross_x],
            y=[cross_y],
            mode="markers",
            marker={
                "symbol": marker_symbol,
                "color": marker_color,
                "size": 14,
                "line": {"color": "white", "width": 1},
            },
            name=label,
            showlegend=True,
        ),
        row=1, col=1,
    )
    # Text annotation pointing to the cross
    fig.add_annotation(
        x=cross_x,
        y=cross_y,
        xref="x",
        yref="y",
        text=label,
        showarrow=True,
        arrowhead=2,
        arrowcolor=marker_color,
        arrowsize=1,
        arrowwidth=1.5,
        ay=ay,
        ax=0,
        font={"color": marker_color, "size": 11, "family": "Arial Bold"},
        bgcolor="rgba(255,255,255,0.85)",
        bordercolor=marker_color,
        borderwidth=1,
        row=1, col=1,
    )


def _add_fibonacci_lines(fig: go.Figure, result: dict[str, Any], df: pd.DataFrame) -> None:
    """Stage 3 (opt-in): draw the Fibonacci retracement levels as horizontal
    reference lines on the price chart.

    Detection follows the documented rule — *presence* of ``result["fib_levels"]``
    is how this module tells whether the "Enhance with Fibonacci Confluence"
    checkbox was on for this analysis (``analyse`` only ever adds that key
    when ``use_fibonacci=True`` — see analysis/demand_supply.py). When it's
    absent (checkbox off, or there wasn't enough history to anchor a swing),
    this draws nothing at all — the chart is unchanged from Stage 2.

    Each level gets a full-width horizontal line styled per the documented
    importance ranking (see ``_FIB_LINE_STYLES`` — the golden ratio 0.618 is
    solid and slightly thicker; the rest are dashed) plus a left-edge label
    such as "Fib 61.8%". Purely visual context — no analysis/scoring math
    lives here (see ``analysis.zone_engine.fibonacci``).
    """
    fib_levels = result.get("fib_levels")
    if not fib_levels or df.empty:
        return

    x0, x1 = df.index[0], df.index[-1]
    for ratio, price in fib_levels.items():
        # Coerce the ratio key to float so a JSON round-tripped result (whose
        # dict keys become strings, e.g. "0.618") still matches _FIB_LINE_STYLES
        # and labels correctly instead of being silently dropped.
        try:
            ratio_f = float(ratio)
        except (TypeError, ValueError):
            continue
        style = _FIB_LINE_STYLES.get(ratio_f)
        if style is None:
            continue

        # Guard: only ever draw a real, finite, positive price. A 0/NaN/None
        # level (e.g. from a degenerate swing anchored on a partial candle)
        # must never be plotted — it would drag the y-axis toward 0 and
        # collapse every Fib line to the bottom of the chart.
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price_f) or price_f <= 0:
            continue

        fig.add_shape(
            type="line",
            xref="x", yref="y",
            x0=x0, x1=x1, y0=price_f, y1=price_f,
            line={"color": style["color"], "width": style["width"], "dash": style["dash"]},
            layer="below",
            row=1, col=1,
        )
        # Left-edge label, e.g. "Fib 61.8%" — mirrors the right-edge zone
        # labels in _add_zone_overlays but anchored to the opposite side so
        # the two never collide.
        fig.add_annotation(
            x=x0, y=price_f,
            xref="x", yref="y",
            xanchor="right", yanchor="bottom",
            text=f"Fib {ratio_f * 100:.1f}%",
            showarrow=False,
            align="right",
            font={"color": style["color"], "size": 10},
            bgcolor="rgba(255,255,255,0.75)",
            row=1, col=1,
        )


def _add_sma_line(
    fig: go.Figure,
    df: pd.DataFrame,
    sma_value: float | None,
    label: str,
    color: str,
) -> None:
    if sma_value is None:
        return
    # Compute the rolling SMA series for a proper line (not just hline)
    period = 200 if "200" in label else 50
    sma_series = df["Close"].rolling(window=min(period, len(df))).mean()
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=sma_series,
            name=label,
            line={"color": color, "width": 1.5, "dash": "dash"},
            showlegend=True,
        ),
        row=1, col=1,
    )


def _add_vwap_line(
    fig: go.Figure, df: pd.DataFrame, vwap_value: float | None
) -> None:
    if vwap_value is None:
        return
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    import numpy as np
    cumulative_tpv = (typical_price * df["Volume"]).cumsum()
    cumulative_vol = df["Volume"].cumsum()
    vwap_series = cumulative_tpv / cumulative_vol.replace(0, np.nan)
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=vwap_series,
            name="VWAP",
            line={"color": _CHART_PURPLE, "width": 1.5, "dash": "dot"},
            showlegend=True,
        ),
        row=1, col=1,
    )


# ---------------------------------------------------------------------------
# Historical Analysis Section
# ---------------------------------------------------------------------------

def _render_history_section(stock_id: int, analysis_type: str) -> None:
    """Render the last 7 analysis results as a timeline table."""
    st.markdown("### Historical Analysis")
    try:
        comparison = compare_analysis_results(stock_id, analysis_type)
    except Exception as exc:
        st.warning(f"Could not load history: {exc}")
        return

    history = comparison.get("history", [])
    if not history:
        st.caption("No history yet — history is saved after each analysis run.")
        return

    # Trend summary
    direction = comparison.get("trend_direction", "stable")
    dominant = comparison.get("dominant_status", "neutral")
    consistent = comparison.get("consistent_trend", False)
    dir_icon = {"improving": "📈", "deteriorating": "📉", "stable": "➡️"}.get(direction, "➡️")
    st.caption(
        f"{dir_icon} Trend is **{direction}** over last {len(history)} runs. "
        f"Dominant status: **{dominant}**"
        + (" — consistently so." if consistent else ".")
    )

    # Timeline table
    rows_html = ""
    status_colors = {"bullish": "#d4edda", "bearish": "#f8d7da", "neutral": "#fff3cd"}
    for h in history:
        bg = status_colors.get(h["status"], "#ffffff")
        ts = format_timestamp(h["created_at"])
        rows_html += (
            f"<tr style='background:{bg};'>"
            f"<td style='padding:4px 8px;'>{ts}</td>"
            f"<td style='padding:4px 8px;font-weight:600;'>{h['status'].upper()}</td>"
            f"<td style='padding:4px 8px;'>{h['strength']}</td>"
            f"<td style='padding:4px 8px;font-size:0.8rem;'>{h['summary'][:80]}{'…' if len(h['summary'])>80 else ''}</td>"
            f"</tr>"
        )

    table_html = f"""
    <table style="width:100%;border-collapse:collapse;font-size:0.85rem;">
        <thead>
            <tr style="background:#343a40;color:white;">
                <th style="padding:6px 8px;text-align:left;">Date</th>
                <th style="padding:6px 8px;text-align:left;">Status</th>
                <th style="padding:6px 8px;text-align:left;">Strength</th>
                <th style="padding:6px 8px;text-align:left;">Summary</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
    """
    st.markdown(table_html, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Personal Notes Section
# ---------------------------------------------------------------------------

def _render_notes_section(stock_id: int) -> None:
    """Render the personal notes input and history panel."""
    st.markdown("### My Notes")

    with st.form("add_note_form", clear_on_submit=True):
        note_text = st.text_area(
            "Add a note",
            placeholder="Write your observations, trade plan, or reminders here…",
            height=100,
            label_visibility="collapsed",
        )
        if st.form_submit_button("Save Note"):
            if note_text.strip():
                try:
                    save_note(stock_id, note_text)
                    st.success("Note saved.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to save note: {exc}")
            else:
                st.warning("Please write something before saving.")

    try:
        notes = get_notes(stock_id, limit=5)
    except Exception:
        notes = []

    if notes:
        st.caption(f"Last {len(notes)} notes:")
        for note in notes:
            ts = format_timestamp(note["created_at"])
            ncol1, ncol2 = st.columns([8, 1])
            with ncol1:
                st.markdown(
                    f"<div style='background:#f8f9fa;border-left:3px solid #6c757d;"
                    f"padding:8px 12px;border-radius:4px;margin-bottom:6px;'>"
                    f"<div style='font-size:0.7rem;color:#999;'>{ts}</div>"
                    f"<div style='font-size:0.88rem;margin-top:2px;'>{note['note_text']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with ncol2:
                if st.button("🗑", key=f"del_note_{note['id']}"):
                    try:
                        delete_note(note["id"])
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
    else:
        st.caption("No notes yet.")
