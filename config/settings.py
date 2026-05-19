"""Global application settings."""

from datetime import time

APP_VERSION: str = "0.1.0"
APP_NAME: str = "Market Lens"

MAX_WATCHLISTS: int = 10
MAX_STOCKS_PER_WATCHLIST: int = 10

SUPPORTED_DATA_SOURCES: list[str] = [
    "Yahoo Finance",
    "NSE India",
    "Zerodha Kite Connect",
    "Upstox API",
    "TradingView",
]

ANALYSIS_TYPES: list[str] = [
    "Demand/Supply Zones",
    "Long Term Investment",
    "Short Term Investment",
    "Intraday Trading",
]

EXCHANGES: list[str] = ["NSE", "BSE"]

# Market hours in IST
MARKET_OPEN: time = time(9, 15)
MARKET_CLOSE: time = time(15, 30)

# Auto-refresh interval in seconds
AUTO_REFRESH_INTERVAL: int = 300  # 5 minutes

# Credentials required per data source
CREDENTIALS_REQUIRED: dict[str, list[str]] = {
    "Yahoo Finance": [],
    "NSE India": [],
    "Zerodha Kite Connect": ["api_key", "api_secret", "access_token"],
    "Upstox API": ["api_key", "api_secret", "access_token"],
    "TradingView": ["username", "password"],
}
