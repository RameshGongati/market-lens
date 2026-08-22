"""Research Engine: isolation guards, store round-trip, importer, page helpers.

The isolation tests are the contract: importing the Research page (or any
non-harness research module) must leave the production zone engine untouched.
The backtest-only survivorship patch exists solely behind
enable_backtest_mode(), which only harness runners call.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- #
# Isolation: page/store/importer imports never patch the zone engine
# --------------------------------------------------------------------------- #

def _zone_engine_identity():
    from analysis.zone_engine import patterns, scoring
    return patterns.score_zone, scoring.score_zone


def test_research_page_import_does_not_patch_zone_engine():
    from analysis.zone_engine import patterns, scoring
    before = patterns.score_zone
    assert before is scoring.score_zone, "engine must start unpatched"
    importlib.import_module("ui.pages.research_page")
    importlib.import_module("research_engine.store")
    importlib.import_module("research_engine.importer")
    after, orig = _zone_engine_identity()
    assert after is orig, "importing research UI/store/importer patched score_zone"


def test_research_page_module_does_not_import_harness():
    page = importlib.import_module("ui.pages.research_page")
    assert not any(m.startswith("research_engine.harness") for m in sys.modules
                   if sys.modules[m] is not None and m in sys.modules), \
        "research page pulled in harness modules"
    # and the page module itself has no harness attribute chain
    assert "harness" not in vars(page)


def test_zone_engine_still_discards_invalidated_zones():
    """Functional check: a zone whose distal is later breached must be gone."""
    from analysis.zone_engine.patterns import detect_zones

    rows = [
        (100, 101, 99, 100.5),      # filler
        (100.5, 101, 99.5, 100),
        (100, 100.5, 99, 99.2),     # legin: bearish exciting
        (99.2, 99.6, 98.9, 99.3),   # base: boring
        (99.3, 102.5, 99.2, 102.3),  # legout: bullish exciting -> demand zone
        (102.3, 103, 101.8, 102.8),
        (102.8, 103.2, 90.0, 91.0),  # CRASH through the distal -> invalidated
        (91, 92, 90, 91.5),
    ]
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"])
    zones = detect_zones(df)
    assert all(z.distal > 90.0 or z.category != "demand" for z in zones) or not zones, \
        "an invalidated demand zone survived in the LIVE engine"


def test_backtest_mode_is_explicit_and_reversible():
    from analysis.zone_engine import patterns
    from research_engine.harness import detectors

    orig = patterns.score_zone
    try:
        # importing the harness module alone must not patch
        assert patterns.score_zone is orig
        # zone detectors refuse to run unpatched
        df = pd.DataFrame({"Open": [1.0] * 90, "High": [1.0] * 90,
                           "Low": [1.0] * 90, "Close": [1.0] * 90})
        with pytest.raises(RuntimeError, match="enable_backtest_mode"):
            detectors.zone_signals(df)
        detectors.enable_backtest_mode()
        assert patterns.score_zone is not orig, "enable_backtest_mode did not patch"
    finally:
        detectors.disable_backtest_mode()
    assert patterns.score_zone is orig, "disable_backtest_mode did not restore"


# --------------------------------------------------------------------------- #
# Store round-trip
# --------------------------------------------------------------------------- #

def test_store_round_trip(tmp_path):
    from research_engine import store

    db = tmp_path / "research_engine.db"
    run_id = store.create_run("test run", params={"a": 1}, db_path=db)
    df = pd.DataFrame({"stage": ["1 base", "7 engine"], "expectancy_r": [-0.15, 0.09]})
    store.save_findings(run_id, "engine_ladder", df, db_path=db)
    got = store.get_findings(run_id, "engine_ladder", db_path=db)
    assert list(got.columns) == ["stage", "expectancy_r"]
    assert len(got) == 2 and got.iloc[1]["expectancy_r"] == 0.09

    cands = pd.DataFrame({
        "symbol": ["RELIANCE", "TCS"], "sector": ["Energy", "IT"],
        "timeframe": ["daily", "60m"], "signal_date": ["2026-01-05", "2026-01-06"],
        "setup_name": ["gap_up_go", "zone_touch_fresh"],
        "bullish_or_bearish": ["bullish", "bullish"],
        "final_decision": ["TAKE", "WAIT"],
        "entry_price": [1300.0, 4100.0], "stop_loss": [1280.0, 4050.0],
        "target_1": [1320.0, 4150.0], "target_2": [1340.0, 4200.0],
        "target_3": [1360.0, 4250.0], "rr_to_opposing": [2.5, 1.2],
        "trap_probability_score": [0.0, 25.0], "trap_reasons": ["", "T9"],
        "final_confidence_score": [71.0, 55.0],
        "sma50_trend": ["above", "above"], "ema20_confluence": [True, False],
        "fibonacci_confluence": [False, True], "volume_confirmation": [True, None],
        "days_to_result": [None, 1], "result": ["win", "loss"],
        "r_multiple": [1.98, -1.05], "holding_period": [4, 9],
    })
    n = store.save_candidates(run_id, cands, db_path=db)
    assert n == 2
    got = store.get_candidates(run_id, db_path=db)
    assert len(got) == 2 and set(got["final_decision"]) == {"TAKE", "WAIT"}

    store.update_run(run_id, headline={"engine_expectancy_r": 0.087},
                     warnings=["w1"], db_path=db)
    runs = store.get_runs(db_path=db)
    assert runs[0]["headline"]["engine_expectancy_r"] == 0.087
    assert runs[0]["warnings"] == ["w1"]

    store.delete_run("test run", db_path=db)
    assert store.get_runs(db_path=db) == []


def test_store_uses_separate_database_path():
    from research_engine import store
    assert store.DB_PATH.name == "research_engine.db"
    assert store.DB_PATH.name != "market_lens.db"


# --------------------------------------------------------------------------- #
# Importer: partial sources degrade to warnings, never raise
# --------------------------------------------------------------------------- #

def _write_minimal_outputs(src: Path) -> None:
    src.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({
        "ladder": ["recommended_setups", "recommended_setups"],
        "stage": ["1 base setups alone", "7 full engine decision"],
        "n": [1000, 100], "pct_filtered_out": [0.0, 90.0],
        "expectancy_r": [-0.15, 0.09], "profit_factor": [0.8, 1.15],
    }).to_csv(src / "engine_ladder.csv", index=False)
    pd.DataFrame({
        "final_decision": ["TAKE", "NO TRADE"], "n": [100, 900],
        "expectancy": [0.09, -0.2], "win2r": [0.3, 0.2], "stop": [0.5, 0.6],
    }).to_csv(src / "decision_validation.csv", index=False)
    (src / "trap_weights.json").write_text(json.dumps(
        {"weights_pp": {"T4": 0.029}, "score_points": {"T4": 46.8},
         "base_stop_rate": 0.62}))
    pd.DataFrame({
        "symbol": ["RELIANCE"], "sector": ["Energy"], "timeframe": ["daily"],
        "signal_date": ["2026-01-05"], "setup_name": ["gap_up_go"],
        "bullish_or_bearish": ["bullish"], "final_decision": ["TAKE"],
        "entry_price": [1300.0], "stop_loss": [1280.0], "target_1": [1320.0],
        "target_2": [1340.0], "target_3": [1360.0], "rr_to_opposing": [2.5],
        "trap_probability_score": [0.0], "trap_reasons": [""],
        "final_confidence_score": [70.0], "sma50_trend": ["above"],
        "ema20_confluence": [True], "fibonacci_confluence": [False],
        "volume_confirmation": [True], "days_to_result": [None],
        "result": ["win"], "r_multiple": [1.98], "holding_period": [4],
    }).to_csv(src / "candidates_take_only.csv", index=False)


def test_importer_partial_sources(tmp_path):
    from research_engine import importer

    src = tmp_path / "output"
    _write_minimal_outputs(src)
    db = tmp_path / "re.db"
    result = importer.import_run(source_dir=src, db_path=db,
                                 charts_dir=tmp_path / "charts")
    assert result["n_candidates"] == 1
    assert "engine_ladder" in result["imported"]
    assert "trap_weights" in result["imported"]
    # everything not provided is a warning, not an exception
    assert any("setup_rankings" in w for w in result["warnings"])
    assert any("charts" in w for w in result["warnings"])

    from research_engine import store
    run = store.latest_run(db_path=db)
    assert run["label"] == importer.RUN1_LABEL
    assert run["headline"]["engine_expectancy_r"] == 0.09
    assert run["headline"]["take_n"] == 100


def test_importer_reimport_replaces_run(tmp_path):
    from research_engine import importer, store

    src = tmp_path / "output"
    _write_minimal_outputs(src)
    db = tmp_path / "re.db"
    importer.import_run(source_dir=src, db_path=db, charts_dir=tmp_path / "c1")
    importer.import_run(source_dir=src, db_path=db, charts_dir=tmp_path / "c2")
    runs = store.get_runs(db_path=db)
    assert len(runs) == 1, "re-import must replace the same-label run, not duplicate"


def test_ensure_run1_noop_when_run_exists(tmp_path):
    from research_engine import importer, store

    db = tmp_path / "re.db"
    store.create_run("existing", db_path=db)
    assert importer.ensure_run1(db_path=db) is None


def test_importer_empty_source_dir_returns_warnings(tmp_path):
    from research_engine import importer

    result = importer.import_run(source_dir=tmp_path / "nothing_here",
                                 db_path=tmp_path / "re.db",
                                 charts_dir=tmp_path / "charts")
    assert result["n_candidates"] == 0
    assert len(result["warnings"]) >= len(importer._FINDINGS_FILES)


# --------------------------------------------------------------------------- #
# Page pure helpers
# --------------------------------------------------------------------------- #

def _candidates_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": ["RELIANCE", "TCS", "SBIN", "ONGC"],
        "timeframe": ["daily", "60m", "daily", "weekly"],
        "setup_name": ["gap_up_go", "zone_touch_fresh", "demand_bounce", "gap_up_go"],
        "bullish_or_bearish": ["bullish", "bullish", "bullish", "bearish"],
        "final_decision": ["TAKE", "WAIT", "TAKE", "WATCH"],
        "final_confidence_score": [70, 55, 65, 50],
        "r_multiple": [1.9, -1.0, 0.4, -0.2],
    })


def test_filter_candidates_by_decision_and_tf():
    from ui.pages.research_page import filter_candidates

    df = _candidates_frame()
    out = filter_candidates(df, ["TAKE"], ["daily"], "both", [], "")
    assert set(out["symbol"]) == {"RELIANCE", "SBIN"}


def test_filter_candidates_direction_and_symbol_query():
    from ui.pages.research_page import filter_candidates

    df = _candidates_frame()
    out = filter_candidates(df, [], [], "bearish", [], "")
    assert set(out["symbol"]) == {"ONGC"}
    out = filter_candidates(df, [], [], "both", [], "reli")
    assert set(out["symbol"]) == {"RELIANCE"}


def test_filter_candidates_setups():
    from ui.pages.research_page import filter_candidates

    df = _candidates_frame()
    out = filter_candidates(df, [], [], "both", ["gap_up_go"], "")
    assert set(out["symbol"]) == {"RELIANCE", "ONGC"}


def test_headline_bits_formats_available_metrics_only():
    from ui.pages.research_page import headline_bits

    bits = headline_bits({"headline": {"engine_expectancy_r": 0.087,
                                       "engine_n": 12266}})
    labels = [b[0] for b in bits]
    assert "Engine expectancy" in labels and "Engine trades" in labels
    assert "Base expectancy" not in labels
    assert dict(bits)["Engine expectancy"] == "+0.087 R"


def test_headline_bits_empty_run():
    from ui.pages.research_page import headline_bits

    assert headline_bits({}) == []
