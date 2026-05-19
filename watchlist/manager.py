"""Watchlist CRUD operations with business-rule enforcement."""

from config.settings import MAX_STOCKS_PER_WATCHLIST, MAX_WATCHLISTS
from storage import database as db
from watchlist.models import Stock, Watchlist


def create_watchlist(name: str) -> Watchlist:
    """Create a new watchlist.

    Args:
        name: Unique display name for the watchlist.

    Returns:
        The newly created Watchlist instance.

    Raises:
        ValueError: If the maximum number of watchlists has been reached or
            *name* is blank/duplicate.
    """
    name = name.strip()
    if not name:
        raise ValueError("Watchlist name cannot be empty.")
    existing = db.get_all_watchlists()
    if len(existing) >= MAX_WATCHLISTS:
        raise ValueError(
            f"Maximum of {MAX_WATCHLISTS} watchlists reached. "
            "Delete an existing watchlist before creating a new one."
        )
    if any(w["name"].lower() == name.lower() for w in existing):
        raise ValueError(f"A watchlist named '{name}' already exists.")
    wl_id = db.create_watchlist(name)
    rows = db.get_all_watchlists()
    row = next(r for r in rows if r["id"] == wl_id)
    return Watchlist.from_db_row(row)


def delete_watchlist(watchlist_id: int) -> None:
    """Delete a watchlist and all its stocks.

    Args:
        watchlist_id: Primary key of the watchlist to delete.
    """
    db.delete_watchlist(watchlist_id)


def get_all_watchlists() -> list[Watchlist]:
    """Return all watchlists without their stock lists populated."""
    rows = db.get_all_watchlists()
    return [Watchlist.from_db_row(r) for r in rows]


def add_stock(watchlist_id: int, symbol: str, exchange: str) -> Stock:
    """Add a stock to a watchlist.

    Args:
        watchlist_id: Target watchlist primary key.
        symbol: Stock ticker symbol (e.g. "RELIANCE").
        exchange: Exchange identifier ("NSE" or "BSE").

    Returns:
        The newly created Stock instance.

    Raises:
        ValueError: If the watchlist is full or the stock already exists.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Stock symbol cannot be empty.")
    count = db.count_stocks(watchlist_id)
    if count >= MAX_STOCKS_PER_WATCHLIST:
        raise ValueError(
            f"Maximum of {MAX_STOCKS_PER_WATCHLIST} stocks per watchlist reached."
        )
    existing = db.get_stocks(watchlist_id)
    if any(
        s["symbol"] == symbol and s["exchange"].upper() == exchange.upper()
        for s in existing
    ):
        raise ValueError(f"{symbol} ({exchange}) is already in this watchlist.")
    stock_id = db.add_stock(watchlist_id, symbol, exchange)
    db.touch_watchlist(watchlist_id)
    rows = db.get_stocks(watchlist_id)
    row = next(r for r in rows if r["id"] == stock_id)
    return Stock.from_db_row(row)


def remove_stock(watchlist_id: int, stock_id: int) -> None:
    """Remove a stock from a watchlist.

    Args:
        watchlist_id: Parent watchlist primary key (used to update timestamp).
        stock_id: Stock primary key to remove.
    """
    db.remove_stock(stock_id)
    db.touch_watchlist(watchlist_id)


def get_stocks(watchlist_id: int) -> list[Stock]:
    """Return all stocks in the given watchlist.

    Args:
        watchlist_id: Target watchlist primary key.
    """
    rows = db.get_stocks(watchlist_id)
    return [Stock.from_db_row(r) for r in rows]
