"""Earnings/results calendar for the Reports page.

Supplies, per symbol, the next result date plus the consensus EPS and revenue
estimates, and the recent reported history with its surprise percentage.

**Why this is disk-cached rather than fetched on demand.** Each symbol costs
one ``calendar`` call plus one ``earnings_dates`` call — measured at ~790ms
cold. Over the 208-stock F&O universe that is ~164 seconds, which cannot sit
in a page render. Result dates change rarely (a company announces one board
meeting per quarter), so a cache keyed on the calendar day is both safe and
sufficient: the first refresh of the day pays the cost, every later load is
a disk read.

Kept free of Streamlit so it stays testable; the caller owns progress
reporting and any in-memory caching.

What is NOT here, deliberately:

* **BMO/AMO session.** yfinance carries a timestamp but it is US/Eastern
  converted and does not reliably indicate an Indian pre- or post-market
  session. NSE publishes board-meeting intimations with the session, but only
  as unstructured announcements. Reported as unknown rather than guessed.
* **Open interest / volume spike.** Needs an F&O derivatives pipeline
  (``jugaad_data.derivatives_df``) with its own history and cache. Phase 2.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, TypedDict

import pandas as pd
import yfinance as yf

from utils.helpers import load_predefined_watchlists
from utils.logger import get_logger

logger = get_logger(__name__)

# Module-level so tests can redirect it to tmp_path. Writing a fixture into
# the real cache would have the app serve fabricated earnings dates back as
# genuine — the same trap the bhavcopy cache hit (see Gotcha 19).
_DISK_CACHE = Path.home() / ".market-lens" / "earnings"

# One crore, for converting yfinance's absolute revenue figures.
_CRORE = 1e7

# Sector watchlists we already ship. Used as the Sector column: instant, and
# in NSE's own taxonomy rather than yfinance's US-style sector names (which
# would also cost one network call per symbol).
_SECTOR_LISTS = {
    "Nifty Auto": "Auto",
    "Nifty Bank": "Banks",
    "Nifty IT": "IT",
    "Nifty Pharma": "Pharma",
    "Nifty Metal": "Metals",
    "Nifty Energy": "Energy",
    "Nifty FMCG": "FMCG",
}

# --- High Impact thresholds ------------------------------------------------
# Deliberately simple and inspectable: every one of these appears verbatim in
# the reason string shown in the UI tooltip, so a user can always see why a
# row was flagged rather than trusting an opaque score.
HIGH_IMPACT_WITHIN_DAYS = 2      # result due today, tomorrow or the day after
HIGH_IMPACT_SURPRISE_PCT = 5.0   # |EPS surprise| that counts as large
HIGH_IMPACT_REACTION_PCT = 3.0   # |price move| on result day that counts
HIGH_IMPACT_REVENUE_CR = 10_000  # revenue estimate that counts as major
HIGH_IMPACT_MOVE_PCT = 5.0       # recent multi-session move that counts


class EarningsRow(TypedDict, total=False):
    """One symbol's calendar entry."""
    symbol: str
    result_date: str | None      # ISO date of the NEXT result
    eps_estimate: float | None
    revenue_estimate_cr: float | None
    last_result_date: str | None  # ISO date of the most recent REPORTED result
    reported_eps: float | None
    surprise_pct: float | None
    fetched_on: str
    ok: bool


# ---------------------------------------------------------------------------
# Sector lookup
# ---------------------------------------------------------------------------

def sector_map() -> dict[str, str]:
    """Symbol -> sector, from the predefined sector watchlists."""
    out: dict[str, str] = {}
    try:
        for wl in load_predefined_watchlists():
            label = _SECTOR_LISTS.get(wl.get("name", ""))
            if not label:
                continue
            for sym in wl.get("symbols", []):
                out.setdefault(sym, label)
    except Exception as exc:
        logger.warning("Could not build sector map: %s", exc)
    return out


