"""Global Cues card: pure interpretation layer.

Every classification quotes a historical hit rate from the 10y
global-influence study; these tests pin the thresholds, the priority order
(VIX panic outranks S&P direction; fresh Asia can veto a stale US close),
and that missing data degrades to nothing instead of guessing.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.preferences import _DEFAULTS  # noqa: E402
from data.global_cues import (  # noqa: E402
    build_cue_report, classify_gap_bias, risk_flags, sector_flags,
)


def _raw(**changes) -> dict:
    values = {name: {"prev_change": chg, "unit": "%"}
              for name, chg in changes.items()}
    return {"ok": True, "as_of": "test", "values": values}


# ---------------------------------------------------------------------------
# Gap bias classification
# ---------------------------------------------------------------------------

def test_vix_panic_outranks_us_direction():
    bias, evidence = classify_gap_bias(sp500=1.4, nikkei_gap=0.9, usvix=25.0)
    assert bias == "bearish"
    assert "80.7%" in evidence


def test_strong_us_close_rules():
    bias, evidence = classify_gap_bias(1.2, None, 2.0)
    assert bias == "bullish" and "86.8%" in evidence
    bias, evidence = classify_gap_bias(-1.3, None, None)
    assert bias == "bearish" and "71.9%" in evidence


def test_fresh_asia_can_veto_stale_us():
    bias, evidence = classify_gap_bias(0.7, -0.5, None)
    assert bias == "mixed" and "63.6%" in evidence
    bias, evidence = classify_gap_bias(0.7, 0.5, None)
    assert bias == "bullish" and "85.8%" in evidence


def test_moderate_and_neutral_states():
    bias, evidence = classify_gap_bias(0.6, None, None)
    assert bias == "bullish" and "81.5%" in evidence
    bias, evidence = classify_gap_bias(-0.6, None, None)
    assert bias == "bearish" and "57.7%" in evidence
    bias, evidence = classify_gap_bias(0.1, 0.1, 1.0)
    assert bias == "mixed" and "55.8%" in evidence
    bias, _ = classify_gap_bias(None, 1.0, None)
    assert bias == "unknown"


# ---------------------------------------------------------------------------
# Sector and risk flags
# ---------------------------------------------------------------------------

def test_sector_flags_fire_only_at_validated_thresholds():
    assert sector_flags(_raw(BRENT=3.5))          # crude spike -> OMC warning
    assert sector_flags(_raw(BRENT=-3.5))         # crude drop -> OMC gap-up
    assert sector_flags(_raw(COPPER=2.4))
    assert sector_flags(_raw(COPPER=-2.4))
    assert sector_flags(_raw(GOLD=2.2))
    assert sector_flags(_raw(NASDAQ100=-1.8))
    assert sector_flags(_raw(NASDAQ100=1.8))
    # Below threshold or absent: silence, never a guess.
    assert sector_flags(_raw(BRENT=2.9, COPPER=1.9, GOLD=1.9,
                             NASDAQ100=1.4)) == []
    assert sector_flags(_raw()) == []


def test_risk_flags_require_the_validated_combination():
    assert risk_flags(_raw(US_VIX=12.0))
    assert risk_flags(_raw(DXY=0.4, US_10Y=4.0))
    assert risk_flags(_raw(DXY=0.4)) == []          # DXY alone: not a flag
    assert risk_flags(_raw(US_10Y=5.0)) == []       # yields alone: not a flag
    assert risk_flags(_raw(US_VIX=6.0)) == []


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def test_report_prefers_asia_opening_print_and_carries_the_caption():
    raw = _raw(SP500=0.8, US_VIX=1.0, BRENT=1.0)
    raw["values"]["NIKKEI"] = {"prev_change": -0.2, "open_gap": 0.6, "unit": "%"}
    report = build_cue_report(raw)
    assert report["ok"] is True
    assert report["bias"] == "bullish" and "85.8%" in report["evidence"]
    nikkei = next(r for r in report["groups"]["asia"] if r["name"] == "NIKKEI")
    assert nikkei["open_gap"] == 0.6
    assert "09:15" in report["caption"]
    assert "not trade advice" in report["caption"]


def test_report_degrades_when_fetch_failed():
    report = build_cue_report({"ok": False, "as_of": "x", "values": {}})
    assert report["ok"] is False
    assert report["sector_flags"] == [] and report["risk_flags"] == []


def test_dashboard_preference_default_is_on():
    assert _DEFAULTS["dashboard_show_global_cues"] is True
