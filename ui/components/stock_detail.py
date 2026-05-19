"""Full detailed stock analysis view with chart toggle, history, and notes."""

from datetime import datetime
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from analysis.base import STRENGTH_BG, STRENGTH_COLORS
from storage.database import (
    compare_analysis_results,
    delete_note,
    get_notes,
    save_note,
)
from utils.helpers import format_timestamp, get_company_name
from utils.logger import get_logger

logger = get_logger(__name__)

_STATUS_COLOR = {"bullish": "#28a745", "bearish": "#dc3545", "neutral": "#ffc107"}


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
    st.caption(f"{company_name} · {exchange} · {analysis_type} · ₹{current_price:,.2f}")

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
    st.markdown("### Price Chart")
    chart_type = st.radio(
        "Chart Type",
        ["Candlestick", "Line"],
        horizontal=True,
        key="chart_type_radio",
    )

    if history_df is not None and not history_df.empty:
        fig = _build_chart(symbol, history_df, result, analysis_type, chart_type)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("Price chart unavailable — historical data was not passed to this view.")

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
        _add_zone_overlays(fig, result)
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
        height=520 + (120 if show_rsi else 0),
        template="plotly_white",
        xaxis_rangeslider_visible=False,
        margin={"t": 40, "b": 20, "l": 60, "r": 20},
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Price (₹)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    if show_rsi:
        fig.update_yaxes(title_text="RSI", row=3, col=1, range=[0, 100])

    return fig


def _add_zone_overlays(fig: go.Figure, result: dict[str, Any]) -> None:
    for zone in result.get("demand_zones", []):
        fig.add_hrect(
            y0=zone["bottom"], y1=zone["top"],
            fillcolor="rgba(40,167,69,0.12)",
            line_width=0,
            annotation_text=f"Demand ({zone.get('touches', 0)} tests)",
            annotation_position="right",
            row=1, col=1,
        )
    for zone in result.get("supply_zones", []):
        fig.add_hrect(
            y0=zone["bottom"], y1=zone["top"],
            fillcolor="rgba(220,53,69,0.12)",
            line_width=0,
            annotation_text=f"Supply ({zone.get('touches', 0)} tests)",
            annotation_position="right",
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
