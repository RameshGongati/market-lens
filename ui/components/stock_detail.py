"""Full detailed stock analysis view with chart toggle, history, and notes."""

import math
from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st
import streamlit.components.v1 as st_components

from analysis.base import STRENGTH_BG, STRENGTH_COLORS
from analysis.demand_supply import DemandSupplyAnalysis
from analysis.trend_following import TrendFollowingAnalysis
from config.preferences import load_preferences
from config.trading_config import get_timeframe
from data.manager import (
    INTERVAL_OPTIONS,
    default_interval_label,
    fetch_by_interval,
    fetch_for_trading_type,
    interval_display_label,
)
from storage.database import (
    compare_analysis_results,
    delete_note,
    get_notes,
    save_note,
)
from ui.components.tradingview_chart import get_tradingview_url, render_tradingview_chart
from utils.helpers import format_timestamp, get_company_name
from utils.logger import get_logger

logger = get_logger(__name__)

_STATUS_COLOR = {"bullish": "#28a745", "bearish": "#dc3545", "neutral": "#ffc107"}

def _crosshair_js(show_date: bool) -> str:
    """Build JS for crosshair labels: price on y-axis always, date at top when tooltip is off."""
    show_date_flag = "true" if show_date else "false"
    return (
        "<script>\n"
        "(function() {\n"
        "    var doc = window.parent.document;\n"
        "    var showDate = " + show_date_flag + ";\n"
        "    doc.querySelectorAll('.y-price-label,.x-date-label')\n"
        "       .forEach(function(el) { el.remove(); });\n"
        "\n"
        "    function init(n) {\n"
        "        if (n > 15) return;\n"
        "        var plots = doc.querySelectorAll('.js-plotly-plot');\n"
        "        if (!plots.length) { setTimeout(function(){ init(n+1); }, 300); return; }\n"
        "        var plot = plots[plots.length - 1];\n"
        "        var drags = plot.querySelectorAll('.nsewdrag');\n"
        "        if (!drags.length) { setTimeout(function(){ init(n+1); }, 300); return; }\n"
        "\n"
        "        plot.style.position = 'relative';\n"
        "        var badge = 'background:#787b86;color:#fff;font-size:11px;padding:1px 5px;'\n"
        "                  + 'pointer-events:none;display:none;z-index:1000;font-family:monospace;'\n"
        "                  + 'border-radius:2px;white-space:nowrap;position:absolute;';\n"
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
        "        drags[0].addEventListener('mousemove', function(e) {\n"
        "            var ya = plot._fullLayout.yaxis;\n"
        "            if (!ya || !ya.range) return;\n"
        "            var r = drags[0].getBoundingClientRect();\n"
        "            var pr = plot.getBoundingClientRect();\n"
        "            var frac = (e.clientY - r.top) / r.height;\n"
        "            var price = ya.range[1] - frac * (ya.range[1] - ya.range[0]);\n"
        "            priceLabel.textContent = price.toFixed(2);\n"
        "            priceLabel.style.top = (e.clientY - pr.top) + 'px';\n"
        "            priceLabel.style.display = 'block';\n"
        "\n"
        "            if (dateLabel) {\n"
        "                var xa = plot._fullLayout.xaxis;\n"
        "                if (!xa || !xa.range) return;\n"
        "                var xf = (e.clientX - r.left) / r.width;\n"
        "                var r0 = typeof xa.range[0]==='number' ? xa.range[0] : new Date(xa.range[0]).getTime();\n"
        "                var r1 = typeof xa.range[1]==='number' ? xa.range[1] : new Date(xa.range[1]).getTime();\n"
        "                var d = new Date(r0 + xf * (r1 - r0));\n"
        "                var M = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];\n"
        "                dateLabel.textContent = d.getDate() + ' ' + M[d.getMonth()] + ' ' + d.getFullYear();\n"
        "                dateLabel.style.left = (e.clientX - pr.left) + 'px';\n"
        "                dateLabel.style.display = 'block';\n"
        "            }\n"
        "        });\n"
        "        drags[0].addEventListener('mouseleave', function() {\n"
        "            priceLabel.style.display = 'none';\n"
        "            if (dateLabel) dateLabel.style.display = 'none';\n"
        "        });\n"
        "    }\n"
        "    init(0);\n"
        "})();\n"
        "</script>\n"
    )

# Lookback windows (calendar days) for the period selector buttons
_PERIOD_DAYS = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}

