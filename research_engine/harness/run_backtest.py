"""Orchestrator v2: universe x timeframe -> signals -> trades (enriched) -> parquet.

Usage: python research_engine/harness/run_backtest.py <tf> <scope> [a:b]
Adds engine-research features to every trade row: rich zone state, extension,
institutional PROXIES (OBV/AD/VWAP/volume — labelled proxies, not confirmed
flows), relative strength, market/sector extension, earnings joins and
news-proxy event flags. All point-in-time.
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.harness import datafetch, universe  # noqa: E402
from research_engine.harness.detectors import (  # noqa: E402
    candlestick_signals, indicator_signals, pattern_signals, zone_signals,
    zone_context_v2,
)
from research_engine.harness.indicators import compute, fib_levels_at  # noqa: E402
from research_engine.harness.simulate import simulate  # noqa: E402

OUT = REPO_ROOT / "research_engine" / "output"
CACHE = REPO_ROOT / "research_engine" / "cache"
TEST_START = pd.Timestamp("2025-08-11")

SETUP_TYPE = {}
for s in ("bullish_engulfing bearish_engulfing hammer shooting_star inverted_hammer "
          "morning_star evening_star inside_bar_breakout inside_bar_breakdown "
          "nr7_breakout nr7_breakdown strong_bull_candle strong_bear_candle").split():
    SETUP_TYPE[s] = "candlestick"
for s in ("ema20_bounce ema20_rejection sma50_pullback_long sma50_pullback_short "
          "macd_cross_long macd_cross_short bb_squeeze_breakout bb_squeeze_breakdown "
          "gap_up_go gap_down_go rsi_bull_divergence rsi_bear_divergence "
          "stoch_bull_divergence stoch_bear_divergence hh_hl_continuation "
          "lh_ll_continuation pdh_breakout pdl_breakdown fib_pullback_long "
          "fib_pullback_short").split():
    SETUP_TYPE[s] = "indicator"
for s in "demand_bounce supply_rejection zone_touch_fresh".split():
    SETUP_TYPE[s] = "zone"
for s in ("triangle_sym_breakout triangle_sym_breakdown ascending_triangle_break "
          "descending_triangle_break vcp_breakout range_breakout range_breakdown "
          "bull_flag_break bear_flag_break bull_pennant_break bear_pennant_break "
          "double_bottom_break double_top_break").split():
    SETUP_TYPE[s] = "pattern"


def _daily_context(sym: str) -> dict | None:
    """Daily regime/extension/return series for an instrument, date-indexed."""
    df = datafetch.load(sym, "daily")
    if df is None or len(df) < 60:
        return None
    c = df["Close"]
    ema20 = c.ewm(span=20, adjust=False).mean()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(),
                    (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    idx = pd.DatetimeIndex(pd.Series(df.index).dt.normalize())
    return {
        "regime": pd.Series((c > ema20).to_numpy(), index=idx),
        "ext_atr": pd.Series(((c - ema20) / atr).to_numpy(), index=idx),
        "ret20": pd.Series(c.pct_change(20).to_numpy(), index=idx),
        "close": pd.Series(c.to_numpy(), index=idx),
    }


def _asof(series: pd.Series | None, date: pd.Timestamp, lag_days: int = 0):
    if series is None:
        return None
    date = pd.Timestamp(date) - pd.Timedelta(days=lag_days)
    sub = series.loc[:date]
    return sub.iloc[-1] if len(sub) else None


def _earnings_features(edf: pd.DataFrame | None, sym: str, date: pd.Timestamp,
                       daily_close: pd.Series | None) -> dict:
    out = {"days_to_result": None, "days_since_result": None,
           "last_surprise_pct": None, "result_reaction_pct": None}
    if edf is None or daily_close is None:
        return out
    dates = edf[edf.symbol == sym]["earnings_date"].sort_values()
    if dates.empty:
        return out
    d = pd.Timestamp(date).normalize()
    nxt = dates[dates >= d]
    prv = dates[dates < d]
    if len(nxt):
        dt_days = (nxt.iloc[0] - d).days
        if dt_days <= 30:
            out["days_to_result"] = int(dt_days)
    if len(prv):
        last = prv.iloc[-1]
        since = (d - last).days
        if since <= 60:
            out["days_since_result"] = int(since)
            row = edf[(edf.symbol == sym) & (edf.earnings_date == last)]
            if len(row):
                sp = row["surprise_pct"].iloc[0]
                out["last_surprise_pct"] = None if pd.isna(sp) else round(float(sp), 2)
            after = daily_close.loc[last:]
            before = daily_close.loc[:last - pd.Timedelta(days=1)]
            if len(after) >= 2 and len(before) >= 1:
                out["result_reaction_pct"] = round(float(after.iloc[1] / before.iloc[-1] - 1) * 100, 2)
    return out


def process_symbol(sym: str, tf: str, sector: str, cname: str,
                   mkt_ctx: dict | None, sect_ctx: dict | None,
                   earnings: pd.DataFrame | None) -> list[dict]:
    raw = datafetch.load(sym, tf)
    if raw is None or len(raw) < 80:
        return []
    df = compute(raw)
    intraday = tf in ("60m", "75m", "15m")

    sigs = []
    sigs += candlestick_signals(df)
    sigs += indicator_signals(df, tf)
    sigs += zone_signals(df)
    if len(df) >= 100:
        sigs += pattern_signals(df, tf)
    if tf in ("daily", "weekly"):
        keep_from = df.index.searchsorted(TEST_START)
        sigs = [s for s in sigs if s.i >= keep_from]
    sigs.sort(key=lambda s: (s.setup, s.i))
    deduped, last_i = [], {}
    for s in sigs:
        if s.i - last_i.get(s.setup, -99) >= 3:
            deduped.append(s)
            last_i[s.setup] = s.i
    sigs = deduped
    if not sigs:
        return []

    zctx = zone_context_v2(df)
    o = df["Open"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    ema20 = df["ema20"].to_numpy(float)
    sma50 = df["sma50"].to_numpy(float)
    sma200 = df["sma200"].to_numpy(float)
    atr = df["atr"].to_numpy(float)
    rsi = df["rsi"].to_numpy(float)
    stoch = df["stoch_k"].to_numpy(float)
    vexp = df["vol_exp"].to_numpy(bool)
    vcon = df["vol_con"].to_numpy(bool)
    vma20 = df["vol_ma20"].to_numpy(float)
    squeeze = df["bb_squeeze"].to_numpy(bool)

    # institutional PROXIES (price/volume-derived; not confirmed flows)
    dirn = np.sign(np.diff(c, prepend=c[0]))
    obv = np.cumsum(dirn * np.nan_to_num(v))
    obv_up = obv > np.roll(obv, 20)
    rng = np.maximum(h - l, 1e-9)
    ad = np.cumsum(((c - l) - (h - c)) / rng * np.nan_to_num(v))
    ad_up = ad > np.roll(ad, 20)
    turnover20 = pd.Series(v * c).rolling(20).median().to_numpy()
    vwap_above = None
    if intraday and getattr(df.index, "tz", None) is not None:
        day = pd.Series(df.index.tz_convert("Asia/Kolkata").date, index=df.index)
        tp = (h + l + c) / 3
        cum_pv = pd.Series(tp * v, index=df.index).groupby(day).cumsum()
        cum_v = pd.Series(v, index=df.index).groupby(day).cumsum().replace(0, np.nan)
        vwap_above = (df["Close"] > (cum_pv / cum_v)).to_numpy()

    # news-proxy events
    prev_c = np.roll(c, 1); prev_c[0] = c[0]
    gap_ev = np.abs(o / prev_c - 1) >= 0.02
    vol_ev = v >= 2.5 * np.where(np.isfinite(vma20) & (vma20 > 0), vma20, np.inf)
    move_ev = np.abs(c - prev_c) >= 2 * np.where(np.isfinite(atr), atr, np.inf)
    any_ev = gap_ev | vol_ev | move_ev
    ev_recent = np.zeros(len(c), bool)
    for k in range(4):
        ev_recent |= np.roll(any_ev, k)
    ev_recent[:4] = any_ev[:4]

    stock_daily = _daily_context(sym) if intraday else None
    stock_daily_close = (stock_daily or {}).get("close") if intraday else None
    if not intraday:
        dc_self = _daily_context(sym)
        stock_daily_close = (dc_self or {}).get("close")
        stock_ret20_series = (dc_self or {}).get("ret20")
    else:
        stock_ret20_series = (stock_daily or {}).get("ret20")

    trades = simulate(df, sigs, tf)
    sig_by_key = {}
    for s in sigs:
        sig_by_key.setdefault((s.i, s.setup), s)

    out = []
    lag = 1 if intraday else 0
    for t in trades:
        i = t["signal_i"]
        ts = df.index[i]
        if intraday and ts.tzinfo is not None:
            date = pd.Timestamp(ts.tz_convert("Asia/Kolkata").date())
        else:
            date = pd.Timestamp(ts).normalize()
        fib_conf = False
        try:
            levels = fib_levels_at(df, max(0, i - 1))
            fib_conf = any(abs(c[i] - lv) / lv <= 0.01 for lv in levels.values())
        except Exception:
            pass
        ema_conf = bool(np.isfinite(ema20[i]) and np.isfinite(atr[i]) and (
            abs(c[i] - ema20[i]) <= 0.25 * atr[i] or (l[i] <= ema20[i] <= h[i])))
        mreg = _asof((mkt_ctx or {}).get("regime"), date, lag)
        sreg = _asof((sect_ctx or {}).get("regime"), date, lag)
        m_ext = _asof((mkt_ctx or {}).get("ext_atr"), date, lag)
        s_ext = _asof((sect_ctx or {}).get("ext_atr"), date, lag)
        m_ret20 = _asof((mkt_ctx or {}).get("ret20"), date, lag)
        s_ret20 = _asof((sect_ctx or {}).get("ret20"), date, lag)
        stock_ret20 = _asof(stock_ret20_series, date, lag) if stock_ret20_series is not None else (
            float(pd.Series(c).pct_change(20).iloc[i]) if i >= 20 else None)
        stock_daily_up = _asof((stock_daily or {}).get("regime"), date, lag) if intraday else None
        direction_bull = t["bullish_or_bearish"] == "bullish"
        risk_pct = t["risk_pct"]
        d_sup = zctx["dist_supply_pct"][i]
        d_dem = zctx["dist_demand_pct"][i]
        rr_opp = None
        opp = d_sup if direction_bull else d_dem
        if np.isfinite(opp) and risk_pct > 0:
            rr_opp = round(float(opp) / risk_pct, 2)
        lvl = t.get("tag_breakout_level")
        level_tests = None
        if lvl and np.isfinite(lvl):
            lo = max(0, i - 60)
            near = (np.abs(h[lo:i] - lvl) / lvl <= 0.01) | (np.abs(l[lo:i] - lvl) / lvl <= 0.01)
            level_tests = int(near.sum())
        efeat = _earnings_features(earnings, sym, date, stock_daily_close)
        t.update({
            "symbol": sym.replace("^NSEI", "NIFTY50").replace("^NSEBANK", "BANKNIFTY"),
            "company_name": cname, "sector": sector, "exchange": "NSE", "timeframe": tf,
            "setup_type": SETUP_TYPE.get(t["setup_name"], "other"),
            "near_demand": bool(zctx["near_demand"][i]), "near_supply": bool(zctx["near_supply"][i]),
            "demand_zone_present": bool(zctx["near_demand"][i]), "supply_zone_present": bool(zctx["near_supply"][i]),
            "stale_demand": bool(zctx["stale_demand"][i]), "stale_supply": bool(zctx["stale_supply"][i]),
            "demand_broken_recent": bool(zctx["demand_broken_recent"][i]),
            "supply_broken_recent": bool(zctx["supply_broken_recent"][i]),
            "dist_demand_pct": round(float(d_dem), 3) if np.isfinite(d_dem) else None,
            "dist_supply_pct": round(float(d_sup), 3) if np.isfinite(d_sup) else None,
            "rr_to_opposing": rr_opp,
            "ema20_confluence": ema_conf,
            "sma50_trend": "above" if c[i] > sma50[i] else "below",
            "sma200_trend": ("above" if c[i] > sma200[i] else "below") if np.isfinite(sma200[i]) else "na",
            "ext_ema20_atr": round(float((c[i] - ema20[i]) / atr[i]), 2) if np.isfinite(atr[i]) and atr[i] > 0 else None,
            "ret5_atr": round(float((c[i] - c[max(0, i - 5)]) / atr[i]), 2) if np.isfinite(atr[i]) and atr[i] > 0 else None,
            "rsi_value": round(float(rsi[i]), 1) if np.isfinite(rsi[i]) else None,
            "stochastic_value": round(float(stoch[i]), 1) if np.isfinite(stoch[i]) else None,
            "volume_confirmation": bool(vexp[i]),
            "volume_contraction_before": bool(vcon[max(0, i - 1)]),
            "bb_squeeze_before": bool(squeeze[max(0, i - 1)]),
            "fibonacci_confluence": fib_conf,
            "obv_up": bool(obv_up[i]), "ad_up": bool(ad_up[i]),
            "vwap_above": bool(vwap_above[i]) if vwap_above is not None else None,
            "turnover20": round(float(turnover20[i]), 0) if np.isfinite(turnover20[i]) else None,
            "level_tests": level_tests,
            "stock_daily_uptrend": None if stock_daily_up is None else bool(stock_daily_up),
            "rs20_vs_nifty": round(float(stock_ret20 - m_ret20) * 100, 2) if stock_ret20 is not None and m_ret20 is not None and np.isfinite(stock_ret20) and np.isfinite(m_ret20) else None,
            "rs20_vs_sector": round(float(stock_ret20 - s_ret20) * 100, 2) if stock_ret20 is not None and s_ret20 is not None and np.isfinite(stock_ret20) and np.isfinite(s_ret20) else None,
            "market_regime_up": None if mreg is None else bool(mreg),
            "sector_regime_up": None if sreg is None else bool(sreg),
            "market_ext_atr": round(float(m_ext), 2) if m_ext is not None and np.isfinite(m_ext) else None,
            "sector_ext_atr": round(float(s_ext), 2) if s_ext is not None and np.isfinite(s_ext) else None,
            "market_confirmation": (bool(mreg) if direction_bull else (not bool(mreg))) if mreg is not None else None,
            "sector_confirmation": (bool(sreg) if direction_bull else (not bool(sreg))) if sreg is not None else None,
            "atr_pct": round(float(atr[i] / c[i] * 100), 2) if np.isfinite(atr[i]) else None,
            "news_gap_event": bool(gap_ev[i]), "news_vol_event": bool(vol_ev[i]),
            "news_move_event": bool(move_ev[i]), "news_event_recent": bool(ev_recent[i]),
            **efeat,
            "notes": "",
        })
        out.append(t)
    return out


def main() -> None:
    from research_engine.harness.detectors import enable_backtest_mode
    enable_backtest_mode()  # survivorship patch: harness runners only
    tf = sys.argv[1]
    scope = sys.argv[2] if len(sys.argv) > 2 else "all"
    shard = sys.argv[3] if len(sys.argv) > 3 else None
    syms = universe.nifty50_symbols() if scope == "nifty50" else universe.fno_symbols()
    smap = universe.sector_map()
    names = universe.company_names()

    instruments = [(s, smap.get(s, "Other"), names.get(s, s)) for s in syms]
    instruments += [("^NSEI", "Index", "NIFTY 50"), ("^NSEBANK", "Index", "BANK NIFTY")]
    suffix = ""
    if shard:
        a, b = (int(x) for x in shard.split(":"))
        instruments = instruments[a:b]
        suffix = f"_shard{a:03d}"

    mkt_ctx = _daily_context("^NSEI")
    sect_ctx = {sec: _daily_context(tick) for sec, tick in universe.SECTOR_INDEX_TICKERS.items()}
    epath = CACHE / "earnings.parquet"
    earnings = pd.read_parquet(epath) if epath.exists() else None

    all_rows: list[dict] = []
    t0 = time.time()
    for k, (sym, sector, cname) in enumerate(instruments, 1):
        try:
            rows = process_symbol(sym, tf, sector, cname, mkt_ctx, sect_ctx.get(sector), earnings)
            all_rows.extend(rows)
        except Exception:
            print(f"[{tf}] ERROR {sym}")
            traceback.print_exc()
        if k % 25 == 0:
            print(f"[{tf}] {k}/{len(instruments)} symbols, {len(all_rows)} trades, {time.time()-t0:.0f}s", flush=True)

    df = pd.DataFrame(all_rows)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"trades_{tf}_{scope}{suffix}.parquet"
    df.to_parquet(dest)
    print(f"[{tf}] DONE: {len(df)} trades -> {dest} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
