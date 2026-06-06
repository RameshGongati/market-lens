"""
TradingView chart widget component using free embeddable widget.
No API key required.
"""

import streamlit.components.v1 as components

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


def render_tradingview_chart(
    symbol: str,
    exchange: str,
    height: int = 600,
    width: str = "100%",
    default_interval: str = "D",
    compact: bool = False,
    theme: str = "light",
) -> None:
    """Render an embedded TradingView advanced chart widget.

    Uses the free TradingView embeddable widget (``tv.js``) — no API key or
    account is required. The widget is rendered inside ``st.components.v1.html``
    so it runs in its own sandboxed iframe and cannot interfere with the rest
    of the Streamlit app.

    Args:
        symbol: Raw stock symbol (e.g. "WIPRO").
        exchange: "NSE" or "BSE".
        height: Widget height in pixels.
        width: Widget width (CSS string, e.g. "100%" or "800").
        default_interval: Default chart interval/timeframe (e.g. "D", "W", "60").
        compact: When True, renders a smaller/simplified widget suited for
            inline mini-charts (hides the side toolbar and extra studies).
        theme: TradingView theme — "light" or "dark". The app uses a light
            theme throughout, so this should normally stay "light".
    """
    try:
        tv_symbol = get_tv_symbol(symbol, exchange)

        if compact:
            hide_side_toolbar = "true"
            hide_legend = "false"
            studies = "[]"
        else:
            hide_side_toolbar = "false"
            hide_legend = "false"
            studies = '["Volume@tv-basicstudies"]'

        widget_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body {{
    margin: 0;
    padding: 0;
    background-color: #ffffff;
    overflow: hidden;
  }}
  #tv_chart_container {{
    position: relative;
    width: {width};
    height: {height}px;
  }}
  #tv_chart_container.fullscreen {{
    position: fixed;
    top: 0;
    left: 0;
    width: 100vw !important;
    height: 100vh !important;
    z-index: 999999;
    background-color: #ffffff;
  }}
  #tradingview_widget {{
    width: 100%;
    height: 100%;
  }}
  .fullscreen-btn {{
    position: absolute;
    top: 8px;
    right: 8px;
    z-index: 1000000;
    background-color: #f1f3f6;
    color: #131722;
    border: 1px solid #d1d4dc;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 12px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    cursor: pointer;
  }}
  .fullscreen-btn:hover {{
    background-color: #e0e3eb;
  }}
</style>
</head>
<body>
  <div id="tv_chart_container">
    <button class="fullscreen-btn" id="fullscreen_btn" onclick="toggleFullscreen()">⛶ Full Screen</button>
    <div id="tradingview_widget"></div>
  </div>

  <script src="https://s3.tradingview.com/tv.js"></script>
  <script type="text/javascript">
    function loadWidget() {{
      try {{
        new TradingView.widget({{
          "width": "{width}",
          "height": {height},
          "symbol": "{tv_symbol}",
          "interval": "{default_interval}",
          "timezone": "Asia/Kolkata",
          "theme": "{theme}",
          "style": "1",
          "locale": "en",
          "toolbar_bg": "#f1f3f6",
          "enable_publishing": false,
          "hide_side_toolbar": {hide_side_toolbar},
          "hide_legend": {hide_legend},
          "allow_symbol_change": true,
          "save_image": true,
          "studies": {studies},
          "container_id": "tradingview_widget",
          "show_popup_button": false,
          "popup_width": "1000",
          "popup_height": "650",
          "withdateranges": true,
          "range": "12M",
          "details": true,
          "hotlist": false,
          "calendar": false
        }});
      }} catch (err) {{
        document.getElementById("tradingview_widget").innerHTML =
          "<p style='font-family:sans-serif;color:#888;padding:16px;'>" +
          "TradingView chart could not be loaded. Please check your internet connection." +
          "</p>";
      }}
    }}

    function toggleFullscreen() {{
      var container = document.getElementById("tv_chart_container");
      var btn = document.getElementById("fullscreen_btn");
      if (container.classList.contains("fullscreen")) {{
        container.classList.remove("fullscreen");
        btn.innerHTML = "⛶ Full Screen";
      }} else {{
        container.classList.add("fullscreen");
        btn.innerHTML = "✕ Exit Full Screen";
      }}
    }}

    document.addEventListener("keydown", function (event) {{
      if (event.key === "Escape") {{
        var container = document.getElementById("tv_chart_container");
        var btn = document.getElementById("fullscreen_btn");
        if (container.classList.contains("fullscreen")) {{
          container.classList.remove("fullscreen");
          btn.innerHTML = "⛶ Full Screen";
        }}
      }}
    }});

    loadWidget();
  </script>
</body>
</html>
"""

        components.html(widget_html, height=height + 10, scrolling=False)
    except Exception as exc:
        logger.warning("TradingView widget failed to render for %s (%s): %s", symbol, exchange, exc)
        import streamlit as st
        st.warning("TradingView chart could not be loaded. Please check your internet connection.")