# Interval-selector labels list — stable order for the radio widget.
_INTERVAL_LABELS: list[str] = list(INTERVAL_OPTIONS.keys())


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
    history_df: pd.DataFrame | None = None,
    stock_id: int | None = None,
) -> None:
    """Render the full detailed analysis view for a single stock.

    Args:
        symbol: Stock ticker.
        exchange: Exchange (NSE/BSE).
        analysis_type: The analysis type run.
        result: Analysis result dict from the analysis module.
        history_df: Optional OHLCV DataFrame for the price chart.
        stock_id: Database stock id for history/notes lookup.
    """
    if st.button("← Back to Dashboard", key="back_btn"):
        st.session_state.active_page = "dashboard"
        st.session_state.selected_stock_symbol = None
        st.rerun()

    status = result.get("status", "neutral")
    strength = result.get("strength", "—")
    color = _STATUS_COLOR.get(status, "#ffc107")
    s_color = STRENGTH_COLORS.get(strength, "#856404")
    s_bg = STRENGTH_BG.get(strength, "#fff3cd")
    current_price = result.get("current_price", 0.0)
    company_name = get_company_name(symbol)

    st.markdown(
        f"## {symbol} "
        f"<span style='color:{color};font-size:0.75em;background:{color}22;"
        f"padding:2px 8px;border-radius:8px;'>"
        f"{'▲' if status=='bullish' else '▼' if status=='bearish' else '●'} {status.upper()}"
        f"</span>"
        f"&nbsp;<span style='font-size:0.7em;color:{s_color};background:{s_bg};"
        f"padding:2px 8px;border-radius:8px;border:1px solid {s_color};'>{strength}</span>",
        unsafe_allow_html=True,
    )
    # Stage C: show the effective timeframe alongside analysis type.
    # Prefer the session-state label (which reflects any intraday fallback)
    # over the configured label from get_timeframe().
    _trading_type = st.session_state.get("trading_type", "Short-term Trading")
    _tf_label = st.session_state.get("_used_tf_label") or interval_display_label(
        get_timeframe(_trading_type)["interval"]
    )
    st.caption(
        f"{company_name} · {exchange} · {analysis_type} · "
        f"Timeframe: {_tf_label} · ₹{current_price:,.2f}"
    )

    if "error" in result:
        st.error(result["error"])
        return

    # Export single stock button
    col_exp, _ = st.columns([1, 4])
    with col_exp:
        if st.button("📥 Export PDF", key="export_single_btn"):
            try:
                from utils.export import export_to_pdf
                path = export_to_pdf(
                    {symbol: result},
                    watchlist_name="single",
                    analysis_type=analysis_type,
                    symbol_filter=symbol,
                    trading_type=st.session_state.get("trading_type", ""),
                    primary_strategy=st.session_state.get(
                        "primary_strategy", analysis_type
                    ),
                    enhancers=st.session_state.get("enhancers", []),
                )
                st.success(f"Exported to: `{path}`")
            except Exception as exc:
                st.error(f"Export failed: {exc}")

    st.markdown("---")

    # ---------- Chart section ----------
    # -----------------------------------------------------------------------
    # Interval selector — lets the user pick candle size independently of
    # the trading-type default.  Changing it re-fetches data AND re-runs
    # analysis at the new interval so chart overlays stay consistent.
    # -----------------------------------------------------------------------
    _trading_type = st.session_state.get("trading_type", "Short-term Trading")
    _default_label = default_interval_label(_trading_type)
    _iv_key = f"detail_interval_radio_{symbol}"
    # Initialise to the trading-type default on first open for this stock.
    st.session_state.setdefault(_iv_key, _default_label)

    # Prime the per-interval cache with the dashboard's already-computed result
    # for the default interval so the first render is instant (no extra fetch).
    _default_cache_key = f"detail_cache_{symbol}_{_default_label}"
    if (
        st.session_state.get(_default_cache_key) is None
        and history_df is not None
        and not history_df.empty
    ):
        st.session_state[_default_cache_key] = (history_df, result, "")

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
        selected_period = st.radio(
            "Period",
            list(_PERIOD_DAYS.keys()),
            index=4,
            horizontal=True,
            key="chart_period_radio",
            label_visibility="collapsed",
        )

    # -----------------------------------------------------------------------
    # Fetch + re-analyse for the selected interval (with per-stock caching).
    # Cache key: f"detail_cache_{symbol}_{interval_label}" — switching back
    # to a previously viewed interval reuses the cached (df, result) pair
    # without an additional network call.
    # -----------------------------------------------------------------------
    _use_fib = st.session_state.get("use_fibonacci", False)
    _chart_cache_key = f"detail_cache_{symbol}_{interval_label}_{_use_fib}"
    if st.session_state.get(_chart_cache_key) is None:
        # Need a fresh fetch at this interval.
        suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
        full_symbol = f"{symbol}{suffix}"
        with st.spinner(f"Fetching {interval_label} data for {symbol}…"):
            _chart_df, _fetch_meta = fetch_by_interval(full_symbol, interval_label)

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
        _tc = _TREND_BADGE_COLORS.get(_chart_trend, "#6c757d")
        chart_header += (
            f" <span style='color:{_tc};font-size:0.65em;background:{_tc}22;"
            f"padding:2px 8px;border-radius:8px;border:1px solid {_tc};'>"
            f"Trend: {_chart_trend}</span>"
        )
    if _chart_is_tf:
        _chart_signal = chart_result.get("signal", "HOLD")
        _sc = {"BUY": "#28a745", "SELL": "#dc3545"}.get(_chart_signal, "#6c757d")
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
        fig = _build_chart(symbol, df_view, chart_result, analysis_type, chart_type)
        if analysis_type == "Demand/Supply Zones":
            st.caption(
                "Showing nearest fresh zones (score >= 5). "
                "Tested/used-up zones hidden."
            )
        elif analysis_type == "Trend Following (SMA50/EMA20)":
            st.caption(
                "50 SMA (orange) and 200 SMA (navy) — cross marker shown "
                "if within the displayed window."
            )
        show_tooltip = load_preferences().get("show_candle_tooltip", True)
        if not show_tooltip:
            fig.update_layout(hovermode="closest")
            fig.update_traces(hoverinfo="none")
        st.plotly_chart(fig, use_container_width=True)
        st_components.html(_crosshair_js(show_date=not show_tooltip), height=0)
    else:
        st.warning(
            "Unable to load chart data for the selected interval. "
            "Try a different interval or check your internet connection."
        )

    st.markdown("---")

    # ---------- Key metrics (from the interval-specific re-analysis) ----------
    _render_metrics(chart_result, analysis_type)

    st.markdown("---")

    # ---------- Recommendation (from the interval-specific re-analysis) ----------
    recommendation = chart_result.get("recommendation") or chart_result.get("summary", "")
    if recommendation:
        st.markdown("### Recommendation")
        for line in recommendation.split("\n"):
            if line.strip():
                st.markdown(line.strip())

    # ---------- Historical analysis ----------
    if stock_id is not None:
        st.markdown("---")
        _render_history_section(stock_id, analysis_type)

    # ---------- Personal notes ----------
    if stock_id is not None:
        st.markdown("---")
        _render_notes_section(stock_id)


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
        period: One of "1W", "1M", "3M", "6M", "1Y".

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
                increasing_line_color="#28a745",
                decreasing_line_color="#dc3545",
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
                line={"color": "#1f77b4", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(31,119,180,0.08)",
                showlegend=False,
            ),
            row=1, col=1,
        )

    # --- Analysis overlays ---
    if analysis_type == "Demand/Supply Zones":
        _add_trend_context_lines(fig, df)
        _add_zone_overlays(fig, result, df)
        # Stage 3 (opt-in) — only draws anything when the Fibonacci
        # confluence checkbox was on (detected via result["fib_levels"]).
        _add_fibonacci_lines(fig, result, df)
    elif analysis_type == "Trend Following (SMA50/EMA20)":
        # Prominent 50 SMA + 200 SMA lines plus a cross marker — no zone
        # rectangles (there are none in a Trend Following result).
        _add_tf_sma_lines(fig, df)
        _add_tf_cross_marker(fig, df, result)
    elif analysis_type == "Long Term Investment":
        _add_sma_line(fig, df, result.get("sma_200"), "SMA 200", "#1f77b4")
    elif analysis_type == "Short Term Investment":
        _add_sma_line(fig, df, result.get("sma_50"), "SMA 50", "#ff7f0e")
    elif analysis_type == "Intraday Trading":
        _add_vwap_line(fig, df, result.get("vwap"))

    # --- Volume bars ---
    vol_colors = [
        "#28a745" if c >= o else "#dc3545"
        for c, o in zip(df["Close"], df["Open"])
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker_color=vol_colors,
            showlegend=False,
            opacity=0.7,
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
                line={"color": "#9b59b6", "width": 1.5},
                showlegend=False,
            ),
            row=3, col=1,
        )
        fig.add_hline(y=70, line_dash="dot", line_color="red", row=3, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="green", row=3, col=1)

    fig.update_layout(
        height=500 + (120 if show_rsi else 0),
        template="plotly_white",
        margin={"t": 40, "b": 20, "l": 60, "r": 20},
        hovermode="x unified",
        xaxis=dict(
            rangeslider=dict(visible=False),
            showspikes=True,
            spikemode="across",
            spikethickness=1,
            spikecolor="grey",
            spikedash="dash",
            spikesnap="cursor",
        ),
        yaxis=dict(
            showspikes=True,
            spikemode="across",
            spikethickness=1,
            spikecolor="grey",
            spikedash="dash",
            spikesnap="cursor",
            side="left",
        ),
    )
    # Place the rangeslider at the very bottom of the chart (below the last subplot)
    bottom_xaxis = f"xaxis{rows}"   # "xaxis2" for 2-row, "xaxis3" for 3-row
    fig.update_layout(
        **{bottom_xaxis: {"rangeslider": {"visible": True, "thickness": 0.04}}}
    )
    fig.update_yaxes(title_text="Price (₹)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    if show_rsi:
        fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])

    return fig


