"""Fetch current index constituents and F&O stock lists from NSE.

Index constituents (Nifty 50, Bank, IT, etc.) are fetched from
niftyindices.com CSV files — no session/cookie required, just a
browser-like User-Agent header.

F&O stocks are fetched from the NSE API which requires a session
cookie obtained by first hitting the NSE homepage.
"""

import csv
import json
import logging
import time
from io import StringIO
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_PREDEFINED_WL_PATH = Path(__file__).parent / "predefined_watchlists.json"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) "
    "Gecko/20100101 Firefox/118.0"
)
_TIMEOUT = 30

# niftyindices.com CSV endpoints — no session needed, stable format.
# Each CSV has columns: Company Name, Industry, Symbol, Series, ISIN Code
_INDEX_CSV_URLS: dict[str, str] = {
    "Nifty 50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "Nifty Next 50": "https://www.niftyindices.com/IndexConstituent/ind_niftynext50list.csv",
    "Nifty Auto": "https://www.niftyindices.com/IndexConstituent/ind_niftyautolist.csv",
    "Nifty Bank": "https://www.niftyindices.com/IndexConstituent/ind_niftybanklist.csv",
    "Nifty IT": "https://www.niftyindices.com/IndexConstituent/ind_niftyitlist.csv",
    "Nifty Pharma": "https://www.niftyindices.com/IndexConstituent/ind_niftypharmalist.csv",
    "Nifty Metal": "https://www.niftyindices.com/IndexConstituent/ind_niftymetallist.csv",
    "Nifty Energy": "https://www.niftyindices.com/IndexConstituent/ind_niftyenergylist.csv",
    "Nifty FMCG": "https://www.niftyindices.com/IndexConstituent/ind_niftyfmcglist.csv",
}

# NSE API endpoint for F&O-eligible stocks (requires session cookie).
# The OI spurts endpoint reliably lists all F&O underlyings even
# after hours, unlike equity-stockIndices which was deprecated.
_FNO_API_URL = (
    "https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings"
)
_NSE_COOKIE_URL = "https://www.nseindia.com/"


