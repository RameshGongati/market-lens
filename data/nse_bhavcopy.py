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
from datetime import date

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
_cache: dict[date, dict[str, dict[str, float]] | None] = {}


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

    A miss is cached as None too: the file genuinely does not exist for
    holidays and for sessions NSE has not published yet, and retrying it on
    every symbol in a watchlist would be slow for no gain.
    """
    if dt in _cache:
        return _cache[dt]

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

    _cache[dt] = parsed
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
