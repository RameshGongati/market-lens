"""NR7 (Narrow Range 7) study — research only, no engine changes.

NR7 (Toby Crabel): a day whose high-low RANGE is strictly the narrowest of
the last 7 sessions. The claim is volatility contraction -> expansion: after
a quiet coil the next session tends to move big. The pattern claims NOTHING
about direction; the classic trade brackets the NR7 bar next day (buy stop
above its high, sell stop below its low, opposite band as the stop loss,
exit by the close).

Three test layers, all house rules (no lookahead, costs 0.1%, conservative
ambiguity handling, base-rate comparisons, per-year splits):

  A. Volatility claim (daily, F&O ~4y + NIFTY 10y): is the day after an NR7
     wider-ranged than an average day? Is there any DIRECTION bias (there
     should not be)?
  B. Daily breakout simulation (~4y, F&O): bracket trade approximated on
     daily bars — gap-through entries at the open, both-bands-hit days are
     counted as full losses because daily bars cannot sequence the touches.
  C. Hourly breakout simulation (1y, F&O; the honest intraday test): the
     next day is walked bar by bar on cached 60m data, entering at the first
     band crossed, stop loss at the opposite band, exit at the day's close.
     A single bar that spans BOTH bands is counted as a loss.

Run:  python research_engine/harness/nr7_study.py
Outputs: research_engine/output/nr7_study/*.csv + printed summary.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.harness import datafetch, universe  # noqa: E402

OUT = REPO_ROOT / "research_engine" / "output" / "nr7_study"
GLOBAL_CACHE = REPO_ROOT / "research_engine" / "cache" / "global"

_COST_PCT = 0.001          # 0.1% round trip
_LOOKBACK = 7


def _nr7_mask(df: pd.DataFrame) -> pd.Series:
    """True where the day's range is strictly the narrowest of the last 7."""
    rng = (df["High"] - df["Low"]).astype(float)
    prior_min = rng.shift(1).rolling(_LOOKBACK - 1).min()
    return (rng > 0) & (rng < prior_min)


# --------------------------------------------------------------------------- #
# A. Volatility / direction stats on daily bars
# --------------------------------------------------------------------------- #

def _daily_stats(df: pd.DataFrame) -> dict | None:
    rng = (df["High"] - df["Low"]).astype(float)
    rel_range = (rng / df["Close"].shift(1)).astype(float) * 100
    med20 = rel_range.shift(1).rolling(20).median()
    o2c = (df["Close"] / df["Open"] - 1) * 100
    gap = (df["Open"] / df["Close"].shift(1) - 1) * 100
    nr7 = _nr7_mask(df)
    nxt = nr7.shift(1).fillna(False)          # the day AFTER an NR7
    base = med20.notna() & rel_range.notna()
    if int(nr7.sum()) < 10 or int(base.sum()) < 200:
        return None
    return {
        "days": int(base.sum()),
        "nr7_days": int(nr7.sum()),
        # expansion: next-day relative range vs the trailing 20-day median
        "exp_after_nr7_pct": float((rel_range[nxt & base] > med20[nxt & base]).mean()) * 100,
        "exp_base_pct": float((rel_range[base] > med20[base]).mean()) * 100,
        "relrange_after_nr7": float(rel_range[nxt & base].mean()),
        "relrange_base": float(rel_range[base].mean()),
        "up_after_nr7_pct": float((o2c[nxt] > 0).mean()) * 100,
        "up_base_pct": float((o2c[base] > 0).mean()) * 100,
        "abs_gap_after_nr7": float(gap[nxt].abs().mean()),
        "abs_gap_base": float(gap[base].abs().mean()),
    }


# --------------------------------------------------------------------------- #
# B. Daily-bar bracket simulation (conservative approximation)
# --------------------------------------------------------------------------- #

