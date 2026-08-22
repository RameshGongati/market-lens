"""Trap taxonomy: define trap conditions, measure each against the data,
calibrate a 0-100 Trap Probability Score from measured stop-hit uplift.

A 'trap' here = a signal-time condition that historically raised the chance of
the trade stopping out (or materially cut expectancy). Weights come from the
data, not intuition: each trap's score contribution is proportional to its
measured increase in stop-hit probability vs the same setup family without it.
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUT = REPO_ROOT / "research_engine" / "output"

# --------------------------------------------------------------------------- traps
# Each: id, label, mask function, early-detection guidance (goes to report/CSV).
TRAPS = [
    ("T1", "Bullish setup directly below live supply",
     lambda d: (d.bullish_or_bearish == "bullish") & d.near_supply,
     "Check nearest supply proximal before entry; skip longs within 2% of live supply."),
    ("T2", "Bearish setup directly above live demand",
     lambda d: (d.bullish_or_bearish == "bearish") & d.near_demand,
     "Check nearest demand proximal; skip shorts within 2% of live demand."),
    ("T3", "Breakout of an over-tested level (4+ touches in 60 bars)",
     lambda d: d.level_tests.fillna(0) >= 4,
     "Count prior touches of the breakout level; many touches = well-known level = crowded."),
    ("T4", "Breakout without volume expansion",
     lambda d: (d.setup_type == "pattern") & ~d.volume_confirmation.astype(bool),
     "Require signal-bar volume >= 1.5x its 20-bar average on breakout entries."),
    ("T5", "Against higher-timeframe trend (SMA200 misaligned)",
     lambda d: np.where(d.bullish_or_bearish == "bullish", d.sma200_trend == "below", d.sma200_trend == "above"),
     "Compare direction with the 200-SMA side; flag counter-trend entries."),
    ("T6", "Overextended entry (close > EMA20 + 2 ATR for longs, mirror for shorts)",
     lambda d: np.where(d.bullish_or_bearish == "bullish", d.ext_ema20_atr.fillna(0) > 2, d.ext_ema20_atr.fillna(0) < -2),
     "Measure (close - EMA20)/ATR at signal; >2 means chasing an extended move."),
    ("T7", "Short while broad market is bullish",
     lambda d: (d.bullish_or_bearish == "bearish") & d.market_regime_up.fillna(False).astype(bool),
     "Check NIFTY vs its 20-EMA before shorts."),
    ("T8", "Sharp pre-signal run-up/down (5-bar move > 3 ATR)",
     lambda d: d.ret5_atr.abs().fillna(0) > 3,
     "Measure 5-bar move in ATRs; late entries after vertical moves revert."),
    ("T9", "Signal within 1 day before a results announcement",
     lambda d: d.days_to_result.fillna(99) <= 1,
     "Join the earnings calendar; flag entries into event risk."),
    ("T10", "Buying stale demand / selling stale supply (zone tested 2+ times)",
     lambda d: np.where(d.bullish_or_bearish == "bullish", d.stale_demand, d.stale_supply),
     "Zone touch count from the zone engine; 2+ prior tests = consumed orders."),
    ("T11", "Bottom-decile liquidity (20-bar median turnover)",
     lambda d: d._liq_decile.fillna(5) <= 0,
     "Rank 20-bar median turnover within the scan; bottom decile = fills and stops slip."),
    ("T12", "News-proxy event bar (gap/volume/range shock) at signal",
     lambda d: d.news_event_recent.astype(bool) & ~d.setup_name.isin(["gap_up_go", "gap_down_go"]),
     "Abnormal gap/volume/range in the last 3 bars (excluding gap setups themselves)."),
    ("T13", "Market overextended (NIFTY > 2 ATR above its 20-EMA) for longs",
     lambda d: (d.bullish_or_bearish == "bullish") & (d.market_ext_atr.fillna(0) > 2),
     "Measure NIFTY extension in ATRs, not just direction."),
    ("T14", "Recently broken zone on the trade side",
     lambda d: np.where(d.bullish_or_bearish == "bullish", d.demand_broken_recent, d.supply_broken_recent),
     "An M46 break on the trade side within 10 bars = structure just failed."),
]


def _naive_ist(x):
    ts = pd.Timestamp(x)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
    return ts


def load() -> pd.DataFrame:
    d = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob(str(OUT / "trades_*_all.parquet")))],
                  ignore_index=True)
    d["r_multiple"] = d["r_multiple"].clip(-3, 10)
    # tz-naive IST sort keys (daily rows are naive, intraday rows tz-aware)
    for col in ("signal_date", "exit_date"):
        d[f"_{col}_sort"] = [_naive_ist(x) for x in d[col]]
    # liquidity decile within (timeframe) universe
    d["_liq_decile"] = d.groupby("timeframe")["turnover20"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 10, labels=False))
    return d


def stratified_delta(d: pd.DataFrame, mask: pd.Series, col: str,
                     keys=("setup_name", "timeframe"), min_cell: int = 50) -> tuple[float | None, int]:
    """Composition-safe effect: mean(with) - mean(without) computed WITHIN each
    (setup, timeframe) cell, pooled weighted by the smaller side's n. A pooled
    contrast is confounded by mix (a flag that under-samples 15m looks good for
    free); this is the honest version."""
    m = pd.Series(np.asarray(mask).astype(bool), index=d.index)
    if col == "stop":
        vals = (d["result"] == "loss").astype(float)
    else:
        vals = d[col]
    num = den = 0.0
    n_with_total = 0
    for _, g in d.groupby(list(keys), observed=True):
        gm = m.loc[g.index]
        nw, nwo = int(gm.sum()), int((~gm).sum())
        if nw < min_cell or nwo < min_cell:
            continue
        delta = vals.loc[g.index][gm].mean() - vals.loc[g.index][~gm].mean()
        w = min(nw, nwo)
        num += delta * w
        den += w
        n_with_total += nw
    if den == 0:
        return None, int(m.sum())
    return float(num / den), n_with_total


def main() -> None:
    d = load()
    stop_rate_all = (d.result == "loss").mean()
    rows = []
    weights = {}
    for tid, label, fn, detect in TRAPS:
        m = pd.Series(np.asarray(fn(d)).astype(bool), index=d.index)
        sub, rest = d[m], d[~m]
        if len(sub) < 200:
            continue
        stop_w = (sub.result == "loss").mean()
        stop_wo = (rest.result == "loss").mean()
        exp_w = sub.r_multiple.mean()
        exp_wo = rest.r_multiple.mean()
        strat_stop, _ = stratified_delta(d, m, "stop")
        strat_exp, _ = stratified_delta(d, m, "r_multiple")
        by_tf = sub.groupby("timeframe").r_multiple.mean().round(3).to_dict()
        worst_tf = min(by_tf, key=by_tf.get) if by_tf else None
        sect = sub.groupby("sector").r_multiple.agg(["count", "mean"])
        sect = sect[sect["count"] >= 100]
        worst_sect = sect["mean"].idxmin() if len(sect) else None
        reversal = ((sub.mae_r >= 1) & ~sub.hit_1r).mean()
        rows.append({
            "trap_id": tid, "trap": label, "n": len(sub),
            "pct_of_all_signals": round(m.mean() * 100, 1),
            "stop_hit_pct_with": round(stop_w * 100, 1),
            "stop_hit_pct_without": round(stop_wo * 100, 1),
            "stop_uplift_pp_pooled": round((stop_w - stop_wo) * 100, 1),
            "stop_uplift_pp_stratified": None if strat_stop is None else round(strat_stop * 100, 1),
            "expectancy_with": round(exp_w, 3),
            "expectancy_without": round(exp_wo, 3),
            "delta_expectancy_pooled": round(exp_w - exp_wo, 3),
            "delta_expectancy_stratified": None if strat_exp is None else round(strat_exp, 3),
            "false_breakout_pct": round(sub.false_breakout.mean() * 100, 1),
            "reversed_after_entry_pct": round(reversal * 100, 1),
            "hit_2r_pct": round(sub.hit_2r.mean() * 100, 1),
            "worst_timeframe": worst_tf,
            "worst_sector_n100": worst_sect,
            "early_detection": detect,
        })
        # weight: a condition earns points only if, WITHIN setup/timeframe
        # strata, it BOTH raises stop-outs (>=0.5pp) AND does not improve
        # expectancy. Pooled contrasts are composition-confounded; conditions
        # failing this test are reported as refuted, not scored.
        qualifies = (strat_stop is not None and strat_stop >= 0.005
                     and strat_exp is not None and strat_exp <= 0)
        weights[tid] = round(float(strat_stop), 4) if qualifies else 0.0
    ta = pd.DataFrame(rows).sort_values("stop_uplift_pp_stratified", ascending=False)
    ta.to_csv(OUT / "trap_analysis.csv", index=False)

    # calibrate score: points proportional to stop-hit uplift, scaled so the
    # worst realistic stack (all surviving traps at once) approaches 100.
    total_realistic = sum(v for v in weights.values() if v > 0) or 1.0
    scale = 100.0 / total_realistic
    score_map = {k: round(v * scale, 1) for k, v in weights.items()}
    json.dump({"weights_pp": weights, "score_points": score_map,
               "base_stop_rate": round(float(stop_rate_all), 4)},
              open(OUT / "trap_weights.json", "w"), indent=2)

    # validate the score the composition-safe way: does trap_score>0 raise
    # stop-outs and cut expectancy WITHIN the same (setup, timeframe)?
    # (A cross-setup bucket table is misleading: pattern breakouts dominate the
    # mid buckets and have different base rates than candlesticks.)
    d["trap_score"] = 0.0
    for tid, label, fn, _ in TRAPS:
        if score_map.get(tid):
            d["trap_score"] += np.asarray(fn(d)).astype(float) * score_map[tid]
    d["trap_score"] = d["trap_score"].clip(0, 100)
    rows_v = []
    for name, msk in [("score>0 vs 0", d.trap_score > 0),
                      ("score>=40 vs <40", d.trap_score >= 40),
                      ("score>=60 vs <60", d.trap_score >= 60)]:
        ds, nw = stratified_delta(d, msk, "stop")
        de, _ = stratified_delta(d, msk, "r_multiple")
        rows_v.append({"comparison": name, "n_flagged": int(msk.sum()),
                       "stratified_stop_uplift_pp": None if ds is None else round(ds * 100, 1),
                       "stratified_delta_expectancy": None if de is None else round(de, 3)})
    val = pd.DataFrame(rows_v)
    val.to_csv(OUT / "trap_score_validation.csv", index=False)
    print(ta[["trap_id", "trap", "n", "stop_uplift_pp_pooled", "stop_uplift_pp_stratified",
              "delta_expectancy_stratified"]].to_string(index=False))
    print()
    print(val.to_string())


if __name__ == "__main__":
    main()
