"""Individual stock status card component."""

import math
from datetime import datetime
from typing import Any

import streamlit as st

from analysis.base import STRENGTH_BG, STRENGTH_COLORS
from utils.helpers import format_timestamp, get_company_name

_STATUS_CONFIG = {
    "bullish": {"bg": "#d4edda", "border": "#28a745", "icon": "▲", "text": "#155724"},
    "bearish": {"bg": "#f8d7da", "border": "#dc3545", "icon": "▼", "text": "#721c24"},
    "neutral": {"bg": "#fff3cd", "border": "#ffc107", "icon": "●", "text": "#856404"},
}
_DEFAULT_CONFIG = _STATUS_CONFIG["neutral"]

# Trend Following signal badge colors (BUY/SELL/HOLD)
_TF_SIGNAL_COLORS = {
    "BUY":  {"bg": "#28a745", "text": "white"},
    "SELL": {"bg": "#dc3545", "text": "white"},
    "HOLD": {"bg": "#6c757d", "text": "white"},
}


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
    result: dict[str, Any] | None = None,
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
        result: Full analysis result dict (optional). When present and the
            result shape is ``strategy == "Trend Following"``, the card
            shows BUY/SELL/HOLD prominently and surfaces the cross context
            in the badge row; for Demand/Supply results behaviour is
            unchanged.
    """
    cfg = _STATUS_CONFIG.get(status, _DEFAULT_CONFIG)
    icon = cfg["icon"]
    change_sign = "+" if change_pct >= 0 else ""
    change_color = "#28a745" if change_pct >= 0 else "#dc3545"
    company_name = get_company_name(symbol)
    strength_color = STRENGTH_COLORS.get(strength, "#721c24")
    strength_bg = STRENGTH_BG.get(strength, "#f8d7da")
    ts = format_timestamp(updated_at) if updated_at else ""

    # Guard: treat None, NaN, or non-positive prices as "unavailable" so the
    # card never renders "₹nan". NaN is truthy in Python so a plain truthiness
    # check would incorrectly pass — we need an explicit isfinite test.
    _price_ok = (
        current_price is not None
        and math.isfinite(current_price)
        and current_price > 0
    )
    if _price_ok:
        price_html = f"₹{current_price:,.2f}"
        change_html = f"{change_sign}₹{abs(change):.2f} ({change_sign}{change_pct:.2f}%)"
    else:
        price_html = "<span style='color:#999;font-style:italic;'>Price unavailable</span>"
        change_html = "—"

    # --- Strategy-aware badge row -------------------------------------------
    # For Trend Following results: show BUY/SELL/HOLD prominently instead of
    # BULLISH/BEARISH/NEUTRAL, and surface the cross type if recent.
    # For Demand/Supply (or any other shape): fall back to status badge.
    # All result access is defensive via .get() so neither shape breaks the card.
    _is_tf = (result or {}).get("strategy") == "Trend Following"
    if _is_tf:
        _signal = (result or {}).get("signal", "HOLD")
        _sig_cfg = _TF_SIGNAL_COLORS.get(_signal, _TF_SIGNAL_COLORS["HOLD"])
        _last_cross = (result or {}).get("last_cross") or {}
        _cross_type = _last_cross.get("type")
        _candles_ago = _last_cross.get("candles_ago")
        # Show cross badge only if recent (within 30 candles)
        _show_cross = (
            _cross_type is not None
            and isinstance(_candles_ago, int)
            and _candles_ago <= 30
        )
        _cross_label = (
            f"{'Golden' if _cross_type == 'golden' else 'Death'} Cross ({_candles_ago}c)"
            if _show_cross else ""
        )
        _cross_badge_html = (
            f"&nbsp;<span style='font-size:0.72rem;"
            f"background:{'#28a745' if _cross_type == 'golden' else '#dc3545'};"
            f"color:white;padding:2px 7px;border-radius:10px;font-weight:600;'>"
            f"{_cross_label}</span>"
        ) if _show_cross else ""
        primary_badge_html = (
            f"<span style='font-size:0.72rem;background:{_sig_cfg['bg']};"
            f"color:{_sig_cfg['text']};padding:2px 7px;border-radius:10px;"
            f"font-weight:600;'>{_signal}</span>"
            f"{_cross_badge_html}"
        )
    else:
        primary_badge_html = (
            f"<span style='font-size:0.72rem;background:{cfg['border']};color:white;"
            f"padding:2px 7px;border-radius:10px;font-weight:600;'>"
            f"{status.upper()}</span>"
        )
    # Strength badge — shared by both strategies
    strength_badge_html = (
        f"<span style='font-size:0.72rem;background:{strength_bg};color:{strength_color};"
        f"padding:2px 7px;border-radius:10px;font-weight:600;"
        f"border:1px solid {strength_color};'>{strength}</span>"
    )

    # "In Zone" badge when CMP is inside a fresh zone
    in_zone_badge = ""
    if _price_ok and result:
        for _zk, _zcolor, _zlabel in (
            ("nearest_demand", "#28a745", "In Demand Zone"),
            ("nearest_supply", "#dc3545", "In Supply Zone"),
        ):
            _z = result.get(_zk)
            if not _z or not _z.get("proximal"):
                continue
            _ztop = max(_z["proximal"], _z["distal"])
            _zbot = min(_z["proximal"], _z["distal"])
            if _zbot <= current_price <= _ztop:
                in_zone_badge = (
                    f"<style>@keyframes zp{{0%,100%{{opacity:1}}50%{{opacity:.3}}}}</style>"
                    f"<div style='margin-top:4px;text-align:right;'>"
                    f"<span style='font-size:0.72rem;background:{_zcolor};"
                    f"color:white;padding:2px 7px;border-radius:10px;"
                    f"font-weight:600;white-space:nowrap;"
                    f"animation:zp 1.5s ease-in-out infinite;'>{_zlabel}</span>"
                    f"</div>"
                )
                break

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
                {primary_badge_html}
                &nbsp;
                {strength_badge_html}
            </div>
        </div>{in_zone_badge}
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px;">
            <span style="font-size:1.1rem;font-weight:700;">{price_html}</span>
            <span style="color:{change_color};font-weight:600;font-size:0.88rem;">
                {change_html}
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
        key=f"card_btn_{symbol}_{stock_id}",
        use_container_width=True,
        type="secondary",
    ):
        st.session_state.selected_stock_symbol = symbol
        st.session_state.selected_stock_id = stock_id
        st.session_state.active_page = "stock_detail"
        st.rerun()
