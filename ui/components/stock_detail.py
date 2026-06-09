"""Full detailed stock analysis view with chart toggle, history, and notes."""

from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from analysis.base import STRENGTH_BG, STRENGTH_COLORS
from config.trading_config import get_timeframe
from data.manager import fetch_for_trading_type, interval_display_label
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

# Lookback windows (calendar days) for the period selector buttons
_PERIOD_DAYS = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "1Y": 365}


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
                )
                st.success(f"Exported to: `{path}`")
            except Exception as exc:
                st.error(f"Export failed: {exc}")

    st.markdown("---")

    # ---------- Chart section ----------
    # Stage 2: surface the overall trend (50 SMA clock method) as a small
    # badge next to the chart title — green/red/grey for UP/DOWN/SIDEWAYS —
    # so the directional context driving zone tradeability is visible at a
    # glance, without having to read the full summary line.
    trend = result.get("trend") if analysis_type == "Demand/Supply Zones" else None
    if trend:
        t_color = _TREND_BADGE_COLORS.get(trend, "#6c757d")
        st.markdown(
            f"### Price Chart "
            f"<span style='color:{t_color};font-size:0.65em;background:{t_color}22;"
            f"padding:2px 8px;border-radius:8px;border:1px solid {t_color};'>"
            f"Trend: {trend}</span>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown("### Price Chart")

    # Self-healing fallback: if the caller didn't pass OHLCV data (e.g. the
    # user navigated here without running a full analysis), fetch with the
    # currently selected trading type so the chart bars match the analysis.
    # Stage C: uses fetch_for_trading_type (with intraday fallback) rather
    # than a hardcoded 1y/1d yfinance call.
    if history_df is None or history_df.empty:
        try:
            suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
            fetched, _ = fetch_for_trading_type(
                f"{symbol}{suffix}",
                st.session_state.get("trading_type", "Short-term Trading"),
            )
            history_df = fetched if fetched is not None and not fetched.empty else None
        except Exception as exc:
            logger.warning("Fallback chart fetch failed for %s: %s", symbol, exc)
            history_df = None

    # Chart controls row: type toggle on the left, period selector on the right.
    # The period selector only applies to the Plotly-rendered chart types —
    # the TradingView widget has its own built-in timeframe controls.
    ct_col, pd_col = st.columns([2, 5])
    with ct_col:
        chart_type = st.radio(
            "Chart Type",
            ["Candlestick", "Line", "TradingView"],
            horizontal=True,
            key="chart_type_radio",
        )
    selected_period = "1Y"
    if chart_type != "TradingView":
        with pd_col:
            selected_period = st.radio(
                "Period",
                list(_PERIOD_DAYS.keys()),
                index=4,           # default: 1Y (show all fetched data)
                horizontal=True,
                key="chart_period_radio",
                label_visibility="collapsed",
            )

    if chart_type == "TradingView":
        # Primary entry point: open the live, fully-interactive chart (with
        # all timeframes, indicators and drawing tools) directly on
        # tradingview.com. The placeholder box rendered below explains why —
        # the embedded widget can't reliably load Indian market data here.
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
    elif history_df is not None and not history_df.empty:
        # Slice the 1-year dataset to the selected period without a new fetch
        df_view = _filter_by_period(history_df, selected_period)
        fig = _build_chart(symbol, df_view, result, analysis_type, chart_type)
        if analysis_type == "Demand/Supply Zones":
            st.caption(
                "Showing nearest fresh zones (score >= 5). "
                "Tested/used-up zones hidden."
            )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning(
            "Unable to load chart data. "
            "Please check your internet connection and try again."
        )

    st.markdown("---")

    # ---------- Key metrics ----------
    _render_metrics(result, analysis_type)

    st.markdown("---")

    # ---------- Recommendation ----------
    recommendation = result.get("recommendation") or result.get("summary", "")
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
        xaxis_rangeslider_visible=False,   # hide default rangeslider on price row
        margin={"t": 40, "b": 20, "l": 60, "r": 20},
        hovermode="x unified",
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
        style = _FIB_LINE_STYLES.get(ratio)
        if style is None or price is None:
            continue

        fig.add_shape(
            type="line",
            xref="x", yref="y",
            x0=x0, x1=x1, y0=price, y1=price,
            line={"color": style["color"], "width": style["width"], "dash": style["dash"]},
            layer="below",
            row=1, col=1,
        )
        # Left-edge label, e.g. "Fib 61.8%" — mirrors the right-edge zone
        # labels in _add_zone_overlays but anchored to the opposite side so
        # the two never collide.
        fig.add_annotation(
            x=x0, y=price,
            xref="x", yref="y",
            xanchor="right", yanchor="bottom",
            text=f"Fib {ratio * 100:.1f}%",
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