def fno_symbols() -> set[str]:
    """The F&O universe, from the shipped watchlist."""
    try:
        for wl in load_predefined_watchlists():
            if wl.get("name") == "F&O Stocks":
                return set(wl.get("symbols", []))
    except Exception as exc:
        logger.warning("Could not load F&O list: %s", exc)
    return set()


def index_symbols(name: str) -> set[str]:
    """Members of one predefined watchlist, e.g. "Nifty 50"."""
    try:
        for wl in load_predefined_watchlists():
            if wl.get("name") == name:
                return set(wl.get("symbols", []))
    except Exception:
        pass
    return set()


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

def _cache_file(symbol: str) -> Path:
    # Symbols can contain "&" (M&M); keep the filename filesystem-safe.
    safe = symbol.replace("&", "_and_").replace("/", "_")
    return _DISK_CACHE / f"{safe}.json"


def load_cached(symbol: str, max_age_days: int = 1) -> EarningsRow | None:
    """Return the cached row when it is still fresh, else ``None``.

    Freshness is by calendar day, not by clock: a result date announced this
    morning is still correct this evening.
    """
    path = _cache_file(symbol)
    try:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(payload["fetched_on"]).date()
        if (date.today() - fetched).days >= max_age_days:
            return None
        return payload
    except Exception:
        return None


def save_cached(row: EarningsRow) -> None:
    try:
        _DISK_CACHE.mkdir(parents=True, exist_ok=True)
        _cache_file(row["symbol"]).write_text(
            json.dumps(row), encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not cache earnings for %s: %s",
                       row.get("symbol"), exc)


def cache_status(symbols: list[str]) -> tuple[int, int]:
    """(cached_and_fresh, total) — drives the "needs refresh" hint."""
    fresh = sum(1 for s in symbols if load_cached(s) is not None)
    return fresh, len(symbols)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _as_float(value: Any) -> float | None:
    try:
        f = float(value)
        return None if pd.isna(f) else f
    except (TypeError, ValueError):
        return None


def fetch_one(symbol: str) -> EarningsRow:
    """Fetch one symbol's calendar. Never raises."""
    row: EarningsRow = {
        "symbol": symbol, "result_date": None, "eps_estimate": None,
        "revenue_estimate_cr": None, "last_result_date": None,
        "reported_eps": None, "surprise_pct": None,
        "fetched_on": date.today().isoformat(), "ok": False,
    }
    try:
        ticker = yf.Ticker(f"{symbol}.NS")

        cal = ticker.calendar or {}
        dates = cal.get("Earnings Date") or []
        if dates:
            nxt = dates[0]
            row["result_date"] = (
                nxt.isoformat() if hasattr(nxt, "isoformat") else str(nxt)
            )
        row["eps_estimate"] = _as_float(cal.get("Earnings Average"))
        rev = _as_float(cal.get("Revenue Average"))
        row["revenue_estimate_cr"] = (rev / _CRORE) if rev else None

        # Most recent REPORTED quarter, for the released-results panels.
        hist = ticker.earnings_dates
        if hist is not None and not hist.empty and "Reported EPS" in hist:
            reported = hist[hist["Reported EPS"].notna()]
            if not reported.empty:
                # Index is descending (newest first) but sort to be certain.
                reported = reported.sort_index(ascending=False)
                idx = reported.index[0]
                row["last_result_date"] = idx.date().isoformat()
                row["reported_eps"] = _as_float(
                    reported["Reported EPS"].iloc[0])
                if "Surprise(%)" in reported:
                    row["surprise_pct"] = _as_float(
                        reported["Surprise(%)"].iloc[0])
        row["ok"] = True
    except Exception as exc:
        logger.warning("Earnings fetch failed for %s: %s", symbol, exc)
    return row


