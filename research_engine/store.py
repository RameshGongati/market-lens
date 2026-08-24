"""Research Engine storage — a SEPARATE SQLite database.

Lives at ~/.market-lens/research_engine.db so experimental research data never
touches the app's market_lens.db. Stores run metadata, findings tables
(small summary frames as JSON), selected candidates (real table, filterable),
and file-path references to large artifacts (parquet/CSV/charts) that stay on
disk outside both git and the database.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DB_PATH = Path.home() / ".market-lens" / "research_engine.db"
CHARTS_DIR = Path.home() / ".market-lens" / "research_engine" / "charts"

_CANDIDATE_COLUMNS = [
    "symbol", "sector", "timeframe", "signal_date", "setup_name",
    "bullish_or_bearish", "final_decision", "entry_price", "stop_loss",
    "target_1", "target_2", "target_3", "rr_to_opposing",
    "trap_probability_score", "trap_reasons", "final_confidence_score",
    "sma50_trend", "ema20_confluence", "fibonacci_confluence",
    "volume_confirmation", "days_to_result", "result", "r_multiple",
    "holding_period",
]


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path | None = None) -> None:
    with _conn(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'imported',
                params_json TEXT NOT NULL DEFAULT '{}',
                headline_json TEXT NOT NULL DEFAULT '{}',
                artifacts_json TEXT NOT NULL DEFAULT '{}',
                warnings_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS findings (
                run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                columns_json TEXT NOT NULL,
                rows_json TEXT NOT NULL,
                PRIMARY KEY (run_id, name)
            );
            CREATE TABLE IF NOT EXISTS trade_lab_analyses (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                mode TEXT NOT NULL,
                created_at TEXT NOT NULL,
                inputs_json TEXT NOT NULL DEFAULT '{}',
                report_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS candidates (
                run_id INTEGER NOT NULL REFERENCES research_runs(id) ON DELETE CASCADE,
                symbol TEXT, sector TEXT, timeframe TEXT, signal_date TEXT,
                setup_name TEXT, bullish_or_bearish TEXT, final_decision TEXT,
                entry_price REAL, stop_loss REAL,
                target_1 REAL, target_2 REAL, target_3 REAL,
                rr_to_opposing REAL, trap_probability_score REAL,
                trap_reasons TEXT, final_confidence_score REAL,
                sma50_trend TEXT, ema20_confluence INTEGER,
                fibonacci_confluence INTEGER, volume_confirmation INTEGER,
                days_to_result REAL, result TEXT, r_multiple REAL,
                holding_period REAL
            );
            CREATE INDEX IF NOT EXISTS idx_candidates_run
                ON candidates(run_id, final_decision, timeframe);
            """
        )


def create_run(label: str, params: dict | None = None, status: str = "imported",
               db_path: Path | None = None) -> int:
    init_db(db_path)
    with _conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO research_runs (label, created_at, status, params_json)"
            " VALUES (?, ?, ?, ?)",
            (label, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             status, json.dumps(params or {})),
        )
        return int(cur.lastrowid)


def delete_run(label: str, db_path: Path | None = None) -> None:
    init_db(db_path)
    with _conn(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM research_runs WHERE label = ?", (label,))


def update_run(run_id: int, *, headline: dict | None = None,
               artifacts: dict | None = None, warnings: list[str] | None = None,
               status: str | None = None, db_path: Path | None = None) -> None:
    with _conn(db_path) as conn:
        if headline is not None:
            conn.execute("UPDATE research_runs SET headline_json = ? WHERE id = ?",
                         (json.dumps(headline), run_id))
        if artifacts is not None:
            conn.execute("UPDATE research_runs SET artifacts_json = ? WHERE id = ?",
                         (json.dumps(artifacts), run_id))
        if warnings is not None:
            conn.execute("UPDATE research_runs SET warnings_json = ? WHERE id = ?",
                         (json.dumps(warnings), run_id))
        if status is not None:
            conn.execute("UPDATE research_runs SET status = ? WHERE id = ?",
                         (status, run_id))


def get_runs(db_path: Path | None = None) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM research_runs ORDER BY id DESC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ("params_json", "headline_json", "artifacts_json", "warnings_json"):
            try:
                d[key.replace("_json", "")] = json.loads(d.pop(key))
            except (TypeError, ValueError):
                d[key.replace("_json", "")] = {}
        out.append(d)
    return out


def latest_run(db_path: Path | None = None) -> dict | None:
    runs = get_runs(db_path)
    return runs[0] if runs else None


def save_findings(run_id: int, name: str, df: pd.DataFrame,
                  db_path: Path | None = None) -> None:
    payload = df.where(pd.notna(df), None)
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO findings (run_id, name, columns_json, rows_json)"
            " VALUES (?, ?, ?, ?)",
            (run_id, name, json.dumps(list(payload.columns)),
             json.dumps(payload.to_dict(orient="records"), default=str)),
        )


def get_findings(run_id: int, name: str, db_path: Path | None = None) -> pd.DataFrame | None:
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT columns_json, rows_json FROM findings WHERE run_id = ? AND name = ?",
            (run_id, name)).fetchone()
    if row is None:
        return None
    return pd.DataFrame(json.loads(row["rows_json"]), columns=json.loads(row["columns_json"]))


def list_findings(run_id: int, db_path: Path | None = None) -> list[str]:
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM findings WHERE run_id = ? ORDER BY name", (run_id,)).fetchall()
    return [r["name"] for r in rows]


def save_candidates(run_id: int, df: pd.DataFrame, db_path: Path | None = None) -> int:
    keep = df.copy()
    for col in _CANDIDATE_COLUMNS:
        if col not in keep.columns:
            keep[col] = None
    keep = keep[_CANDIDATE_COLUMNS]
    keep["signal_date"] = keep["signal_date"].astype(str)
    for col in ("ema20_confluence", "fibonacci_confluence", "volume_confirmation"):
        keep[col] = keep[col].fillna(False).astype(bool).astype(int)
    keep.insert(0, "run_id", run_id)
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM candidates WHERE run_id = ?", (run_id,))
        keep.to_sql("candidates", conn, if_exists="append", index=False)
    return len(keep)


def get_candidates(run_id: int, db_path: Path | None = None) -> pd.DataFrame:
    with _conn(db_path) as conn:
        return pd.read_sql_query(
            "SELECT * FROM candidates WHERE run_id = ?", conn, params=(run_id,))


# ------------------------------------------------------- Options Trade Lab
def save_trade_lab_analysis(symbol: str, mode: str, inputs: dict, report: dict,
                            db_path: Path | None = None) -> str:
    from uuid import uuid4
    init_db(db_path)
    analysis_id = uuid4().hex
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO trade_lab_analyses (id, symbol, mode, created_at, "
            "inputs_json, report_json) VALUES (?, ?, ?, ?, ?, ?)",
            (analysis_id, symbol, mode,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             json.dumps(inputs, default=str), json.dumps(report, default=str)))
    return analysis_id


def list_trade_lab_analyses(limit: int = 20, db_path: Path | None = None) -> list[dict]:
    init_db(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT id, symbol, mode, created_at FROM trade_lab_analyses "
            "ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_trade_lab_analysis(analysis_id: str, db_path: Path | None = None) -> dict | None:
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM trade_lab_analyses WHERE id = ?", (analysis_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["inputs"] = json.loads(d.pop("inputs_json") or "{}")
    d["report"] = json.loads(d.pop("report_json") or "{}")
    return d
