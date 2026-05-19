"""Full detailed stock analysis view component."""

from typing import Any

import plotly.graph_objects as go
import streamlit as st

from utils.logger import get_logger

logger = get_logger(__name__)

_STATUS_COLOR = {"bullish": "#28a745", "bearish": "#dc3545", "neutral": "#ffc107"}


def render_stock_detail(
    symbol: str,
    exchange: str,
    analysis_type: str,
    result: dict[str, Any],
    history_df=None,
) -> None:
    """Render a full detailed analysis view for a single stock.

    Args:
        symbol: Stock ticker.
        exchange: Exchange (NSE/BSE).
        analysis_type: The analysis type run.
        result: Analysis result dictionary from the analysis module.
        history_df: Optional OHLCV DataFrame for the price chart.
    """
    if st.button("← Back to Dashboard", key="back_btn"):
        st.session_state.active_page = "dashboard"
        st.session_state.selected_stock_symbol = None
        st.rerun()

    status = result.get("status", "neutral")
    color = _STATUS_COLOR.get(status, "#ffc107")
    current_price = result.get("current_price", 0.0)

    st.markdown(
        f"## {symbol} &nbsp;<span style='color:{color};font-size:0.8em;'>"
        f"{'▲' if status=='bullish' else '▼' if status=='bearish' else '●'} "
        f"{status.upper()}</span>",
        unsafe_allow_html=True,
    )
    st.caption(f"{exchange} · {analysis_type} · ₹{current_price:,.2f}")

    if "error" in result:
        st.error(result["error"])
        return

    # Price chart with zones/levels
    if history_df is not None and not history_df.empty:
        fig = _build_price_chart(symbol, history_df, result, analysis_type)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Price chart unavailable — no historical data returned.")

    st.markdown("---")

    # Metrics row
    _render_metrics(result, analysis_type)

    st.markdown("---")

    # Full recommendation text
    recommendation = result.get("recommendation") or result.get("summary", "")
    if recommendation:
        st.markdown("### Recommendation")
        st.markdown(recommendation)


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
            ("Status", result.get("status", "—").upper()),
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


def _build_price_chart(
    symbol: str,
    df,
    result: dict[str, Any],
    analysis_type: str,
) -> go.Figure:
    """Build a Plotly candlestick chart with analysis overlays."""
    fig = go.Figure()

    # Candlestick
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df["Open"],
        high=df["High"],
        low=df["Low"],
        close=df["Close"],
        name=symbol,
        increasing_line_color="#28a745",
        decreasing_line_color="#dc3545",
    ))

    # Overlays by analysis type
    if analysis_type == "Demand/Supply Zones":
        _add_zone_overlays(fig, df, result)
    elif analysis_type == "Long Term Investment":
        _add_sma_overlay(fig, df, result.get("sma_200"), "SMA 200", "#1f77b4")
    elif analysis_type == "Short Term Investment":
        _add_sma_overlay(fig, df, result.get("sma_50"), "SMA 50", "#ff7f0e")
    elif analysis_type == "Intraday Trading":
        _add_vwap_overlay(fig, df, result.get("vwap"))

    fig.update_layout(
        title=f"{symbol} — {analysis_type}",
        xaxis_title="Date",
        yaxis_title="Price (₹)",
        xaxis_rangeslider_visible=False,
        template="plotly_white",
        height=480,
    )
    return fig


def _add_zone_overlays(fig: go.Figure, df, result: dict[str, Any]) -> None:
    """Add demand (green) and supply (red) zone bands to the chart."""
    for zone in result.get("demand_zones", []):
        fig.add_hrect(y0=zone["bottom"], y1=zone["top"],
                      fillcolor="rgba(40,167,69,0.15)",
                      line_width=0, annotation_text="Demand")
    for zone in result.get("supply_zones", []):
        fig.add_hrect(y0=zone["bottom"], y1=zone["top"],
                      fillcolor="rgba(220,53,69,0.15)",
                      line_width=0, annotation_text="Supply")


def _add_sma_overlay(
    fig: go.Figure, df, sma_value: float | None, label: str, color: str
) -> None:
    """Add a horizontal SMA line to the chart."""
    if sma_value is None:
        return
    fig.add_hline(
        y=sma_value,
        line_color=color,
        line_dash="dash",
        annotation_text=label,
    )


def _add_vwap_overlay(fig: go.Figure, df, vwap_value: float | None) -> None:
    """Add a horizontal VWAP line to the chart."""
    if vwap_value is None:
        return
    fig.add_hline(
        y=vwap_value,
        line_color="#9b59b6",
        line_dash="dot",
        annotation_text="VWAP",
    )
