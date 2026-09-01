"""Global-markets influence on the Indian market — the empirical engine.

Research only. Reads research_engine/cache/global/ (see global_fetch.py) and
answers, with numbers: which foreign series move the NIFTY/sector OPENING GAP,
and which (if any) move the rest of the day AFTER the open.

No-lookahead calendar, for each Indian trading date T:
  - "prev" signals: the most recent completed session with exchange-local
    date STRICTLY BEFORE T (US, Europe, commodities, FX, rates, EEM/INDA).
  - "sameday gap" signals: Asian markets open 2-4 hours before India, so
    their day-T OPENING print (vs their own prior close) is known before the
    NSE opens. Their day-T close is NOT (it overlaps India) and is never used
    as a predictor.
India outputs per index: gap = O_T/C_{T-1}-1, o2c = C_T/O_T-1 (the day after
the open), c2c = full day. A series whose Open equals the prior Close on >5%
of days has no real opening print on Yahoo; its gap stats are excluded.

Outputs (research_engine/output/global_influence/):
  correlations.csv   signal x window x (gap / o2c / c2c) correlations vs NIFTY
  rules.csv          conditional rules with n, hit rates, base rates, fade
  sector_matrix.csv  India index/basket sensitivity to the main signals
  regimes.csv        the S&P rule split by trend and India-VIX regime
  rolling.json       rolling-1y correlation stability of the headline pairs
  events.json        dated examples: biggest cues, false signals, crude days
  intraday.json      first-hour, ES-overnight and DAX-afternoon tests (2y)
  summary.json       headline numbers used by the report
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.harness import datafetch  # noqa: E402  (F&O baskets)

CACHE = REPO_ROOT / "research_engine" / "cache" / "global"
OUT = REPO_ROOT / "research_engine" / "output" / "global_influence"

_GAP_EPS = 0.10        # % — |gap| below this is "flat", not up/down
_OPEN_QUALITY_MAX = 0.05

WINDOWS = {"1y": 252, "3y": 756, "5y": 1260, "10y": None}

# Signals measured as previous-session % change (or diff where noted).
PREV_SIGNALS = [
    "SP500", "DOW", "NASDAQ100", "RUSSELL2000", "US_VIX", "DXY",
    "NVIDIA", "APPLE", "MICROSOFT",
    "FTSE", "DAX", "CAC", "STOXX50",
    "BRENT", "WTI", "GOLD", "SILVER", "COPPER", "NATGAS",
    "USDINR", "MSCI_EM", "MSCI_INDIA",
    "NIKKEI", "HANGSENG", "SHANGHAI", "KOSPI", "TAIWAN", "ASX200", "STRAITS",
]
DIFF_SIGNALS = ["US_10Y"]          # yield: change in points (^TNX/10 = pct-pts)
ASIA_GAP_SIGNALS = ["NIKKEI", "HANGSENG", "KOSPI", "TAIWAN", "STRAITS", "ASX200"]

INDIA_OUTPUTS = [
    "NIFTY50", "BANKNIFTY", "SENSEX", "NIFTY_IT", "NIFTY_AUTO", "NIFTY_PHARMA",
    "NIFTY_METAL", "NIFTY_ENERGY", "NIFTY_FMCG", "NIFTY_REALTY",
    "NIFTY_PSUBANK", "NIFTY_NEXT50", "NIFTY_MIDCAP50", "NIFTY_SMALLCAP100",
]

BASKETS = {                        # equal-weight, from the F&O daily cache
    "OMC_BASKET": ["IOC", "BPCL", "HINDPETRO"],
    "AIRLINE": ["INDIGO"],
    "PAINTS": ["ASIANPAINT"],
    "TYRES": ["APOLLOTYRE", "BALKRISIND"],
    "GOLD_STOCKS": ["TITAN", "MUTHOOTFIN", "MANAPPURAM"],
    # Yahoo's ^CNX sector indices for these are newborn symbols (single bar),
    # so sectors are covered by equal-weight F&O baskets over the cache's ~4y
    # window instead — DISCLOSED in the report as baskets, not indices.
    "REALTY_BASKET": ["DLF", "GODREJPROP", "OBEROIRLTY", "LODHA", "PRESTIGE"],
    "PSUBANK_BASKET": ["SBIN", "BANKBARODA", "PNB", "CANBK", "UNIONBANK"],
}

# Sector baskets built from the shipped niftyindices constituent watchlists,
# intersected with whatever the F&O daily cache actually holds.
_SECTOR_WATCHLIST_BASKETS = {
    "METAL_BASKET": "Nifty Metal",
    "AUTO_BASKET": "Nifty Auto",
    "ENERGY_BASKET": "Nifty Energy",
    "FMCG_BASKET": "Nifty FMCG",
}


def _watchlist_symbols(name: str) -> list[str]:
    wl_path = REPO_ROOT / "data" / "predefined_watchlists.json"
    for wl in json.loads(wl_path.read_text(encoding="utf-8")):
        if wl.get("name") == name:
            return list(wl.get("symbols", []))
    return []


for _bname, _wl in _SECTOR_WATCHLIST_BASKETS.items():
    _syms = _watchlist_symbols(_wl)
    if _syms:
        BASKETS[_bname] = _syms


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def _load(name: str) -> pd.DataFrame | None:
    p = CACHE / f"{name}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df = df.copy()
    df["date"] = pd.to_datetime([d.date() for d in df.index])
    return df.reset_index(drop=True)


def _meta() -> dict:
    return json.loads((CACHE / "_meta.json").read_text())


def _gap_ok(meta: dict, name: str) -> bool:
    m = meta.get(name, {})
    return bool(m.get("ok")) and m.get("open_eq_prevclose_frac", 1.0) <= _OPEN_QUALITY_MAX


# --------------------------------------------------------------------------- #
# Master frame construction
# --------------------------------------------------------------------------- #

def build_master() -> tuple[pd.DataFrame, dict]:
    meta = _meta()
    nifty = _load("NIFTY50")
    if nifty is None:
        raise RuntimeError("NIFTY50 cache missing — run global_fetch.py first")
    master = pd.DataFrame({"date": nifty["date"]})

    # India outputs: gap / o2c / c2c per index.
    gap_quality: dict[str, bool] = {}
    for name in INDIA_OUTPUTS:
        df = _load(name)
        if df is None or len(df) < 300:
            continue
        prev_close = df["Close"].shift(1)
        cols = pd.DataFrame({
            "date": df["date"],
            f"{name}_gap": (df["Open"] / prev_close - 1) * 100,
            f"{name}_o2c": (df["Close"] / df["Open"] - 1) * 100,
            f"{name}_c2c": (df["Close"] / prev_close - 1) * 100,
        })
        gap_quality[name] = _gap_ok(meta, name)
        if not gap_quality[name]:
            cols = cols.drop(columns=[f"{name}_gap", f"{name}_o2c"])
        master = master.merge(cols, on="date", how="left")

    # Baskets from the F&O daily cache.
    for bname, symbols in BASKETS.items():
        parts_gap, parts_o2c, parts_c2c = [], [], []
        for sym in symbols:
            df = datafetch.load(sym, "daily")
            if df is None or len(df) < 300:
                continue
            d = pd.DataFrame({
                "date": pd.to_datetime([x.date() for x in df.index]),
                "gap": (df["Open"].values / pd.Series(df["Close"].values).shift(1).values - 1) * 100,
                "o2c": (df["Close"].values / df["Open"].values - 1) * 100,
            })
            d["c2c"] = (1 + d["gap"] / 100) * (1 + d["o2c"] / 100) * 100 - 100
            parts_gap.append(d.set_index("date")["gap"])
            parts_o2c.append(d.set_index("date")["o2c"])
            parts_c2c.append(d.set_index("date")["c2c"])
        if not parts_gap:
            continue
        master = master.merge(pd.DataFrame({
            "date": pd.concat(parts_gap, axis=1).mean(axis=1).index,
            f"{bname}_gap": pd.concat(parts_gap, axis=1).mean(axis=1).values,
            f"{bname}_o2c": pd.concat(parts_o2c, axis=1).mean(axis=1).values,
            f"{bname}_c2c": pd.concat(parts_c2c, axis=1).mean(axis=1).values,
        }), on="date", how="left")
        gap_quality[bname] = True

    # Previous-session signals (strictly before T).
    for name in PREV_SIGNALS + DIFF_SIGNALS:
        df = _load(name)
        if df is None or len(df) < 300:
            continue
        if name in DIFF_SIGNALS:
            # Yahoo's ^TNX series quotes the yield directly in percent
            # (verified: daily-change std ~5bp), so the diff is already in
            # percentage points.
            sig = df["Close"].diff()
        else:
            sig = df["Close"].pct_change() * 100
        right = pd.DataFrame({"date": df["date"], f"{name}_prev": sig}).dropna()
        master = pd.merge_asof(
            master.sort_values("date"), right.sort_values("date"),
            on="date", direction="backward", allow_exact_matches=False,
        )

    # Asia same-day opening gaps (their open prints before the NSE opens).
    for name in ASIA_GAP_SIGNALS:
        df = _load(name)
        if df is None or not _gap_ok(meta, name):
            continue
        right = pd.DataFrame({
            "date": df["date"],
            f"{name}_gapopen": (df["Open"] / df["Close"].shift(1) - 1) * 100,
        }).dropna()
        master = master.merge(right, on="date", how="left")

    # India VIX: same-day change (response variable) + prior level (regime).
    vix = _load("INDIA_VIX")
    if vix is not None:
        master = master.merge(pd.DataFrame({
            "date": vix["date"],
            "INDIA_VIX_chg": vix["Close"].pct_change() * 100,
            "INDIA_VIX_prevlevel": vix["Close"].shift(1),
        }), on="date", how="left")

    # Regime columns from NIFTY itself (known at T-1 close).
    close = _load("NIFTY50").set_index("date")["Close"]
    sma200 = close.rolling(200).mean()
    rel = (close / sma200 - 1).shift(1) * 100          # position vs 200SMA at T-1
    regime = pd.Series(np.where(rel > 2, "bull", np.where(rel < -2, "bear", "sideways")),
                       index=rel.index)
    master = master.merge(pd.DataFrame({"date": rel.index, "trend_regime": regime.values}),
                          on="date", how="left")
    if "INDIA_VIX_prevlevel" in master:
        med = master["INDIA_VIX_prevlevel"].rolling(252, min_periods=100).median()
        master["vix_regime"] = np.where(master["INDIA_VIX_prevlevel"] > med, "high_vix", "low_vix")
    return master, gap_quality


# --------------------------------------------------------------------------- #
# Stats helpers
# --------------------------------------------------------------------------- #

def _window(master: pd.DataFrame, days: int | None) -> pd.DataFrame:
    return master if days is None else master.iloc[-days:]


def _corr_table(master: pd.DataFrame) -> pd.DataFrame:
    signals = [c for c in master.columns if c.endswith("_prev") or c.endswith("_gapopen")]
    rows = []
    for wname, days in WINDOWS.items():
        m = _window(master, days)
        for s in signals:
            for target, label in (("NIFTY50_gap", "gap"), ("NIFTY50_o2c", "o2c"),
                                  ("NIFTY50_c2c", "c2c")):
                pair = m[[s, target]].dropna()
                if len(pair) < 60:
                    continue
                rows.append({"signal": s, "window": wname, "target": label,
                             "corr": float(pair[s].corr(pair[target])), "n": len(pair)})
    return pd.DataFrame(rows)


def _rule_stats(m: pd.DataFrame, mask: pd.Series, target_prefix: str) -> dict:
    g, o = f"{target_prefix}_gap", f"{target_prefix}_o2c"
    sel = m.loc[mask].dropna(subset=[g])
    base = m.dropna(subset=[g])
    if len(sel) < 15:
        return {"n": len(sel)}
    out = {
        "n": len(sel),
        "gap_up_pct": float((sel[g] > _GAP_EPS).mean()) * 100,
        "gap_down_pct": float((sel[g] < -_GAP_EPS).mean()) * 100,
        "mean_gap_pct": float(sel[g].mean()),
        "base_gap_up_pct": float((base[g] > _GAP_EPS).mean()) * 100,
        "base_gap_down_pct": float((base[g] < -_GAP_EPS).mean()) * 100,
        "base_mean_gap_pct": float(base[g].mean()),
    }
    if o in sel:
        so = sel.dropna(subset=[o])
        out["mean_o2c_pct"] = float(so[o].mean())
        out["o2c_up_pct"] = float((so[o] > 0).mean()) * 100
        out["base_mean_o2c_pct"] = float(base.dropna(subset=[o])[o].mean())
        gap_up = so[so[g] > 0.25]
        if len(gap_up) >= 10:
            out["fade_after_gapup_pct"] = float((gap_up[o] < 0).mean()) * 100
    return out


RULES = [
    # (rule id, signal column, op, threshold, output prefix)
    ("SP500 >= +1.0%", "SP500_prev", ">=", 1.0, "NIFTY50"),
    ("SP500 <= -1.0%", "SP500_prev", "<=", -1.0, "NIFTY50"),
    ("SP500 >= +0.5%", "SP500_prev", ">=", 0.5, "NIFTY50"),
    ("SP500 <= -0.5%", "SP500_prev", "<=", -0.5, "NIFTY50"),
    ("NASDAQ100 <= -1.5% -> NIFTY IT", "NASDAQ100_prev", "<=", -1.5, "NIFTY_IT"),
    ("NASDAQ100 <= -1.5% -> NIFTY", "NASDAQ100_prev", "<=", -1.5, "NIFTY50"),
    ("NASDAQ100 >= +1.5% -> NIFTY IT", "NASDAQ100_prev", ">=", 1.5, "NIFTY_IT"),
    ("US VIX >= +10%", "US_VIX_prev", ">=", 10.0, "NIFTY50"),
    ("US VIX >= +20%", "US_VIX_prev", ">=", 20.0, "NIFTY50"),
    ("BRENT >= +3% -> NIFTY", "BRENT_prev", ">=", 3.0, "NIFTY50"),
    ("BRENT >= +3% -> ENERGY", "BRENT_prev", ">=", 3.0, "ENERGY_BASKET"),
    ("BRENT >= +3% -> OMC", "BRENT_prev", ">=", 3.0, "OMC_BASKET"),
    ("BRENT >= +3% -> AIRLINE", "BRENT_prev", ">=", 3.0, "AIRLINE"),
    ("BRENT >= +3% -> PAINTS", "BRENT_prev", ">=", 3.0, "PAINTS"),
    ("BRENT <= -3% -> OMC", "BRENT_prev", "<=", -3.0, "OMC_BASKET"),
    ("HANGSENG <= -2% (prev)", "HANGSENG_prev", "<=", -2.0, "NIFTY50"),
    ("HANGSENG <= -2% -> METAL", "HANGSENG_prev", "<=", -2.0, "METAL_BASKET"),
    ("NIKKEI gap >= +1% (same day)", "NIKKEI_gapopen", ">=", 1.0, "NIFTY50"),
    ("NIKKEI gap <= -1% (same day)", "NIKKEI_gapopen", "<=", -1.0, "NIFTY50"),
    ("USDINR >= +0.3% (rupee weak) -> IT", "USDINR_prev", ">=", 0.3, "NIFTY_IT"),
    ("USDINR >= +0.3% -> PHARMA", "USDINR_prev", ">=", 0.3, "NIFTY_PHARMA"),
    ("USDINR >= +0.3% -> BANK", "USDINR_prev", ">=", 0.3, "BANKNIFTY"),
    ("COPPER >= +2% -> METAL", "COPPER_prev", ">=", 2.0, "METAL_BASKET"),
    ("COPPER <= -2% -> METAL", "COPPER_prev", "<=", -2.0, "METAL_BASKET"),
    ("GOLD >= +2% -> GOLD STOCKS", "GOLD_prev", ">=", 2.0, "GOLD_STOCKS"),
    ("MSCI INDIA (INDA) >= +1%", "MSCI_INDIA_prev", ">=", 1.0, "NIFTY50"),
    ("MSCI INDIA (INDA) <= -1%", "MSCI_INDIA_prev", "<=", -1.0, "NIFTY50"),
]


def _apply_op(col: pd.Series, op: str, thr: float) -> pd.Series:
    return col >= thr if op == ">=" else col <= thr


def _rules_table(master: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for wname in ("10y", "3y"):
        m = _window(master, WINDOWS[wname])
        for rid, col, op, thr, target in RULES:
            if col not in m or f"{target}_gap" not in m:
                continue
            stats = _rule_stats(m, _apply_op(m[col], op, thr).fillna(False), target)
            rows.append({"rule": rid, "window": wname, "target": target, **stats})
        # Composite rules
        if "DXY_prev" in m and "US_10Y_prev" in m:
            mask = (m["DXY_prev"] >= 0.3) & (m["US_10Y_prev"] > 0.03)
            rows.append({"rule": "DXY >= +0.3% AND US10Y +3bp",
                         "window": wname, "target": "NIFTY50",
                         **_rule_stats(m, mask.fillna(False), "NIFTY50")})
        if "SP500_prev" in m and "NIKKEI_gapopen" in m:
            conflict = (m["SP500_prev"] >= 0.5) & (m["NIKKEI_gapopen"] <= -0.3)
            rows.append({"rule": "US up >=0.5% BUT Nikkei gaps down <=-0.3%",
                         "window": wname, "target": "NIFTY50",
                         **_rule_stats(m, conflict.fillna(False), "NIFTY50")})
            agree = (m["SP500_prev"] >= 0.5) & (m["NIKKEI_gapopen"] >= 0.3)
            rows.append({"rule": "US up >=0.5% AND Nikkei gaps up >=0.3%",
                         "window": wname, "target": "NIFTY50",
                         **_rule_stats(m, agree.fillna(False), "NIFTY50")})
    return pd.DataFrame(rows)


SECTOR_SIGNALS = ["SP500_prev", "NASDAQ100_prev", "US_VIX_prev", "HANGSENG_prev",
                  "BRENT_prev", "USDINR_prev", "COPPER_prev", "MSCI_INDIA_prev",
                  "NIKKEI_gapopen", "US_10Y_prev", "DXY_prev", "GOLD_prev"]


def _sector_matrix(master: pd.DataFrame) -> pd.DataFrame:
    m = _window(master, WINDOWS["5y"])
    rows = []
    targets = INDIA_OUTPUTS + list(BASKETS)
    for t in targets:
        for kind in ("gap", "o2c"):
            col = f"{t}_{kind}"
            if col not in m:
                continue
            row = {"index": t, "kind": kind,
                   "n": int(m[col].notna().sum())}
            for s in SECTOR_SIGNALS:
                if s not in m:
                    continue
                pair = m[[s, col]].dropna()
                row[s] = round(float(pair[s].corr(pair[col])), 3) if len(pair) > 200 else np.nan
            rows.append(row)
    return pd.DataFrame(rows)


def _regime_table(master: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for regime_col in ("trend_regime", "vix_regime"):
        if regime_col not in master:
            continue
        for rname, g in master.groupby(regime_col):
            for rid, col, op, thr, target in [
                ("SP500 >= +0.5%", "SP500_prev", ">=", 0.5, "NIFTY50"),
                ("SP500 <= -0.5%", "SP500_prev", "<=", -0.5, "NIFTY50"),
            ]:
                stats = _rule_stats(g, _apply_op(g[col], op, thr).fillna(False), target)
                rows.append({"regime_type": regime_col, "regime": rname,
                             "rule": rid, **stats})
    return pd.DataFrame(rows)


def _rolling_stability(master: pd.DataFrame) -> dict:
    out = {}
    for s in ("SP500_prev", "HANGSENG_prev", "USDINR_prev", "MSCI_INDIA_prev"):
        pair = master[["date", s, "NIFTY50_gap"]].dropna()
        if len(pair) < 500:
            continue
        r = pair[s].rolling(252).corr(pair["NIFTY50_gap"]).dropna()
        out[s] = {"min": round(float(r.min()), 3), "median": round(float(r.median()), 3),
                  "max": round(float(r.max()), 3), "latest": round(float(r.iloc[-1]), 3)}
    return out


def _events(master: pd.DataFrame) -> dict:
    ev: dict[str, list] = {}
    m = master.dropna(subset=["SP500_prev", "NIFTY50_gap"])
    big = m.reindex(m["SP500_prev"].abs().sort_values(ascending=False).index[:5])
    ev["biggest_us_moves"] = [
        {"date": str(r.date.date()), "sp500_prev": round(r.SP500_prev, 2),
         "nifty_gap": round(r.NIFTY50_gap, 2),
         "nifty_o2c": round(r.NIFTY50_o2c, 2) if pd.notna(r.NIFTY50_o2c) else None}
        for r in big.itertuples()
    ]
    false_sig = m[(m["SP500_prev"] >= 1.0) & (m["NIFTY50_gap"] < -0.25)]
    ev["false_signals_us_up_india_gapdown"] = [
        {"date": str(r.date.date()), "sp500_prev": round(r.SP500_prev, 2),
         "nifty_gap": round(r.NIFTY50_gap, 2)}
        for r in false_sig.sort_values("date").tail(5).itertuples()
    ]
    ev["n_false_signals"] = int(len(false_sig))
    ev["n_us_up_1pct"] = int((m["SP500_prev"] >= 1.0).sum())
    if "BRENT_prev" in m:
        crude = master[master["BRENT_prev"] >= 3.0].dropna(subset=["NIFTY50_gap"])
        ev["crude_spike_days"] = int(len(crude))
        cols = {c: round(float(crude[c].mean()), 2)
                for c in ("NIFTY50_c2c", "ENERGY_BASKET_c2c", "OMC_BASKET_c2c",
                          "AIRLINE_c2c", "PAINTS_c2c", "TYRES_c2c", "NIFTY_IT_c2c")
                if c in crude}
        ev["crude_spike_avg_next_day"] = cols
    # US VIX -> India VIX transmission
    if "US_VIX_prev" in master and "INDIA_VIX_chg" in master:
        spike = master[master["US_VIX_prev"] >= 10.0].dropna(subset=["INDIA_VIX_chg"])
        ev["usvix_spike_days"] = int(len(spike))
        ev["indiavix_up_after_usvix_spike_pct"] = round(
            float((spike["INDIA_VIX_chg"] > 0).mean()) * 100, 1)
        ev["indiavix_avg_chg_after_usvix_spike"] = round(
            float(spike["INDIA_VIX_chg"].mean()), 2)
    return ev


# --------------------------------------------------------------------------- #
# Intraday extensions (2y hourly)
# --------------------------------------------------------------------------- #

def _intraday(master: pd.DataFrame) -> dict:
    out: dict = {}
    hourly = CACHE / "NIFTY50_60m.parquet"
    if hourly.exists():
        h = pd.read_parquet(hourly)
        h = h.copy()
        h["date"] = pd.to_datetime([d.date() for d in h.index])
        first = h.groupby("date").first()
        last = h.groupby("date").last()
        day = pd.DataFrame({
            "first_hour": (first["Close"] / first["Open"] - 1) * 100,
            "rest_of_day": (last["Close"] / first["Close"] - 1) * 100,
        }).reset_index()
        m = master.merge(day, on="date", how="inner").dropna(
            subset=["first_hour", "SP500_prev", "NIFTY50_gap"])
        out["n_days_hourly"] = int(len(m))
        out["corr_sp500_first_hour"] = round(float(m["SP500_prev"].corr(m["first_hour"])), 3)
        out["corr_sp500_rest_of_day"] = round(float(m["SP500_prev"].corr(m["rest_of_day"])), 3)
        gap_up = m[m["NIFTY50_gap"] > 0.3]
        gap_dn = m[m["NIFTY50_gap"] < -0.3]
        out["gapup_first_hour_up_pct"] = round(float((gap_up["first_hour"] > 0).mean()) * 100, 1)
        out["gapup_n"] = int(len(gap_up))
        out["gapdn_first_hour_dn_pct"] = round(float((gap_dn["first_hour"] < 0).mean()) * 100, 1)
        out["gapdn_n"] = int(len(gap_dn))

    es = CACHE / "ES_FUT_60m.parquet"
    if es.exists() and hourly.exists():
        e = pd.read_parquet(es)
        e = e.tz_convert("Asia/Kolkata") if e.index.tz is not None else e
        # ES level just before the NSE open (bars up to 08:45 IST on date T)
        e = e.copy()
        e["ist_date"] = pd.to_datetime([d.date() for d in e.index])
        pre_open = e[e.index.hour < 9].groupby("ist_date")["Close"].last()
        daily_sp = _load("SP500").set_index("date")["Close"]
        rows = []
        for d, lvl in pre_open.items():
            prior = daily_sp[daily_sp.index < d]
            if prior.empty:
                continue
            rows.append({"date": d, "es_overnight": (lvl / prior.iloc[-1] - 1) * 100})
        drift = pd.DataFrame(rows)
        m2 = master.merge(drift, on="date", how="inner").dropna(
            subset=["es_overnight", "SP500_prev", "NIFTY50_gap"])
        if len(m2) > 100:
            out["n_days_es"] = int(len(m2))
            out["corr_es_overnight_gap"] = round(
                float(m2["es_overnight"].corr(m2["NIFTY50_gap"])), 3)
            out["corr_sp500prev_gap_same_window"] = round(
                float(m2["SP500_prev"].corr(m2["NIFTY50_gap"])), 3)
            resid = m2["NIFTY50_gap"] - (
                np.polyfit(m2["SP500_prev"], m2["NIFTY50_gap"], 1)[0] * m2["SP500_prev"])
            out["corr_es_with_gap_residual"] = round(float(m2["es_overnight"].corr(resid)), 3)

    dax = CACHE / "DAX_60m.parquet"
    if dax.exists() and hourly.exists():
        dx = pd.read_parquet(dax)
        dx = dx.copy()
        dx["date"] = pd.to_datetime([d.date() for d in dx.index])
        dax_first = dx.groupby("date").first()
        dax_daily = _load("DAX").set_index("date")["Close"]
        rows = []
        for d, r in dax_first.iterrows():
            prior = dax_daily[dax_daily.index < d]
            if prior.empty:
                continue
            rows.append({"date": d, "dax_open_gap": (r["Open"] / prior.iloc[-1] - 1) * 100})
        dgap = pd.DataFrame(rows)
        h = pd.read_parquet(hourly)
        h = h.copy()
        h["date"] = pd.to_datetime([d.date() for d in h.index])
        h["hour"] = h.index.hour
        pre = h[h["hour"] < 13].groupby("date")["Close"].last()   # ~12:15 bar close
        close = h.groupby("date")["Close"].last()
        aft = ((close / pre - 1) * 100).rename("nifty_afternoon").reset_index()
        m3 = dgap.merge(aft, on="date").dropna()
        if len(m3) > 100:
            out["n_days_dax"] = int(len(m3))
            out["corr_daxopen_nifty_afternoon"] = round(
                float(m3["dax_open_gap"].corr(m3["nifty_afternoon"])), 3)
            weak = m3[m3["dax_open_gap"] <= -0.5]
            out["dax_weak_n"] = int(len(weak))
            out["nifty_afternoon_dn_pct_when_dax_weak"] = round(
                float((weak["nifty_afternoon"] < 0).mean()) * 100, 1) if len(weak) > 10 else None
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    master, gap_quality = build_master()
    master.to_parquet(OUT / "master.parquet")

    corr = _corr_table(master)
    corr.to_csv(OUT / "correlations.csv", index=False)
    rules = _rules_table(master)
    rules.to_csv(OUT / "rules.csv", index=False)
    sector = _sector_matrix(master)
    sector.to_csv(OUT / "sector_matrix.csv", index=False)
    regimes = _regime_table(master)
    regimes.to_csv(OUT / "regimes.csv", index=False)
    rolling = _rolling_stability(master)
    events = _events(master)
    intraday = _intraday(master)

    summary = {
        "n_days": int(len(master)),
        "first": str(master["date"].iloc[0].date()),
        "last": str(master["date"].iloc[-1].date()),
        "gap_quality": gap_quality,
        "base_gap_up_pct": round(float((master["NIFTY50_gap"] > _GAP_EPS).mean()) * 100, 1),
        "base_gap_down_pct": round(float((master["NIFTY50_gap"] < -_GAP_EPS).mean()) * 100, 1),
        "rolling": rolling, "events": events, "intraday": intraday,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))

    pd.set_option("display.width", 250)
    top = (corr[(corr["window"] == "5y") & (corr["target"] == "gap")]
           .sort_values("corr", key=lambda s: s.abs(), ascending=False).head(20))
    print("\n=== Top gap correlations (5y) ===")
    print(top.to_string(index=False))
    o2c = (corr[(corr["window"] == "5y") & (corr["target"] == "o2c")]
           .sort_values("corr", key=lambda s: s.abs(), ascending=False).head(10))
    print("\n=== Top open-to-close correlations (5y) ===")
    print(o2c.to_string(index=False))
    print("\n=== Rules (10y) ===")
    print(rules[rules["window"] == "10y"].to_string(index=False,
          float_format=lambda x: f"{x:7.2f}"))
    print("\nIntraday:", json.dumps(intraday, indent=2))
    print("\nEvents:", json.dumps(events, indent=2))
    print("\nRolling stability:", json.dumps(rolling, indent=2))


if __name__ == "__main__":
    main()