def get_earnings(
    symbols: list[str],
    force: bool = False,
    cache_only: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, EarningsRow]:
    """Calendar rows for *symbols*, cache-first.

    Args:
        symbols: NSE symbols without the ``.NS`` suffix.
        force: Ignore the cache and refetch everything.
        cache_only: Return ONLY what is already on disk and never touch the
            network. This is what a page render must use — without it a page
            that merely opens would fetch every uncached symbol, which for
            the F&O universe is ~164 seconds of blocking work nobody asked
            for. Fetching is an explicit action, not a side effect of
            navigation.
        progress: Called as ``(done, total, symbol)`` per fetched symbol —
            only for symbols that actually hit the network, so a fully cached
            run reports no progress and returns immediately.
    """
    out: dict[str, EarningsRow] = {}
    to_fetch: list[str] = []

    for sym in symbols:
        cached = None if force else load_cached(sym)
        if cached is not None:
            out[sym] = cached
        elif not cache_only:
            to_fetch.append(sym)

    for i, sym in enumerate(to_fetch, 1):
        if progress:
            progress(i, len(to_fetch), sym)
        row = fetch_one(sym)
        save_cached(row)
        out[sym] = row
    return out


def fetch_price_reactions(
    symbols: list[str], lookback_days: int = 12,
) -> dict[str, float]:
    """Close-to-close move on each symbol's most recent result day.

    Batched into one download rather than one call per symbol: only a handful
    of companies report on any given day, so this stays cheap.
    """
    if not symbols:
        return {}
    out: dict[str, float] = {}
    try:
        tickers = [f"{s}.NS" for s in symbols]
        data = yf.download(
            tickers, period=f"{lookback_days}d", interval="1d",
            auto_adjust=False, progress=False, group_by="ticker",
            threads=True,
        )
        for sym in symbols:
            try:
                frame = data[f"{sym}.NS"] if len(tickers) > 1 else data
                closes = frame["Close"].dropna()
                if len(closes) >= 2:
                    out[sym] = float(
                        (closes.iloc[-1] - closes.iloc[-2])
                        / closes.iloc[-2] * 100
                    )
            except Exception:
                continue
    except Exception as exc:
        logger.warning("Price reaction batch failed: %s", exc)
    return out


# ---------------------------------------------------------------------------
# Derived fields
# ---------------------------------------------------------------------------

def days_until(iso_date: str | None) -> int | None:
    """Whole days from today to *iso_date*; negative once it has passed."""
    if not iso_date:
        return None
    try:
        return (date.fromisoformat(iso_date[:10]) - date.today()).days
    except Exception:
        return None


def countdown_label(days: int | None) -> str:
    if days is None:
        return "—"
    if days < 0:
        return "Released"
    if days == 0:
        return "TODAY"
    if days == 1:
        return "1 DAY"
    return f"{days} DAYS"


def status_label(days: int | None) -> str:
    if days is None:
        return "No date"
    if days < 0:
        return "Released"
    if days == 0:
        return "Due Today"
    return "Upcoming"


