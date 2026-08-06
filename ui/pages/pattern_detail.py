"""Pattern Scanner stock-level detail page."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
import html
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from analysis.pattern_models import PatternMatch
from data.manager import build_source_manager, fetch_by_interval
from ui.components.panels import bias_pill, kv_row, page_title, section_title, spacer, stat_card
from ui.components.tradingview_chart import get_tradingview_url
from ui.pages.pattern_common import match_by_symbol
from utils.logger import get_logger

logger = get_logger(__name__)


def render_pattern_detail() -> None:
    """Render the selected pattern match in detail."""
    matches: list[PatternMatch] = st.session_state.get("pattern_scan_results", []) or []
    symbol = st.session_state.get("selected_pattern_symbol")
    if not symbol and matches:
        symbol = matches[0].symbol
        st.session_state["selected_pattern_symbol"] = symbol
    match = match_by_symbol(matches, symbol or "") if symbol else None
    if match is None:
        st.warning("Select a pattern match from the Pattern Results page.")
        if st.button("Back to Results", icon=":material/arrow_back:"):
            st.session_state.active_page = "pattern_results"
            st.rerun()
        return

    chart_data = st.session_state.get("pattern_chart_data", {}) or {}
    zone_results = st.session_state.get("pattern_zone_results", {}) or {}
    df = chart_data.get(match.symbol)
    if df is None or df.empty:
        df = _fetch_detail_frame(match)
        if df is not None and not df.empty:
            chart_data[match.symbol] = df
            st.session_state["pattern_chart_data"] = chart_data
    zone_result = zone_results.get(match.symbol, {})

    _render_header(match)
    spacer(8)
    _render_top_cards(match)
    spacer(8)

    chart_col, rail_col = st.columns([3.2, 1])
    with chart_col:
        with st.container(border=True):
            _render_chart(match, df, zone_result)
    with rail_col:
        _render_anatomy(match)
        spacer(8)
        _render_breakout_checklist(match)
        spacer(8)
        _render_status(match)
        spacer(8)
        _render_risk_plan(match)

    spacer(10)
    _render_key_level_cards(match)
    spacer(10)
    bottom_left, bottom_right = st.columns([1.35, 1.35])
    with bottom_left:
        _render_timeline(match)
    with bottom_right:
        _render_action_panel(match, zone_result)

    spacer(8)
    st.info(
        "Patterns show structure, not guaranteed direction. Zone context "
        "increases usefulness. Always wait for confirmation and manage risk."
    )


def _render_header(match: PatternMatch) -> None:
    left, right = st.columns([2.2, 1.45])
    with left:
        page_title(
            f"Pattern Detail - {match.symbol}",
            "Deep dive into the detected setup, zone context, trigger levels, and confirmation signals.",
            icon="target",
        )
    with right:
        b1, b2, b3 = st.columns(3)
        with b1:
            if st.button("Back to Results", icon=":material/arrow_back:", use_container_width=True):
                st.session_state.active_page = "pattern_results"
                st.rerun()
        with b2:
            st.download_button(
                "Export PDF",
                data=_detail_pdf_bytes(match),
                file_name=f"market_lens_pattern_{match.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                mime="application/pdf",
                icon=":material/picture_as_pdf:",
                use_container_width=True,
            )
        with b3:
            st.link_button(
                "Full Chart",
                url=get_tradingview_url(match.symbol, match.exchange),
                icon=":material/open_in_new:",
                use_container_width=True,
            )


def _render_top_cards(match: PatternMatch) -> None:
    cols = st.columns(6)
    cards = [
        ("Pattern", match.pattern_type, match.pattern_family, "layers", "info"),
        ("Stage", match.stage, "Current pattern status", "target", _stage_tone(match.stage)),
        ("Confidence", f"{match.confidence_score:.0f}%", "Detector score", "check_circle", "bullish"),
        ("Breakout Bias", match.breakout_bias, "Direction only after break", "trend_up", _bias_tone(match.breakout_bias)),
        ("Zone Context", match.zone_context, "Demand/supply context", "shield", _zone_tone(match.zone_context)),
        ("Freshness", f"{match.freshness_candles} candles", "Since latest touch", "clock", "purple"),
    ]
    for col, (label, value, sub, icon, tone) in zip(cols, cards):
        with col:
            stat_card(label, value, sub, icon, tone=tone)  # type: ignore[arg-type]


def _render_chart(
    match: PatternMatch,
    df: pd.DataFrame | None,
    zone_result: dict[str, Any],
) -> None:
    section_title(f"{match.symbol} Pattern Chart", hint=f"{match.timeframe} candles")
    if df is None or df.empty:
        st.warning("Chart data for this match is not available. Re-run the pattern scan.")
        return
    view = df.tail(150).copy()
    fig = _build_pattern_figure(match, view, zone_result)
    st.plotly_chart(
        fig,
        use_container_width=True,
        key=f"pattern_chart_{match.symbol}_{match.pattern_type}",
        config={"scrollZoom": True},
    )
    st.caption("Pattern labels appear at the right edge of active pattern and zone levels.")


def _fetch_detail_frame(match: PatternMatch) -> pd.DataFrame | None:
    source_name = (
        st.session_state.get("pattern_scan_source_name")
        or st.session_state.get("selected_data_source", "Yahoo Finance")
    )
    credentials = st.session_state.get("credentials", {}).get(source_name, {})
    try:
        fetch_fn = build_source_manager(source_name, credentials).get_history
        full_symbol = _source_symbol(match.symbol, match.exchange, source_name)
        df, meta = fetch_by_interval(full_symbol, match.timeframe, fetch_fn=fetch_fn)
        if meta.get("message"):
            st.caption(meta["message"])
        return df
    except Exception as exc:
        logger.warning("Pattern detail chart fetch failed for %s: %s", match.symbol, exc)
        return None


def _source_symbol(symbol: str, exchange: str, source_name: str) -> str:
    if source_name == "Yahoo Finance":
        suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
        return f"{symbol}{suffix}"
    if source_name == "TradingView":
        return f"{exchange.upper()}:{symbol}"
    return symbol


def _build_pattern_figure(
    match: PatternMatch,
    df: pd.DataFrame,
    zone_result: dict[str, Any],
) -> go.Figure:
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.72, 0.28],
        subplot_titles=(match.symbol, "Volume"),
    )
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            increasing_line_color="#16794A",
            decreasing_line_color="#C23B33",
            name=match.symbol,
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    ema20 = df["Close"].astype(float).ewm(span=min(20, len(df)), adjust=False).mean()
    ema50 = df["Close"].astype(float).ewm(span=min(50, len(df)), adjust=False).mean()
    fig.add_trace(go.Scatter(x=df.index, y=ema20, name="EMA 20", line={"color": "#2F80ED", "width": 1.2}), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=ema50, name="EMA 50", line={"color": "#EF9F27", "width": 1.2}), row=1, col=1)

    _add_zone_shapes(fig, df, match, zone_result)
    _add_pattern_lines(fig, match)
    _add_swing_markers(fig, match)

    colours = ["#16794A" if c >= o else "#C23B33" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(
        go.Bar(x=df.index, y=df["Volume"], marker_color=colours, opacity=0.6, name="Volume", showlegend=False),
        row=2,
        col=1,
    )

    fig.update_layout(
        height=560,
        template="plotly_white",
        margin={"t": 40, "b": 20, "l": 55, "r": 80},
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        xaxis={"rangeslider": {"visible": False}},
        xaxis2={"rangeslider": {"visible": True, "thickness": 0.04}},
    )
    if match.timeframe not in ("Weekly",):
        fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(title_text="Price", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    return fig


def _add_pattern_lines(fig: go.Figure, match: PatternMatch) -> None:
    upper = match.upper_trendline_points
    lower = match.lower_trendline_points
    if len(upper) >= 2:
        fig.add_trace(
            go.Scatter(
                x=[p.timestamp for p in upper],
                y=[p.price for p in upper],
                mode="lines",
                name="Upper Trendline",
                line={"color": "#5A47B8", "width": 2.1},
            ),
            row=1,
            col=1,
        )
    if len(lower) >= 2:
        fig.add_trace(
            go.Scatter(
                x=[p.timestamp for p in lower],
                y=[p.price for p in lower],
                mode="lines",
                name="Lower Trendline",
                line={"color": "#5A47B8", "width": 2.1},
            ),
            row=1,
            col=1,
        )
    apex = match.apex_point
    trigger_label = str(match.metadata.get("trigger_label") or "Apex")
    fig.add_trace(
        go.Scatter(
            x=[apex.timestamp],
            y=[apex.price],
            mode="markers+text",
            text=[trigger_label],
            textposition="top center",
            marker={"color": "#7B61E3", "size": 11, "symbol": "circle-open", "line": {"width": 2}},
            name=trigger_label,
            showlegend=False,
        ),
        row=1,
        col=1,
    )
    if upper:
        last_x = upper[-1].timestamp
        fig.add_annotation(
            x=last_x,
            y=match.breakout_level,
            text="Breakout",
            showarrow=True,
            arrowhead=2,
            ax=38,
            ay=-42,
            font={"color": "#16794A", "size": 11},
            arrowcolor="#16794A",
            row=1,
            col=1,
        )
    if lower:
        last_x = lower[-1].timestamp
        fig.add_annotation(
            x=last_x,
            y=match.breakdown_level,
            text="Breakdown",
            showarrow=True,
            arrowhead=2,
            ax=38,
            ay=42,
            font={"color": "#C23B33", "size": 11},
            arrowcolor="#C23B33",
            row=1,
            col=1,
        )


def _add_swing_markers(fig: go.Figure, match: PatternMatch) -> None:
    highs = match.metadata.get("swing_highs") or []
    lows = match.metadata.get("swing_lows") or []
    if highs:
        fig.add_trace(
            go.Scatter(
                x=[p.timestamp for p in highs],
                y=[p.price for p in highs],
                mode="markers",
                marker={"color": "#C23B33", "size": 7, "symbol": "triangle-down"},
                name="Resistance Points",
            ),
            row=1,
            col=1,
        )
    if lows:
        fig.add_trace(
            go.Scatter(
                x=[p.timestamp for p in lows],
                y=[p.price for p in lows],
                mode="markers",
                marker={"color": "#16794A", "size": 7, "symbol": "triangle-up"},
                name="Support Points",
            ),
            row=1,
            col=1,
        )


def _add_zone_shapes(
    fig: go.Figure,
    df: pd.DataFrame,
    match: PatternMatch,
    zone_result: dict[str, Any],
) -> None:
    zones = []
    if match.nearest_demand_zone:
        zones.append(match.nearest_demand_zone)
    if match.nearest_supply_zone:
        zones.append(match.nearest_supply_zone)
    if not zones and zone_result:
        zones = [*(zone_result.get("demand_zones") or [])[:1], *(zone_result.get("supply_zones") or [])[:1]]
    for zone in zones:
        category = zone.get("category", "demand")
        top = max(float(zone.get("proximal", 0)), float(zone.get("distal", 0)))
        bottom = min(float(zone.get("proximal", 0)), float(zone.get("distal", 0)))
        fill = "rgba(34,165,91,0.14)" if category == "demand" else "rgba(235,87,87,0.13)"
        line = "rgba(34,165,91,0.5)" if category == "demand" else "rgba(235,87,87,0.5)"
        label = "Demand Zone" if category == "demand" else "Supply Zone"
        fig.add_shape(
            type="rect",
            xref="x",
            yref="y",
            x0=df.index[0],
            x1=df.index[-1],
            y0=bottom,
            y1=top,
            fillcolor=fill,
            line={"color": line, "width": 1},
            layer="below",
            row=1,
            col=1,
        )
        fig.add_annotation(
            x=df.index[-1],
            y=(top + bottom) / 2,
            text=label,
            showarrow=False,
            xanchor="left",
            font={"size": 10, "color": "#16794A" if category == "demand" else "#C23B33"},
            bgcolor="rgba(255,255,255,0.8)",
            row=1,
            col=1,
        )


def _render_anatomy(match: PatternMatch) -> None:
    with st.container(border=True):
        section_title("Pattern Anatomy")
        for item in _anatomy_lines(match.pattern_type):
            st.markdown(
                f"<div style='font-size:0.8rem;padding:3px 0;color:#4A5361;'>"
                f"&bull; {html.escape(item)}</div>",
                unsafe_allow_html=True,
            )


def _render_breakout_checklist(match: PatternMatch) -> None:
    with st.container(border=True):
        section_title("Breakout Checklist")
        direction = _match_direction(match)
        upper_label = "Break above upper boundary"
        lower_label = "Break below lower boundary"
        primary_break = lower_label if direction == "short" else upper_label
        checked = {
            primary_break: match.stage == "Breakout Confirmed",
            "Volume confirms the break": match.stage == "Breakout Confirmed" and not match.volume_contraction,
            "Zone confirmation": match.zone_context in ("Near Demand Zone", "Near Supply Zone"),
            "Candle close confirmation": match.stage == "Breakout Confirmed",
        }
        for label, ok in checked.items():
            box = "&#9745;" if ok else "&#9744;"
            st.markdown(
                f"<div style='font-size:0.8rem;padding:3px 0;color:#4A5361;'>"
                f"{box} {html.escape(label)}</div>",
                unsafe_allow_html=True,
            )


def _render_status(match: PatternMatch) -> None:
    with st.container(border=True):
        section_title("Pattern Status")
        stages = ["Forming", "Near Apex", "Breakout Confirmed"]
        active = stages.index(match.stage) if match.stage in stages else 0
        cols = st.columns(3)
        for idx, (col, label) in enumerate(zip(cols, stages)):
            tone = "#FF7A1A" if idx == active else "#A8A8A0"
            fill = "#FFF3E6" if idx == active else "#EEF0F3"
            col.markdown(
                f"<div style='text-align:center;'>"
                f"<span style='display:inline-flex;align-items:center;justify-content:center;"
                f"width:30px;height:30px;border-radius:50%;background:{fill};color:{tone};"
                f"font-weight:800;border:1px solid {tone};'>{idx + 1}</span>"
                f"<div style='font-size:0.72rem;margin-top:4px;color:#4A5361;'>"
                f"{html.escape('Near Trigger' if label == 'Near Apex' else label)}</div></div>",
                unsafe_allow_html=True,
            )


def _render_risk_plan(match: PatternMatch) -> None:
    with st.container(border=True):
        section_title("Risk Plan")
        direction = _match_direction(match)
        if direction == "short":
            kv_row("Entry Trigger", "Break below lower boundary")
            kv_row("Stop Loss", f"Above {match.breakout_level:,.2f}")
        elif direction == "long":
            kv_row("Entry Trigger", "Break above upper boundary")
            kv_row("Stop Loss", f"Below {match.breakdown_level:,.2f}")
        else:
            kv_row("Entry Trigger", "Wait for confirmed break")
            kv_row("Stop Loss", "Set after direction confirms")
        upside = _zone_price(match.nearest_supply_zone) or f"{match.breakout_level * 1.04:,.2f}"
        downside = _zone_price(match.nearest_demand_zone) or f"{match.breakdown_level:,.2f}"
        kv_row("Upside Scenario", str(upside), tone="#16794A")
        kv_row("Downside Scenario", str(downside), tone="#C23B33")
        kv_row("Reward / Risk", f"{match.risk_reward:.2f}:1" if match.risk_reward else "Not calculable")


def _render_key_level_cards(match: PatternMatch) -> None:
    cols = st.columns(6)
    demand = _zone_price(match.nearest_demand_zone) or "No nearby demand"
    supply = _zone_price(match.nearest_supply_zone) or "No nearby supply"
    values = [
        ("Current Price", f"{match.current_price:,.2f}", f"{match.change:+.2f} ({match.change_pct:+.2f}%)", "info"),
        ("Trigger Distance", f"{match.apex_proximity:.1f}%", "Lower means closer", "purple"),
        ("Demand Zone", str(demand), match.zone_context, "bullish"),
        ("Supply Zone", str(supply), match.zone_context, "bearish"),
        ("Reward / Risk", f"{match.risk_reward:.2f}:1" if match.risk_reward else "N/A", "Indicative only", "warning"),
        ("Volatility Contraction", "Yes" if match.volume_contraction else "No", f"ratio {match.volume_contraction_ratio:.2f}", "info"),
    ]
    for col, (label, value, sub, tone) in zip(cols, values):
        with col:
            stat_card(label, value, sub, "target", tone=tone)  # type: ignore[arg-type]


def _render_timeline(match: PatternMatch) -> None:
    with st.container(border=True):
        section_title("Recent Pattern Timeline")
        steps = [
            ("1", "Pattern first detected", "Valid swing structure found."),
            ("2", "Contraction improved", "Trendlines converged with lower volatility."),
            ("3", f"{match.stage} detected", f"Freshness: {match.freshness_candles} candles."),
            ("4", "Awaiting confirmation", "Monitor breakout close and volume."),
        ]
        cols = st.columns(4)
        for col, (num, title, text) in zip(cols, steps):
            col.markdown(
                f"<div style='font-size:0.78rem;color:#4A5361;'>"
                f"<span style='display:inline-flex;width:24px;height:24px;border-radius:50%;"
                f"align-items:center;justify-content:center;background:#EEF4FF;color:#2F5FE0;"
                f"font-weight:800;margin-bottom:5px;'>{num}</span><br>"
                f"<b>{html.escape(title)}</b><br>"
                f"<span style='color:#71757C;'>{html.escape(text)}</span></div>",
                unsafe_allow_html=True,
            )


def _render_action_panel(match: PatternMatch, zone_result: dict[str, Any]) -> None:
    with st.container(border=True):
        section_title("Action Panel")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("Add to Watch", icon=":material/star:", use_container_width=True):
                watch = st.session_state.setdefault("pattern_watch_symbols", set())
                watch.add(match.symbol)
                st.session_state["pattern_watch_symbols"] = watch
                st.success(f"{match.symbol} added to the pattern watch list.")
        with c2:
            if st.button("Create Alert", icon=":material/notifications:", use_container_width=True):
                st.session_state.setdefault("notifications", []).append(
                    {"type": "info", "message": f"Pattern alert staged for {match.symbol}."}
                )
                st.success("Pattern alert staged for this session.")
        with c3:
            if st.button("View Stock Report", icon=":material/article:", use_container_width=True):
                results = st.session_state.get("analysis_results", {}) or {}
                if zone_result:
                    results[match.symbol] = {**zone_result, "exchange": match.exchange}
                    st.session_state["analysis_results"] = results
                st.session_state["selected_stock_symbol"] = match.symbol
                st.session_state["selected_stock_id"] = 0
                st.session_state.active_page = "stock_detail"
                st.rerun()
        with c4:
            if st.button("Mark Reviewed", icon=":material/check_circle:", use_container_width=True):
                reviewed = st.session_state.setdefault("pattern_reviewed_symbols", set())
                reviewed.add(match.symbol)
                st.session_state["pattern_reviewed_symbols"] = reviewed
                st.success("Marked as reviewed.")
        st.caption("Pattern-specific anatomy, levels, zones, and risk context are shown for the selected setup.")


def _anatomy_lines(pattern_type: str) -> list[str]:
    if pattern_type == "VCP / Tight Base":
        return [
            "Volatility contracts through smaller price swings.",
            "Volume should cool during the base.",
            "Pivot is the upper edge of the tight base.",
            "Breakout needs a candle close beyond the pivot.",
            "Invalidation usually sits below the base support.",
        ]
    if pattern_type in ("Rectangle Range", "Bullish Rectangle Breakout", "Bearish Rectangle Breakdown"):
        return [
            "Repeated support and resistance define the range.",
            "Breakout confirms only after a close outside the box.",
            "A retest holding the old boundary improves quality.",
            "Range height gives an indicative target projection.",
            "Failed re-entry into the box weakens the setup.",
        ]
    if pattern_type in ("Bull Flag", "Bear Flag", "Bull Pennant", "Bear Pennant"):
        return [
            "A sharp impulse move forms the flagpole.",
            "Price pauses in a compact flag or pennant.",
            "Volume ideally contracts during the pause.",
            "Continuation confirms when price clears the pause boundary.",
            "Invalidation sits beyond the opposite side of the pause.",
        ]
    if pattern_type == "Double Bottom":
        return [
            "Two similar lows show sellers failing near support.",
            "The neckline is resistance between the two lows.",
            "Breakout above the neckline confirms the reversal attempt.",
            "Demand-zone context improves the bullish setup.",
            "Invalidation sits below the double-bottom support.",
        ]
    if pattern_type == "Double Top":
        return [
            "Two similar highs show buyers failing near resistance.",
            "The neckline is support between the two highs.",
            "Breakdown below the neckline confirms the reversal attempt.",
            "Supply-zone context improves the bearish setup.",
            "Invalidation sits above the double-top resistance.",
        ]
    if pattern_type == "Ascending Triangle":
        return [
            "Flat resistance along the upper boundary.",
            "Higher lows form rising support.",
            "Price compresses into a tighter range.",
            "Breakout bias is pressure only until confirmed.",
            "Zone context can improve the setup quality.",
        ]
    if pattern_type == "Descending Triangle":
        return [
            "Falling highs press into support.",
            "Flat base marks the lower boundary.",
            "Price compresses into the apex.",
            "Breakdown bias is pressure only until confirmed.",
            "Demand/supply context helps validate the break.",
        ]
    return [
        "Lower highs form descending resistance.",
        "Higher lows form rising support.",
        "Price compresses into a tight range.",
        "Converging trendlines meet near the apex.",
        "Direction stays neutral until breakout or breakdown.",
    ]


def _zone_price(zone: dict[str, Any] | None) -> str | None:
    if not zone or not zone.get("proximal"):
        return None
    top = max(float(zone["proximal"]), float(zone["distal"]))
    bottom = min(float(zone["proximal"]), float(zone["distal"]))
    return f"{bottom:,.2f} - {top:,.2f}"


def _match_direction(match: PatternMatch) -> str | None:
    if match.breakout_bias == "Up Break":
        return "long"
    if match.breakout_bias == "Down Break":
        return "short"
    if match.pattern_type in (
        "Ascending Triangle",
        "VCP / Tight Base",
        "Bullish Rectangle Breakout",
        "Bull Flag",
        "Bull Pennant",
        "Double Bottom",
    ):
        return "long"
    if match.pattern_type in (
        "Descending Triangle",
        "Bearish Rectangle Breakdown",
        "Bear Flag",
        "Bear Pennant",
        "Double Top",
    ):
        return "short"
    return None


def _stage_tone(stage: str) -> str:
    return {"Forming": "bullish", "Near Apex": "warning", "Breakout Confirmed": "purple"}.get(stage, "neutral")


def _bias_tone(text: str) -> str:
    if "Bullish" in text or text == "Up Break":
        return "bullish"
    if "Bearish" in text or text == "Down Break":
        return "bearish"
    return "muted"


def _zone_tone(text: str) -> str:
    if text == "Near Demand Zone":
        return "bullish"
    if text == "Near Supply Zone":
        return "bearish"
    return "info"


def _detail_pdf_bytes(match: PatternMatch) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    except Exception:
        return b"ReportLab is not available."

    bio = BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=letter)
    data = [
        ["Field", "Value"],
        ["Symbol", match.symbol],
        ["Company", match.company_name],
        ["Pattern", match.pattern_type],
        ["Stage", match.stage],
        ["Confidence", f"{match.confidence_score:.0f}%"],
        ["Trigger Distance", f"{match.apex_proximity:.1f}%"],
        ["Breakout Bias", match.breakout_bias],
        ["Zone Context", match.zone_context],
        ["Breakout Level", f"{match.breakout_level:,.2f}"],
        ["Breakdown Level", f"{match.breakdown_level:,.2f}"],
    ]
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF4FF")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8DCE2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]
        )
    )
    doc.build([table])
    return bio.getvalue()
