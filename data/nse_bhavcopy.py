"""NSE end-of-day bhavcopy — the authoritative close for a single session.

Both working sources can fail to deliver the most recent daily bar:

  * Yahoo sometimes finalises a bar with open, high, low and volume but a
    MISSING close (BAJAJHLDNG on 2026-07-31).
  * ``jugaad-data``'s per-symbol historical endpoint can return a malformed
    response for a symbol and simply omit recent sessions, or fail outright.

The bhavcopy is a different NSE endpoint: one static file per trading day
covering the whole market, published after the close. It does not share the
per-symbol failure mode, and it is the file the other sources are ultimately
derived from — so it settles disagreements rather than adding a third guess.

Used only to REPAIR a bar the primary source returned incomplete; it is not
a data source in its own right and never replaces a complete bar.
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import date
from pathlib import Path

from utils.logger import get_logger

logger = get_logger(__name__)

# NSE changed the bhavcopy schema: the modern file uses TckrSymb/OpnPric/…
# while older archives use SYMBOL/OPEN/…. Both are read so that a request for
# an older session does not silently find nothing — the failure mode of using
# the wrong names is an empty lookup, not an error.
_SCHEMAS: tuple[dict[str, str], ...] = (
    {
        "symbol": "TckrSymb", "series": "SctySrs",
        "open": "OpnPric", "high": "HghPric",
        "low": "LwPric", "close": "ClsPric",
    },
    {
        "symbol": "SYMBOL", "series": "SERIES",
        "open": "OPEN", "high": "HIGH",
        "low": "LOW", "close": "CLOSE",
    },
)

# One trading day's file is ~3,400 rows and identical for every symbol, so a
# session that repairs several stocks downloads it once. Keyed by date.
_cache: dict[date, dict[str, dict[str, float]]] = {}

# When a date's download fails, note when, so the next symbol does not retry
# immediately but the session is not written off either.
_failed_at: dict[date, float] = {}
_RETRY_AFTER_SEC = 120

# Downloading the file costs ~5s; parsing a local copy costs ~50ms. Without a
# disk copy that 5s is paid again on the FIRST chart opened after every app
# restart, which is what made the detail view feel slow. A session's prices
# never change once published, so the file is written next to the app's other
# state and re-read on later runs.
_DISK_CACHE = Path.home() / ".market-lens" / "bhavcopy"


def _disk_path(dt: date) -> Path:
    return _DISK_CACHE / f"{dt.isoformat()}.json"


def _read_disk(dt: date) -> dict[str, dict[str, float]] | None:
    """Return a previously saved session, or None if absent or unreadable."""
    path = _disk_path(dt)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        return data or None
    except Exception as exc:  # noqa: BLE001 — a bad cache file must not break a fetch
        logger.warning("Discarding unreadable bhavcopy cache %s: %s", path.name, exc)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def _write_disk(dt: date, table: dict[str, dict[str, float]]) -> None:
    """Save a parsed session. Failure to cache is never fatal."""
    try:
        _DISK_CACHE.mkdir(parents=True, exist_ok=True)
        # Write then rename so a crash mid-write cannot leave a half file that
        # would be read back as corrupt on the next run.
        tmp = _disk_path(dt).with_suffix(".tmp")
        tmp.write_text(json.dumps(table))
        tmp.replace(_disk_path(dt))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not cache bhavcopy for %s: %s", dt, exc)


def _parse(raw: str) -> dict[str, dict[str, float]]:
    """Parse bhavcopy CSV text into ``{symbol: {open, high, low, close}}``.

    Only the EQ series is kept — the file also carries BE, BZ and other
    series whose prices would otherwise collide on the same ticker.
    """
    rows = list(csv.DictReader(io.StringIO(raw)))
    if not rows:
        return {}

    header = set(rows[0].keys())
    schema = next((s for s in _SCHEMAS if s["symbol"] in header), None)
    if schema is None:
        logger.warning("Unrecognised bhavcopy schema: %s", sorted(header)[:8])
        return {}

    out: dict[str, dict[str, float]] = {}
    for row in rows:
        if (row.get(schema["series"]) or "").strip().upper() != "EQ":
            continue
        symbol = (row.get(schema["symbol"]) or "").strip().upper()
        if not symbol:
            continue
        try:
            out[symbol] = {
                "open": float(row[schema["open"]]),
                "high": float(row[schema["high"]]),
                "low": float(row[schema["low"]]),
                "close": float(row[schema["close"]]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _load(dt: date) -> dict[str, dict[str, float]] | None:
    """Download and parse one session's bhavcopy, or return None.

    A SUCCESS is cached for the life of the process — the file never changes
    once published, and a watchlist run would otherwise re-download it per
    symbol.

    A FAILURE is retried, at most once per :data:`_RETRY_AFTER_SEC`. NSE
    occasionally serves a truncated or rate-limited response, and caching
    that permanently was observed to strip the latest bar from every chart
    for a whole session after a single blip. Rate-limiting the retry keeps a
    genuine outage (a holiday, or a session not yet published) from issuing
    one request per symbol.
    """
    cached = _cache.get(dt)
    if cached:
        return cached

    from_disk = _read_disk(dt)
    if from_disk:
        _cache[dt] = from_disk
        logger.info(
            "Bhavcopy for %s read from local cache (%d EQ symbols)", dt, len(from_disk)
        )
        return from_disk

    last_try = _failed_at.get(dt)
    if last_try is not None and (time.monotonic() - last_try) < _RETRY_AFTER_SEC:
        return None

    parsed: dict[str, dict[str, float]] | None = None
    try:
        from jugaad_data.nse import bhavcopy_raw

        raw = bhavcopy_raw(dt)
        parsed = _parse(raw) or None
        if parsed:
            logger.info("Bhavcopy loaded for %s (%d EQ symbols)", dt, len(parsed))
        else:
            logger.warning("Bhavcopy for %s parsed to no usable rows", dt)
    except Exception as exc:  # noqa: BLE001 — never let a repair break a fetch
        logger.warning("Bhavcopy unavailable for %s: %s", dt, exc)

    if parsed:
        _cache[dt] = parsed
        _failed_at.pop(dt, None)
        _write_disk(dt, parsed)
    else:
        _failed_at[dt] = time.monotonic()
    return parsed


def fetch_eod_ohlc(symbol: str, dt: date) -> dict[str, float] | None:
    """Return ``{open, high, low, close}`` for *symbol* on *dt*, or None.

    Args:
        symbol: NSE ticker, with or without a ``.NS`` suffix.
        dt: The trading date to look up.

    Returns:
        The session's OHLC, or None when the file is unavailable, the date
        was not a trading day, or the symbol is not listed in it.
    """
    table = _load(dt)
    if not table:
        return None
    clean = symbol.upper().replace(".NS", "").replace(".BO", "").strip()
    return table.get(clean)
