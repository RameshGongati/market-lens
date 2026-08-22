"""Aggregate trade results into rankings, combos, stock/sector findings, CSVs."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUT = REPO_ROOT / "research_engine" / "output"
MIN_N_RELIABLE = 30
MIN_N_INDICATIVE = 10

CSV_ORDER = [
    "symbol", "company_name", "sector", "exchange", "timeframe", "setup_name",
    "setup_type", "bullish_or_bearish", "signal_date", "entry_price", "stop_loss",
    "target_1", "target_2", "target_3", "exit_date", "exit_price", "result",
    "max_profit_percent", "max_loss_percent", "r_multiple", "win_loss",
    "holding_period", "volume_confirmation", "ema20_confluence", "sma50_trend",
    "rsi_value", "stochastic_value", "fibonacci_confluence", "demand_zone_present",
    "supply_zone_present", "sector_confirmation", "market_confirmation", "notes",
]


def load_all() -> pd.DataFrame:
    frames = []
    for p in sorted(OUT.glob("trades_*_all.parquet")):
        frames.append(pd.read_parquet(p))
    df = pd.concat(frames, ignore_index=True)
    df["r_multiple"] = df["r_multiple"].clip(-3, 10)  # data-error guard
    return df


def stats(g: pd.DataFrame) -> pd.Series:
    n = len(g)
    r = g["r_multiple"]
    pos = r[r > 0].sum()
    neg = -r[r <= 0].sum()
    return pd.Series({
        "n": n,
        "win_rate_2r": round(g["hit_2r"].mean() * 100, 1),
        "win_rate_any": round((r > 0).mean() * 100, 1),
        "loss_rate": round((g["result"] == "loss").mean() * 100, 1),
        "expectancy_r": round(r.mean(), 3),
        "median_r": round(r.median(), 3),
        "profit_factor": round(pos / neg, 2) if neg > 0 else np.inf,
        "avg_return_pct": round(g["return_pct"].mean(), 3),
        "median_return_pct": round(g["return_pct"].median(), 3),
        "avg_mfe_r": round(g["mfe_r"].mean(), 2),
        "avg_mae_r": round(g["mae_r"].mean(), 2),
        "hit_1r_pct": round(g["hit_1r"].mean() * 100, 1),
        "hit_2r_pct": round(g["hit_2r"].mean() * 100, 1),
        "hit_3r_pct": round(g["hit_3r"].mean() * 100, 1),
        "false_breakout_pct": round(g["false_breakout"].mean() * 100, 1),
        "timeout_pct": round((g["result"] == "timeout").mean() * 100, 1),
        "avg_holding_bars": round(g["holding_period"].mean(), 1),
        "avg_risk_pct": round(g["risk_pct"].mean(), 2),
        "n_symbols": g["symbol"].nunique(),
    })


def consistency(df: pd.DataFrame) -> pd.DataFrame:
    """share of symbols (with >=3 trades) where the setup had positive expectancy"""
    rows = []
    groups = [(setup, tf, g) for (setup, tf), g in df.groupby(["setup_name", "timeframe"])]
    groups += [(setup, "ALL", g) for setup, g in df.groupby("setup_name")]
    for setup, tf, g in groups:
        per_sym = g.groupby("symbol")["r_multiple"].agg(["count", "mean"])
        per_sym = per_sym[per_sym["count"] >= 3]
        if len(per_sym) >= 5:
            rows.append({"setup_name": setup, "timeframe": tf,
                         "symbols_with_3plus": len(per_sym),
                         "pct_symbols_positive": round((per_sym["mean"] > 0).mean() * 100, 1)})
    return pd.DataFrame(rows)


COMBO_FLAGS = {
    "vol_expansion": lambda d: d["volume_confirmation"],
    "vol_contraction_before": lambda d: d["volume_contraction_before"],
    "ema20_confluence": lambda d: d["ema20_confluence"],
    "fib_confluence": lambda d: d["fibonacci_confluence"],
    "near_demand": lambda d: d["near_demand"],
    "near_supply": lambda d: d["near_supply"],
    "zone_context_aligned": lambda d: np.where(d["bullish_or_bearish"] == "bullish",
                                               d["near_demand"], d["near_supply"]),
    "sma50_aligned": lambda d: np.where(d["bullish_or_bearish"] == "bullish",
                                        d["sma50_trend"] == "above", d["sma50_trend"] == "below"),
    "sma200_aligned": lambda d: np.where(d["bullish_or_bearish"] == "bullish",
                                         d["sma200_trend"] == "above", d["sma200_trend"] == "below"),
    "market_confirmation": lambda d: d["market_confirmation"].fillna(False).astype(bool),
    "sector_confirmation": lambda d: d["sector_confirmation"].fillna(False).astype(bool),
    "bb_squeeze_before": lambda d: d["bb_squeeze_before"],
    "rsi_not_extreme": lambda d: np.where(d["bullish_or_bearish"] == "bullish",
                                          d["rsi_value"] < 70, d["rsi_value"] > 30),
}

NAMED_COMBOS = [
    ("Demand bounce + EMA20 confluence", "demand_bounce", ["ema20_confluence"]),
    ("Demand bounce + Fib confluence", "demand_bounce", ["fib_confluence"]),
    ("Demand bounce + volume expansion", "demand_bounce", ["vol_expansion"]),
    ("Demand bounce + market support", "demand_bounce", ["market_confirmation"]),
    ("Demand bounce + sector support", "demand_bounce", ["sector_confirmation"]),
    ("Demand bounce + SMA50 uptrend", "demand_bounce", ["sma50_aligned"]),
    ("Fresh zone touch + SMA50 trend", "zone_touch_fresh", ["sma50_aligned"]),
    ("Fresh zone touch + market support", "zone_touch_fresh", ["market_confirmation"]),
    ("Supply rejection + EMA20 confluence", "supply_rejection", ["ema20_confluence"]),
    ("Supply rejection + Fib confluence", "supply_rejection", ["fib_confluence"]),
    ("Supply rejection + market weakness", "supply_rejection", ["market_confirmation"]),
    ("Bullish engulfing near demand", "bullish_engulfing", ["near_demand"]),
    ("Bearish engulfing near supply", "bearish_engulfing", ["near_supply"]),
    ("Hammer near demand", "hammer", ["near_demand"]),
    ("Shooting star near supply", "shooting_star", ["near_supply"]),
    ("EMA20 bounce near demand", "ema20_bounce", ["near_demand"]),
    ("EMA20 bounce + SMA50/200 uptrend", "ema20_bounce", ["sma200_aligned"]),
    ("Gap up from demand", "gap_up_go", ["near_demand"]),
    ("Gap down from supply", "gap_down_go", ["near_supply"]),
    ("RSI bull divergence near demand", "rsi_bull_divergence", ["near_demand"]),
    ("RSI bear divergence near supply", "rsi_bear_divergence", ["near_supply"]),
    ("Triangle breakout + volume", "triangle_sym_breakout", ["vol_expansion"]),
    ("Ascending triangle + volume", "ascending_triangle_break", ["vol_expansion"]),
    ("VCP breakout + volume expansion", "vcp_breakout", ["vol_expansion"]),
    ("VCP breakout + prior contraction", "vcp_breakout", ["vol_contraction_before"]),
    ("Range breakout + volume", "range_breakout", ["vol_expansion"]),
    ("Range breakout + market support", "range_breakout", ["market_confirmation"]),
    ("BB squeeze breakout + volume", "bb_squeeze_breakout", ["vol_expansion"]),
    ("Squeeze break + zone aligned", "bb_squeeze_breakout", ["zone_context_aligned"]),
    ("Trend + pullback + zone (long)", "ema20_bounce", ["sma200_aligned", "zone_context_aligned"]),
    ("Pattern break + market support", None, ["market_confirmation"]),  # None -> all pattern setups
    ("Pattern break + sector support", None, ["sector_confirmation"]),
]


def combos(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, setup, flags in NAMED_COMBOS:
        base = df[df["setup_type"] == "pattern"] if setup is None else df[df["setup_name"] == setup]
        if base.empty:
            continue
        mask = pd.Series(True, index=base.index)
        for f in flags:
            mask &= pd.Series(np.asarray(COMBO_FLAGS[f](base)).astype(bool), index=base.index)
        sub = base[mask]
        for tf, g in sub.groupby("timeframe"):
            if len(g) >= MIN_N_INDICATIVE:
                row = stats(g).to_dict()
                base_tf = base[base["timeframe"] == tf]
                row.update({"combo": name, "timeframe": tf,
                            "base_setup": setup or "all_patterns",
                            "base_expectancy_r": round(base_tf["r_multiple"].mean(), 3),
                            "delta_expectancy_r": round(g["r_multiple"].mean() - base_tf["r_multiple"].mean(), 3)})
                rows.append(row)
        # all-timeframes row
        if len(sub) >= MIN_N_INDICATIVE:
            row = stats(sub).to_dict()
            row.update({"combo": name, "timeframe": "ALL", "base_setup": setup or "all_patterns",
                        "base_expectancy_r": round(base["r_multiple"].mean(), 3),
                        "delta_expectancy_r": round(sub["r_multiple"].mean() - base["r_multiple"].mean(), 3)})
            rows.append(row)
    out = pd.DataFrame(rows)
    if len(out):
        front = ["combo", "timeframe", "base_setup", "n", "expectancy_r", "base_expectancy_r",
                 "delta_expectancy_r", "win_rate_2r", "profit_factor"]
        out = out[front + [c for c in out.columns if c not in front]]
    return out


def flag_effects(df: pd.DataFrame) -> pd.DataFrame:
    """Every setup x every single context flag: does the flag help?"""
    rows = []
    for (setup, tf), g in df.groupby(["setup_name", "timeframe"]):
        if len(g) < 2 * MIN_N_INDICATIVE:
            continue
        for fname, fn in COMBO_FLAGS.items():
            m = pd.Series(np.asarray(fn(g)).astype(bool), index=g.index)
            if m.sum() < MIN_N_INDICATIVE or (~m).sum() < MIN_N_INDICATIVE:
                continue
            rows.append({
                "setup_name": setup, "timeframe": tf, "flag": fname,
                "n_with": int(m.sum()), "n_without": int((~m).sum()),
                "exp_with": round(g[m]["r_multiple"].mean(), 3),
                "exp_without": round(g[~m]["r_multiple"].mean(), 3),
                "delta": round(g[m]["r_multiple"].mean() - g[~m]["r_multiple"].mean(), 3),
                "win2r_with": round(g[m]["hit_2r"].mean() * 100, 1),
                "win2r_without": round(g[~m]["hit_2r"].mean() * 100, 1),
            })
    return pd.DataFrame(rows)


def main() -> None:
    df = load_all()
    print(f"total trades: {len(df)}")

    # 1. detailed signals CSV (user's column order + extras at the end)
    detailed = df.copy()
    for col in CSV_ORDER:
        if col not in detailed.columns:
            detailed[col] = None
    extras = [c for c in detailed.columns if c not in CSV_ORDER]
    detailed = detailed[CSV_ORDER + extras]
    detailed.to_csv(OUT / "signals_detailed.csv", index=False)

    # 2. setup rankings (per timeframe + overall)
    per_tf = df.groupby(["setup_name", "setup_type", "bullish_or_bearish", "timeframe"]).apply(
        stats, include_groups=False).reset_index()
    overall = df.groupby(["setup_name", "setup_type", "bullish_or_bearish"]).apply(
        stats, include_groups=False).reset_index()
    overall["timeframe"] = "ALL"
    rankings = pd.concat([per_tf, overall], ignore_index=True)
    cons = consistency(df)
    rankings = rankings.merge(cons, on=["setup_name", "timeframe"], how="left")
    rankings["reliability"] = np.where(rankings["n"] >= MIN_N_RELIABLE, "reliable",
                                np.where(rankings["n"] >= MIN_N_INDICATIVE, "indicative", "small-sample"))
    rankings = rankings.sort_values(["expectancy_r"], ascending=False)
    rankings.to_csv(OUT / "setup_rankings.csv", index=False)

    # 3. combos + flag effects
    combos(df).to_csv(OUT / "combo_rankings.csv", index=False)
    flag_effects(df).to_csv(OUT / "flag_effects.csv", index=False)

    # 4. stock findings
    rows = []
    for sym, g in df.groupby("symbol"):
        best = None
        per_setup = g.groupby("setup_name")["r_multiple"].agg(["count", "mean"])
        per_setup = per_setup[per_setup["count"] >= 5]
        if len(per_setup):
            b = per_setup.sort_values("mean", ascending=False).iloc[0]
            best = f"{per_setup['mean'].idxmax()} (n={int(b['count'])}, exp={b['mean']:.2f}R)"
        per_tf_g = g.groupby("timeframe")["r_multiple"].mean()
        bl = g[g["bullish_or_bearish"] == "bullish"]
        br = g[g["bullish_or_bearish"] == "bearish"]
        rows.append({
            "symbol": sym, "sector": g["sector"].iloc[0], "n_trades": len(g),
            "best_setup": best,
            "best_timeframe": per_tf_g.idxmax() if len(per_tf_g) else None,
            "bullish_n": len(bl), "bullish_exp_r": round(bl["r_multiple"].mean(), 3) if len(bl) else None,
            "bullish_win2r_pct": round(bl["hit_2r"].mean() * 100, 1) if len(bl) else None,
            "bearish_n": len(br), "bearish_exp_r": round(br["r_multiple"].mean(), 3) if len(br) else None,
            "bearish_win2r_pct": round(br["hit_2r"].mean() * 100, 1) if len(br) else None,
            "overall_exp_r": round(g["r_multiple"].mean(), 3),
        })
    pd.DataFrame(rows).sort_values("overall_exp_r", ascending=False).to_csv(
        OUT / "stock_findings.csv", index=False)

    # 5. sector findings
    rows = []
    for sector, g in df.groupby("sector"):
        per_setup = g.groupby("setup_name").apply(stats, include_groups=False)
        per_setup = per_setup[per_setup["n"] >= 20].sort_values("expectancy_r", ascending=False)
        best = per_setup.index[0] if len(per_setup) else None
        per_tf_g = g.groupby("timeframe")["r_multiple"].agg(["count", "mean"])
        per_tf_g = per_tf_g[per_tf_g["count"] >= 30]
        bl = g[g["bullish_or_bearish"] == "bullish"]
        br = g[g["bullish_or_bearish"] == "bearish"]
        rows.append({
            "sector": sector, "n_trades": len(g), "n_symbols": g["symbol"].nunique(),
            "best_setup": best,
            "best_setup_exp_r": round(per_setup.iloc[0]["expectancy_r"], 3) if len(per_setup) else None,
            "best_timeframe": per_tf_g["mean"].idxmax() if len(per_tf_g) else None,
            "bullish_exp_r": round(bl["r_multiple"].mean(), 3) if len(bl) else None,
            "bearish_exp_r": round(br["r_multiple"].mean(), 3) if len(br) else None,
            "overall_exp_r": round(g["r_multiple"].mean(), 3),
        })
    pd.DataFrame(rows).sort_values("overall_exp_r", ascending=False).to_csv(
        OUT / "sector_findings.csv", index=False)

    print("wrote signals_detailed.csv, setup_rankings.csv, combo_rankings.csv, "
          "flag_effects.csv, stock_findings.csv, sector_findings.csv")


if __name__ == "__main__":
    main()