def classify_impact(
    row: EarningsRow,
    *,
    is_fno: bool,
    in_major_list: bool,
    price_reaction: float | None = None,
) -> tuple[bool, str]:
    """Is this a High Impact result, and why?

    The rule is deliberately transparent — the returned reason is shown in the
    UI tooltip, so a flag can always be traced back to the condition that set
    it rather than to an opaque score.

    Upcoming results are High Impact when all of:
      * due within :data:`HIGH_IMPACT_WITHIN_DAYS` days, and
      * the stock is in the F&O universe, and
      * it is in a major list (Nifty 50 / Bank / sector) OR carries a large
        revenue estimate.

    Released results are High Impact when the EPS surprise or the price
    reaction on result day was large, regardless of index membership — a big
    surprise matters wherever it happens.
    """
    days = days_until(row.get("result_date"))
    rev = row.get("revenue_estimate_cr")
    surprise = row.get("surprise_pct")

    if days is not None and days < 0 or days is None:
        reasons = []
        if surprise is not None and abs(surprise) >= HIGH_IMPACT_SURPRISE_PCT:
            reasons.append(
                f"EPS surprise {surprise:+.1f}% "
                f"(>= {HIGH_IMPACT_SURPRISE_PCT:g}%)"
            )
        if (price_reaction is not None
                and abs(price_reaction) >= HIGH_IMPACT_REACTION_PCT):
            reasons.append(
                f"price reaction {price_reaction:+.1f}% "
                f"(>= {HIGH_IMPACT_REACTION_PCT:g}%)"
            )
        if reasons:
            return True, "Released: " + " and ".join(reasons)
        return False, "Released without a large surprise or price reaction"

    if days > HIGH_IMPACT_WITHIN_DAYS:
        return False, f"Result is {days} days away (> {HIGH_IMPACT_WITHIN_DAYS})"
    if not is_fno:
        return False, "Not an F&O stock"

    big_revenue = rev is not None and rev >= HIGH_IMPACT_REVENUE_CR
    if not (in_major_list or big_revenue):
        return False, "Not in a major index or sector list, and no large revenue estimate"

    why = [f"result in {days} day(s)", "F&O stock"]
    if in_major_list:
        why.append("in a major index/sector list")
    if big_revenue:
        why.append(f"revenue estimate {rev:,.0f} Cr "
                   f"(>= {HIGH_IMPACT_REVENUE_CR:,} Cr)")
    return True, "Upcoming: " + ", ".join(why)


def impact_rule_text() -> str:
    """The rule, verbatim, for the page's explainer."""
    return (
        f"**Upcoming** — flagged when all of: result due within "
        f"{HIGH_IMPACT_WITHIN_DAYS} days · stock is in the F&O universe · "
        f"and it is in a major index/sector list **or** has a revenue "
        f"estimate of {HIGH_IMPACT_REVENUE_CR:,} Cr or more.\n\n"
        f"**Released** — flagged when the EPS surprise is at least "
        f"{HIGH_IMPACT_SURPRISE_PCT:g}% **or** the price moved at least "
        f"{HIGH_IMPACT_REACTION_PCT:g}% on result day.\n\n"
        f"Hover any Impact badge to see which condition matched."
    )


def upcoming_groups(
    rows: dict[str, EarningsRow], horizon_days: int = 7,
) -> list[tuple[str, list[str]]]:
    """Symbols grouped by result date, for the Upcoming Calendar panel."""
    buckets: dict[str, list[str]] = {}
    for sym, row in rows.items():
        d = days_until(row.get("result_date"))
        if d is None or d < 0 or d > horizon_days:
            continue
        buckets.setdefault(row["result_date"][:10], []).append(sym)
    return sorted(buckets.items())


def date_heading(iso_date: str) -> str:
    """"02 Aug 2026 (Today)" / "(Tomorrow)" / "(In 2 Days)"."""
    try:
        d = date.fromisoformat(iso_date)
    except Exception:
        return iso_date
    delta = (d - date.today()).days
    suffix = {0: " (Today)", 1: " (Tomorrow)"}.get(delta)
    if suffix is None:
        suffix = f" (In {delta} Days)" if delta > 1 else ""
    return d.strftime("%d %b %Y") + suffix


def recent_releases(
    rows: dict[str, EarningsRow], within_days: int = 7,
) -> list[tuple[str, EarningsRow]]:
    """Symbols that reported within the last *within_days*, newest first."""
    out = []
    cutoff = date.today() - timedelta(days=within_days)
    for sym, row in rows.items():
        last = row.get("last_result_date")
        if not last:
            continue
        try:
            d = date.fromisoformat(last[:10])
        except Exception:
            continue
        if d >= cutoff:
            out.append((sym, row))
    out.sort(key=lambda item: item[1].get("last_result_date") or "",
             reverse=True)
    return out
