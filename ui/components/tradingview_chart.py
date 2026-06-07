"""
TradingView chart widget component using free embeddable widget.
No API key required.
"""

import streamlit as st

from utils.logger import get_logger

logger = get_logger(__name__)


def get_tv_symbol(symbol: str, exchange: str) -> str:
    """Convert a stock symbol/exchange pair into TradingView's symbol format.

    Args:
        symbol: Raw stock symbol (e.g. "WIPRO").
        exchange: "NSE" or "BSE".

    Returns:
        TradingView-formatted symbol string, e.g. "NSE:WIPRO" or "BSE:WIPRO".
        Defaults to the NSE prefix for unrecognised exchanges.
    """
    exchange = (exchange or "").upper()
    if exchange == "NSE":
        return f"NSE:{symbol.upper()}"
    elif exchange == "BSE":
        return f"BSE:{symbol.upper()}"
    else:
        return f"NSE:{symbol.upper()}"


def get_tradingview_url(symbol: str, exchange: str) -> str:
    """Build a deep-link URL that opens the symbol on tradingview.com.

    This is the primary way users view live TradingView charts for Indian
    stocks in this app — the link opens TradingView's full interactive chart
    (with live data, all timeframes, indicators and drawing tools) in a new
    browser tab, defaulting to the daily-interval candlestick view.

    Args:
        symbol: Raw stock symbol (e.g. "WIPRO").
        exchange: "NSE" or "BSE".

    Returns:
        A TradingView chart URL for the given symbol, opened on the daily
        ("D") candlestick (style=1) view.
    """
    tv_symbol = get_tv_symbol(symbol, exchange)
    # style=1 is Candlestick in TradingView
    return (
        f"https://www.tradingview.com/chart/"
        f"?symbol={tv_symbol}"
        f"&interval=D"
        f"&style=1"
    )


def render_tradingview_chart(
    symbol: str,
    exchange: str,
    height: int = 600,
    width: str = "100%",
    default_interval: str = "D",
    compact: bool = False,
    theme: str = "light",
) -> None:
    """Render a TradingView chart placeholder linking out to tradingview.com.

    The free embeddable TradingView widget (``tv.js``) does not reliably load
    Indian (NSE/BSE) market data from unauthenticated/local browser contexts
    (e.g. WSL-hosted Streamlit sessions) — it ends up showing a perpetually
    blank iframe. Rather than render that broken experience, this shows a
    clean, informative placeholder box: an icon, a short explanation, and a
    direct "Open … in TradingView" link that opens the full interactive
    chart — with live data, all timeframes, indicators and drawing tools —
    in a new browser tab (see ``get_tradingview_url``).

    Args:
        symbol: Raw stock symbol (e.g. "WIPRO").
        exchange: "NSE" or "BSE".
        height: Unused; kept for backward-compatible call signatures.
        width: Unused; kept for backward-compatible call signatures.
        default_interval: Unused; kept for backward-compatible call
            signatures (the deep link always opens the daily candlestick
            view via ``get_tradingview_url``).
        compact: When True, renders a smaller placeholder suited for inline
            mini-chart sections (e.g. the watchlist row expander).
        theme: Unused; kept for backward-compatible call signatures. The app
            uses a light theme throughout.
    """
    try:
        tv_url = get_tradingview_url(symbol, exchange)

        # One-time login guidance banner — shown only once per session so it
        # doesn't repeat for every chart placeholder rendered afterwards.
        if not st.session_state.get("tv_login_shown", False):
            st.info(
                "📈 **TradingView Charts** — For the best "
                "experience with Indian stocks, open charts "
                "directly in TradingView using the button below. "
                "Make sure you are logged in to TradingView "
                "for full access."
            )
            st.session_state["tv_login_shown"] = True

        if compact:
            box_height = "120px"
            msg = "Click button above to open full chart"
        else:
            box_height = "300px"
            msg = (
                "Click 'Open in TradingView →' above "
                "to view the full interactive chart "
                "with all timeframes, indicators "
                "and drawing tools."
            )

        st.markdown(
            f"""
            <div style="
                height: {box_height};
                border: 2px dashed #cccccc;
                border-radius: 8px;
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                background: #fafafa;
                color: #888888;
                font-family: Arial, sans-serif;
                gap: 12px;
            ">
                <span style="font-size: 48px;">📈</span>
                <span style="font-size: 14px;
                             text-align: center;
                             padding: 0 20px;">
                    {msg}
                </span>
                <a href="{tv_url}"
                   target="_blank"
                   style="
                       background: #2962ff;
                       color: white;
                       padding: 8px 16px;
                       border-radius: 4px;
                       text-decoration: none;
                       font-size: 13px;
                   ">
                   🔗 Open {get_tv_symbol(symbol, exchange)} in TradingView
                </a>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(
            "TradingView placeholder failed to render for %s (%s): %s", symbol, exchange, exc
        )
        st.warning("TradingView chart could not be loaded. Please check your internet connection.")
