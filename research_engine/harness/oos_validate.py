"""Out-of-sample validation: apply Run 1's FROZEN parameters to the prior year.

Nothing is re-fitted here. Setup qualification tiers, trap weights and gate
thresholds all come from the in-sample outputs; this script only APPLIES them
to trades the parameters have never seen (2024-08-11 .. 2025-08-10, daily +
weekly) and measures what happened. Outputs use the importer's standard file
names so the result lands in Validation History as its own run.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.harness.engine import RECOMMENDED, event_study, proxy_study  # noqa: E402
from research_engine.harness.trap_study import TRAPS, stratified_delta  # noqa: E402

IN_SAMPLE = REPO_ROOT / "research_engine" / "output"
OUT = REPO_ROOT / "research_engine" / "output_oos"

QUALIFY_MIN_EXP = 0.03   # same thresholds the engine used in-sample
QUALIFY_MIN_N = 100


def load_oos_trades() -> pd.DataFrame:
    frames = []
    for tf in ("daily", "weekly"):
        p = IN_SAMPLE / f"trades_{tf}_oos.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p))
    d = pd.concat(frames, ignore_index=True)
    d["r_multiple"] = d["r_multiple"].clip(-3, 10)
    d["_liq_decile"] = d.groupby("timeframe")["turnover20"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False))
    for col in ("signal_date", "exit_date"):
        d[f"_{col}_sort"] = pd.to_datetime(d[col])
    return d


def frozen_parameters() -> tuple[set[tuple[str, str]], dict[str, float], pd.DataFrame]:
    """(qualified setup×tf set, trap score points, in-sample per-setup table)."""
    rankings = pd.read_csv(IN_SAMPLE / "setup_rankings.csv")
    per_tf = rankings[rankings["timeframe"].isin(["daily", "weekly"])]
    per_tf = per_tf.groupby(["setup_name", "timeframe"], as_index=False).agg(
        expectancy_r=("expectancy_r", "mean"), n=("n", "sum"))
    qualified = {(r.setup_name, r.timeframe)
                 for r in per_tf.itertuples()
                 if r.expectancy_r > QUALIFY_MIN_EXP and r.n >= QUALIFY_MIN_N}
    tw = json.loads((IN_SAMPLE / "trap_weights.json").read_text())
    return qualified, tw["score_points"], per_tf


def apply_frozen_engine(d: pd.DataFrame, qualified: set, score_points: dict) -> pd.DataFrame:
    bull = (d.bullish_or_bearish == "bullish").to_numpy()

    trap = np.zeros(len(d))
    reasons = [[] for _ in range(len(d))]
    for tid, _label, fn, _det in TRAPS:
        pts = score_points.get(tid)
        if not pts:
            continue
        m = np.asarray(fn(d)).astype(bool)
        trap += m * pts
        for idx in np.where(m)[0]:
            reasons[idx].append(tid)
    d["trap_probability_score"] = np.clip(trap, 0, 100).round(1)
    d["trap_reasons"] = ["|".join(r) for r in reasons]

    d["_qualified"] = [(s, tf) in qualified
                       for s, tf in zip(d.setup_name, d.timeframe)]
    zone_aligned = np.where(bull, d.near_demand, d.near_supply)
    location = pd.Series(zone_aligned, index=d.index) | (d.setup_type == "zone") | \
        d.setup_name.isin(["gap_up_go", "gap_down_go"])
    rr_opp = d.rr_to_opposing
    trap_lo = d.trap_probability_score < 40
    trap_mid = d.trap_probability_score.between(40, 60, inclusive="left")
    rr_ok = rr_opp.isna() | (rr_opp >= 1.5)
    rr_thin = rr_opp.between(1.0, 1.5, inclusive="left")
    q = d["_qualified"]

    d["final_decision"] = np.select(
        [d.trap_probability_score >= 60,
         q & location & trap_lo & rr_ok,
         q & location & trap_mid & rr_ok,
         q & location & trap_lo & rr_thin,
         q & ~location & trap_lo,
         q],
        ["AVOID", "TAKE", "REDUCE SIZE", "WAIT", "WATCH", "WATCH"],
        default="NO TRADE")
    d["final_confidence_score"] = np.nan  # blended score is not part of the OOS gates
    return d


def _stage_stats(sub: pd.DataFrame, base_n: int, label: str, stage: str) -> dict:
    if len(sub) < 20:
        return {"ladder": label, "stage": stage, "n": len(sub), "note": "insufficient"}
    eq = sub.sort_values("_exit_date_sort").r_multiple.cumsum()
    pos = sub.r_multiple[sub.r_multiple > 0].sum()
    neg = -sub.r_multiple[sub.r_multiple <= 0].sum()
    return {
        "ladder": label, "stage": stage, "n": len(sub),
        "pct_filtered_out": round((1 - len(sub) / base_n) * 100, 1),
        "expectancy_r": round(sub.r_multiple.mean(), 3),
        "median_r": round(sub.r_multiple.median(), 3),
        "win_rate_2r": round(sub.hit_2r.mean() * 100, 1),
        "hit_1r_pct": round(sub.hit_1r.mean() * 100, 1),
        "hit_3r_pct": round(sub.hit_3r.mean() * 100, 1),
        "stop_hit_pct": round((sub.result == "loss").mean() * 100, 1),
        "false_breakout_pct": round(sub.false_breakout.mean() * 100, 1),
        "profit_factor": round(pos / neg, 2) if neg else np.inf,
        "total_r": round(float(sub.r_multiple.sum()), 1),
        "max_drawdown_r": round(float((eq.cummax() - eq).max()), 1),
        "avg_holding": round(sub.holding_period.mean(), 1),
        "trap_rate_pct": round((sub.trap_probability_score >= 40).mean() * 100, 1),
    }


def build_outputs(d: pd.DataFrame, per_tf_in_sample: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    rec = d[d.setup_name.isin(RECOMMENDED)]
    bull = rec.bullish_or_bearish == "bullish"
    zone_al = np.where(bull, rec.near_demand, rec.near_supply) | (rec.setup_type == "zone") | \
        rec.setup_name.isin(["gap_up_go", "gap_down_go"])
    rows = [
        _stage_stats(rec, len(rec), "recommended_setups", "1 base setups alone"),
        _stage_stats(rec[pd.Series(zone_al, index=rec.index)], len(rec),
                     "recommended_setups", "2 + zone context"),
        _stage_stats(rec[pd.Series(zone_al, index=rec.index) & (rec.trap_probability_score < 40)],
                     len(rec), "recommended_setups", "6 + trap filter"),
        _stage_stats(d[d.final_decision.isin(["TAKE", "REDUCE SIZE"])], len(d),
                     "recommended_setups", "7 full engine decision"),
    ]
    pd.DataFrame(rows).to_csv(OUT / "engine_ladder.csv", index=False)

    dec = d.groupby("final_decision").agg(
        n=("r_multiple", "size"), expectancy=("r_multiple", "mean"),
        win2r=("hit_2r", "mean"), stop=("result", lambda s: (s == "loss").mean())).round(3)
    dec.to_csv(OUT / "decision_validation.csv")

    # per-setup OOS vs in-sample comparison (daily+weekly)
    oos = d.groupby(["setup_name", "timeframe"], as_index=False).agg(
        n=("r_multiple", "size"), expectancy_r=("r_multiple", "mean"),
        win_rate_2r=("hit_2r", "mean"), stop_hit=("result", lambda s: (s == "loss").mean()))
    oos["win_rate_2r"] = (oos["win_rate_2r"] * 100).round(1)
    oos["stop_hit"] = (oos["stop_hit"] * 100).round(1)
    oos["expectancy_r"] = oos["expectancy_r"].round(3)
    cmp_df = oos.merge(per_tf_in_sample, on=["setup_name", "timeframe"],
                       suffixes=("", "_in_sample"), how="left")
    cmp_df = cmp_df.rename(columns={"expectancy_r_in_sample": "in_sample_expectancy_r",
                                    "n_in_sample": "in_sample_n"})
    cmp_df["delta_vs_in_sample"] = (cmp_df["expectancy_r"] - cmp_df["in_sample_expectancy_r"]).round(3)
    cmp_df["held_up"] = np.where(
        cmp_df["in_sample_expectancy_r"] > QUALIFY_MIN_EXP,
        np.where(cmp_df["expectancy_r"] > 0, "YES", "NO"), "")
    cmp_df.sort_values("expectancy_r", ascending=False).to_csv(
        OUT / "setup_rankings.csv", index=False)

    # trap re-check on unseen data (stratified, same method)
    score_points = json.loads((IN_SAMPLE / "trap_weights.json").read_text())["score_points"]
    rows = []
    for tid, label, fn, detect in TRAPS:
        m = pd.Series(np.asarray(fn(d)).astype(bool), index=d.index)
        if m.sum() < 100:
            continue
        ds_, _ = stratified_delta(d, m, "stop")
        de_, _ = stratified_delta(d, m, "r_multiple")
        rows.append({"trap_id": tid, "trap": label, "n": int(m.sum()),
                     "stop_uplift_pp_stratified_oos": None if ds_ is None else round(ds_ * 100, 1),
                     "delta_expectancy_stratified_oos": None if de_ is None else round(de_, 3),
                     # zero-valued weights are kept in the JSON; only >0 scores
                     "scored_in_engine": score_points.get(tid, 0) > 0,
                     "early_detection": detect})
    pd.DataFrame(rows).to_csv(OUT / "trap_analysis.csv", index=False)

    event_study(d).to_csv(OUT / "event_impact.csv", index=False)
    proxy_study(d).to_csv(OUT / "institutional_proxy.csv", index=False)
    shutil.copy2(IN_SAMPLE / "trap_weights.json", OUT / "trap_weights.json")
    d.to_parquet(OUT / "trades_scored.parquet")


def main() -> None:
    qualified, score_points, per_tf = frozen_parameters()
    print(f"frozen parameters: {len(qualified)} qualified setup x timeframe cells, "
          f"{len(score_points)} trap weights")
    d = load_oos_trades()
    print(f"OOS trades loaded: {len(d)} (daily+weekly, 2024-08-11..2025-08-10)")
    d = apply_frozen_engine(d, qualified, score_points)
    build_outputs(d, per_tf)

    from research_engine import importer
    result = importer.import_run(
        source_dir=OUT, label="2024-25 out-of-sample (daily+weekly, frozen params)")
    print("imported as run", result["run_id"], "warnings:", len(result["warnings"]))

    dec = pd.read_csv(OUT / "decision_validation.csv")
    lad = pd.read_csv(OUT / "engine_ladder.csv")
    print()
    print(lad.to_string(index=False))
    print()
    print(dec.to_string(index=False))


if __name__ == "__main__":
    main()