def _daily_bracket(df: pd.DataFrame, symbol: str) -> list[dict]:
    nr7 = _nr7_mask(df)
    rows: list[dict] = []
    idxs = np.flatnonzero(nr7.values)
    for i in idxs:
        if i + 1 >= len(df):
            continue
        hi = float(df["High"].iloc[i])
        lo = float(df["Low"].iloc[i])
        band = hi - lo
        if band <= 0:
            continue
        nxt = df.iloc[i + 1]
        o, h, low, c = (float(nxt["Open"]), float(nxt["High"]),
                        float(nxt["Low"]), float(nxt["Close"]))
        hit_hi, hit_lo = h > hi, low < lo
        if not hit_hi and not hit_lo:
            continue                                    # no breakout — no trade
        year = int(pd.Timestamp(df.index[i + 1]).year)
        if hit_hi and hit_lo:
            # Daily bars cannot sequence the touches: full bracket loss.
            rows.append({"symbol": symbol, "year": year, "side": "whipsaw",
                         "r": -1.0 - _COST_PCT * hi / band})
            continue
        if hit_hi:
            entry = o if o > hi else hi                 # gap-through opens at open
            r = (c - entry) / band - _COST_PCT * entry / band
            rows.append({"symbol": symbol, "year": year, "side": "long", "r": r})
        else:
            entry = o if o < lo else lo
            r = (entry - c) / band - _COST_PCT * entry / band
            rows.append({"symbol": symbol, "year": year, "side": "short", "r": r})
    return rows


# --------------------------------------------------------------------------- #
# C. Hourly walk — the honest intraday simulation
# --------------------------------------------------------------------------- #

def _hourly_bracket(daily: pd.DataFrame, hourly: pd.DataFrame,
                    symbol: str) -> list[dict]:
    nr7 = _nr7_mask(daily)
    hby = dict(tuple(hourly.groupby(
        pd.to_datetime([t.date() for t in hourly.index]))))
    daily_dates = [pd.Timestamp(t.date()) for t in daily.index]
    rows: list[dict] = []
    for i in np.flatnonzero(nr7.values):
        if i + 1 >= len(daily):
            continue
        hi = float(daily["High"].iloc[i])
        lo = float(daily["Low"].iloc[i])
        band = hi - lo
        if band <= 0:
            continue
        day = daily_dates[i + 1]
        bars = hby.get(day)
        if bars is None or len(bars) < 4:
            continue
        year = int(day.year)
        side, entry, outcome, exit_px = None, None, None, None
        for j, bar in enumerate(bars.itertuples()):
            bo, bh, bl = float(bar.Open), float(bar.High), float(bar.Low)
            if side is None:
                crosses_hi = bh >= hi
                crosses_lo = bl <= lo
                if crosses_hi and crosses_lo:
                    outcome, side = "ambiguous_bar", "whipsaw"
                    rows.append({"symbol": symbol, "year": year, "side": side,
                                 "r": -1.0 - _COST_PCT * hi / band})
                    break
                if crosses_hi:
                    side, entry = "long", (bo if bo > hi else hi)
                elif crosses_lo:
                    side, entry = "short", (bo if bo < lo else lo)
                if side is not None and j == len(bars) - 1:
                    exit_px = float(bar.Close)
                continue
            # position open: stop loss first within each bar (conservative)
            if side == "long" and bl <= lo:
                outcome, exit_px = "stop_loss", lo
                break
            if side == "short" and bh >= hi:
                outcome, exit_px = "stop_loss", hi
                break
            exit_px = float(bar.Close)
        if side in ("long", "short") and entry is not None and exit_px is not None:
            direction = 1 if side == "long" else -1
            r = (exit_px - entry) / band * direction - _COST_PCT * entry / band
            rows.append({"symbol": symbol, "year": year, "side": side, "r": r,
                         "outcome": outcome or "day_close"})
    return rows


