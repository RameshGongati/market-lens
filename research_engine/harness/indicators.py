"""Point-in-time indicator + context features.

Every column is safe to read on the bar it is indexed at — anything using a
future value would be look-ahead. Swing flags therefore CONFIRM `order` bars
late (a swing high at bar i is only knowable at bar i+order), and the
`*_conf` columns are indexed at the confirmation bar, not the swing bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SWING_ORDER = 3


def compute(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    c, h, l, v = out["Close"], out["High"], out["Low"], out["Volume"]

    out["ema20"] = c.ewm(span=20, adjust=False).mean()
    out["sma50"] = c.rolling(50).mean()
    out["sma200"] = c.rolling(200).mean()

    # RSI(14) — Wilder
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi"] = 100 - 100 / (1 + rs)

    # Stochastic %K(14) smoothed 3, %D 3
    ll, hh = l.rolling(14).min(), h.rolling(14).max()
    k_raw = 100 * (c - ll) / (hh - ll).replace(0, np.nan)
    out["stoch_k"] = k_raw.rolling(3).mean()
    out["stoch_d"] = out["stoch_k"].rolling(3).mean()

    # MACD 12/26/9
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    out["macd"] = ema12 - ema26
    out["macd_sig"] = out["macd"].ewm(span=9, adjust=False).mean()

    # Bollinger 20/2 + bandwidth squeeze percentile
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    out["bb_up"], out["bb_lo"] = mid + 2 * sd, mid - 2 * sd
    bw = (out["bb_up"] - out["bb_lo"]) / mid
    out["bb_bw"] = bw
    out["bb_squeeze"] = bw <= bw.rolling(120, min_periods=60).quantile(0.2)

    # ATR(14) — Wilder
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()

    out["vol_ma20"] = v.rolling(20).mean()
    out["vol_exp"] = v >= 1.5 * out["vol_ma20"]
    out["vol_con"] = v.rolling(5).mean() <= 0.8 * out["vol_ma20"]

    # Candle anatomy (Market Lens M5 vocabulary)
    body = (c - out["Open"]).abs()
    rng = (h - l).replace(0, np.nan)
    out["body_pct"] = (body / rng).fillna(0.0)
    out["body_pct_price"] = body / c
    out["exciting"] = (out["body_pct"] >= 0.50) & (out["body_pct_price"] >= 0.013)
    out["strong"] = out["exciting"] & (out["body_pct"] >= 0.80)
    out["bull"] = c > out["Open"]
    out["bear"] = c < out["Open"]

    # Swings, confirmed `order` bars late.
    o = SWING_ORDER
    sh = (h == h.rolling(2 * o + 1, center=True).max())
    sl = (l == l.rolling(2 * o + 1, center=True).min())
    out["swing_high_conf"] = sh.shift(o).fillna(False)   # at bar i: bar i-o was a swing high
    out["swing_low_conf"] = sl.shift(o).fillna(False)

    # Prior-day levels for intraday frames (index must be tz-aware datetimes)
    if isinstance(out.index, pd.DatetimeIndex) and out.index.tz is not None:
        dates = out.index.tz_convert("Asia/Kolkata").date
        day = pd.Series(dates, index=out.index)
        dh = h.groupby(day).cummax()
        dl = l.groupby(day).cummin()
        day_high = h.groupby(day).max()
        day_low = l.groupby(day).min()
        uniq = pd.Series(day.unique())
        prev_map_h = dict(zip(uniq[1:], day_high[uniq[:-1]].values))
        prev_map_l = dict(zip(uniq[1:], day_low[uniq[:-1]].values))
        out["pdh"] = day.map(prev_map_h)
        out["pdl"] = day.map(prev_map_l)
        out["intraday_high"] = dh
        out["intraday_low"] = dl

    # Trend structure: higher-highs/higher-lows over confirmed swings
    out["close_gt_sma50"] = c > out["sma50"]
    out["close_gt_sma200"] = c > out["sma200"]
    out["ema20_slope_up"] = out["ema20"] > out["ema20"].shift(3)
    return out


def fib_levels_at(df: pd.DataFrame, i: int, lookback: int = 120) -> dict[float, float]:
    """Fibonacci retracement levels of the last `lookback` bars ENDING AT i (inclusive)."""
    lo = max(0, i - lookback + 1)
    win_h = df["High"].iloc[lo : i + 1]
    win_l = df["Low"].iloc[lo : i + 1]
    hi_i, lo_i = win_h.idxmax(), win_l.idxmin()
    swing_high, swing_low = float(win_h.max()), float(win_l.min())
    rng = swing_high - swing_low
    if rng <= 0 or not np.isfinite(rng):
        return {}
    up = win_l.index.get_loc(lo_i) < win_h.index.get_loc(hi_i)
    out = {}
    for ratio in (0.382, 0.5, 0.618, 0.786):
        out[ratio] = swing_high - rng * ratio if up else swing_low + rng * ratio
    return out
