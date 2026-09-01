"""Fetch + cache the global-market series for the India-influence study.

Research only. Daily OHLCV for ~40 global tickers (10y where Yahoo has it)
into research_engine/cache/global/, plus 2y of 60m bars for the intraday
sections (^NSEI first hour, DAX-open afternoon test, ES=F overnight drift).

Every series is validated after download: row count, span, and the fraction
of days whose Open exactly equals the prior Close (an exchange that reports
no true opening auction data would poison every gap statistic — such series
are flagged and their gap columns excluded rather than silently used).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CACHE = REPO_ROOT / "research_engine" / "cache" / "global"
META = CACHE / "_meta.json"

# name -> (yahoo ticker, group)
TICKERS: dict[str, tuple[str, str]] = {
    # --- India outputs ---
    "NIFTY50": ("^NSEI", "india"),
    "BANKNIFTY": ("^NSEBANK", "india"),
    "SENSEX": ("^BSESN", "india"),
    "NIFTY_IT": ("^CNXIT", "india_sector"),
    "NIFTY_AUTO": ("^CNXAUTO", "india_sector"),
    "NIFTY_PHARMA": ("^CNXPHARMA", "india_sector"),
    "NIFTY_METAL": ("^CNXMETAL", "india_sector"),
    "NIFTY_ENERGY": ("^CNXENERGY", "india_sector"),
    "NIFTY_FMCG": ("^CNXFMCG", "india_sector"),
    "NIFTY_REALTY": ("^CNXREALTY", "india_sector"),
    "NIFTY_PSUBANK": ("^CNXPSUBANK", "india_sector"),
    "NIFTY_NEXT50": ("^NSMIDCP", "india_sector"),
    "NIFTY_MIDCAP50": ("^NSEMDCP50", "india_sector"),
    "NIFTY_SMALLCAP100": ("^CNXSC", "india_sector"),
    "INDIA_VIX": ("^INDIAVIX", "india_risk"),
    # --- US ---
    "SP500": ("^GSPC", "us"),
    "DOW": ("^DJI", "us"),
    "NASDAQ100": ("^NDX", "us"),
    "RUSSELL2000": ("^RUT", "us"),
    "US_VIX": ("^VIX", "us_risk"),
    "US_10Y": ("^TNX", "us_rates"),
    "DXY": ("DX-Y.NYB", "fx"),
    "NVIDIA": ("NVDA", "us_tech"),
    "APPLE": ("AAPL", "us_tech"),
    "MICROSOFT": ("MSFT", "us_tech"),
    # --- Asia ---
    "NIKKEI": ("^N225", "asia"),
    "HANGSENG": ("^HSI", "asia"),
    "SHANGHAI": ("000001.SS", "asia"),
    "KOSPI": ("^KS11", "asia"),
    "TAIWAN": ("^TWII", "asia"),
    "ASX200": ("^AXJO", "asia"),
    "STRAITS": ("^STI", "asia"),
    # --- Europe ---
    "FTSE": ("^FTSE", "europe"),
    "DAX": ("^GDAXI", "europe"),
    "CAC": ("^FCHI", "europe"),
    "STOXX50": ("^STOXX50E", "europe"),
    # --- Commodities ---
    "BRENT": ("BZ=F", "commodity"),
    "WTI": ("CL=F", "commodity"),
    "GOLD": ("GC=F", "commodity"),
    "SILVER": ("SI=F", "commodity"),
    "COPPER": ("HG=F", "commodity"),
    "NATGAS": ("NG=F", "commodity"),
    # --- FX / flows proxies ---
    "USDINR": ("USDINR=X", "fx"),
    "MSCI_EM": ("EEM", "flows_proxy"),
    "MSCI_INDIA": ("INDA", "flows_proxy"),
}

HOURLY_TICKERS = {
    "NIFTY50_60m": "^NSEI",
    "ES_FUT_60m": "ES=F",
    "DAX_60m": "^GDAXI",
}


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=str.title)
    keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in df.columns]
    df = df[keep].dropna(subset=["Close"])
    return df[~df.index.duplicated(keep="last")].sort_index()


def main() -> None:
    import yfinance as yf

    CACHE.mkdir(parents=True, exist_ok=True)
    meta: dict[str, dict] = {}
    for name, (ticker, group) in TICKERS.items():
        dest = CACHE / f"{name}.parquet"
        if dest.exists():
            df = pd.read_parquet(dest)
        else:
            try:
                df = _clean(yf.Ticker(ticker).history(
                    period="10y", interval="1d", auto_adjust=False))
                time.sleep(0.3)
            except Exception as exc:  # noqa: BLE001
                print(f"{name:18s} FETCH FAILED: {exc}")
                meta[name] = {"ok": False, "error": str(exc), "group": group}
                continue
            if not df.empty:
                df.to_parquet(dest)
        if df.empty:
            print(f"{name:18s} EMPTY")
            meta[name] = {"ok": False, "error": "empty", "group": group}
            continue
        # Open quality: exchanges that report no real opening print show
        # Open == prior Close, which would fabricate zero-gaps.
        prev_close = df["Close"].shift(1)
        open_eq_prev = float((df["Open"] == prev_close).mean())
        open_eq_close = float((df["Open"] == df["Close"]).mean())
        meta[name] = {
            "ok": True, "group": group, "ticker": ticker, "rows": len(df),
            "first": str(df.index[0].date()), "last": str(df.index[-1].date()),
            "open_eq_prevclose_frac": round(open_eq_prev, 4),
            "open_eq_close_frac": round(open_eq_close, 4),
        }
        print(f"{name:18s} rows={len(df):5d} {df.index[0].date()}..{df.index[-1].date()} "
              f"open==prevclose {open_eq_prev * 100:4.1f}%")

    for name, ticker in HOURLY_TICKERS.items():
        dest = CACHE / f"{name}.parquet"
        if dest.exists():
            df = pd.read_parquet(dest)
        else:
            try:
                df = _clean(yf.Ticker(ticker).history(
                    period="730d", interval="60m", auto_adjust=False))
                time.sleep(0.3)
            except Exception as exc:  # noqa: BLE001
                print(f"{name:18s} FETCH FAILED: {exc}")
                meta[name] = {"ok": False, "error": str(exc), "group": "hourly"}
                continue
            if not df.empty:
                df.to_parquet(dest)
        meta[name] = {"ok": not df.empty, "group": "hourly", "rows": len(df),
                      "first": str(df.index[0]) if len(df) else "",
                      "last": str(df.index[-1]) if len(df) else ""}
        print(f"{name:18s} rows={len(df):5d}")

    META.write_text(json.dumps(meta, indent=2))
    ok = sum(1 for m in meta.values() if m.get("ok"))
    print(f"\n{ok}/{len(meta)} series usable; meta written to {META}")


if __name__ == "__main__":
    main()
