"""Market Lens Overall Strategy Engine — research implementation.

Builds seven evidence-weighted sub-scores per historical signal, a final
confidence + decision, then backtests the honest ablation ladder:
  base -> +zone -> +market/sector -> +news -> +institutional -> +trap -> full engine
Every rung is measured against the base on the SAME signals. Weights that the
data did not support are zeroed and reported as such, not silently kept.

In-sample caveat (also in the report): weights are calibrated on the same year
they are evaluated on. Treat results as an upper bound; out-of-sample
validation is listed under 'needs more research'.
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

from research_engine.harness.trap_study import TRAPS, load as load_trades, stratified_delta  # noqa: E402

OUT = REPO_ROOT / "research_engine" / "output"

RECOMMENDED = ["gap_up_go", "zone_touch_fresh", "demand_bounce", "supply_rejection",
               "ema20_bounce", "ema20_rejection", "macd_cross_long", "hammer",
               "morning_star", "rsi_bull_divergence", "range_breakout", "vcp_breakout",
               "triangle_sym_breakout", "double_bottom_break"]


def _delta(df, mask, col="r_multiple"):
    m = pd.Series(np.asarray(mask).astype(bool), index=df.index)
    if m.sum() < 100 or (~m).sum() < 100:
        return None, int(m.sum())
    return round(float(df[m][col].mean() - df[~m][col].mean()), 3), int(m.sum())


def _study_rows(df, conds, min_n=100):
    rows = []
    for name, fn in conds:
        m = pd.Series(np.asarray(fn(df)).astype(bool), index=df.index)
        sub, rest = df[m], df[~m]
        if len(sub) < min_n:
            rows.append({"condition": name, "n": len(sub), "note": "insufficient sample"})
            continue
        pos = sub.r_multiple[sub.r_multiple > 0].sum()
        neg = -sub.r_multiple[sub.r_multiple <= 0].sum()
        strat, _ = stratified_delta(df, m, "r_multiple")
        rows.append({
            "condition": name, "n": len(sub),
            "win_rate_2r": round(sub.hit_2r.mean() * 100, 1),
            "expectancy_r": round(sub.r_multiple.mean(), 3),
            "expectancy_without": round(rest.r_multiple.mean(), 3),
            "delta_r_pooled": round(sub.r_multiple.mean() - rest.r_multiple.mean(), 3),
            "delta_r_stratified": None if strat is None else round(strat, 3),
            "profit_factor": round(pos / neg, 2) if neg else np.inf,
            "stop_hit_pct": round((sub.result == "loss").mean() * 100, 1),
            "false_breakout_pct": round(sub.false_breakout.mean() * 100, 1),
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ event study
def event_study(d: pd.DataFrame) -> pd.DataFrame:
    bull = d.bullish_or_bearish == "bullish"
    conds = [
        ("Result within next 1 day", lambda x: x.days_to_result.fillna(99) <= 1),
        ("Result within next 2 days", lambda x: x.days_to_result.fillna(99) <= 2),
        ("Result released today/yesterday", lambda x: x.days_since_result.fillna(99) <= 1),
        ("Result within last 5 days", lambda x: x.days_since_result.fillna(99) <= 5),
        ("Positive EPS surprise (>5%), recent result", lambda x: (x.days_since_result.fillna(99) <= 10) & (x.last_surprise_pct.fillna(0) > 5)),
        ("Negative EPS surprise (<-5%), recent result", lambda x: (x.days_since_result.fillna(99) <= 10) & (x.last_surprise_pct.fillna(0) < -5)),
        ("Strong positive result reaction (>2%), bullish signal", lambda x: bull & (x.days_since_result.fillna(99) <= 10) & (x.result_reaction_pct.fillna(0) > 2)),
        ("Strong negative result reaction (<-2%), bearish signal", lambda x: ~bull & (x.days_since_result.fillna(99) <= 10) & (x.result_reaction_pct.fillna(0) < -2)),
        ("Reaction against signal direction, recent result", lambda x: (x.days_since_result.fillna(99) <= 10) & (np.where(bull, x.result_reaction_pct.fillna(0) < -2, x.result_reaction_pct.fillna(0) > 2))),
        ("News-proxy gap event at signal", lambda x: x.news_gap_event),
        ("News-proxy volume shock at signal", lambda x: x.news_vol_event),
        ("News-proxy event in last 3 bars", lambda x: x.news_event_recent),
        ("Event bar aligned with signal direction", lambda x: x.news_event_recent & (np.where(bull, x.ret5_atr.fillna(0) > 0, x.ret5_atr.fillna(0) < 0))),
        ("Event bar against signal direction", lambda x: x.news_event_recent & (np.where(bull, x.ret5_atr.fillna(0) < 0, x.ret5_atr.fillna(0) > 0))),
    ]
    return _study_rows(d, conds)


# --------------------------------------------------- institutional proxy study
def proxy_study(d: pd.DataFrame) -> pd.DataFrame:
    bull = d.bullish_or_bearish == "bullish"
    aligned_obv = np.where(bull, d.obv_up, ~d.obv_up.astype(bool))
    aligned_ad = np.where(bull, d.ad_up, ~d.ad_up.astype(bool))
    vw = d.vwap_above.fillna(False).astype(bool)
    aligned_vwap = np.where(bull, vw, ~vw) & d.vwap_above.notna()
    conds = [
        ("OBV 20-bar trend aligned with direction", lambda x: aligned_obv),
        ("Accum/Dist line 20-bar trend aligned", lambda x: aligned_ad),
        ("VWAP side aligned (intraday only)", lambda x: aligned_vwap),
        ("Volume expansion on signal bar", lambda x: x.volume_confirmation),
        ("Volume contraction before breakout (patterns)", lambda x: (x.setup_type == "pattern") & x.volume_contraction_before),
        ("Accumulation proxy: near demand + vol expansion (longs)", lambda x: bull & x.near_demand & x.volume_confirmation),
        ("Distribution proxy: near supply + vol expansion (shorts)", lambda x: ~bull & x.near_supply & x.volume_confirmation),
        ("OBV+AD both aligned", lambda x: aligned_obv & aligned_ad),
        ("Top-3 liquidity deciles", lambda x: x._liq_decile.fillna(0) >= 7),
        ("Bottom liquidity decile", lambda x: x._liq_decile.fillna(5) <= 0),
    ]
    return _study_rows(d, conds)


# ---------------------------------------------------------------------- scores
def compute_scores(d: pd.DataFrame) -> pd.DataFrame:
    bull = (d.bullish_or_bearish == "bullish").to_numpy()

    # setup tier from per-(setup, tf) expectancy — in-sample lookup, capped
    tier = d.groupby(["setup_name", "timeframe"])["r_multiple"].transform("mean").clip(-0.4, 0.4)
    setup_base = ((tier + 0.4) / 0.8 * 40).to_numpy()  # 0..40

    zone_aligned = np.where(bull, d.near_demand, d.near_supply)
    zone_stale = np.where(bull, d.stale_demand, d.stale_supply)
    sma50_al = np.where(bull, d.sma50_trend == "above", d.sma50_trend == "below")
    sma200_al = np.where(bull, d.sma200_trend == "above", d.sma200_trend == "below")
    vol_on_break = ((d.setup_type == "pattern") & d.volume_confirmation).to_numpy()
    gap_from_zone = (d.setup_name.isin(["gap_up_go", "gap_down_go"]) & zone_aligned).to_numpy()

    technical = (setup_base
                 + np.where(zone_aligned, 15, 0) - np.where(zone_stale, 10, 0)
                 + np.where(sma50_al, 10, 0)
                 + np.where(d.fibonacci_confluence, 5, 0)
                 + np.where(vol_on_break, 10, 0)
                 + np.where(d.volume_contraction_before & (d.setup_type == "pattern"), 5, 0)
                 + np.where(gap_from_zone, 10, 0) + 15)
    d["technical_score"] = np.clip(technical, 0, 100)

    # Weights below follow the STRATIFIED evidence. Deliberately absent, because
    # the data refuted them this year (see trap_analysis.csv): overextension
    # penalties (T6/T8 — momentum conditions REDUCED stop-outs), short-in-bull-
    # market penalty (T7), counter-SMA200 penalty (T5 measured ~zero). These are
    # findings, not omissions; revisit on multi-year data.
    m_ext = d.market_ext_atr.fillna(0).to_numpy()
    s_ext = d.sector_ext_atr.fillna(0).to_numpy()
    mreg = d.market_regime_up.fillna(False).astype(bool).to_numpy()
    sreg_al = d.sector_confirmation.fillna(False).astype(bool).to_numpy()
    market = (55.0
              - np.where(bull & (m_ext > 2), 25, 0)             # T13: validated trap
              + np.where(bull & ~mreg, 10, 0)                    # measured: buying fear > buying strength
              + np.where(sreg_al & (d.timeframe == "weekly"), 10, 0)  # flag_effects: weekly sector alignment
              - np.where(np.abs(s_ext) > 2, 5, 0))               # weak analog of T13, small weight
    d["market_context_score"] = np.clip(market, 0, 100)

    rs = d.rs20_vs_nifty.fillna(0).to_numpy()
    rs_al = np.where(bull, rs > 0, rs < 0)
    liq = d._liq_decile.fillna(5).to_numpy()
    sd_up = d.stock_daily_uptrend
    sd_al = np.where(bull, sd_up.fillna(False), ~sd_up.fillna(True))
    stock = (50.0 + np.where(rs_al, 10, 0)
             + np.where(sd_al & sd_up.notna(), 15, 0)
             - np.where(liq <= 0, 10, 0) + np.where(liq >= 7, 5, 0))
    d["stock_context_score"] = np.clip(stock, 0, 100)

    dtr = d.days_to_result.fillna(99).to_numpy()
    dsr = d.days_since_result.fillna(99).to_numpy()
    surp = d.last_surprise_pct.fillna(0).to_numpy()
    react = d.result_reaction_pct.fillna(0).to_numpy()
    react_al = np.where(bull, react > 2, react < -2)
    react_ag = np.where(bull, react < -2, react > 2)
    surp_al = np.where(bull, surp > 5, surp < -5)
    news = (50.0 - np.where(dtr <= 1, 30, 0) - np.where((dtr > 1) & (dtr <= 2), 15, 0)
            + np.where((dsr <= 10) & react_al, 15, 0)
            - np.where((dsr <= 10) & react_ag, 15, 0)
            + np.where((dsr <= 10) & surp_al, 10, 0))
    d["news_event_score"] = np.clip(news, 0, 100)

    obv_al = np.where(bull, d.obv_up, ~d.obv_up.astype(bool))
    ad_al = np.where(bull, d.ad_up, ~d.ad_up.astype(bool))
    vw = d.vwap_above.fillna(False).astype(bool).to_numpy()
    vwap_al = np.where(bull, vw, ~vw) & d.vwap_above.notna().to_numpy()
    accum = (bull & d.near_demand & d.volume_confirmation) | (~bull & d.near_supply & d.volume_confirmation)
    inst = (40.0 + np.where(obv_al, 15, 0) + np.where(ad_al, 10, 0)
            + np.where(vwap_al, 10, 0) + np.where(accum, 15, 0)
            + np.where(vol_on_break, 10, 0))
    d["institutional_proxy_score"] = np.clip(inst, 0, 100)

    tw = json.load(open(OUT / "trap_weights.json"))["score_points"]
    trap = np.zeros(len(d))
    reasons = [[] for _ in range(len(d))]
    for tid, label, fn, _ in TRAPS:
        if tid not in tw:
            continue
        m = np.asarray(fn(d)).astype(bool)
        trap += m * tw[tid]
        for idx in np.where(m)[0]:
            reasons[idx].append(tid)
    d["trap_probability_score"] = np.clip(trap, 0, 100).round(1)
    d["trap_reasons"] = ["|".join(r) for r in reasons]

    rr_opp = d.rr_to_opposing
    rr = np.where(rr_opp.isna(), 70,
                  np.where(rr_opp >= 2, 80, np.where(rr_opp >= 1.5, 60, np.where(rr_opp >= 1, 40, 20))))
    risk = d.risk_pct.to_numpy()
    rr = rr + np.where((risk >= 1) & (risk <= 4), 10, 0) - np.where(risk > 6, 20, 0)
    d["risk_reward_score"] = np.clip(rr, 0, 100)

    conf = (0.30 * d.technical_score + 0.10 * d.market_context_score
            + 0.15 * d.stock_context_score + 0.10 * d.news_event_score
            + 0.10 * d.institutional_proxy_score + 0.25 * d.risk_reward_score)
    d["final_confidence_score"] = np.clip(conf - 0.35 * d.trap_probability_score, 0, 100).round(1)

    # --- decision: evidence-ordered GATES, not a blended score --------------
    # The blended-confidence decision tested non-monotonic (WAIT/WATCH scored
    # below NO TRADE), so the score is kept only for ranking WITHIN a category.
    # Gate order mirrors the measured evidence hierarchy:
    #   0) setup x timeframe cohort must be positive at n>=100 (in-sample tier,
    #      disclosed as such)                       — the largest single factor
    #   1) location: direction-aligned live zone, or a zone/gap setup itself
    #   2) traps: only the three validated traps score points (T4/T9/T13)
    #   3) RR to the opposing zone
    cohort_exp = d.groupby(["setup_name", "timeframe"])["r_multiple"].transform("mean")
    cohort_n = d.groupby(["setup_name", "timeframe"])["r_multiple"].transform("size")
    qualified = (cohort_exp > 0.03) & (cohort_n >= 100)
    location = pd.Series(zone_aligned, index=d.index) | (d.setup_type == "zone") | d.setup_name.isin(
        ["gap_up_go", "gap_down_go"])
    trap_lo = d.trap_probability_score < 40
    trap_mid = d.trap_probability_score.between(40, 60, inclusive="left")
    rr_ok = rr_opp.isna() | (rr_opp >= 1.5)
    rr_thin = rr_opp.between(1.0, 1.5, inclusive="left")

    dec = np.select(
        [d.trap_probability_score >= 60,
         qualified & location & trap_lo & rr_ok,
         qualified & location & trap_mid & rr_ok,
         qualified & location & trap_lo & rr_thin,
         qualified & ~location & trap_lo,
         qualified],
        ["AVOID", "TAKE", "REDUCE SIZE", "WAIT", "WATCH", "WATCH"],
        default="NO TRADE")
    d["final_decision"] = dec
    d["setup_cohort_expectancy"] = cohort_exp.round(3)
    return d


# ------------------------------------------------------------- ablation ladder
def ladder(d: pd.DataFrame, label: str) -> pd.DataFrame:
    bull = d.bullish_or_bearish == "bullish"
    zone_al = np.where(bull, d.near_demand, d.near_supply) | (d.setup_type == "zone")
    # market filter = only the VALIDATED trap (T13); the intuitive
    # "no shorts in a bullish market" rule was refuted and is not applied
    mkt_ok = ~(bull & (d.market_ext_atr.fillna(0) > 2))
    news_ok = d.days_to_result.fillna(99) > 1
    obv_al = np.where(bull, d.obv_up, ~d.obv_up.astype(bool))
    ad_al = np.where(bull, d.ad_up, ~d.ad_up.astype(bool))
    inst_ok = obv_al | ad_al
    trap_ok = d.trap_probability_score < 40
    engine_ok = d.final_decision.isin(["TAKE", "REDUCE SIZE"])

    stages = [
        ("1 base setups alone", pd.Series(True, index=d.index)),
        ("2 + zone context", pd.Series(zone_al, index=d.index)),
        ("3 + market/sector filter", pd.Series(zone_al & mkt_ok, index=d.index)),
        ("4 + news/result filter", pd.Series(zone_al & mkt_ok & news_ok, index=d.index)),
        ("5 + institutional proxy", pd.Series(zone_al & mkt_ok & news_ok & inst_ok, index=d.index)),
        ("6 + trap filter", pd.Series(zone_al & mkt_ok & news_ok & inst_ok & trap_ok, index=d.index)),
        ("7 full engine decision", engine_ok),
    ]
    rows = []
    base_n = len(d)
    for name, m in stages:
        sub = d[m]
        if len(sub) < 30:
            rows.append({"ladder": label, "stage": name, "n": len(sub), "note": "insufficient"})
            continue
        eq = sub.sort_values("_exit_date_sort").r_multiple.cumsum()
        dd = float((eq.cummax() - eq).max())
        pos = sub.r_multiple[sub.r_multiple > 0].sum()
        neg = -sub.r_multiple[sub.r_multiple <= 0].sum()
        rows.append({
            "ladder": label, "stage": name, "n": len(sub),
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
            "max_drawdown_r": round(dd, 1),
            "avg_holding": round(sub.holding_period.mean(), 1),
            "trap_rate_pct": round((sub.trap_probability_score >= 40).mean() * 100, 1),
        })
    return pd.DataFrame(rows)


def main() -> None:
    d = load_trades()
    print(f"loaded {len(d)} trades")
    event_study(d).to_csv(OUT / "event_impact.csv", index=False)
    proxy_study(d).to_csv(OUT / "institutional_proxy.csv", index=False)
    d = compute_scores(d)

    lad_all = ladder(d, "all_setups")
    lad_rec = ladder(d[d.setup_name.isin(RECOMMENDED)], "recommended_setups")
    lad_daily = ladder(d[(d.timeframe.isin(["daily", "weekly", "60m"])) & d.setup_name.isin(RECOMMENDED)],
                       "recommended_daily_weekly_60m")
    pd.concat([lad_all, lad_rec, lad_daily]).to_csv(OUT / "engine_ladder.csv", index=False)

    dec_stats = d.groupby("final_decision").agg(
        n=("r_multiple", "size"), expectancy=("r_multiple", "mean"),
        win2r=("hit_2r", "mean"), stop=("result", lambda s: (s == "loss").mean())).round(3)
    dec_stats.to_csv(OUT / "decision_validation.csv")

    cols = ["symbol", "company_name", "sector", "timeframe", "signal_date", "setup_name",
            "bullish_or_bearish", "technical_score", "market_context_score",
            "stock_context_score", "news_event_score", "institutional_proxy_score",
            "trap_probability_score", "risk_reward_score", "final_confidence_score",
            "final_decision", "entry_price", "stop_loss", "target_1", "target_2", "target_3",
            "rr_to_opposing", "dist_demand_pct", "dist_supply_pct", "sma50_trend",
            "ema20_confluence", "fibonacci_confluence", "volume_confirmation",
            "days_to_result", "days_since_result", "last_surprise_pct", "result_reaction_pct",
            "news_event_recent", "obv_up", "ad_up", "vwap_above", "trap_reasons",
            "exit_date", "exit_price", "result", "r_multiple", "mfe_r", "mae_r",
            "holding_period"]
    d[cols].to_csv(OUT / "candidates_detailed.csv", index=False)
    d[d.final_decision.isin(["TAKE", "REDUCE SIZE"])][cols].to_csv(
        OUT / "candidates_take_only.csv", index=False)
    d.to_parquet(OUT / "trades_scored.parquet")

    print(pd.concat([lad_rec]).to_string(index=False))
    print()
    print(dec_stats.to_string())


if __name__ == "__main__":
    main()
