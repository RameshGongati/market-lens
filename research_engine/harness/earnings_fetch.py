"""Fetch historical earnings dates + EPS surprises for the F&O universe.

Point-in-time note: exchange result dates are announced days/weeks in advance
via board-meeting intimations, so treating the NEXT result date as known at
signal time is a fair assumption (stated in the report). Revenue surprises are
NOT available from this source — marked unavailable, never invented.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.harness.universe import fno_symbols  # noqa: E402

CACHE = REPO_ROOT / "research_engine" / "cache"


def main() -> None:
    import yfinance as yf

    rows = []
    syms = fno_symbols()
    for k, sym in enumerate(syms, 1):
        try:
            t = yf.Ticker(f"{sym}.NS")
            ed = t.get_earnings_dates(limit=12)
            if ed is None or ed.empty:
                continue
            for ts, r in ed.iterrows():
                rows.append({
                    "symbol": sym,
                    "earnings_date": pd.Timestamp(ts).tz_localize(None).normalize(),
                    "eps_estimate": r.get("EPS Estimate"),
                    "eps_actual": r.get("Reported EPS"),
                    "surprise_pct": r.get("Surprise(%)"),
                })
        except Exception as e:  # noqa: BLE001
            print("skip", sym, type(e).__name__)
        if k % 25 == 0:
            print(f"{k}/{len(syms)}", flush=True)
            time.sleep(1)
    df = pd.DataFrame(rows).drop_duplicates(["symbol", "earnings_date"])
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE / "earnings.parquet")
    print("saved", len(df), "earnings rows for", df["symbol"].nunique(), "symbols")


if __name__ == "__main__":
    main()
