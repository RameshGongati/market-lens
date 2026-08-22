"""Universe + sector mapping for the F&O backtest study.

Sectors come from the shipped sector watchlists first, then the heatmap's
manual group lists. Anything unmapped is "Other" (the report states this).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_WATCHLISTS = REPO_ROOT / "data" / "predefined_watchlists.json"

# Sector watchlist name -> report sector label
_SECTOR_WATCHLISTS = {
    "Nifty Auto": "Auto",
    "Nifty Bank": "Banking",
    "Nifty IT": "IT",
    "Nifty Pharma": "Pharma",
    "Nifty Metal": "Metal",
    "Nifty Energy": "Energy",
    "Nifty FMCG": "FMCG",
}

# Heatmap manual groups (data/market_heatmap.py) -> report sector label
_HEATMAP_GROUP_SECTORS = {
    "financials": "Financial Services",
    "psubank": "PSU Bank",
    "realty": "Realty",
    "oilgas": "Oil & Gas",
    "consdurbl": "Consumer Durables",
    "media": "Media",
    "healthcare": "Healthcare",
}

# Indices scanned as tradeable instruments AND used for regime context.
INDEX_TICKERS = {
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}

# Sector indices: regime context only (patchy Yahoo coverage — see heatmap notes).
SECTOR_INDEX_TICKERS = {
    "IT": "^CNXIT",
    "Auto": "^CNXAUTO",
    "Pharma": "^CNXPHARMA",
    "Metal": "^CNXMETAL",
    "Energy": "^CNXENERGY",
    "FMCG": "^CNXFMCG",
    "Realty": "^CNXREALTY",
    "PSU Bank": "^CNXPSUBANK",
    "Financial Services": "^CNXFIN",
    "Banking": "^NSEBANK",
}


def _load_watchlists() -> list[dict]:
    data = json.loads(_WATCHLISTS.read_text())
    if isinstance(data, dict):
        data = data.get("watchlists", list(data.values()))
    return data


def fno_symbols() -> list[str]:
    for wl in _load_watchlists():
        if wl["name"] == "F&O Stocks":
            return list(dict.fromkeys(wl["symbols"]))
    raise RuntimeError("F&O Stocks watchlist not found")


def nifty50_symbols() -> list[str]:
    for wl in _load_watchlists():
        if wl["name"] == "Nifty 50":
            return list(dict.fromkeys(wl["symbols"]))
    raise RuntimeError("Nifty 50 watchlist not found")


def sector_map() -> dict[str, str]:
    """symbol -> sector. Sector watchlists take priority over heatmap manual lists."""
    mapping: dict[str, str] = {}
    # Heatmap manual lists first (lower priority — overwritten below)
    try:
        from data.market_heatmap import _MANUAL_GROUP_SYMBOLS  # type: ignore
        for group_id, sector in _HEATMAP_GROUP_SECTORS.items():
            for sym in _MANUAL_GROUP_SYMBOLS.get(group_id, ()):
                mapping.setdefault(sym, sector)
    except Exception:
        pass
    for wl in _load_watchlists():
        sector = _SECTOR_WATCHLISTS.get(wl["name"])
        if sector:
            for sym in wl["symbols"]:
                mapping[sym] = sector
    return mapping


def company_names() -> dict[str, str]:
    try:
        from utils.helpers import load_stock_list  # type: ignore
        stocks = load_stock_list()
        out = {}
        for s in stocks:
            if isinstance(s, dict):
                sym = s.get("symbol") or s.get("Symbol")
                name = s.get("name") or s.get("company_name") or s.get("Company Name") or ""
                if sym:
                    out[sym] = name
        return out
    except Exception:
        return {}


if __name__ == "__main__":
    fno = fno_symbols()
    smap = sector_map()
    covered = sum(1 for s in fno if s in smap)
    print(f"F&O universe: {len(fno)} symbols; sector-mapped: {covered}; Other: {len(fno) - covered}")
    from collections import Counter
    print(Counter(smap.get(s, "Other") for s in fno).most_common())
