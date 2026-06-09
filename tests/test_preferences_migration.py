"""Tests for the Stage F preferences migration (old single-axis -> two-axis).

A pre-refactor ~/.market-lens/user_preferences.json holds an old
``selected_analysis_type`` (or bare ``analysis_type``) and lacks the new
two-axis keys.  ``load_preferences`` must migrate it on the fly: map the old
value to ``trading_type`` + ``primary_strategy`` (+ enhancers), drop the old
key, and never crash on a malformed file.  Migration must be idempotent and
leave an already-new-schema file untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config.preferences as prefs_mod
from config.preferences import _migrate_preferences, load_preferences


@pytest.fixture(autouse=True)
def _isolate_prefs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the preferences module at a temp dir so tests never touch the
    real ~/.market-lens/user_preferences.json."""
    app_dir = tmp_path / ".market-lens"
    monkeypatch.setattr(prefs_mod, "_APP_DIR", app_dir)
    monkeypatch.setattr(prefs_mod, "_PREFS_FILE", app_dir / "user_preferences.json")
    yield


def _write_prefs(payload: dict) -> None:
    prefs_mod._APP_DIR.mkdir(parents=True, exist_ok=True)
    prefs_mod._PREFS_FILE.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Old value -> two-axis mapping (via load_preferences end-to-end)
# ---------------------------------------------------------------------------

def test_migrate_long_term_investment() -> None:
    _write_prefs({"selected_analysis_type": "Long Term Investment"})
    prefs = load_preferences()
    assert prefs["trading_type"] == "Long-term Investment"
    assert prefs["primary_strategy"] == "Trend Following (SMA50/EMA20)"
    assert "selected_analysis_type" not in prefs
    assert "analysis_type" not in prefs


def test_migrate_demand_supply_zones() -> None:
    _write_prefs({"selected_analysis_type": "Demand/Supply Zones"})
    prefs = load_preferences()
    assert prefs["trading_type"] == "Short-term Trading"
    assert prefs["primary_strategy"] == "Demand/Supply Zones"


def test_migrate_short_term_investment() -> None:
    _write_prefs({"selected_analysis_type": "Short Term Investment"})
    prefs = load_preferences()
    assert prefs["trading_type"] == "Short-term Trading"
    assert prefs["primary_strategy"] == "Demand/Supply Zones"


def test_migrate_intraday_trading() -> None:
    _write_prefs({"selected_analysis_type": "Intraday Trading"})
    prefs = load_preferences()
    assert prefs["trading_type"] == "Intraday Trading"
    assert prefs["primary_strategy"] == "Demand/Supply Zones"


def test_migrate_bare_analysis_type_key() -> None:
    """An even older file may use the bare ``analysis_type`` key."""
    _write_prefs({"analysis_type": "Long Term Investment"})
    prefs = load_preferences()
    assert prefs["trading_type"] == "Long-term Investment"
    assert prefs["primary_strategy"] == "Trend Following (SMA50/EMA20)"
    assert "analysis_type" not in prefs


def test_migrate_unknown_value_falls_back_to_defaults() -> None:
    _write_prefs({"selected_analysis_type": "Some Removed Mode"})
    prefs = load_preferences()
    assert prefs["trading_type"] == "Short-term Trading"
    assert prefs["primary_strategy"] == "Demand/Supply Zones"


def test_migrated_enhancers_match_trading_type_defaults() -> None:
    """Enhancers come from get_defaults(trading_type) for the migrated type."""
    from config.trading_config import get_defaults
    _write_prefs({"selected_analysis_type": "Long Term Investment"})
    prefs = load_preferences()
    assert prefs["enhancers"] == list(get_defaults("Long-term Investment")["enhancers"])


# ---------------------------------------------------------------------------
# Malformed / empty files -> defaults, no crash
# ---------------------------------------------------------------------------

def test_malformed_json_returns_defaults() -> None:
    prefs_mod._APP_DIR.mkdir(parents=True, exist_ok=True)
    prefs_mod._PREFS_FILE.write_text("{not valid json", encoding="utf-8")
    prefs = load_preferences()
    assert prefs["trading_type"] == "Short-term Trading"
    assert prefs["primary_strategy"] == "Demand/Supply Zones"


def test_empty_object_file_gets_defaults() -> None:
    _write_prefs({})
    prefs = load_preferences()
    assert prefs["trading_type"] == "Short-term Trading"
    assert prefs["primary_strategy"] == "Demand/Supply Zones"
    assert prefs["enhancers"]


def test_non_dict_json_returns_defaults() -> None:
    """A JSON file holding a list (not an object) must not crash."""
    prefs_mod._APP_DIR.mkdir(parents=True, exist_ok=True)
    prefs_mod._PREFS_FILE.write_text("[1, 2, 3]", encoding="utf-8")
    prefs = load_preferences()
    assert prefs["trading_type"] == "Short-term Trading"


def test_no_file_returns_defaults() -> None:
    prefs = load_preferences()
    assert prefs["trading_type"] == "Short-term Trading"
    assert "selected_analysis_type" not in prefs


# ---------------------------------------------------------------------------
# Idempotency + new-schema-untouched
# ---------------------------------------------------------------------------

def test_migration_is_idempotent() -> None:
    raw = {"selected_analysis_type": "Long Term Investment"}
    once = _migrate_preferences(dict(raw))
    twice = _migrate_preferences(dict(once))
    assert once == twice
    assert twice["trading_type"] == "Long-term Investment"
    assert "selected_analysis_type" not in twice


def test_new_schema_file_left_unchanged() -> None:
    """A file already on the new schema must keep the user's exact choices."""
    payload = {
        "trading_type": "Intraday Trading",
        "primary_strategy": "Trend Following (SMA50/EMA20)",
        "enhancers": ["RSI"],
        "selected_data_source": "Yahoo Finance",
    }
    _write_prefs(payload)
    prefs = load_preferences()
    assert prefs["trading_type"] == "Intraday Trading"
    assert prefs["primary_strategy"] == "Trend Following (SMA50/EMA20)"
    assert prefs["enhancers"] == ["RSI"]


def test_new_schema_with_lingering_legacy_key_drops_legacy_keeps_new() -> None:
    """If both old and new keys exist, new wins and the old key is removed."""
    payload = {
        "selected_analysis_type": "Intraday Trading",   # lingering legacy
        "trading_type": "Long-term Investment",
        "primary_strategy": "Trend Following (SMA50/EMA20)",
        "enhancers": [],
    }
    migrated = _migrate_preferences(payload)
    assert migrated["trading_type"] == "Long-term Investment"   # new wins
    assert "selected_analysis_type" not in migrated             # legacy dropped


def test_migrate_non_dict_input_returns_defaults() -> None:
    assert _migrate_preferences([]) == prefs_mod._DEFAULTS  # type: ignore[arg-type]
