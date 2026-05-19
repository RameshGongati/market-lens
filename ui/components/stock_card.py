"""Individual stock status card component."""

import streamlit as st

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
    change_pct: float,
    stock_id: int,
) -> None:
    """Render a colour-coded stock card.

    Green for bullish, red for bearish, yellow for neutral.
    Clicking the card sets the selected stock in session state.

    Args:
        symbol: Stock ticker symbol.
        exchange: Exchange (NSE/BSE).
        status: "bullish", "bearish", or "neutral".
        summary: One-line analysis summary.
        current_price: Latest price.
        change_pct: Intraday percentage change.
        stock_id: Database ID used to navigate to detail view.
    """
    cfg = _STATUS_CONFIG.get(status, _DEFAULT_CONFIG)
    icon = cfg["icon"]
    change_sign = "+" if change_pct >= 0 else ""

    card_html = f"""
    <div style="
        background:{cfg['bg']};
        border-left: 4px solid {cfg['border']};
        border-radius:6px;
        padding:12px 14px;
        margin-bottom:8px;
    ">
        <div style="display:flex;justify-content:space-between;align-items:center;">
            <span style="font-weight:700;font-size:1rem;color:{cfg['text']};">
                {icon} {symbol}
            </span>
            <span style="font-size:0.8rem;color:#666;">{exchange}</span>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:4px;">
            <span style="font-size:1rem;font-weight:600;">₹{current_price:,.2f}</span>
            <span style="color:{cfg['border']};font-weight:600;">
                {change_sign}{change_pct:.2f}%
            </span>
        </div>
        <div style="font-size:0.78rem;color:#555;margin-top:6px;">{summary}</div>
    </div>
    """
    st.markdown(card_html, unsafe_allow_html=True)

    if st.button(
        f"View {symbol} details",
        key=f"card_btn_{stock_id}",
        use_container_width=True,
    ):
        st.session_state.selected_stock_symbol = symbol
        st.session_state.active_page = "stock_detail"
        st.rerun()