# Rule: zone styling — semi-transparent fills (demand=green, supply=red),
# subtle boundary-line colors that echo the fill, and dark text colors for
# the right-edge labels so they stay readable over the candlesticks.
_ZONE_FILL_COLORS = {"demand": "rgba(40,167,69,0.15)", "supply": "rgba(220,53,69,0.15)"}
_ZONE_LINE_COLORS = {"demand": "rgba(40,167,69,0.55)", "supply": "rgba(220,53,69,0.55)"}
_ZONE_TEXT_COLORS = {"demand": "#1e7e34", "supply": "#a71d2a"}  # dark green / dark red

# Stage 2: trend-context moving-average reference lines (thin, muted so
# they don't compete visually with the candles or zone overlays).
_SMA50_LINE_COLOR = "#9e9e9e"   # thin grey — the 50 SMA "clock method" input
_EMA20_LINE_COLOR = "#1f77b4"   # thin blue — the EMA 20 confluence input

# Stage 2: zone-label flag colors — TRADEABLE/AVOID echo the trend badge's
# bullish-green / cautionary-orange palette so they read at a glance.
_TRADEABLE_FLAG_COLOR = "#1e7e34"   # dark green
_AVOID_FLAG_COLOR = "#e8590c"       # dark orange

# Stage 2: trend badge palette — UP green, DOWN red, SIDEWAYS neutral grey.
_TREND_BADGE_COLORS = {"UP": "#28a745", "DOWN": "#dc3545", "SIDEWAYS": "#6c757d"}

