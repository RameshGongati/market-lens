"""User preferences — persist sidebar selections across app restarts."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from config.trading_config import get_defaults

_APP_DIR = Path.home() / ".market-lens"
_PREFS_FILE = _APP_DIR / "user_preferences.json"

# The two-axis keys that define a "new schema" preferences file.  When all of
# these are present, no migration from the old single "analysis type" model is
# needed.
_NEW_SCHEMA_KEYS: tuple[str, ...] = ("trading_type", "primary_strategy", "enhancers")

# The legacy single-axis preference key(s) that Stage F migrates away from.
# The running app used ``selected_analysis_type``; ``analysis_type`` is also
# accepted in case an even older file used the bare name.
_LEGACY_KEYS: tuple[str, ...] = ("selected_analysis_type", "analysis_type")

# Map each old "analysis type" value to its (trading_type, primary_strategy)
# in the two-axis model.  Enhancers are derived from the trading type via
# ``config.trading_config.get_defaults`` so they stay consistent with the
# sidebar's own per-trading-type defaults.
_ANALYSIS_TYPE_MIGRATION: dict[str, tuple[str, str]] = {
    "Demand/Supply Zones":   ("Short-term Trading",   "Demand/Supply Zones"),
    "Long Term Investment":  ("Long-term Investment", "Trend Following (SMA50/EMA20)"),
    "Short Term Investment": ("Short-term Trading",   "Demand/Supply Zones"),
    "Intraday Trading":      ("Intraday Trading",     "Demand/Supply Zones"),
}

# Fallback target when the old value is unknown / missing / malformed.
_MIGRATION_FALLBACK: tuple[str, str] = ("Short-term Trading", "Demand/Supply Zones")

_DEFAULTS: dict[str, Any] = {
    "selected_watchlist_id": None,
    "selected_data_source": "Yahoo Finance",
    "alerts_on": False,
    "last_analysis_timestamp": None,
    # Two-axis trading-type model (the only analysis model after Stage F).
    "trading_type": "Options Trading",
    "primary_strategy": "Demand/Supply Zones",
    "enhancers": ["Fibonacci Confluence", "EMA 20 Confluence"],
    # Chart display preferences.
    "show_candle_tooltip": True,
}


def _migrate_preferences(saved: dict[str, Any]) -> dict[str, Any]:
    """Migrate an old single-axis preferences dict to the two-axis model.

    Behaviour:
      * If the new two-axis keys are already present, the file is left as-is
        apart from dropping any lingering legacy key (so re-running is a no-op
        — the migration is idempotent).
      * Otherwise the old ``selected_analysis_type`` / ``analysis_type`` value
        is mapped to ``trading_type`` + ``primary_strategy`` (see
        :data:`_ANALYSIS_TYPE_MIGRATION`) with enhancers taken from
        ``get_defaults(trading_type)``.  An unknown/missing/malformed value
        falls back to Short-term Trading + Demand/Supply Zones.
      * The legacy key is always removed so it doesn't linger.

    The whole body is defensive: any unexpected structure returns a fresh copy
    of the defaults rather than raising, so a corrupt file can never crash the
    load path.

    Args:
        saved: The raw dict parsed from the preferences file.

    Returns:
        A migrated copy safe to merge over :data:`_DEFAULTS`.
    """
    try:
        if not isinstance(saved, dict):
            return dict(_DEFAULTS)

        migrated = dict(saved)

        # Pull (and remove) any legacy key value, regardless of schema, so it
        # never lingers in the saved file after the next save.
        legacy_value: Any = None
        for key in _LEGACY_KEYS:
            if key in migrated:
                legacy_value = migrated.pop(key)

        # Already on the new schema → keep the user's choices untouched.
        if all(k in migrated for k in _NEW_SCHEMA_KEYS):
            return migrated

        # Derive the two-axis selection from the old value (or the fallback).
        trading_type, primary = _ANALYSIS_TYPE_MIGRATION.get(
            legacy_value if isinstance(legacy_value, str) else "",
            _MIGRATION_FALLBACK,
        )
        migrated.setdefault("trading_type", trading_type)
        migrated.setdefault("primary_strategy", primary)
        migrated.setdefault(
            "enhancers", list(get_defaults(trading_type)["enhancers"])
        )
        return migrated
    except Exception:
        return dict(_DEFAULTS)


def load_preferences() -> dict[str, Any]:
    """Load user preferences from disk, returning defaults for missing keys.

    Old single-axis preference files are migrated to the two-axis model on the
    fly (see :func:`_migrate_preferences`); the migration is idempotent and
    never raises on a malformed file.

    Returns:
        Merged dict of (migrated) saved values over the defaults.
    """
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    if not _PREFS_FILE.exists():
        return dict(_DEFAULTS)
    try:
        saved = json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
        migrated = _migrate_preferences(saved)
        return {**_DEFAULTS, **migrated}
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULTS)


def save_preferences(prefs: dict[str, Any]) -> None:
    """Persist user preferences to disk.

    Args:
        prefs: Dict of preference keys to save.
    """
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    merged = {**load_preferences(), **prefs}
    _PREFS_FILE.write_text(
        json.dumps(merged, indent=2, default=str), encoding="utf-8"
    )


def update_last_analysis_timestamp() -> None:
    """Record the current UTC time as the last analysis run timestamp."""
    save_preferences({"last_analysis_timestamp": datetime.utcnow().isoformat()})


def reset_preferences() -> None:
    """Reset all preferences to their default values."""
    _PREFS_FILE.write_text(
        json.dumps(_DEFAULTS, indent=2), encoding="utf-8"
    )
