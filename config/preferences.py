"""User preferences — persist sidebar selections across app restarts."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_APP_DIR = Path.home() / ".market-lens"
_PREFS_FILE = _APP_DIR / "user_preferences.json"

_DEFAULTS: dict[str, Any] = {
    "selected_watchlist_id": None,
    "selected_analysis_type": "Demand/Supply Zones",
    "selected_data_source": "Yahoo Finance",
    "alerts_on": False,
    "last_analysis_timestamp": None,
}


def load_preferences() -> dict[str, Any]:
    """Load user preferences from disk, returning defaults for missing keys.

    Returns:
        Merged dict of saved + default preferences.
    """
    _APP_DIR.mkdir(parents=True, exist_ok=True)
    if not _PREFS_FILE.exists():
        return dict(_DEFAULTS)
    try:
        saved = json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
        return {**_DEFAULTS, **saved}
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
