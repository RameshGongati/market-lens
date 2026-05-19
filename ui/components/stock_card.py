"""Individual stock status card component."""

from datetime import datetime

import streamlit as st

from analysis.base import STRENGTH_BG, STRENGTH_COLORS
from utils.helpers import format_timestamp, get_company_name

_STATUS_CONFIG = {
    "bullish": {"bg": "#d4edda", "border": "#28a745", "icon": "▲", "text": "#155724"},
    "bearish": {"bg": "#f8d7da", "border": "#dc3545", "icon": "▼", "text": "#721c24"},
    "neutral": {"bg": "#fff3cd", "border": "#ffc107", "icon": "●", "text": "#856404"},
}
_DEFAULT_CONFIG = _STATUS_CONFIG["neutral"]


def render_stock_card(
    symbol: str,
    exchange: str,
    status: str,
    summary: str,
    current_price: float,
    change: float,
    change_pct: float,
    stock_id: int,
    strength: str = "Weak",
    updated_at: datetime | str | None = None,
) -> None:
    """Render a colour-coded stock card with full price and strength info.

    Args:
        symbol: Stock ticker symbol.
        exchange: Exchange (NSE/BSE).
        status: "bullish", "bearish", or "neutral".
        summary: One-line analysis summary.
        current_price: Latest price.
        change: Absolute intraday price change.
        change_pct: Intraday percentage change.
        stock_id: Database ID used to navigate to detail view.
        strength: "Strong", "Medium", or "Weak".
        updated_at: Timestamp of the last analysis run.
    """
    cfg = _STATUS_CONFIG.get(status, _DEFAULT_CONFIG)
    icon = cfg["icon"]
    change_sign = "+" if change_pct >= 0 else ""
    change_color = "#28a745" if change_pct >= 0 else "#dc3545"
    company_name = get_company_name(symbol)
    strength_color = STRENGTH_COLORS.get(strength, "#721c24")
    strength_bg = STRENGTH_BG.get(strength, "#f8d7da")
    ts = format_timestamp(updated_at) if updated_at else ""

    card_html = f"""
    <div style="
        background:{cfg['bg']};
        border-left:4px solid {cfg['border']};
        border-radius:8px;
        padding:14px 16px;
        margin-bottom:10px;
        cursor:pointer;
    ">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
                <span style="font-weight:700;font-size:1.05rem;color:{cfg['text']};">
                    {icon} {symbol}
                </span>
                <div style="font-size:0.75rem;color:#666;margin-top:1px;">{company_name}</div>
            </div>
            <div style="text-align:right;">
                <span style="font-size:0.72rem;background:{cfg['border']};color:white;
                    padding:2px 7px;border-radius:10px;font-weight:600;">
                    {status.upper()}
                </span>
                &nbsp;
                <span style="font-size:0.72rem;background:{strength_bg};color:{strength_color};
                    padding:2px 7px;border-radius:10px;font-weight:600;border:1px solid {strength_color};">
                    {strength}
                </span>
            </div>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
            <span style="font-size:1.1rem;font-weight:700;">₹{current_price:,.2f}</span>
            <span style="color:{change_color};font-weight:600;font-size:0.88rem;">
                {change_sign}₹{abs(change):.2f} ({change_sign}{change_pct:.2f}%)
            </span>
        </div>
        <div style="font-size:0.76rem;color:#555;margin-top:6px;line-height:1.4;">
            {summary[:100]}{"…" if len(summary) > 100 else ""}
        </div>
        {"<div style='font-size:0.68rem;color:#999;margin-top:4px;'>Updated: " + ts + "</div>" if ts else ""}
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    if st.button(
        f"View {symbol} →",
        key=f"card_btn_{stock_id}",
        use_container_width=True,
        type="secondary",
    ):
        st.session_state.selected_stock_symbol = symbol
        st.session_state.selected_stock_id = stock_id
        st.session_state.active_page = "stock_detail"
        st.rerun()
