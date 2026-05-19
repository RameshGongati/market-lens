"""Common helper functions used across the application."""

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytz

_IST = pytz.timezone("Asia/Kolkata")
_STOCK_LIST_PATH = Path(__file__).parent.parent / "data" / "stock_list.json"


def format_currency(amount: float, currency: str = "₹") -> str:
    """Format a number as Indian currency with comma separators.

    Args:
        amount: Numeric value.
        currency: Currency symbol prefix.

    Returns:
        Formatted string, e.g. "₹2,456.75".
    """
    return f"{currency}{amount:,.2f}"


def format_price(price: float, currency: str = "₹") -> str:
    """Alias for format_currency — kept for backward compatibility."""
    return format_currency(price, currency)


def format_change(change: float, change_pct: float) -> str:
    """Format absolute and percentage change for display.

    Args:
        change: Absolute price change.
        change_pct: Percentage change.

    Returns:
        Formatted string, e.g. "+₹45.20 | +1.87%".
    """
    sign = "+" if change >= 0 else ""
    return f"{sign}₹{change:.2f} | {sign}{change_pct:.2f}%"


def format_timestamp(dt: datetime | str | None) -> str:
    """Format a datetime as a human-friendly relative string.

    Args:
        dt: UTC datetime object or ISO-format string.

    Returns:
        Strings like "Today 14:32", "Yesterday 09:15", or "15 May 2025".
    """
    if dt is None:
        return "Never"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt

    now_ist = datetime.now(_IST)
    # Convert naive UTC datetime to IST
    try:
        dt_ist = pytz.utc.localize(dt).astimezone(_IST)
    except Exception:
        dt_ist = dt  # type: ignore[assignment]

    diff_days = (now_ist.date() - dt_ist.date()).days  # type: ignore[union-attr]
    time_str = dt_ist.strftime("%H:%M")  # type: ignore[union-attr]
    if diff_days == 0:
        return f"Today {time_str}"
    if diff_days == 1:
        return f"Yesterday {time_str}"
    if diff_days < 7:
        return f"{dt_ist.strftime('%A')} {time_str}"  # type: ignore[union-attr]
    return dt_ist.strftime("%d %b %Y")  # type: ignore[union-attr]


@lru_cache(maxsize=1)
def _load_stock_list() -> list[dict[str, str]]:
    """Load and cache the stock list from data/stock_list.json."""
    try:
        return json.loads(_STOCK_LIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def get_company_name(symbol: str) -> str:
    """Look up the company name for a given stock symbol.

    Args:
        symbol: Stock ticker symbol (case-insensitive).

    Returns:
        Company name string, or the symbol itself if not found.
    """
    sym_upper = symbol.upper()
    for stock in _load_stock_list():
        if stock.get("symbol", "").upper() == sym_upper:
            return stock["name"]
    return symbol


def search_stocks(query: str, limit: int = 10) -> list[dict[str, str]]:
    """Search stocks by symbol or company name prefix.

    Args:
        query: Search string (case-insensitive).
        limit: Maximum number of results to return.

    Returns:
        List of matching stock dicts with symbol, name, exchange keys.
    """
    if not query or len(query) < 1:
        return []
    q = query.upper()
    results = [
        s for s in _load_stock_list()
        if q in s.get("symbol", "").upper() or q in s.get("name", "").upper()
    ]
    return results[:limit]


def safe_get(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Return a value from *data* or *default* if the key is missing/None."""
    value = data.get(key)
    return value if value is not None else default


def truncate(text: str, max_len: int = 80) -> str:
    """Truncate *text* to *max_len* characters, appending ellipsis if needed."""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"
