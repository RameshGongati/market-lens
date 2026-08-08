"""On-disk footprint of the app's own data directory.

Everything Market Lens writes lives under ``~/.market-lens``: the SQLite
database, the bhavcopy and earnings disk caches, and the logs. The settings
page reports each separately because they answer different questions — the
database is data the user created, the caches are disposable, and the logs
grow without bound.

Sizes are read on demand rather than cached. Walking these directories is a
few hundred stat calls (measured under 15ms for a populated app dir), and a
cached figure on a page whose whole purpose is showing current state would be
wrong the moment anything was cleared from that same page.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

APP_DIR = Path.home() / ".market-lens"

# Caches are safe to delete: every entry can be re-fetched from its source.
_CACHE_DIRS = ("bhavcopy", "earnings")
_LOG_DIR = "logs"


def format_bytes(size: int) -> str:
    """Human-readable size, e.g. ``28.7 MB``."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            precision = 0 if unit == "B" else 1
            return f"{value:.{precision}f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def dir_size(path: Path) -> int:
    """Total bytes of every file under *path*, or 0 when it does not exist."""
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            # A file vanishing mid-walk (a cache write completing) is normal
            # and must not take the settings page down with it.
            continue
    return total


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def storage_stats() -> dict[str, object]:
    """Sizes and last-modified times for the app's data directory.

    Returns keys ``db_bytes``, ``cache_bytes``, ``log_bytes``, ``total_bytes``
    and ``db_modified`` / ``prefs_modified`` (``datetime`` or ``None``).
    """
    db_file = APP_DIR / "market_lens.db"
    db_bytes = db_file.stat().st_size if db_file.exists() else 0
    cache_bytes = sum(dir_size(APP_DIR / name) for name in _CACHE_DIRS)
    log_bytes = dir_size(APP_DIR / _LOG_DIR)
    return {
        "db_bytes": db_bytes,
        "cache_bytes": cache_bytes,
        "log_bytes": log_bytes,
        "total_bytes": db_bytes + cache_bytes + log_bytes,
        "db_modified": _mtime(db_file),
        "prefs_modified": _mtime(APP_DIR / "user_preferences.json"),
    }


def cache_dirs() -> list[Path]:
    """The disposable cache directories, for a clear-cache action."""
    return [APP_DIR / name for name in _CACHE_DIRS]


def clear_caches() -> int:
    """Delete every cached file, returning how many were removed.

    Only files inside the known cache directories are touched, and the
    directories themselves are left in place so the next fetch does not have
    to recreate them.
    """
    removed = 0
    for directory in cache_dirs():
        if not directory.exists():
            continue
        for entry in directory.rglob("*"):
            try:
                if entry.is_file():
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
    return removed
