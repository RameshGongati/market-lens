"""Common helper functions used across the application."""

from datetime import datetime, time
from typing import Any

import pytz

_IST = pytz.timezone("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)


def is_market_open() -> bool:
    """Return True if the Indian equity market is currently open (IST).

    Does not account for public holidays — a simple time-range check.
    """
    now_ist = datetime.now(_IST).time()
    return _MARKET_OPEN <= now_ist <= _MARKET_CLOSE


def format_price(price: float, currency: str = "₹") -> str:
    """Format a price with currency symbol and comma separators.

    Args:
        price: Numeric price value.
        currency: Currency symbol prefix.

    Returns:
        Formatted string, e.g. "₹1,23,456.78".
    """
    return f"{currency}{price:,.2f}"


def format_change(change: float, change_pct: float) -> str:
    """Format price change and percentage for display.

    Args:
        change: Absolute price change.
        change_pct: Percentage change.

    Returns:
        Formatted string, e.g. "+12.50 (+0.75%)".
    """
    sign = "+" if change >= 0 else ""
    return f"{sign}{change:.2f} ({sign}{change_pct:.2f}%)"


def safe_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Return a value from *data* or *default* if the key is missing/None.

    Args:
        data: Source dictionary.
        key: Key to look up.
        default: Fallback value.
    """
    value = data.get(key)
    return value if value is not None else default


def truncate(text: str, max_len: int = 80) -> str:
    """Truncate *text* to *max_len* characters, appending ellipsis if needed.

    Args:
        text: Input string.
        max_len: Maximum allowed length.
    """
    return text if len(text) <= max_len else text[: max_len - 1] + "…"
