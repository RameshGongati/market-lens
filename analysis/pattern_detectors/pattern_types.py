"""Shared chart-pattern family and type labels."""

from __future__ import annotations

ALL_CHART_PATTERNS_FAMILY = "All Chart Patterns"
TRIANGLE_FAMILY = "Triangle Patterns"
VCP_FAMILY = "VCP / Tight Base"
RANGE_FAMILY = "Range Breakouts"
FLAG_FAMILY = "Flag / Pennant"
DOUBLE_FAMILY = "Double Top / Bottom"

ALL_CHART_PATTERN_TYPES_LABEL = "All Chart Pattern Types"

TRIANGLE_TYPES = [
    "All Triangle Patterns",
    "Symmetrical Triangle",
    "Ascending Triangle",
    "Descending Triangle",
]

VCP_TYPE = "VCP / Tight Base"
VCP_TYPES = ["All VCP / Tight Base", VCP_TYPE]

RECTANGLE_RANGE_TYPE = "Rectangle Range"
BULL_RECTANGLE_TYPE = "Bullish Rectangle Breakout"
BEAR_RECTANGLE_TYPE = "Bearish Rectangle Breakdown"
RANGE_TYPES = [
    "All Range Breakouts",
    RECTANGLE_RANGE_TYPE,
    BULL_RECTANGLE_TYPE,
    BEAR_RECTANGLE_TYPE,
]

BULL_FLAG_TYPE = "Bull Flag"
BEAR_FLAG_TYPE = "Bear Flag"
BULL_PENNANT_TYPE = "Bull Pennant"
BEAR_PENNANT_TYPE = "Bear Pennant"
FLAG_TYPES = [
    "All Flag / Pennant",
    BULL_FLAG_TYPE,
    BEAR_FLAG_TYPE,
    BULL_PENNANT_TYPE,
    BEAR_PENNANT_TYPE,
]

DOUBLE_BOTTOM_TYPE = "Double Bottom"
DOUBLE_TOP_TYPE = "Double Top"
DOUBLE_TYPES = ["All Double Top / Bottom", DOUBLE_BOTTOM_TYPE, DOUBLE_TOP_TYPE]

PATTERN_FAMILIES = [
    ALL_CHART_PATTERNS_FAMILY,
    TRIANGLE_FAMILY,
    VCP_FAMILY,
    RANGE_FAMILY,
    FLAG_FAMILY,
    DOUBLE_FAMILY,
]

ALL_CHART_PATTERN_TYPES = [
    ALL_CHART_PATTERN_TYPES_LABEL,
    *TRIANGLE_TYPES[1:],
    VCP_TYPE,
    RECTANGLE_RANGE_TYPE,
    BULL_RECTANGLE_TYPE,
    BEAR_RECTANGLE_TYPE,
    BULL_FLAG_TYPE,
    BEAR_FLAG_TYPE,
    BULL_PENNANT_TYPE,
    BEAR_PENNANT_TYPE,
    DOUBLE_BOTTOM_TYPE,
    DOUBLE_TOP_TYPE,
]

PATTERN_TYPES_BY_FAMILY = {
    ALL_CHART_PATTERNS_FAMILY: ALL_CHART_PATTERN_TYPES,
    TRIANGLE_FAMILY: TRIANGLE_TYPES,
    VCP_FAMILY: VCP_TYPES,
    RANGE_FAMILY: RANGE_TYPES,
    FLAG_FAMILY: FLAG_TYPES,
    DOUBLE_FAMILY: DOUBLE_TYPES,
}

ALL_PATTERN_TYPES = [
    "All Pattern Types",
    *TRIANGLE_TYPES[1:],
    VCP_TYPE,
    RECTANGLE_RANGE_TYPE,
    BULL_RECTANGLE_TYPE,
    BEAR_RECTANGLE_TYPE,
    BULL_FLAG_TYPE,
    BEAR_FLAG_TYPE,
    BULL_PENNANT_TYPE,
    BEAR_PENNANT_TYPE,
    DOUBLE_BOTTOM_TYPE,
    DOUBLE_TOP_TYPE,
]

_ALL_TYPE_LABELS = {
    "All Pattern Types",
    ALL_CHART_PATTERN_TYPES_LABEL,
    "All Triangle Patterns",
    VCP_TYPES[0],
    RANGE_TYPES[0],
    FLAG_TYPES[0],
    DOUBLE_TYPES[0],
}


def normalise_pattern_family(family: str | None) -> str:
    """Map unknown family labels to current Pattern Scanner labels."""
    if family in PATTERN_FAMILIES:
        return str(family)
    return ALL_CHART_PATTERNS_FAMILY


def normalise_pattern_type(pattern_type: str | None) -> str:
    """Map unknown type labels to current Pattern Scanner labels."""
    if pattern_type in ALL_PATTERN_TYPES or pattern_type in _ALL_TYPE_LABELS:
        return str(pattern_type)
    return ALL_CHART_PATTERN_TYPES_LABEL


def pattern_types_for_family(family: str) -> list[str]:
    """Return selectable pattern-type labels for a pattern family."""
    return PATTERN_TYPES_BY_FAMILY.get(
        normalise_pattern_family(family),
        ALL_CHART_PATTERN_TYPES,
    )


def is_all_pattern_type(pattern_type: str) -> bool:
    """Return true when the type label means no exact pattern-type filter."""
    return pattern_type in _ALL_TYPE_LABELS
