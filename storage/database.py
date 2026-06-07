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
    """Create database directory and all tables if they do not exist."""
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

            CREATE TABLE IF NOT EXISTS stock_notes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_id   INTEGER NOT NULL REFERENCES stocks(id) ON DELETE CASCADE,
                note_text  TEXT    NOT NULL,
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );
            """
        )
    logger.info("Database initialised at %s", _DB_PATH)


# ---------------------------------------------------------------------------
# Watchlist CRUD
# ---------------------------------------------------------------------------

def create_watchlist(name: str) -> int:
    now = _now()
    with _get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO watchlists (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name, now, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_all_watchlists() -> list[dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchlists ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]


def delete_watchlist(watchlist_id: int) -> None:
    with _get_conn() as conn:
        conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))


def touch_watchlist(watchlist_id: int) -> None:
    with _get_conn() as conn:
        conn.execute(
            "UPDATE watchlists SET updated_at = ? WHERE id = ?", (_now(), watchlist_id)
        )


# ---------------------------------------------------------------------------
# Stock CRUD
# ---------------------------------------------------------------------------

def add_stock(watchlist_id: int, symbol: str, exchange: str) -> int:
    with _get_conn() as conn:
        cursor = conn.execute(
            "INSERT INTO stocks (watchlist_id, symbol, exchange, added_at) VALUES (?, ?, ?, ?)",
            (watchlist_id, symbol.upper(), exchange.upper(), _now()),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_stocks(watchlist_id: int) -> list[dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stocks WHERE watchlist_id = ? ORDER BY added_at",
            (watchlist_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def remove_stock(stock_id: int) -> None:
    with _get_conn() as conn:
        conn.execute("DELETE FROM stocks WHERE id = ?", (stock_id,))


def count_stocks(watchlist_id: int) -> int:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM stocks WHERE watchlist_id = ?", (watchlist_id,)
        ).fetchone()
        return row[0]


# ---------------------------------------------------------------------------
# Analysis Results CRUD (history preserved)
# ---------------------------------------------------------------------------

def save_analysis_result(
    stock_id: int,
    analysis_type: str,
    result: dict[str, Any],
    status: str = "completed",
) -> int:
    """Append a new analysis result row (history is preserved, not overwritten).

    Keeps the last 20 results per stock+analysis_type to bound DB growth.
    """
    with _get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO analysis_results
               (stock_id, analysis_type, result_json, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (stock_id, analysis_type, json.dumps(result), status, _now()),
        )
        row_id = cursor.lastrowid
        # Prune oldest beyond 20 rows per stock+type
        conn.execute(
            """DELETE FROM analysis_results WHERE id NOT IN (
               SELECT id FROM analysis_results
               WHERE stock_id = ? AND analysis_type = ?
               ORDER BY created_at DESC LIMIT 20
            ) AND stock_id = ? AND analysis_type = ?""",
            (stock_id, analysis_type, stock_id, analysis_type),
        )
        return row_id  # type: ignore[return-value]


def get_analysis_result(
    stock_id: int, analysis_type: str
) -> dict[str, Any] | None:
    """Retrieve the most recent analysis result for a stock."""
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


def get_analysis_history(
    stock_id: int, analysis_type: str, limit: int = 7
) -> list[dict[str, Any]]:
    """Return the last *limit* analysis results for a stock, newest first.

    Args:
        stock_id: Database ID of the stock.
        analysis_type: Analysis type to filter by.
        limit: Maximum number of rows to return.

    Returns:
        List of dicts with id, status, strength, created_at, summary fields
        extracted from result_json for easy timeline display.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT id, status, result_json, created_at FROM analysis_results
               WHERE stock_id = ? AND analysis_type = ?
               ORDER BY created_at DESC LIMIT ?""",
            (stock_id, analysis_type, limit),
        ).fetchall()

    history = []
    for row in rows:
        result = json.loads(row["result_json"])
        history.append({
            "id": row["id"],
            "status": row["status"],
            "strength": result.get("strength", "—"),
            "summary": result.get("summary", ""),
            "created_at": row["created_at"],
        })
    return history


def compare_analysis_results(
    stock_id: int, analysis_type: str
) -> dict[str, Any]:
    """Compare the last 7 analysis results to detect trend consistency.

    Returns:
        Dict with: history (list), consistent_trend (bool),
        dominant_status (str), trend_direction (improving/deteriorating/stable).
    """
    history = get_analysis_history(stock_id, analysis_type, limit=7)
    if not history:
        return {"history": [], "consistent_trend": False, "dominant_status": "neutral"}

    statuses = [h["status"] for h in history]
    bullish_count = statuses.count("bullish")
    bearish_count = statuses.count("bearish")
    dominant = "bullish" if bullish_count > bearish_count else (
        "bearish" if bearish_count > bullish_count else "neutral"
    )
    consistent = (bullish_count >= 5 or bearish_count >= 5)

    # Trend direction: compare first half vs second half of history
    mid = len(statuses) // 2
    recent = statuses[:mid]
    older = statuses[mid:]
    recent_bull = recent.count("bullish") / max(len(recent), 1)
    older_bull = older.count("bullish") / max(len(older), 1)
    if recent_bull > older_bull + 0.2:
        direction = "improving"
    elif older_bull > recent_bull + 0.2:
        direction = "deteriorating"
    else:
        direction = "stable"

    return {
        "history": history,
        "consistent_trend": consistent,
        "dominant_status": dominant,
        "trend_direction": direction,
    }


def clear_all_analysis_history() -> None:
    """Delete all analysis result rows from the database."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM analysis_results")


# ---------------------------------------------------------------------------
# Alerts CRUD
# ---------------------------------------------------------------------------

def create_alert(stock_id: int, analysis_type: str, message: str) -> int:
    with _get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO alerts (stock_id, analysis_type, message, is_read, created_at)
               VALUES (?, ?, ?, 0, ?)""",
            (stock_id, analysis_type, message, _now()),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_unread_alerts() -> list[dict[str, Any]]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts WHERE is_read = 0 ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_alerts(limit: int = 50) -> list[dict[str, Any]]:
    """Return all alerts (read and unread) for export."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM alerts ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_alert_read(alert_id: int) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))


def mark_all_alerts_read() -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE alerts SET is_read = 1")


# ---------------------------------------------------------------------------
# Stock Notes CRUD
# ---------------------------------------------------------------------------

def save_note(stock_id: int, note_text: str) -> int:
    """Insert a new note for a stock and return its id.

    Args:
        stock_id: Database ID of the stock.
        note_text: The note content to save.
    """
    now = _now()
    with _get_conn() as conn:
        cursor = conn.execute(
            """INSERT INTO stock_notes (stock_id, note_text, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (stock_id, note_text.strip(), now, now),
        )
        return cursor.lastrowid  # type: ignore[return-value]


def get_notes(stock_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """Return the last *limit* notes for a stock, newest first.

    Args:
        stock_id: Database ID of the stock.
        limit: Maximum number of notes to return.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM stock_notes WHERE stock_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (stock_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_note(note_id: int) -> None:
    """Delete a single note by id.

    Args:
        note_id: Primary key of the note to delete.
    """
    with _get_conn() as conn:
        conn.execute("DELETE FROM stock_notes WHERE id = ?", (note_id,))


def clear_all_notes() -> None:
    """Delete all stock notes from the database."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM stock_notes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat()
