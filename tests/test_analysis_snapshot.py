"""Tests for restoring the latest full analysis scan across sessions."""

from __future__ import annotations

from pathlib import Path

import storage.database as database


def _use_temp_database(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "_APP_DIR", tmp_path)
    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "market_lens.db")
    database.init_db()


def test_latest_analysis_snapshot_round_trips_predefined_watchlist_results(
    monkeypatch, tmp_path: Path,
) -> None:
    _use_temp_database(monkeypatch, tmp_path)
    results = {
        "RELIANCE": {
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "current_price": 1420.5,
            "demand_zones": [{"proximal": 1400.0, "odd_score": 6.5}],
        },
        "INFY": {"symbol": "INFY", "exchange": "NSE", "current_price": 1580.0},
    }
    metadata = {
        "last_scan_label": "Today, 10:15 AM",
        "used_tf_label": "Daily",
        "fallback_symbols": ["INFY"],
    }

    database.save_latest_analysis_snapshot(results, metadata)

    restored = database.load_latest_analysis_snapshot()
    assert restored is not None
    assert restored["results"] == results
    assert restored["metadata"] == metadata


def test_latest_analysis_snapshot_replaces_the_previous_scan(monkeypatch, tmp_path: Path) -> None:
    _use_temp_database(monkeypatch, tmp_path)
    database.save_latest_analysis_snapshot({"OLD": {"current_price": 10}}, {"scan": 1})
    database.save_latest_analysis_snapshot({"NEW": {"current_price": 20}}, {"scan": 2})

    restored = database.load_latest_analysis_snapshot()
    assert restored is not None
    assert restored["results"] == {"NEW": {"current_price": 20}}
    assert restored["metadata"] == {"scan": 2}
