"""Telegram alert configuration — read/write config/alert_config.json."""

import json
import os
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "alert_config.json")

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "telegram": {
        "bot_token": "",
        "recipients": [],
    },
    "conditions": {
        "stocks_source": "watchlist",
        "custom_stocks": [],
        "proximity_pct": 1.0,
        "min_score": 6,
        "zone_type": "both",
        "cooldown": "once_per_zone_per_day",
        # Gap-Up Continuation alerts (Signals V1.1). Nested keys are NOT
        # back-filled into older config files — readers must default to True
        # when the key is absent (gap_alerts.gap_alerts_enabled does).
        "gap_alerts": True,
    },
    "alert_history": {},
    # Active gap signals the intraday touch pass watches; rebuilt by every
    # EOD pass. [{symbol, signal_date, status, entry_price, stop, target}]
    "gap_registry": [],
}


def load_alert_config() -> dict[str, Any]:
    """Load alert config from disk, creating from defaults if missing."""
    if not os.path.exists(_CONFIG_PATH):
        save_alert_config(_DEFAULTS)
        return dict(_DEFAULTS)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Back-fill any missing top-level keys from defaults so older
        # config files don't break when new fields are added.
        for key, default_val in _DEFAULTS.items():
            cfg.setdefault(key, default_val)
        return cfg
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Corrupt alert config, resetting to defaults: %s", exc)
        save_alert_config(_DEFAULTS)
        return dict(_DEFAULTS)


def save_alert_config(cfg: dict[str, Any]) -> None:
    """Persist alert config to disk (overwrites existing file)."""
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        logger.error("Failed to write alert config: %s", exc)
