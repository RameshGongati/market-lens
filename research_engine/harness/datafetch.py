"""Batched OHLCV fetch + parquet cache for the backtest study.

Timeframes:
  daily  : 2y history (indicator warm-up), test window = last 1y
  60m    : 1y   (Yahoo hourly limit is 730d)
  15m    : 60d  (Yahoo limit) -> also resampled to 75m at load time
  weekly : resampled from daily at load time (W-FRI)

125m is NOT fetched: Yahoo has no native interval, and the only resample source
(5m) is capped at 60 days — too small a sample to rank against the others.

Prices are UNADJUSTED (auto_adjust=False) to match Market Lens zone levels.
The still-forming bar (last bar of the current session) is dropped at load time.
"""
from __future__ import annotations

import datetime as dt
import sys
import time
import zoneinfo
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CACHE = REPO_ROOT / "research_engine" / "cache"
IST = zoneinfo.ZoneInfo("Asia/Kolkata")

FETCH_SPECS = {
    "daily": {"period": "2y", "interval": "1d"},
    "60m": {"period": "1y", "interval": "60m"},
    "15m": {"period": "60d", "interval": "15m"},
}
_BATCH = 60


def _yahoo_symbol(sym: str) -> str:
    return sym if sym.startswith("^") else f"{sym}.NS"


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.title)
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep].dropna(subset=["Open", "High", "Low", "Close"])
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df


def fetch_batch(symbols: list[str], tf: str) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    spec = FETCH_SPECS[tf]
    tickers = [_yahoo_symbol(s) for s in symbols]
    raw = yf.download(
        tickers=" ".join(tickers), period=spec["period"], interval=spec["interval"],
        auto_adjust=False, group_by="ticker", threads=True, progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    for sym, tick in zip(symbols, tickers):
        try:
            df = raw[tick] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = _clean(df)
            if not df.empty:
                out[sym] = df
        except (KeyError, TypeError):
            continue
    return out


def fetch_all(symbols: list[str], tf: str, force: bool = False) -> None:
    dest = CACHE / tf
    dest.mkdir(parents=True, exist_ok=True)
    todo = [s for s in symbols if force or not (dest / f"{_safe(s)}.parquet").exists()]
    print(f"[{tf}] {len(symbols)} requested, {len(todo)} to fetch")
    missing: list[str] = []
    for i in range(0, len(todo), _BATCH):
        batch = todo[i : i + _BATCH]
        got = fetch_batch(batch, tf)
        for sym in batch:
            if sym in got:
                got[sym].to_parquet(dest / f"{_safe(sym)}.parquet")
            else:
                missing.append(sym)
        print(f"[{tf}] batch {i // _BATCH + 1}: {len(got)}/{len(batch)} ok")
        time.sleep(2)
    # one retry pass for stragglers, singly
    still_missing = []
    for sym in missing:
        got = fetch_batch([sym], tf)
        if sym in got:
            got[sym].to_parquet(dest / f"{_safe(sym)}.parquet")
        else:
            still_missing.append(sym)
        time.sleep(0.5)
    if still_missing:
        print(f"[{tf}] MISSING after retry: {still_missing}")


def _safe(sym: str) -> str:
    return sym.replace("^", "IDX_").replace("&", "_AND_")


def _drop_forming(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    now = dt.datetime.now(IST)
    last = df.index[-1]
    last_date = last.date() if last.tzinfo is None else last.astimezone(IST).date()
    if last_date >= now.date() and now.hour < 16:
        return df.iloc[:-1]
    return df


def load(sym: str, tf: str) -> pd.DataFrame | None:
    """Load a cached frame; weekly/75m are derived here."""
    if tf == "weekly":
        base = load(sym, "daily")
        if base is None:
            return None
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        wk = base.resample("W-FRI").agg(agg).dropna(subset=["Open", "High", "Low", "Close"])
        # last (possibly incomplete) week is only kept if the week has ended
        if not wk.empty and base.index[-1].weekday() < 4:
            wk = wk.iloc[:-1]
        return wk
    if tf == "75m":
        base = load(sym, "15m")
        if base is None:
            return None
        agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        r = base.resample("75min", origin="start_day", offset="555min").agg(agg)
        return r.dropna(subset=["Open", "High", "Low", "Close"])
    path = CACHE / tf / f"{_safe(sym)}.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    return _drop_forming(df)


if __name__ == "__main__":
    from research_engine.harness.universe import fno_symbols, INDEX_TICKERS, SECTOR_INDEX_TICKERS

    which = sys.argv[1] if len(sys.argv) > 1 else "daily"
    scope = sys.argv[2] if len(sys.argv) > 2 else "all"
    syms = fno_symbols()
    if scope == "nifty50":
        from research_engine.harness.universe import nifty50_symbols
        syms = nifty50_symbols()
    syms = syms + list(dict.fromkeys(list(INDEX_TICKERS.values()) + list(SECTOR_INDEX_TICKERS.values())))
    fetch_all(syms, which)
    print("done", which)