def _summarise_trades(trades: pd.DataFrame, label: str) -> None:
    if trades.empty:
        print(f"\n=== {label}: no trades ===")
        return
    print(f"\n=== {label} ===")
    total = len(trades)
    whip = trades[trades["side"] == "whipsaw"]
    print(f"trades={total}  whipsaw(both bands)={len(whip)} "
          f"({len(whip) / total * 100:.1f}%)")
    for side, g in trades.groupby("side"):
        wins = g[g["r"] > 0]["r"].sum()
        losses = -g[g["r"] <= 0]["r"].sum()
        pf = wins / losses if losses > 0 else float("inf")
        print(f"  {side:8s} n={len(g):6d} win={float((g['r'] > 0).mean()) * 100:5.1f}% "
              f"avgR={g['r'].mean():+.3f} PF={pf:.2f}")
    both = trades[trades["side"].isin(["long", "short"])]
    print(f"  combined n={len(both)} avgR={both['r'].mean():+.3f} "
          f"(whipsaws excluded) | incl. whipsaws avgR={trades['r'].mean():+.3f}")
    by_year = trades.groupby("year")["r"].agg(["count", "mean"])
    print(by_year.to_string(float_format=lambda x: f"{x:+.3f}"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    symbols = universe.fno_symbols()

    stats_rows, daily_trades, hourly_trades = [], [], []
    for sym in symbols:
        daily = datafetch.load(sym, "daily")
        if daily is None or len(daily) < 250:
            continue
        s = _daily_stats(daily)
        if s:
            stats_rows.append({"symbol": sym, **s})
        daily_trades.extend(_daily_bracket(daily, sym))
        hourly = datafetch.load(sym, "60m")
        if hourly is not None and len(hourly) > 200:
            hourly_trades.extend(_hourly_bracket(daily, hourly, sym))

    stats = pd.DataFrame(stats_rows)
    dtr = pd.DataFrame(daily_trades)
    htr = pd.DataFrame(hourly_trades)
    stats.to_csv(OUT / "volatility_stats.csv", index=False)
    dtr.to_csv(OUT / "daily_bracket_trades.csv", index=False)
    htr.to_csv(OUT / "hourly_bracket_trades.csv", index=False)

    total_days = stats["days"].sum()
    nr7_days = stats["nr7_days"].sum()
    print(f"Universe: {len(stats)} symbols | {total_days:,} days | "
          f"{nr7_days:,} NR7 days ({nr7_days / total_days * 100:.1f}% of days; "
          f"random baseline 1/7 = 14.3%)")
    w = stats["days"]
    print("\n=== A. Volatility claim (weighted means across symbols) ===")
    for col, base_col, label in [
        ("exp_after_nr7_pct", "exp_base_pct",
         "next-day range > its 20d median  (% of days)"),
        ("relrange_after_nr7", "relrange_base", "next-day range as % of price"),
        ("up_after_nr7_pct", "up_base_pct", "next day closes above open (%)"),
        ("abs_gap_after_nr7", "abs_gap_base", "absolute opening gap (%)"),
    ]:
        after = float((stats[col] * w).sum() / w.sum())
        base = float((stats[base_col] * w).sum() / w.sum())
        print(f"  {label:48s} after NR7 {after:6.2f} | all days {base:6.2f}")

    # NIFTY index, 10y
    nifty_path = GLOBAL_CACHE / "NIFTY50.parquet"
    if nifty_path.exists():
        nifty = pd.read_parquet(nifty_path)
        s = _daily_stats(nifty)
        if s:
            print(f"\nNIFTY 50 (10y): {s['nr7_days']} NR7 days / {s['days']} | "
                  f"expansion {s['exp_after_nr7_pct']:.1f}% vs base "
                  f"{s['exp_base_pct']:.1f}% | next-day up "
                  f"{s['up_after_nr7_pct']:.1f}% vs {s['up_base_pct']:.1f}%")

    _summarise_trades(dtr, "B. Daily-bar bracket (~4y, conservative)")
    _summarise_trades(htr, "C. Hourly walk bracket (1y, honest sequencing)")


if __name__ == "__main__":
    main()
