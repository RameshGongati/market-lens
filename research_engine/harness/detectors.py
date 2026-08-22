"""Signal detectors for the F&O backtest study.

Every detector returns Signal records anchored at a SIGNAL BAR i such that all
information used is available at the close of bar i (no look-ahead). Entry is
simulated at bar i+1's open by the simulator, except `entry_price` overrides
(zone limit-touch entries).

Directions: +1 long, -1 short.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.zone_engine import patterns as _zone_patterns  # noqa: E402
from analysis.zone_engine.patterns import detect_zones  # noqa: E402
from research_engine.harness.indicators import fib_levels_at  # noqa: E402

# --- survivorship-bias fix (BACKTEST ONLY, quarantined) ----------------------
# detect_zones() discards zones whose FUTURE price action invalidates them
# (M46 forward scan). Correct for the live app, fatal for a backtest: it
# leaves only zones whose stops were never hit. During research runs
# score_zone is patched to keep every structurally valid zone; the
# point-in-time walk below re-derives touches/invalidation/habitation
# without future data.
#
# The patch is NOT applied at import time. Harness runner entry points call
# enable_backtest_mode() explicitly; nothing in the live app or UI may do so.
# The zone detectors below refuse to run without it, because unpatched
# results would be silently survivorship-biased.
_orig_score_zone = _zone_patterns.score_zone
_BACKTEST_MODE = False


def _score_zone_keep_all(*args, **kwargs):
    s = _orig_score_zone(*args, **kwargs)
    s["is_invalidated"] = False  # ZoneScore is a TypedDict (plain dict at runtime)
    return s


def enable_backtest_mode() -> None:
    """Patch the zone engine for walk-forward research. Harness runners only."""
    global _BACKTEST_MODE
    if not _BACKTEST_MODE:
        _zone_patterns.score_zone = _score_zone_keep_all
        _BACKTEST_MODE = True


def disable_backtest_mode() -> None:
    """Restore the live zone engine behaviour (used by tests)."""
    global _BACKTEST_MODE
    _zone_patterns.score_zone = _orig_score_zone
    _BACKTEST_MODE = False


def _require_backtest_mode() -> None:
    if not _BACKTEST_MODE:
        raise RuntimeError(
            "research_engine.harness zone detectors need enable_backtest_mode(); "
            "without it results are survivorship-biased. Never enable in app/UI paths."
        )
# ---------------------------------------------------------------------------

ATR_BUFFER = 0.1        # M7-spirit stop buffer, fraction of ATR(14)
BREAKOUT_ATR_STOP = 1.0  # pattern-breakout stop distance below/above the level


@dataclass
class Signal:
    i: int                 # signal bar (positional)
    setup: str
    direction: int         # +1 long, -1 short
    stop: float
    entry_price: float | None = None   # None -> next bar open
    entry_at_signal_bar: bool = False  # zone touch entries fill on the signal bar
    tags: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Candlestick setups
# --------------------------------------------------------------------------- #

def candlestick_signals(df: pd.DataFrame) -> list[Signal]:
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    body = np.abs(c - o)
    rng = np.maximum(h - l, 1e-9)
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    bull = c > o
    bear = c < o
    body_pct = body / rng
    sigs: list[Signal] = []
    n = len(df)

    for i in range(30, n):
        buf = ATR_BUFFER * atr[i]
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        # Engulfing
        if bear[i - 1] and bull[i] and o[i] <= c[i - 1] and c[i] >= o[i - 1] and body[i] > body[i - 1]:
            sigs.append(Signal(i, "bullish_engulfing", 1, min(l[i], l[i - 1]) - buf))
        if bull[i - 1] and bear[i] and o[i] >= c[i - 1] and c[i] <= o[i - 1] and body[i] > body[i - 1]:
            sigs.append(Signal(i, "bearish_engulfing", -1, max(h[i], h[i - 1]) + buf))
        # Hammer / shooting star / inverted hammer (3-bar prior move as context)
        decline = c[i - 1] < c[i - 4] if i >= 4 else False
        rise = c[i - 1] > c[i - 4] if i >= 4 else False
        if decline and lower[i] >= 2 * body[i] and upper[i] <= 0.35 * rng[i] and c[i] >= l[i] + 0.6 * rng[i]:
            sigs.append(Signal(i, "hammer", 1, l[i] - buf))
        if rise and upper[i] >= 2 * body[i] and lower[i] <= 0.35 * rng[i] and c[i] <= l[i] + 0.4 * rng[i]:
            sigs.append(Signal(i, "shooting_star", -1, h[i] + buf))
        if decline and upper[i] >= 2 * body[i] and lower[i] <= 0.35 * rng[i]:
            sigs.append(Signal(i, "inverted_hammer", 1, l[i] - buf))
        # Morning / evening star
        if i >= 2:
            mid_prev = (o[i - 2] + c[i - 2]) / 2
            small_mid = body_pct[i - 1] < 0.3
            if bear[i - 2] and body_pct[i - 2] >= 0.5 and small_mid and bull[i] and c[i] > mid_prev:
                sigs.append(Signal(i, "morning_star", 1, min(l[i - 1], l[i]) - buf))
            if bull[i - 2] and body_pct[i - 2] >= 0.5 and small_mid and bear[i] and c[i] < mid_prev:
                sigs.append(Signal(i, "evening_star", -1, max(h[i - 1], h[i]) + buf))
        # Inside bar breakout (mother bar at i-2, inside bar at i-1)
        if i >= 2 and h[i - 1] < h[i - 2] and l[i - 1] > l[i - 2]:
            if c[i] > h[i - 2]:
                sigs.append(Signal(i, "inside_bar_breakout", 1, l[i - 1] - buf))
            elif c[i] < l[i - 2]:
                sigs.append(Signal(i, "inside_bar_breakdown", -1, h[i - 1] + buf))
        # NR7 breakout
        if i >= 7 and rng[i - 1] == rng[i - 7 : i].min():
            if c[i] > h[i - 1]:
                sigs.append(Signal(i, "nr7_breakout", 1, l[i - 1] - buf))
            elif c[i] < l[i - 1]:
                sigs.append(Signal(i, "nr7_breakdown", -1, h[i - 1] + buf))
        # Strong (M5) exciting continuation candle
        if bool(df["strong"].iat[i]):
            if bull[i]:
                sigs.append(Signal(i, "strong_bull_candle", 1, l[i] - buf))
            elif bear[i]:
                sigs.append(Signal(i, "strong_bear_candle", -1, h[i] + buf))
    return sigs


# --------------------------------------------------------------------------- #
# Indicator setups
# --------------------------------------------------------------------------- #

def indicator_signals(df: pd.DataFrame, tf: str) -> list[Signal]:
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    ema20 = df["ema20"].to_numpy(float)
    sma50 = df["sma50"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    stoch = df["stoch_k"].to_numpy(float)
    macd = df["macd"].to_numpy(float)
    macd_sig = df["macd_sig"].to_numpy(float)
    bb_up = df["bb_up"].to_numpy(float)
    bb_lo = df["bb_lo"].to_numpy(float)
    squeeze = df["bb_squeeze"].to_numpy(bool)
    slope_up = df["ema20_slope_up"].to_numpy(bool)
    bull = c > o
    bear = c < o
    n = len(df)
    sigs: list[Signal] = []

    sw_hi_conf = df["swing_high_conf"].to_numpy(bool)
    sw_lo_conf = df["swing_low_conf"].to_numpy(bool)
    ORDER = 3  # matches indicators.SWING_ORDER

    conf_lows: list[int] = []   # positional indices of confirmed swing-low BARS
    conf_highs: list[int] = []

    pdh = df["pdh"].to_numpy(float) if "pdh" in df.columns else None
    pdl = df["pdl"].to_numpy(float) if "pdl" in df.columns else None
    pdh_done_day = None
    pdl_done_day = None
    days = df.index.tz_convert("Asia/Kolkata").date if getattr(df.index, "tz", None) is not None else None

    for i in range(60, n):
        buf = ATR_BUFFER * atr[i]
        if not np.isfinite(atr[i]) or atr[i] <= 0 or not np.isfinite(sma50[i]):
            continue
        # EMA20 bounce / rejection
        if c[i] > sma50[i] and slope_up[i] and l[i] <= ema20[i] * 1.002 and c[i] > ema20[i] and bull[i]:
            sigs.append(Signal(i, "ema20_bounce", 1, min(l[i], ema20[i]) - buf))
        if c[i] < sma50[i] and not slope_up[i] and h[i] >= ema20[i] * 0.998 and c[i] < ema20[i] and bear[i]:
            sigs.append(Signal(i, "ema20_rejection", -1, max(h[i], ema20[i]) + buf))
        # SMA50 pullback resume
        if c[i - 1] <= sma50[i - 1] * 1.01 and c[i] > sma50[i] and bull[i] and c[i - 5] > sma50[i - 5]:
            sigs.append(Signal(i, "sma50_pullback_long", 1, l[i] - buf))
        if c[i - 1] >= sma50[i - 1] * 0.99 and c[i] < sma50[i] and bear[i] and c[i - 5] < sma50[i - 5]:
            sigs.append(Signal(i, "sma50_pullback_short", -1, h[i] + buf))
        # MACD cross
        if macd[i - 1] <= macd_sig[i - 1] and macd[i] > macd_sig[i] and macd[i] < 0:
            sigs.append(Signal(i, "macd_cross_long", 1, min(l[i - 3 : i + 1]) - buf))
        if macd[i - 1] >= macd_sig[i - 1] and macd[i] < macd_sig[i] and macd[i] > 0:
            sigs.append(Signal(i, "macd_cross_short", -1, max(h[i - 3 : i + 1]) + buf))
        # Bollinger squeeze breakout
        if squeeze[i - 1] and c[i] > bb_up[i]:
            sigs.append(Signal(i, "bb_squeeze_breakout", 1, min(l[i - 5 : i + 1]) - buf))
        if squeeze[i - 1] and c[i] < bb_lo[i]:
            sigs.append(Signal(i, "bb_squeeze_breakdown", -1, max(h[i - 5 : i + 1]) + buf))
        # Gaps (>= 1.3%, the app's gap threshold)
        if o[i] > h[i - 1] * 1.013 and c[i] >= o[i]:
            sigs.append(Signal(i, "gap_up_go", 1, l[i - 1] - buf, tags={"gap_pct": (o[i] / h[i - 1] - 1) * 100}))
        if o[i] < l[i - 1] * 0.987 and c[i] <= o[i]:
            sigs.append(Signal(i, "gap_down_go", -1, h[i - 1] + buf, tags={"gap_pct": (1 - o[i] / l[i - 1]) * 100}))

        # Swing bookkeeping + divergences + structure breaks
        if sw_lo_conf[i]:
            j = i - ORDER
            if conf_lows:
                j2 = conf_lows[-1]
                if l[j] < l[j2]:
                    if np.isfinite(rsi[j]) and np.isfinite(rsi[j2]) and rsi[j] > rsi[j2] + 2:
                        sigs.append(Signal(i, "rsi_bull_divergence", 1, l[j] - buf))
                    if np.isfinite(stoch[j]) and np.isfinite(stoch[j2]) and stoch[j] > stoch[j2] + 5 and stoch[j2] < 25:
                        sigs.append(Signal(i, "stoch_bull_divergence", 1, l[j] - buf))
            conf_lows.append(j)
        if sw_hi_conf[i]:
            j = i - ORDER
            if conf_highs:
                j2 = conf_highs[-1]
                if h[j] > h[j2]:
                    if np.isfinite(rsi[j]) and np.isfinite(rsi[j2]) and rsi[j] < rsi[j2] - 2:
                        sigs.append(Signal(i, "rsi_bear_divergence", -1, h[j] + buf))
                    if np.isfinite(stoch[j]) and np.isfinite(stoch[j2]) and stoch[j] < stoch[j2] - 5 and stoch[j2] > 75:
                        sigs.append(Signal(i, "stoch_bear_divergence", -1, h[j] + buf))
            conf_highs.append(j)
        # HH/HL continuation: structure up + close breaks last confirmed swing high
        if len(conf_lows) >= 2 and len(conf_highs) >= 2:
            hh = h[conf_highs[-1]] > h[conf_highs[-2]]
            hl = l[conf_lows[-1]] > l[conf_lows[-2]]
            lh = h[conf_highs[-1]] < h[conf_highs[-2]]
            ll_ = l[conf_lows[-1]] < l[conf_lows[-2]]
            level_up = h[conf_highs[-1]]
            level_dn = l[conf_lows[-1]]
            if hh and hl and c[i - 1] <= level_up < c[i]:
                sigs.append(Signal(i, "hh_hl_continuation", 1, l[conf_lows[-1]] - buf))
            if lh and ll_ and c[i - 1] >= level_dn > c[i]:
                sigs.append(Signal(i, "lh_ll_continuation", -1, h[conf_highs[-1]] + buf))
        # Previous-day high/low (intraday only), first break of the day
        if pdh is not None and days is not None and np.isfinite(pdh[i]):
            d = days[i]
            if c[i - 1] <= pdh[i] < c[i] and pdh_done_day != d:
                pdh_done_day = d
                sigs.append(Signal(i, "pdh_breakout", 1, min(l[i - 3 : i + 1]) - buf))
            if c[i - 1] >= pdl[i] > c[i] and pdl_done_day != d:
                pdl_done_day = d
                sigs.append(Signal(i, "pdl_breakdown", -1, max(h[i - 3 : i + 1]) + buf))

    # Fibonacci pullback bounce (checked on a stride to bound cost)
    for i in range(120, n, 1):
        buf = ATR_BUFFER * atr[i]
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        # only when bar looks like a reversal candle at a retracement of an up-swing
        if not (bull[i] and l[i] < ema20[i]):
            pass
        levels = None
        if bull[i] and c[i] > o[i]:
            levels = fib_levels_at(df, i - 1)
            if levels:
                # up-swing retracement support: price low near a level, closes above it
                for ratio in (0.382, 0.5, 0.618):
                    lv = levels.get(ratio)
                    if lv and lv < c[i] and abs(l[i] - lv) / lv <= 0.01 and c[i - 3] > c[i - 1]:
                        sigs.append(Signal(i, "fib_pullback_long", 1, l[i] - buf, tags={"fib": ratio}))
                        break
        if bear[i] and levels is None:
            levels = fib_levels_at(df, i - 1)
        if bear[i] and levels:
            for ratio in (0.382, 0.5, 0.618):
                lv = levels.get(ratio)
                if lv and lv > c[i] and abs(h[i] - lv) / lv <= 0.01 and c[i - 3] < c[i - 1]:
                    sigs.append(Signal(i, "fib_pullback_short", -1, h[i] + buf, tags={"fib": ratio}))
                    break
    return sigs


# --------------------------------------------------------------------------- #
# Demand/Supply zone setups (Market Lens zone engine, point-in-time)
# --------------------------------------------------------------------------- #

def _legout_end(df: pd.DataFrame, zone) -> int:
    """Re-derive the extended legout end (created_at_index = legout start)."""
    exciting = df["exciting"].to_numpy(bool)
    bull = (df["Close"] > df["Open"]).to_numpy(bool)
    i = zone.created_at_index
    want_bull = zone.category == "demand"
    end = i
    for k in range(i + 1, min(i + 7, len(df))):
        if exciting[k] and (bull[k] == want_bull):
            end = k
        else:
            break
    return end


def zone_signals(df: pd.DataFrame) -> list[Signal]:
    """demand_bounce / supply_rejection (confirmation close) and
    zone_touch entries (limit at proximal), tracked point-in-time."""
    _require_backtest_mode()
    try:
        zones = detect_zones(df[["Open", "High", "Low", "Close"]])
    except Exception:
        return []
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    n = len(df)
    sigs: list[Signal] = []

    for z in zones:
        start = _legout_end(df, z) + 1
        if start >= n:
            continue
        prox, dist = z.proximal, z.distal
        demand = z.category == "demand"
        buf_ref = atr[min(start, n - 1)]
        touches = 0
        inside = False
        inside_closes = 0
        touched_once = False
        for i in range(start, n):
            buf = ATR_BUFFER * (atr[i] if np.isfinite(atr[i]) else buf_ref)
            breached = l[i] < dist if demand else h[i] > dist
            if breached:
                break  # M46: zone dead
            in_zone = l[i] <= prox if demand else h[i] >= prox
            closed_out = c[i] > prox if demand else c[i] < prox
            if in_zone and not touched_once:
                touched_once = True
                # GTF Type-1 style: limit order at the proximal on first touch
                if touches == 0:
                    sigs.append(Signal(
                        i, "zone_touch_fresh", 1 if demand else -1,
                        dist - buf if demand else dist + buf,
                        entry_price=prox, entry_at_signal_bar=True,
                        tags={"odd_strength": z.strength_points, "odd_time": z.time_points,
                              "zone_type": z.zone_type, "num_base": z.num_base_candles},
                    ))
            if in_zone:
                inside = True
            if inside and closed_out:
                # completed test -> confirmation-close bounce signal
                if touches <= 1:
                    name = "demand_bounce" if demand else "supply_rejection"
                    sigs.append(Signal(
                        i, name, 1 if demand else -1,
                        dist - buf if demand else dist + buf,
                        tags={"touches_before": touches, "zone_type": z.zone_type,
                              "odd_strength": z.strength_points, "odd_time": z.time_points},
                    ))
                touches += 1
                inside = False
                inside_closes = 0
                if touches >= 3:
                    break
            elif inside:
                inside_closes += 1
                if inside_closes >= 4:
                    break  # habitation: imbalance exhausted
    return sigs


def zone_context_v2(df: pd.DataFrame) -> dict[str, np.ndarray]:
    """Per-bar point-in-time zone state.

    Returns arrays (len n):
      near_demand / near_supply        — inside/within 2% of a LIVE (<=1 test) zone
      stale_demand / stale_supply      — inside/within 2% of a 2+-tested, unbroken zone
      demand_broken_recent / supply_broken_recent — an M46 breach happened <=10 bars ago
      dist_demand_pct                  — % down to nearest live demand proximal (nan if none)
      dist_supply_pct                  — % up to nearest live supply proximal (nan if none)
    A zone contributes only after its legout completes; it leaves the live set on
    breach (M46), habitation (4 closes inside) or its 2nd completed test (stale set).
    """
    _require_backtest_mode()
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    n = len(df)
    out = {
        "near_demand": np.zeros(n, bool), "near_supply": np.zeros(n, bool),
        "stale_demand": np.zeros(n, bool), "stale_supply": np.zeros(n, bool),
        "demand_broken_recent": np.zeros(n, bool), "supply_broken_recent": np.zeros(n, bool),
        "dist_demand_pct": np.full(n, np.nan), "dist_supply_pct": np.full(n, np.nan),
    }
    try:
        zones = detect_zones(df[["Open", "High", "Low", "Close"]])
    except Exception:
        return out
    for z in zones:
        start = _legout_end(df, z) + 1
        prox, dist = z.proximal, z.distal
        demand = z.category == "demand"
        touches = 0
        inside = False
        inside_closes = 0
        for i in range(start, n):
            breached = l[i] < dist if demand else h[i] > dist
            if breached:
                key = "demand_broken_recent" if demand else "supply_broken_recent"
                out[key][i : min(n, i + 11)] = True
                break
            live = touches <= 1
            if demand and c[i] >= prox:
                d = (c[i] - prox) / c[i] * 100
                if live and (np.isnan(out["dist_demand_pct"][i]) or d < out["dist_demand_pct"][i]):
                    out["dist_demand_pct"][i] = d
            if not demand and c[i] <= prox:
                d = (prox - c[i]) / c[i] * 100
                if live and (np.isnan(out["dist_supply_pct"][i]) or d < out["dist_supply_pct"][i]):
                    out["dist_supply_pct"][i] = d
            near = (demand and dist <= c[i] <= prox * 1.02) or (not demand and prox * 0.98 <= c[i] <= dist)
            if near:
                if live:
                    out["near_demand" if demand else "near_supply"][i] = True
                else:
                    out["stale_demand" if demand else "stale_supply"][i] = True
            in_zone = l[i] <= prox if demand else h[i] >= prox
            closed_out = c[i] > prox if demand else c[i] < prox
            if in_zone:
                inside = True
            if inside and closed_out:
                touches += 1
                inside = False
                inside_closes = 0
                if touches >= 3:
                    break
            elif inside:
                inside_closes += 1
                if inside_closes >= 4:
                    break
    return out


def active_zone_context(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Back-compat wrapper over zone_context_v2."""
    ctx = zone_context_v2(df)
    return ctx["near_demand"], ctx["near_supply"]


