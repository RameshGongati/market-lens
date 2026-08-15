"""Pattern detector implementations."""

from analysis.pattern_detectors.double_patterns import detect_double_patterns
from analysis.pattern_detectors.flag_pennant import detect_flag_pennant_patterns
from analysis.pattern_detectors.range_breakouts import detect_range_breakout_patterns
from analysis.pattern_detectors.triangles import detect_triangle_patterns
from analysis.pattern_detectors.vcp import detect_vcp_patterns

__all__ = [
    "detect_double_patterns",
    "detect_flag_pennant_patterns",
    "detect_range_breakout_patterns",
    "detect_triangle_patterns",
    "detect_vcp_patterns",
]
