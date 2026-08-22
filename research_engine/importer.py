"""Import generated research outputs into the Research Engine store.

Reads ONLY files (CSV/parquet/JSON/PNG) from the harness output directory —
never imports harness modules, so importing can never patch the zone engine.
Missing files degrade to warnings; whatever exists is imported.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from research_engine import store

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "research_engine" / "output"

RUN1_LABEL = "2026-08 in-sample year"

# findings name -> source CSV
_FINDINGS_FILES = {
    "setup_rankings": "setup_rankings.csv",
    "engine_ladder": "engine_ladder.csv",
    "decision_validation": "decision_validation.csv",
    "trap_analysis": "trap_analysis.csv",
    "trap_score_validation": "trap_score_validation.csv",
    "event_impact": "event_impact.csv",
    "institutional_proxy": "institutional_proxy.csv",
    "combo_rankings": "combo_rankings.csv",
    "flag_effects": "flag_effects.csv",
    "stock_findings": "stock_findings.csv",
    "sector_findings": "sector_findings.csv",
}

_CANDIDATE_DECISIONS = ("TAKE", "REDUCE SIZE", "WAIT", "WATCH")

# large artifacts referenced by path only
_ARTIFACT_FILES = {
    "report_layer1": "FnO_Pattern_Research_Report.docx",
    "report_layer2": "Overall_Strategy_Engine_Report.docx",
    "candidates_full_csv_gz": "candidates_detailed.csv.gz",
    "trades_scored_parquet": "trades_scored.parquet",
    "signals_detailed_csv_gz": "signals_detailed.csv.gz",
}


def _headline_from(ladder: pd.DataFrame | None, decisions: pd.DataFrame | None) -> dict:
    headline: dict = {}
    if ladder is not None and len(ladder):
        rec = ladder[(ladder.get("ladder") == "recommended_setups")]
        base = rec[rec["stage"].astype(str).str.startswith("1 ")]
        full = rec[rec["stage"].astype(str).str.startswith("7 ")]
        if len(base):
            headline["base_expectancy_r"] = float(base.iloc[0]["expectancy_r"])
            headline["base_n"] = int(base.iloc[0]["n"])
        if len(full):
            headline["engine_expectancy_r"] = float(full.iloc[0]["expectancy_r"])
            headline["engine_n"] = int(full.iloc[0]["n"])
            headline["engine_profit_factor"] = float(full.iloc[0]["profit_factor"])
            headline["pct_filtered_out"] = float(full.iloc[0]["pct_filtered_out"])
    if decisions is not None and len(decisions):
        first_col = decisions.columns[0]
        take = decisions[decisions[first_col] == "TAKE"]
        if len(take):
            headline["take_expectancy_r"] = float(take.iloc[0]["expectancy"])
            headline["take_n"] = int(take.iloc[0]["n"])
    return headline


def import_run(source_dir: Path | None = None, label: str = RUN1_LABEL,
               db_path: Path | None = None, charts_dir: Path | None = None,
               replace: bool = True) -> dict:
    """Import outputs from `source_dir` as a run. Returns a result summary."""
    src = Path(source_dir) if source_dir else DEFAULT_SOURCE
    warnings: list[str] = []
    imported: list[str] = []

    if replace:
        store.delete_run(label, db_path=db_path)
    run_id = store.create_run(label, params={"source_dir": str(src)}, db_path=db_path)

    ladder_df = decisions_df = None
    for name, fname in _FINDINGS_FILES.items():
        path = src / fname
        if not path.exists():
            warnings.append(f"missing findings file: {fname}")
            continue
        try:
            df = pd.read_csv(path)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"unreadable findings file {fname}: {exc}")
            continue
        store.save_findings(run_id, name, df, db_path=db_path)
        imported.append(name)
        if name == "engine_ladder":
            ladder_df = df
        if name == "decision_validation":
            decisions_df = df

    # trap weights JSON (engine config evidence)
    tw_path = src / "trap_weights.json"
    if tw_path.exists():
        try:
            tw = json.loads(tw_path.read_text())
            rows = [{"trap_id": k, "score_points": v,
                     "stop_uplift_pp": round(tw.get("weights_pp", {}).get(k, 0) * 100, 2)}
                    for k, v in tw.get("score_points", {}).items()]
            store.save_findings(run_id, "trap_weights", pd.DataFrame(rows), db_path=db_path)
            imported.append("trap_weights")
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"unreadable trap_weights.json: {exc}")
    else:
        warnings.append("missing findings file: trap_weights.json")

    # candidates: prefer scored parquet (has WAIT/WATCH), fall back to TAKE csv
    n_candidates = 0
    scored = src / "trades_scored.parquet"
    take_csv = src / "candidates_take_only.csv"
    try:
        if scored.exists():
            cd = pd.read_parquet(scored)
            cd = cd[cd["final_decision"].isin(_CANDIDATE_DECISIONS)]
            n_candidates = store.save_candidates(run_id, cd, db_path=db_path)
            imported.append("candidates(parquet)")
        elif take_csv.exists():
            cd = pd.read_csv(take_csv)
            n_candidates = store.save_candidates(run_id, cd, db_path=db_path)
            imported.append("candidates(take_only_csv)")
            warnings.append("trades_scored.parquet missing — candidates limited to "
                            "TAKE/REDUCE SIZE rows from candidates_take_only.csv")
        else:
            warnings.append("no candidates source found (trades_scored.parquet / "
                            "candidates_take_only.csv)")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"candidates import failed: {exc}")

    # charts: copy small PNGs into the app dir so the page owns its copies
    charts_src = src / "charts"
    charts_dest = Path(charts_dir) if charts_dir else store.CHARTS_DIR / f"run_{run_id}"
    chart_paths: list[str] = []
    if charts_src.exists():
        charts_dest.mkdir(parents=True, exist_ok=True)
        for png in sorted(charts_src.glob("*.png")):
            try:
                shutil.copy2(png, charts_dest / png.name)
                chart_paths.append(str(charts_dest / png.name))
            except OSError as exc:
                warnings.append(f"chart copy failed {png.name}: {exc}")
    else:
        warnings.append("missing charts directory")

    artifacts = {"charts": chart_paths}
    for key, fname in _ARTIFACT_FILES.items():
        path = src / fname
        if path.exists():
            artifacts[key] = str(path)
        else:
            warnings.append(f"missing artifact (path reference only): {fname}")

    store.update_run(run_id, headline=_headline_from(ladder_df, decisions_df),
                     artifacts=artifacts, warnings=warnings, db_path=db_path)
    return {"run_id": run_id, "label": label, "imported": imported,
            "n_candidates": n_candidates, "n_charts": len(chart_paths),
            "warnings": warnings}


def ensure_run1(db_path: Path | None = None) -> dict | None:
    """Auto-import Run 1 on first open; no-op if any run already exists."""
    if store.get_runs(db_path):
        return None
    return import_run(db_path=db_path)
