"""Watchlist and Stock data models."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Stock:
    """Represents a single stock entry within a watchlist."""

    symbol: str
    exchange: str
    watchlist_id: int
    id: int = 0
    added_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_db_row(cls, row: dict) -> "Stock":
        """Construct a Stock from a database row dict."""
        return cls(
            id=row["id"],
            symbol=row["symbol"],
            exchange=row["exchange"],
            watchlist_id=row["watchlist_id"],
            added_at=datetime.fromisoformat(row["added_at"]),
        )


@dataclass
class Watchlist:
    """Represents a user-defined watchlist containing stocks."""

    name: str
    id: int = 0
    stocks: list[Stock] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @classmethod
    def from_db_row(cls, row: dict, stocks: list[Stock] | None = None) -> "Watchlist":
        """Construct a Watchlist from a database row dict."""
        return cls(
            id=row["id"],
            name=row["name"],
            stocks=stocks or [],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