def _create_nse_session() -> requests.Session:
    """Create an authenticated NSE session with valid cookies.

    NSE requires a session cookie from the homepage before API calls
    work.  Retries up to 2 times if the initial request times out.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": _UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.nseindia.com/",
    })
    for attempt in range(3):
        try:
            session.get(_NSE_COOKIE_URL, timeout=_TIMEOUT)
            return session
        except requests.exceptions.Timeout:
            if attempt < 2:
                time.sleep(1)
                continue
            raise
    return session


def _fetch_index_csv(name: str) -> list[str]:
    """Fetch index constituents from niftyindices.com CSV."""
    url = _INDEX_CSV_URLS[name]
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    resp.raise_for_status()
    reader = csv.DictReader(StringIO(resp.text))
    symbols: list[str] = []
    for row in reader:
        symbol = row.get("Symbol", "").strip()
        if symbol:
            symbols.append(symbol)
    return sorted(symbols)


# Index underlyings that appear in F&O data but are not individual
# stocks — these must be excluded from the F&O stock watchlist.
_INDEX_UNDERLYINGS = {
    "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
}


def _fetch_fno_stocks(session: requests.Session) -> list[str]:
    """Fetch F&O-eligible stock symbols from NSE OI spurts endpoint.

    Filters out index underlyings (NIFTY, BANKNIFTY, etc.) to return
    only individual stock symbols eligible for F&O trading.
    """
    resp = session.get(_FNO_API_URL, timeout=_TIMEOUT)
    resp.raise_for_status()
    data = resp.json().get("data", [])
    # Exclude index underlyings (both space-separated like "NIFTY 50"
    # and single-word like "BANKNIFTY") to keep only stock symbols
    symbols = [
        item["symbol"]
        for item in data
        if "symbol" in item
        and " " not in item["symbol"]
        and item["symbol"] not in _INDEX_UNDERLYINGS
    ]
    return sorted(symbols)


def refresh_all_watchlists() -> dict[str, Any]:
    """Fetch all index constituents and F&O stocks, update the JSON file.

    Returns a summary dict with per-list change details:
      - updated: list names that had changes (added/removed stocks)
      - unchanged: list names fetched successfully but with no diff
      - failed: list names with error descriptions
      - changes: {name: {"added": [...], "removed": [...]}} per updated list
      - total_symbols: {name: count} for all successfully fetched lists
    """
    results: dict[str, Any] = {
        "updated": [],
        "unchanged": [],
        "failed": [],
        "changes": {},
        "total_symbols": {},
    }

    # Load current watchlists to preserve order and descriptions
    try:
        current = json.loads(_PREDEFINED_WL_PATH.read_text(encoding="utf-8"))
    except Exception:
        current = []
    wl_by_name = {w["name"]: w for w in current}

    # Fetch index CSVs (no session needed, can batch quickly)
    for name, url in _INDEX_CSV_URLS.items():
        try:
            symbols = _fetch_index_csv(name)
            if symbols:
                # Compare old vs new to identify added/removed stocks
                old_symbols = set(wl_by_name[name]["symbols"]) if name in wl_by_name else set()
                new_symbols = set(symbols)
                added = sorted(new_symbols - old_symbols)
                removed = sorted(old_symbols - new_symbols)

                if name in wl_by_name:
                    wl_by_name[name]["symbols"] = symbols
                else:
                    wl_by_name[name] = {
                        "name": name,
                        "description": f"{name} index constituents",
                        "symbols": symbols,
                    }

                # Classify as updated (has diff) or unchanged (same stocks)
                if added or removed:
                    results["updated"].append(name)
                    results["changes"][name] = {"added": added, "removed": removed}
                else:
                    results["unchanged"].append(name)
                results["total_symbols"][name] = len(symbols)
            else:
                results["failed"].append(f"{name}: empty response")
        except Exception as e:
            logger.warning("Failed to fetch %s: %s", name, e)
            results["failed"].append(f"{name}: {e}")
        time.sleep(0.3)

    # Fetch F&O stocks (needs session cookie from NSE homepage)
    try:
        session = _create_nse_session()
        time.sleep(0.5)
        fno_symbols = _fetch_fno_stocks(session)
        if fno_symbols:
            old_fno = set(wl_by_name["F&O Stocks"]["symbols"]) if "F&O Stocks" in wl_by_name else set()
            new_fno = set(fno_symbols)
            added = sorted(new_fno - old_fno)
            removed = sorted(old_fno - new_fno)

            if "F&O Stocks" in wl_by_name:
                wl_by_name["F&O Stocks"]["symbols"] = fno_symbols
            else:
                wl_by_name["F&O Stocks"] = {
                    "name": "F&O Stocks",
                    "description": "All stocks eligible for Futures & Options trading",
                    "symbols": fno_symbols,
                }

            if added or removed:
                results["updated"].append("F&O Stocks")
                results["changes"]["F&O Stocks"] = {"added": added, "removed": removed}
            else:
                results["unchanged"].append("F&O Stocks")
            results["total_symbols"]["F&O Stocks"] = len(fno_symbols)
        else:
            results["failed"].append("F&O Stocks: empty response")
    except Exception as e:
        logger.warning("Failed to fetch F&O stocks: %s", e)
        results["failed"].append(f"F&O Stocks: {e}")

    # Rebuild the list preserving original order, appending any new entries
    ordered_names = [w["name"] for w in current]
    for name in wl_by_name:
        if name not in ordered_names:
            ordered_names.append(name)
    updated_list = [wl_by_name[n] for n in ordered_names if n in wl_by_name]

    _PREDEFINED_WL_PATH.write_text(
        json.dumps(updated_list, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return results
