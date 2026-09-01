"""MA / pivot / VWAP daily-signal study — research only, no engine changes.

Three popular intraday logics adapted to DAILY candles, tested on the F&O
universe over the cached ~4-year daily window. The question asked: when one
of these fires, does the NEXT day move in the signal direction — and do
longer holds or an ATR-based stop-loss/2R simulation show an edge over the
unconditional base rate?

Logics (long and short mirrors; a signal fires only on the TRANSITION into
the state, never on every day the state holds):

  A. ema_cross_9_21   EMA9 crosses above EMA21 (long) / below (short)
  B. ema9_pivot       close moves into "above prior-day classic pivot PP AND
                      above EMA9" (long) / mirror below (short)
  C. ema20_pivot_vwap close above PP AND EMA20 AND VWAP20 (long) / mirror.
                      TRUE session VWAP does not exist on daily bars, so
                      VWAP20 is a rolling 20-session volume-weighted average
                      of typical price — a disclosed PROXY, not real VWAP.

Honesty rules carried over from the main study:
  - no lookahead: signals use data through day i only; entry at day i+1 OPEN
  - events, not states (see above)
  - R simulation: stop loss = 1*ATR14 from entry, target = 2R, time stop 10
    bars, stop-first on both-touched bars (matches the production gap
    tracker's conservative walk), 0.1% round-trip costs
  - base rate measured on ALL eligible days of the SAME frames
  - per-calendar-year splits to expose instability
  - survivorship caveat: today's F&O list applied backwards (same as Run 1)

Run:  python research_engine/harness/ma_pivot_study.py
Outputs: research_engine/output/ma_pivot_study/*.csv + printed summary.
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

OUT = REPO_ROOT / "research_engine" / "output" / "ma_pivot_study"

_WARMUP = 40          # bars before the first eligible signal (EMA/VWAP/ATR valid)
_HORIZONS = (1, 3, 5, 10)
_TIME_STOP = 10       # bars, R simulation
_COST_PCT = 0.001     # 0.1% round trip, applied inside the R multiple


# --------------------------------------------------------------------------- #
# Indicators
# --------------------------------------------------------------------------- #

def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c, h, low, v = out["Close"], out["High"], out["Low"], out["Volume"]
    out["ema9"] = c.ewm(span=9, adjust=False).mean()
    out["ema20"] = c.ewm(span=20, adjust=False).mean()
    out["ema21"] = c.ewm(span=21, adjust=False).mean()
    # Classic floor pivot from the PREVIOUS session, applied to today.
    out["pp"] = ((h + low + c) / 3).shift(1)
    # VWAP proxy: rolling 20-session volume-weighted typical price.
    tp = (h + low + c) / 3
    vol = v.fillna(0.0)
    out["vwap20"] = (tp * vol).rolling(20).sum() / vol.rolling(20).sum().replace(0, np.nan)
    # Wilder ATR14 for the R simulation's stop-loss distance.
    prev_c = c.shift(1)
    tr = pd.concat([h - low, (h - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return out


def _events(state: pd.Series) -> pd.Series:
    """True on the bar where *state* BECOMES true (previous bar validly False)."""
    prev = state.shift(1)
    return state.fillna(False) & (prev == False)  # noqa: E712 — NaN prev must not fire


def _signal_frames(df: pd.DataFrame) -> dict[tuple[str, str], pd.Series]:
    """(logic, direction) -> boolean event series."""
    c = df["Close"]
    long_a = df["ema9"] > df["ema21"]
    long_b = (c > df["pp"]) & (c > df["ema9"])
    long_c = (c > df["pp"]) & (c > df["ema20"]) & (c > df["vwap20"])
    short_a = df["ema9"] < df["ema21"]
    short_b = (c < df["pp"]) & (c < df["ema9"])
    short_c = (c < df["pp"]) & (c < df["ema20"]) & (c < df["vwap20"])
    return {
        ("ema_cross_9_21", "long"): _events(long_a),
        ("ema_cross_9_21", "short"): _events(short_a),
        ("ema9_pivot", "long"): _events(long_b),
        ("ema9_pivot", "short"): _events(short_b),
        ("ema20_pivot_vwap", "long"): _events(long_c),
        ("ema20_pivot_vwap", "short"): _events(short_c),
    }


# --------------------------------------------------------------------------- #
# Outcome measurement
# --------------------------------------------------------------------------- #

def _r_walk(df: pd.DataFrame, i: int, direction: int, entry: float,
            risk: float) -> tuple[str, float] | None:
    """Walk bars after entry; stop-first on both-touched bars. None = unresolved."""
    stop = entry - direction * risk
    target = entry + direction * 2 * risk
    last = min(i + _TIME_STOP, len(df) - 1)
    for j in range(i + 1, last + 1):
        bar_low, bar_high = df["Low"].iloc[j], df["High"].iloc[j]
        if direction > 0:
            if bar_low <= stop:
                return "stop_loss", -1.0 - _COST_PCT * entry / risk
            if bar_high >= target:
                return "target", 2.0 - _COST_PCT * entry / risk
        else:
            if bar_high >= stop:
                return "stop_loss", -1.0 - _COST_PCT * entry / risk
            if bar_low <= target:
                return "target", 2.0 - _COST_PCT * entry / risk
    if last < i + _TIME_STOP:
        return None  # series ended before the time stop — unresolved
    exit_px = float(df["Close"].iloc[last])
    r = (exit_px - entry) / risk * direction - _COST_PCT * entry / risk
    return "time_stop", r


def _measure(df: pd.DataFrame, symbol: str) -> tuple[list[dict], list[dict]]:
    """(signal event rows, base-rate day rows) for one symbol."""
    df = _indicators(df)
    opens, closes = df["Open"], df["Close"]
    n = len(df)
    signals: list[dict] = []
    for (logic, direction_name), ev in _signal_frames(df).items():
        direction = 1 if direction_name == "long" else -1
        for i in np.flatnonzero(ev.values):
            if i < _WARMUP or i + 1 >= n:
                continue
            risk = float(df["atr14"].iloc[i])
            if not np.isfinite(risk) or risk <= 0:
                continue
            entry = float(opens.iloc[i + 1])
            if not np.isfinite(entry) or entry <= 0:
                continue
            row = {
                "symbol": symbol, "logic": logic, "direction": direction_name,
                "signal_date": df.index[i],
                "year": int(pd.Timestamp(df.index[i]).year),
            }
            for k in _HORIZONS:
                if i + k < n:
                    row[f"fwd{k}"] = (float(closes.iloc[i + k]) - entry) / entry * 100 * direction
                else:
                    row[f"fwd{k}"] = np.nan
            walked = _r_walk(df, i, direction, entry, risk)
            if walked is not None:
                row["outcome"], row["r_multiple"] = walked
            else:
                row["outcome"], row["r_multiple"] = "unresolved", np.nan
            signals.append(row)

    base: list[dict] = []
    for i in range(_WARMUP, n - 1):
        entry = float(opens.iloc[i + 1])
        if not np.isfinite(entry) or entry <= 0:
            continue
        row = {"symbol": symbol, "year": int(pd.Timestamp(df.index[i]).year)}
        for k in _HORIZONS:
            row[f"fwd{k}"] = (
                (float(closes.iloc[i + k]) - entry) / entry * 100
                if i + k < n else np.nan
            )
        base.append(row)
    return signals, base


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def _fmt_pct(x: float) -> str:
    return f"{x:5.1f}%"


def _summarise(sig: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base_up = {k: float((base[f"fwd{k}"] > 0).mean()) * 100 for k in _HORIZONS}
    base_mean = {k: float(base[f"fwd{k}"].mean()) for k in _HORIZONS}
    for (logic, direction), g in sig.groupby(["logic", "direction"]):
        resolved = g[g["outcome"] != "unresolved"]
        # A short's base "as expected" rate is the chance of a DOWN day.
        b1 = base_up[1] if direction == "long" else 100 - base_up[1]
        row = {
            "logic": logic, "direction": direction, "signals": len(g),
            "win1d_pct": float((g["fwd1"] > 0).mean()) * 100,
            "base1d_pct": b1,
            "uplift1d_pp": float((g["fwd1"] > 0).mean()) * 100 - b1,
            "mean_fwd1_pct": float(g["fwd1"].mean()),
            "mean_fwd3_pct": float(g["fwd3"].mean()),
            "mean_fwd5_pct": float(g["fwd5"].mean()),
            "mean_fwd10_pct": float(g["fwd10"].mean()),
            "rsim_n": len(resolved),
            "rsim_target_pct": float((resolved["outcome"] == "target").mean()) * 100,
            "rsim_stop_pct": float((resolved["outcome"] == "stop_loss").mean()) * 100,
            "rsim_expectancy_R": float(resolved["r_multiple"].mean()),
        }
        wins = resolved.loc[resolved["r_multiple"] > 0, "r_multiple"].sum()
        losses = -resolved.loc[resolved["r_multiple"] <= 0, "r_multiple"].sum()
        row["rsim_profit_factor"] = float(wins / losses) if losses > 0 else np.inf
        rows.append(row)
    out = pd.DataFrame(rows).sort_values(["logic", "direction"])
    out.attrs["base_mean"] = base_mean
    return out


def _yearly(sig: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base_by_year = {
        (year, "long"): float((g["fwd1"] > 0).mean()) * 100
        for year, g in base.groupby("year")
    }
    for (logic, direction, year), g in sig.groupby(["logic", "direction", "year"]):
        b = base_by_year.get((year, "long"), np.nan)
        b1 = b if direction == "long" else 100 - b
        resolved = g[g["outcome"] != "unresolved"]
        rows.append({
            "logic": logic, "direction": direction, "year": year, "signals": len(g),
            "win1d_pct": float((g["fwd1"] > 0).mean()) * 100,
            "base1d_pct": b1,
            "uplift1d_pp": float((g["fwd1"] > 0).mean()) * 100 - b1,
            "expectancy_R": float(resolved["r_multiple"].mean()) if len(resolved) else np.nan,
        })
    return pd.DataFrame(rows).sort_values(["logic", "direction", "year"])


def main() -> None:
    symbols = universe.fno_symbols()
    datafetch.fetch_all(symbols, "daily")   # fills only symbols missing from cache

    all_signals: list[dict] = []
    all_base: list[dict] = []
    used, skipped = 0, 0
    span_lo, span_hi = None, None
    for sym in symbols:
        df = datafetch.load(sym, "daily")
        if df is None or len(df) < _WARMUP + 30:
            skipped += 1
            continue
        used += 1
        span_lo = min(span_lo, df.index[0]) if span_lo is not None else df.index[0]
        span_hi = max(span_hi, df.index[-1]) if span_hi is not None else df.index[-1]
        s, b = _measure(df, sym)
        all_signals.extend(s)
        all_base.extend(b)

    sig = pd.DataFrame(all_signals)
    base = pd.DataFrame(all_base)
    OUT.mkdir(parents=True, exist_ok=True)
    sig.to_csv(OUT / "signals_detailed.csv", index=False)
    summary = _summarise(sig, base)
    summary.to_csv(OUT / "summary.csv", index=False)
    yearly = _yearly(sig, base)
    yearly.to_csv(OUT / "yearly.csv", index=False)

    print(f"\nUniverse: {used} symbols with data ({skipped} skipped)")
    print(f"Window:   {span_lo.date()} .. {span_hi.date()}")
    print(f"Base-rate days: {len(base):,} | next-day up rate "
          f"{_fmt_pct(float((base['fwd1'] > 0).mean()) * 100)} | "
          f"mean next-day move {base['fwd1'].mean():+.3f}%")
    pd.set_option("display.width", 200)
    print("\n=== Summary (all years pooled) ===")
    print(summary.to_string(index=False, float_format=lambda x: f"{x:8.2f}"))
    print("\n=== Per-year 1-day results ===")
    print(yearly.to_string(index=False, float_format=lambda x: f"{x:8.2f}"))


if __name__ == "__main__":
    main()
