"""SQLite database operations for Market Lens."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from utils.logger import get_logger

logger = get_logger(__name__)

_APP_DIR = Path.home() / ".market-lens"
_DB_PATH = _APP_DIR / "market_lens.db"


def db_path() -> Path:
    """Return the path to the SQLite database file."""
    return _DB_PATH


@contextmanager
def _get_conn() -> Generator[sqlite3.Connection, None, None]:
    """Yield a database connection with row factory set."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create database directory and tables if they do not exist."""
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    with _get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlists (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE,
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS stocks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
                symbol       TEXT    NOT NULL,
                exchange     TEXT    NOT NULL DEFAULT 'NSE',
                added_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS analysis_results (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id      INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                analysis_type TEXT    NOT NULL,
                result_json   TEXT    NOT NULL DEFAULT '{}',
                status        TEXT    NOT NULL DEFAULT 'pending',
                created_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id      INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                analysis_type TEXT    NOT NULL,
                message       TEXT    NOT NULL,
                is_read       INTEGER NOT NULL DEFAULT 0,
                created_at    TEXT    NOT NULL
            );
            """
        )
    logger.info("Database initialised at %s", _DB_PATH)


# ---------------------------------------------------------------------------
# Watchlist CRUD
# ---------------------------------------------------------------------------

def create_watchlist(name: str) -> int:
    """Insert a new watchlist and return its id."""
    now = _now()
    with _get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO watchlists (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, now, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_all_watchlists() -> list[dict[str, Any]]:
    """Return all watchlists ordered by creation time."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM watchlists ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_watchlist(watchlist_id: int) -> None:
    """Delete a watchlist by id (cascades to stocks)."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))


def touch_watchlist(watchlist_id: int) -> None:
    """Update the updated_at timestamp for a watchlist."""
    with _get_conn() as conn:
        conn.execute(
            "UPDATE watchlists SET updated_at = ? WHERE id = ?",
            (_now(), watchlist_id),
        )


# ---------------------------------------------------------------------------
# Stock CRUD
# ---------------------------------------------------------------------------

def add_stock(watchlist_id: int, symbol: str, exchange: str) -> int:
    """Insert a stock into a watchlist and return its id."""
    with _get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO stocks (watchlist_id, symbol, exchange, added_at) VALUES (?, ?, ?, ?)",
            (watchlist_id, symbol.upper(), exchange.upper(), _now()),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_stocks(watchlist_id: int) -> list[dict[str, Any]]:
    """Return all stocks in a watchlist."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stocks WHERE watchlist_id = ? ORDER BY added_at",
            (watchlist_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def remove_stock(stock_id: int) -> None:
    """Delete a stock by id."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))


def count_stocks(watchlist_id: int) -> int:
    """Return the number of stocks in a watchlist."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM stocks WHERE watchlist_id = ?", (watchlist_id,)
        ).fetchone()
        return row[0]


# ---------------------------------------------------------------------------
# Analysis Results CRUD
# ---------------------------------------------------------------------------

def save_analysis_result(
    stock_id: int,
    analysis_type: str,
    result: dict[str, Any],
    status: str = "completed",
) -> int:
    """Upsert an analysis result for a stock."""
    with _get_conn() as conn:
        # Delete existing result for this stock + type before inserting fresh
        conn.execute(
            "DELETE FROM analysis_results WHERE stock_id = ? AND analysis_type = ?",
            (stock_id, analysis_type),
        )
        cursor = conn.execute(
            """INSERT INTO analysis_results
               (stock_id, analysis_type, result_json, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (stock_id, analysis_type, json.dumps(result), status, _now()),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_analysis_result(
    stock_id: int, analysis_type: str
) -> dict[str, Any] | None:
    """Retrieve the latest analysis result for a stock."""
    with _get_conn() as conn:
        row = conn.execute(
            """SELECT * FROM analysis_results
               WHERE stock_id = ? AND analysis_type = ?
               ORDER BY created_at DESC LIMIT 1""",
            (stock_id, analysis_type),
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["result_json"] = json.loads(data["result_json"])
        return data


# ---------------------------------------------------------------------------
# Alerts CRUD
# ---------------------------------------------------------------------------

def create_alert(
    stock_id: int, analysis_type: str, message: str
) -> int:
    """Insert a new alert and return its id."""
    with _get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO alerts (stock_id, analysis_type, message, is_read, created_at)
               VALUES (?, ?, ?, 0, ?)""",
            (stock_id, analysis_type, message, _now()),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_unread_alerts() -> list[dict[str, Any]]:
    """Return all unread alerts ordered by creation time descending."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE is_read = 0 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_alert_read(alert_id: int) -> None:
    """Mark a single alert as read."""
    with _get_conn() as conn:
        conn.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))


def mark_all_alerts_read() -> None:
    """Mark all alerts as read."""
    with _get_conn() as conn:
        conn.execute("UPDATE alerts SET is_read = 1")


def _now() -> str:
    return datetime.utcnow().isoformat()