# Stage 3 (opt-in): Fibonacci retracement line styling — per the documented
# importance ranking, the golden ratio (0.618) is drawn solid and slightly
# thicker than the others (which are dashed) so it stands out as the most
# important retracement level on the chart.
_FIB_LINE_STYLES: dict[float, dict[str, Any]] = {
    0.382: {"color": "#add8e6", "dash": "dash", "width": 1},    # light blue dashed
    0.5:   {"color": "#ff7f0e", "dash": "dash", "width": 1},    # orange dashed
    0.618: {"color": "#d4af37", "dash": "solid", "width": 2},   # gold solid, thicker (most important)
    0.786: {"color": "#9b59b6", "dash": "dash", "width": 1},    # purple dashed
}


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


def _add_zone_overlays(fig: go.Figure, result: dict[str, Any], df: pd.DataFrame) -> None:
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
    if not zones or df.empty:
        return

    fib_active = bool(result.get("fib_levels"))
    x0, x1 = df.index[0], df.index[-1]

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

        # Shaded zone rectangle, full chart width, drawn beneath the candles.
        fig.add_shape(
            type="rect",
            xref="x", yref="y",
            x0=x0, x1=x1, y0=bottom, y1=top,
            fillcolor=fill_color,
            line_width=0,
            layer="below",
            row=1, col=1,
        )
        # Proximal boundary (the tradeable edge nearest price) — SOLID.
        fig.add_shape(
            type="line",
            xref="x", yref="y",
            x0=x0, x1=x1, y0=proximal, y1=proximal,
            line={"color": line_color, "width": 1.25, "dash": "solid"},
            layer="below",
            row=1, col=1,
        )
        # Distal boundary (the far/invalidation edge) — DOTTED.
        fig.add_shape(
            type="line",
            xref="x", yref="y",
            x0=x0, x1=x1, y0=distal, y1=distal,
            line={"color": line_color, "width": 1, "dash": "dot"},
            layer="below",
            row=1, col=1,
        )
        # Stage 2 context flags appended to the label: an "| EMA20"
        # confluence bonus (when present) and a colored TRADEABLE/AVOID
        # verdict from the trend-alignment safety rule. Plotly annotation
        # text supports inline <span style="color:..."> for exactly this
        # kind of "mostly one color, one bit highlighted" label.
        flags = " | EMA20" if zone.get("ema20_enhancer") else ""
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
    """Draw the 50 SMA (orange, prominent) and 200 SMA (navy, prominent) for
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
            line={"color": "#ff7f0e", "width": 2.0},   # orange — the fast SMA
            showlegend=True,
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=sma_slow_series,
            name=f"SMA {SMA_SLOW}",
            line={"color": "#1a237e", "width": 2.0},   # navy blue — the slow SMA
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
    marker_color = "#28a745" if is_golden else "#dc3545"
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
            line={"color": "#9b59b6", "width": 1.5, "dash": "dot"},
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
