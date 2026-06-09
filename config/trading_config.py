"""Two-axis trading-type configuration for Market Lens.

This module defines the *vocabulary* for the new two-axis analysis model:

  Axis 1 — TRADING TYPE (time horizon)
      Drives which timeframe data to fetch and which defaults are
      pre-selected. One of: Options Trading, Intraday Trading,
      Short-term Trading, Long-term Investment.

  Axis 2 — STRATEGY (what to do with that data)
      * PRIMARY STRATEGY — the base analytical method (pick one):
          Demand/Supply Zones | Trend Following (SMA50/EMA20)
      * ENHANCERS — optional layers on top (multi-select):
          Fibonacci Confluence | EMA 20 Confluence | RSI

This module defines the constants and helper functions for the two-axis
model, which (as of the Stage F migration) is the application's only
analysis model — the legacy ``config.settings.ANALYSIS_TYPES`` constant and
its single "Analysis Type" dropdown have been removed.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Axis 1 — Trading types (time horizons)
# ---------------------------------------------------------------------------

TRADING_TYPES: list[str] = [
    "Options Trading",
    "Intraday Trading",
    "Short-term Trading",
    "Long-term Investment",
]

# ---------------------------------------------------------------------------
# Axis 2 — Strategies
# ---------------------------------------------------------------------------

PRIMARY_STRATEGIES: list[str] = [
    "Demand/Supply Zones",
    "Trend Following (SMA50/EMA20)",
]

ENHANCERS: list[str] = [
    "Fibonacci Confluence",
    "EMA 20 Confluence",
    "RSI",
]

# ---------------------------------------------------------------------------
# Timeframe mapping
# ---------------------------------------------------------------------------

# The HTF (higher timeframe) used when fetching OHLCV data for each trading
# type.  ITF / LTF slots are reserved for future multi-timeframe work but
# are not populated here — only HTF is used in the current implementation.
TRADING_TYPE_TIMEFRAME: dict[str, dict[str, str]] = {
    "Options Trading":      {"period": "1y",   "interval": "1d"},
    "Intraday Trading":     {"period": "60d",  "interval": "15m"},
    "Short-term Trading":   {"period": "1y",   "interval": "1d"},
    "Long-term Investment": {"period": "5y",   "interval": "1wk"},
}

# ---------------------------------------------------------------------------
# Default selections per trading type
# ---------------------------------------------------------------------------

# Which primary strategy is pre-selected and which enhancers are pre-ticked
# when a user first picks a given trading type.  These are *defaults*, not
# hard constraints — the user can always change them.
TRADING_TYPE_DEFAULTS: dict[str, dict[str, object]] = {
    "Options Trading": {
        "primary":   "Demand/Supply Zones",
        "enhancers": ["Fibonacci Confluence"],
    },
    "Intraday Trading": {
        "primary":   "Demand/Supply Zones",
        "enhancers": ["EMA 20 Confluence"],
    },
    "Short-term Trading": {
        "primary":   "Demand/Supply Zones",
        "enhancers": ["Fibonacci Confluence", "EMA 20 Confluence"],
    },
    "Long-term Investment": {
        "primary":   "Trend Following (SMA50/EMA20)",
        "enhancers": [],
    },
}

# ---------------------------------------------------------------------------
# Available primary strategies per trading type
# ---------------------------------------------------------------------------

# Demand/Supply Zones is available for every trading type but it is *not*
# the default for Long-term Investment (where Trend Following is the designed
# starting point).  Ordering matters: the first entry in each list is the
# one that appears selected in the UI when no user preference has been saved.
PRIMARY_AVAILABLE: dict[str, list[str]] = {
    "Options Trading": [
        "Demand/Supply Zones",
        "Trend Following (SMA50/EMA20)",
    ],
    "Intraday Trading": [
        "Demand/Supply Zones",
        "Trend Following (SMA50/EMA20)",
    ],
    "Short-term Trading": [
        "Demand/Supply Zones",
        "Trend Following (SMA50/EMA20)",
    ],
    "Long-term Investment": [
        "Trend Following (SMA50/EMA20)",
        "Demand/Supply Zones",
    ],
}

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def get_timeframe(trading_type: str) -> dict[str, str]:
    """Return the HTF timeframe spec (``period`` / ``interval``) for
    *trading_type*.

    Args:
        trading_type: One of the strings in :data:`TRADING_TYPES`.

    Returns:
        ``{"period": "...", "interval": "..."}`` dict for use with the data
        source manager, or the Options Trading default if the type is unknown.

    Example::

        >>> get_timeframe("Intraday Trading")
        {'period': '60d', 'interval': '15m'}
    """
    return TRADING_TYPE_TIMEFRAME.get(
        trading_type,
        TRADING_TYPE_TIMEFRAME["Options Trading"],
    )


def get_defaults(trading_type: str) -> dict[str, object]:
    """Return the default ``{"primary": str, "enhancers": list[str]}`` dict
    for *trading_type*.

    Args:
        trading_type: One of the strings in :data:`TRADING_TYPES`.

    Returns:
        Defaults dict; falls back to the Options Trading defaults if the
        type is unknown so callers never receive ``None``.

    Example::

        >>> get_defaults("Long-term Investment")
        {'primary': 'Trend Following (SMA50/EMA20)', 'enhancers': []}
    """
    return TRADING_TYPE_DEFAULTS.get(
        trading_type,
        TRADING_TYPE_DEFAULTS["Options Trading"],
    )


def get_available_primaries(trading_type: str) -> list[str]:
    """Return the ordered list of primary strategies available for
    *trading_type*.

    The first entry is the one shown as the default selection in the UI when
    no user preference has been saved (see :data:`PRIMARY_AVAILABLE`).

    Args:
        trading_type: One of the strings in :data:`TRADING_TYPES`.

    Returns:
        Non-empty list of strategy names; falls back to all
        :data:`PRIMARY_STRATEGIES` if the type is unknown.

    Example::

        >>> get_available_primaries("Long-term Investment")
        ['Trend Following (SMA50/EMA20)', 'Demand/Supply Zones']
    """
    return PRIMARY_AVAILABLE.get(trading_type, list(PRIMARY_STRATEGIES))


def is_valid_combination(
    trading_type: str,
    primary: str,
    enhancers: list[str],
) -> tuple[bool, str]:
    """Validate a (trading_type, primary, enhancers) combination.

    Checks:
    1. *trading_type* is one of :data:`TRADING_TYPES`.
    2. *primary* is in :func:`get_available_primaries` for that type.
    3. Every entry in *enhancers* is one of :data:`ENHANCERS`.

    This function is intentionally conservative — it only rejects
    combinations that are provably wrong (unknown enum values). It does
    *not* enforce the suggested defaults; those are UI hints, not hard
    constraints.

    Args:
        trading_type: Candidate trading type string.
        primary: Candidate primary strategy string.
        enhancers: Candidate list of enhancer strings.

    Returns:
        ``(True, "")`` when the combination is valid, or
        ``(False, "<reason>")`` explaining the first violation found.

    Example::

        >>> is_valid_combination(
        ...     "Options Trading", "Demand/Supply Zones",
        ...     ["Fibonacci Confluence"],
        ... )
        (True, '')
        >>> is_valid_combination("Unknown Type", "Demand/Supply Zones", [])
        (False, "Unknown trading type: 'Unknown Type'")
    """
    if trading_type not in TRADING_TYPES:
        return False, f"Unknown trading type: {trading_type!r}"

    available = get_available_primaries(trading_type)
    if primary not in available:
        return False, (
            f"Primary strategy {primary!r} is not available for "
            f"trading type {trading_type!r}. "
            f"Available: {available}"
        )

    unknown = [e for e in enhancers if e not in ENHANCERS]
    if unknown:
        return False, f"Unknown enhancer(s): {unknown}. Known: {ENHANCERS}"

    return True, ""