# --------------------------------------------------------------------------- #
# Chart pattern setups (Market Lens detectors, walk-forward with a break gate)
# --------------------------------------------------------------------------- #

_PATTERN_SETUPS = {
    "Symmetrical Triangle": ("triangle_sym_break", None),
    "Ascending Triangle": ("ascending_triangle_break", 1),
    "Descending Triangle": ("descending_triangle_break", -1),
    "VCP / Tight Base": ("vcp_breakout", 1),
    "Bullish Rectangle Breakout": ("range_breakout", 1),
    "Bearish Rectangle Breakdown": ("range_breakdown", -1),
    "Bull Flag": ("bull_flag_break", 1),
    "Bear Flag": ("bear_flag_break", -1),
    "Bull Pennant": ("bull_pennant_break", 1),
    "Bear Pennant": ("bear_pennant_break", -1),
    "Double Bottom": ("double_bottom_break", 1),
    "Double Top": ("double_top_break", -1),
}


def pattern_signals(df: pd.DataFrame, tf_label: str) -> list[Signal]:
    from analysis.pattern_detectors.triangles import detect_triangle_patterns
    from analysis.pattern_detectors.vcp import detect_vcp_patterns
    from analysis.pattern_detectors.range_breakouts import detect_range_breakout_patterns
    from analysis.pattern_detectors.flag_pennant import detect_flag_pennant_patterns
    from analysis.pattern_detectors.double_patterns import detect_double_patterns

    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    n = len(df)
    sigs: list[Signal] = []
    last_fire: dict[tuple, int] = {}

    detectors = [
        lambda w: detect_triangle_patterns(w, symbol="X", company_name="", exchange="NSE", timeframe=tf_label),
        lambda w: detect_vcp_patterns(w, symbol="X", company_name="", exchange="NSE", timeframe=tf_label),
        lambda w: detect_range_breakout_patterns(w, symbol="X", company_name="", exchange="NSE", timeframe=tf_label),
        lambda w: detect_flag_pennant_patterns(w, symbol="X", company_name="", exchange="NSE", timeframe=tf_label),
        lambda w: detect_double_patterns(w, symbol="X", company_name="", exchange="NSE", timeframe=tf_label),
    ]

    intraday = tf_label in ("60m", "75m", "15m")
    gate_w = 20 if intraday else 10   # intraday makes 10-bar extremes constantly
    stride = 2 if intraday else 1    # freshness<=2 tolerance makes stride-2 lossless
    last_eval = -99
    for i in range(60, n):
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if i - last_eval < stride:
            continue
        # gate: fresh N-bar extreme close, else no breakout could have confirmed now
        up_gate = c[i] > h[max(0, i - gate_w) : i].max()
        dn_gate = c[i] < l[max(0, i - gate_w) : i].min()
        if not (up_gate or dn_gate):
            continue
        last_eval = i
        window = df.iloc[max(0, i - 159) : i + 1]
        for det in detectors:
            try:
                matches = det(window)
            except Exception:
                continue
            for m in matches:
                if m.stage != "Breakout Confirmed" or m.freshness_candles > 2:
                    continue
                mapped = _PATTERN_SETUPS.get(m.pattern_type)
                if not mapped:
                    continue
                name, fixed_dir = mapped
                if m.breakout_bias == "Up Break":
                    direction = 1
                elif m.breakout_bias == "Down Break":
                    direction = -1
                else:
                    direction = fixed_dir or 0
                if direction == 0 or (fixed_dir and direction != fixed_dir and m.pattern_family != "Triangle Patterns"):
                    continue
                if name == "triangle_sym_break":
                    name = "triangle_sym_breakout" if direction == 1 else "triangle_sym_breakdown"
                key = (name, direction)
                if key in last_fire and i - last_fire[key] < 10:
                    continue
                last_fire[key] = i
                level = m.breakout_level if direction == 1 else m.breakdown_level
                if not level or not np.isfinite(level):
                    level = c[i]
                stop = level - BREAKOUT_ATR_STOP * atr[i] if direction == 1 else level + BREAKOUT_ATR_STOP * atr[i]
                sigs.append(Signal(i, name, direction, stop,
                                   tags={"confidence": m.confidence_score,
                                         "volume_contraction": bool(m.volume_contraction),
                                         "breakout_level": level, "is_breakout": True}))
    return sigs
