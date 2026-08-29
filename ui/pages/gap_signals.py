"""Signals page — home of rules graduated from the Research Engine.

V1.1 carries one rule: Gap-Up Continuation (daily, long only, confirmed
end-of-day bars only), now shown with its FULL LIFECYCLE: signals from the
last ~60 sessions are walked forward under the exact backtest trade rules and
grouped into Active / Target hit / Stop loss hit / Time-stopped tabs, with
counts in the tab labels so the real win/loss ratio is always visible.

Detection and tracking live in analysis.gap_signals — the single source shared
with the research harness — so this page can never drift from what was
backtested.

Isolation contract: nothing here touches the zone engine, ODD/GTF scoring or
the Run Analysis flow. Zone context is read from the last scan's result dicts
(read-only); T9/T13 render as informational tags and affect nothing.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import streamlit as st

from analysis.gap_signals import (
    STATUS_ACTIVE, STATUS_AWAITING, STATUS_STOP, STATUS_TARGET, STATUS_TIME,
    EXCLUDED_SYMBOLS, GapSignal, TrackedSignal, detect_gap_up_continuation,
    evidence_rank, track_signal, volume_expansion_tag, zone_context_tag,
)
from data.manager import build_source_manager, fetch_by_interval
from storage.database import get_gap_scan, save_gap_scan
from ui.components.panels import page_title, scan_progress
from ui.components.stock_card import build_detail_url
from ui.pages.pattern_common import resolve_pattern_universe

_SCOPES = ["Current Watchlist", "Nifty 50", "F&O Stocks", "All NSE"]
_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SIGNAL_LOOKBACK_BARS = 60   # how far back signals are tracked (~3 months)

_SORTS = {
    "Most recent first": lambda r: (r.get("signal_date") or "", r.get("evidence_rank") or 0),
    "Days active": lambda r: (r.get("days_active") or 0, r.get("signal_date") or ""),
    "Unrealized R": lambda r: (r.get("r_multiple") if r.get("r_multiple") is not None else -99,),
    "Evidence rank": lambda r: (r.get("evidence_rank") or 0, r.get("signal_date") or ""),
}


# ---------------------------------------------------------------- pure helpers
def drop_forming_bar(df: pd.DataFrame, now: dt.datetime | None = None) -> pd.DataFrame:
    """Drop today's still-forming daily bar before 4 PM IST (the app rule)."""
    if df is None or df.empty or not isinstance(df.index, pd.DatetimeIndex):
        return df
    now = now or dt.datetime.now(_IST)
    last = df.index[-1]
    last_date = last.date() if last.tzinfo is None else last.tz_convert("Asia/Kolkata").date()
    if last_date >= now.date() and now.hour < 16:
        return df.iloc[:-1]
    return df


def days_to_result(earnings_row: dict | None, as_of: dt.date) -> int | None:
    """Days until the next result date, if the cached calendar knows it."""
    if not earnings_row:
        return None
    raw = earnings_row.get("result_date")
    if not raw:
        return None
    try:
        d = dt.date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None
    delta = (d - as_of).days
    return delta if 0 <= delta <= 30 else None


def build_row(symbol: str, company: str, tracked: TrackedSignal, *, from_zone: bool,
              volume_ok: bool, result_days: int | None,
              market_extended: bool) -> dict[str, Any]:
    sig: GapSignal = tracked.signal
    return {
        "symbol": symbol,
        "company_name": company,
        "status": tracked.status,
        "signal_date": str(pd.Timestamp(sig.date).date()),
        "gap_pct": sig.gap_pct,
        "entry_date": str(pd.Timestamp(tracked.entry_date).date()) if tracked.entry_date is not None else None,
        "entry_price": tracked.entry_price,
        "stop": round(sig.stop, 2),
        "target_2r": tracked.target if tracked.target is not None else sig.target_2r(),
        "exit_date": str(pd.Timestamp(tracked.exit_date).date()) if tracked.exit_date is not None else None,
        "exit_price": tracked.exit_price,
        "days_active": tracked.days_active,
        "r_multiple": tracked.r_multiple,
        "from_demand_zone": bool(from_zone),
        "volume_expansion": bool(volume_ok),
        "result_in_days": result_days,
        "market_extended": bool(market_extended),
        "evidence_rank": evidence_rank(from_zone, volume_ok),
    }


def refresh_open_signal_rows(
    rows: list[dict],
    *,
    fetch_fn,
    source_name: str,
    now: dt.datetime | None = None,
) -> tuple[list[dict], int]:
    """Refresh unresolved saved rows from completed daily bars.

    A stored scan is intentionally reusable across tabs, but its Active rows
    must not remain frozen after a later completed candle reaches a stop or
    target. Re-detecting the original signal by date and delegating to
    ``track_signal`` keeps this refresh byte-for-byte aligned with the
    validated lifecycle rules used by a full scan.
    """
    refreshed: list[dict] = []
    transitions = 0
    for row in rows:
        if row.get("status") not in (STATUS_ACTIVE, STATUS_AWAITING):
            refreshed.append(row)
            continue
        symbol = str(row.get("symbol") or "")
        signal_date = row.get("signal_date")
        if not symbol or not signal_date:
            refreshed.append(row)
            continue
        try:
            full_symbol = f"{symbol}.NS" if source_name == "Yahoo Finance" else symbol
            df = fetch_fn(full_symbol, "1y", "1d")
            df = drop_forming_bar(df, now=now)
            if df is None or len(df) < 30:
                refreshed.append(row)
                continue
            wanted_date = pd.Timestamp(signal_date).date()
            signal = next(
                (candidate for candidate in detect_gap_up_continuation(df)
                 if pd.Timestamp(candidate.date).date() == wanted_date),
                None,
            )
            if signal is None:
                refreshed.append(row)
                continue
            tracked = track_signal(df, signal)
            if tracked is None:
                refreshed.append(row)
                continue
            updated = dict(row)
            updated.update({
                "status": tracked.status,
                "entry_date": (
                    str(pd.Timestamp(tracked.entry_date).date())
                    if tracked.entry_date is not None else None
                ),
                "entry_price": tracked.entry_price,
                "target_2r": tracked.target if tracked.target is not None else row.get("target_2r"),
                "exit_date": (
                    str(pd.Timestamp(tracked.exit_date).date())
                    if tracked.exit_date is not None else None
                ),
                "exit_price": tracked.exit_price,
                "days_active": tracked.days_active,
                "r_multiple": tracked.r_multiple,
            })
            if updated["status"] != row.get("status"):
                transitions += 1
            refreshed.append(updated)
        except Exception:  # noqa: BLE001 -- one unavailable symbol must not hide the scan
            refreshed.append(row)
    return refreshed, transitions


def _outcome_refresh_marker(scan_id: str | None, now: dt.datetime) -> str:
    """Refresh once before and once after the daily bar is considered complete."""
    return f"{scan_id or 'session'}:{now.date().isoformat()}:{now.hour >= 16}"


def apply_tag_filters(rows: list[dict], *, zone_only: bool, volume_only: bool,
                      hide_results_week: bool) -> list[dict]:
    out = rows
    if zone_only:
        out = [r for r in out if r.get("from_demand_zone")]
    if volume_only:
        out = [r for r in out if r.get("volume_expansion")]
    if hide_results_week:
        out = [r for r in out if not (
            r.get("result_in_days") is not None and r["result_in_days"] <= 5)]
    return out


def split_by_status(rows: list[dict]) -> dict[str, list[dict]]:
    """Active (incl. awaiting entry) / target / stop loss / time-stopped."""
    groups: dict[str, list[dict]] = {STATUS_ACTIVE: [], STATUS_TARGET: [],
                                     STATUS_STOP: [], STATUS_TIME: []}
    for r in rows:
        status = r.get("status")
        key = STATUS_ACTIVE if status in (STATUS_ACTIVE, STATUS_AWAITING) else status
        if key in groups:
            groups[key].append(r)
    return groups


def sort_rows(rows: list[dict], sort_name: str) -> list[dict]:
    key = _SORTS.get(sort_name, _SORTS["Most recent first"])
    return sorted(rows, key=key, reverse=True)


def _nifty_extended(manager) -> bool:
    """Informational T13 tag: NIFTY more than 2 ATR above its 20-EMA."""
    try:
        df, _ = fetch_by_interval("^NSEI", "Daily", fetch_fn=manager.get_history)
        if df is None or len(df) < 30:
            return False
        df = drop_forming_bar(df)
        c = df["Close"]
        ema20 = c.ewm(span=20, adjust=False).mean()
        tr = pd.concat([df["High"] - df["Low"], (df["High"] - c.shift()).abs(),
                        (df["Low"] - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
        return bool((c.iloc[-1] - ema20.iloc[-1]) / atr.iloc[-1] > 2)
    except Exception:
        return False


# Concurrent fetches per data source — same policy as the dashboard scan:
# Yahoo serves a modest pool without throttling; NSE-direct sources rate-limit
# and share a session, so they keep the original sequential behaviour.
_SIGNAL_SCAN_WORKERS = {"Yahoo Finance": 8}


def _scan_symbol_worker(
    symbol: str,
    company_name: str,
    full_symbol: str,
    fetch_fn,
    zone_result: dict | None,
    earnings_date,
    market_ext: bool,
    today: dt.date,
) -> list[dict]:
    """Fetch + detect + track ONE symbol; returns its signal rows.

    Runs on a worker thread: no Streamlit calls, no session state — every
    contextual input is resolved by the caller beforehand. The fetch asks for
    1y of daily bars: detection only keeps signals from the last
    SIGNAL_LOOKBACK_BARS sessions, so the Daily interval's 5-year chart window
    was pure overhead here.
    """
    df = fetch_fn(full_symbol, "1y", "1d")
    if df is None or len(df) < 30:
        return []
    df = drop_forming_bar(df)
    keep_from = max(0, len(df) - SIGNAL_LOOKBACK_BARS)
    rows: list[dict] = []
    for sig in detect_gap_up_continuation(df):
        if sig.i < keep_from:
            continue
        tracked = track_signal(df, sig)
        if tracked is None:      # backtest risk guard would skip it
            continue
        rows.append(build_row(
            symbol,
            company_name,
            tracked,
            from_zone=zone_context_tag(zone_result, sig.close),
            volume_ok=volume_expansion_tag(df, sig.i),
            result_days=days_to_result(earnings_date, today),
            market_extended=market_ext,
        ))
    return rows


def _zone_results_by_symbol() -> dict[str, dict]:
    """Read-only lookup of the last Run Analysis results, keyed by symbol.

    ``analysis_results`` is stored as ``{symbol: result}``; a defensive branch
    also accepts a list of result dicts (older snapshots).
    """
    stored = st.session_state.get("analysis_results") or {}
    if isinstance(stored, dict):
        return {sym: res for sym, res in stored.items() if isinstance(res, dict)}
    return {str(res.get("symbol")): res for res in stored if isinstance(res, dict)}


# ------------------------------------------------------------------ scan
def _run_scan(scope: str) -> None:
    label, stocks = resolve_pattern_universe(scope)
    source_name = st.session_state.get("selected_data_source", "Yahoo Finance")
    creds = st.session_state.get("credentials", {}).get(source_name, {})
    try:
        manager = build_source_manager(source_name, creds)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not connect to {source_name}: {exc}")
        return

    from data.earnings_calendar import get_earnings
    try:
        earnings = get_earnings([s.symbol for s in stocks], cache_only=True)
    except Exception:
        earnings = {}
    market_ext = _nifty_extended(manager)
    zone_results = _zone_results_by_symbol()

    placeholder = st.empty()
    errors = 0
    today = dt.datetime.now(_IST).date()
    scannable = [s for s in stocks if s.symbol not in EXCLUDED_SYMBOLS]
    workers = _SIGNAL_SCAN_WORKERS.get(source_name, 1)
    rows_by_symbol: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _scan_symbol_worker,
                stock.symbol,
                getattr(stock, "company_name", "") or stock.symbol,
                f"{stock.symbol}.NS" if source_name == "Yahoo Finance" else stock.symbol,
                manager.get_history,
                zone_results.get(stock.symbol),
                earnings.get(stock.symbol),
                market_ext,
                today,
            ): stock
            for stock in scannable
        }
        for done, future in enumerate(as_completed(futures), 1):
            stock = futures[future]
            placeholder.markdown(
                scan_progress(label, stock.symbol, done, len(scannable)),
                unsafe_allow_html=True,
            )
            try:
                rows_by_symbol[stock.symbol] = future.result()
            except Exception:  # noqa: BLE001
                errors += 1
    # Rows in universe order, as the sequential scan produced them.
    rows: list[dict] = []
    for stock in scannable:
        rows.extend(rows_by_symbol.get(stock.symbol, []))
    placeholder.empty()

    settings = {"scope": scope, "rule": "gap_up_continuation_daily_v1.1",
                "lookback_bars": SIGNAL_LOOKBACK_BARS}
    scan_id = save_gap_scan(settings, label, source_name, rows)
    st.session_state["gap_scan_rows"] = rows
    st.session_state["gap_scan_id"] = scan_id
    st.session_state["gap_scan_label"] = label
    st.session_state["gap_scan_time"] = dt.datetime.now(_IST).strftime("%d %b %Y · %H:%M IST")
    st.session_state["gap_scan_source"] = source_name
    st.session_state["gap_outcome_refresh_marker"] = _outcome_refresh_marker(
        scan_id, dt.datetime.now(_IST)
    )
    if errors:
        st.session_state["gap_scan_errors"] = errors


# ------------------------------------------------------------------ tables
_STATUS_LABELS = {
    STATUS_AWAITING: "Awaiting entry",
    STATUS_ACTIVE: "Active",
    STATUS_TARGET: "Target hit",
    STATUS_STOP: "Stop loss hit",
    STATUS_TIME: "Time-stopped",
}

_TAG_COLUMNS = {
    "from_demand_zone": st.column_config.CheckboxColumn(
        "From zone", help="Gap left from / near a demand zone found by the last "
        "Run Analysis (read-only context)"),
    "volume_expansion": st.column_config.CheckboxColumn("Volume ✓"),
    "result_in_days": st.column_config.NumberColumn(
        "Results in (d)", help="Informational (T9): results ahead raise stop-out "
        "odds slightly; not a gate"),
    "market_extended": st.column_config.CheckboxColumn(
        "Mkt extended ⚠", help="Informational (T13): NIFTY > 2 ATR above its "
        "20-EMA at scan time; not a gate"),
    "evidence_rank": st.column_config.NumberColumn(
        "Evidence rank", help="Sorting aid only: 70 base + 10 from-zone + 5 "
        "volume. Not predictive confidence."),
    "view": st.column_config.LinkColumn("View", display_text="Open ↗"),
}


def _render_table(rows: list[dict], *, active: bool, key: str) -> None:
    if not rows:
        st.caption("Nothing in this bucket for the scanned window.")
        return
    table = pd.DataFrame(rows)
    table["status"] = table["status"].map(_STATUS_LABELS).fillna(table["status"])
    table["view"] = [build_detail_url(r["symbol"], "NSE") + "&sig=gapup" for r in rows]
    if active:
        cols = ["symbol", "company_name", "status", "signal_date", "gap_pct",
                "entry_date", "entry_price", "stop", "target_2r", "days_active",
                "r_multiple", "from_demand_zone", "volume_expansion",
                "result_in_days", "market_extended", "evidence_rank", "view"]
        r_col = st.column_config.NumberColumn(
            "Unrealized R", help="(last close − entry) / (entry − stop loss); "
            "changes every session until the trade resolves", format="%.2f")
    else:
        cols = ["symbol", "company_name", "signal_date", "gap_pct", "entry_date",
                "entry_price", "stop", "target_2r", "exit_date", "exit_price",
                "days_active", "r_multiple", "from_demand_zone",
                "volume_expansion", "evidence_rank", "view"]
        r_col = st.column_config.NumberColumn("Realized R", format="%.2f")
    st.dataframe(
        table[cols], use_container_width=True, hide_index=True,
        height=min(430, 60 + 35 * len(rows)), key=key,
        column_config={
            "symbol": st.column_config.TextColumn("Symbol"),
            "company_name": st.column_config.TextColumn("Company"),
            "status": st.column_config.TextColumn("Status"),
            "signal_date": st.column_config.TextColumn("Signal date"),
            "gap_pct": st.column_config.NumberColumn("Gap %", format="%.2f"),
            "entry_date": st.column_config.TextColumn("Entry date"),
            "entry_price": st.column_config.NumberColumn(
                "Entry ₹", help="The session open AFTER the signal day — the "
                "backtested entry", format="%.2f"),
            "stop": st.column_config.NumberColumn(
                "Stop loss ₹", help="Prior day's low − 0.1 × ATR(14)", format="%.2f"),
            "target_2r": st.column_config.NumberColumn("2R target ₹", format="%.2f"),
            "exit_date": st.column_config.TextColumn("Exit date"),
            "exit_price": st.column_config.NumberColumn("Exit ₹", format="%.2f"),
            "days_active": st.column_config.NumberColumn("Days"),
            "r_multiple": r_col,
            **_TAG_COLUMNS,
        },
    )


# ------------------------------------------------------------------ page
def render_signals_page() -> None:
    page_title(
        "Signals",
        "Rules graduated from the Research Engine — twice-validated on "
        "independent years before reaching this page",
    )
    st.info(
        "**Gap-Up Continuation (daily, long side).** Validated on two "
        "independent years (+0.41R in-sample, +0.34R out-of-sample, profit "
        "factor > 2, positive on 77% of stocks). Expect MOST individual "
        "signals to hit their stop loss — roughly 60 in 100 do; the edge is "
        "that target hits pay 2R. Signals are research classifications for "
        "further study — not buy/sell recommendations. *Evidence rank* is a "
        "sorting aid, not predictive confidence.",
        icon=":material/verified:",
    )

    qp_scan = st.session_state.pop("_qp_gap_scan_id", None)
    if qp_scan and not st.session_state.get("gap_scan_rows"):
        cached = get_gap_scan(str(qp_scan))
        if cached:
            st.session_state["gap_scan_rows"] = cached.get("rows", [])
            st.session_state["gap_scan_id"] = cached.get("id")
            st.session_state["gap_scan_label"] = cached.get("universe_label", "")
            st.session_state["gap_scan_time"] = cached.get("created_at", "")
            st.session_state["gap_scan_source"] = cached.get("source_name", "Yahoo Finance")

    c1, c2 = st.columns([2.2, 1.0])
    scope = c1.selectbox("Universe", _SCOPES, index=2, key="gap_scan_scope")
    if c2.button("Scan signals", type="primary",
                 use_container_width=True, key="gap_scan_run"):
        _run_scan(scope)

    rows = st.session_state.get("gap_scan_rows")
    if rows is None:
        st.caption(
            f"Finds every confirmed signal of the last {SIGNAL_LOOKBACK_BARS} "
            "sessions (gap > 1.3% over the prior day's high, close held) and "
            "walks each forward under the backtested rules — entry at the next "
            "session's open, stop-loss at the prior day's low − 0.1 ATR, 2R "
            "primary target, 20-session time stop. Tabs group them by where "
            "each trade stands now."
        )
        return

    now = dt.datetime.now(_IST)
    scan_id = st.session_state.get("gap_scan_id")
    refresh_marker = _outcome_refresh_marker(scan_id, now)
    if st.session_state.get("gap_outcome_refresh_marker") != refresh_marker:
        source_name = st.session_state.get(
            "gap_scan_source",
            st.session_state.get("selected_data_source", "Yahoo Finance"),
        )
        creds = st.session_state.get("credentials", {}).get(source_name, {})
        try:
            manager = build_source_manager(source_name, creds)
            with st.spinner("Refreshing open signal outcomes…"):
                rows, transitions = refresh_open_signal_rows(
                    rows,
                    fetch_fn=manager.get_history,
                    source_name=source_name,
                    now=now,
                )
            st.session_state["gap_scan_rows"] = rows
            if transitions:
                st.session_state["gap_outcome_refresh_notice"] = transitions
        except Exception:  # noqa: BLE001 -- cached scan remains useful offline
            pass
        st.session_state["gap_outcome_refresh_marker"] = refresh_marker

    st.caption(
        f"{st.session_state.get('gap_scan_label', '')} · scanned "
        f"{st.session_state.get('gap_scan_time', '')} · signals from the last "
        f"{SIGNAL_LOOKBACK_BARS} sessions, confirmed end-of-day only" + (
            f" · {st.session_state['gap_scan_errors']} symbols failed to fetch"
            if st.session_state.get("gap_scan_errors") else "")
    )
    transitions = st.session_state.pop("gap_outcome_refresh_notice", 0)
    if transitions:
        st.caption(f"Updated {transitions} open signal outcome(s) from completed daily candles.")
    f1, f2, f3, f4 = st.columns([1, 1, 1, 1.2])
    zone_only = f1.checkbox("From demand zone only", key="gap_f_zone")
    volume_only = f2.checkbox("Volume confirmed only", key="gap_f_vol")
    hide_res = f3.checkbox("Hide results within 5 days", key="gap_f_res")
    sort_name = f4.selectbox("Sort active by", list(_SORTS), key="gap_sort")

    filtered = apply_tag_filters(rows, zone_only=zone_only, volume_only=volume_only,
                                 hide_results_week=hide_res)
    groups = split_by_status(filtered)
    n_act, n_tgt = len(groups[STATUS_ACTIVE]), len(groups[STATUS_TARGET])
    n_stp, n_tim = len(groups[STATUS_STOP]), len(groups[STATUS_TIME])
    if not filtered:
        st.warning("No signals in the scanned window match the filters.")
        return

    tabs = st.tabs([
        f"Active ({n_act})",
        f"🟢 Target hit ({n_tgt})",
        f"🔴 Stop loss hit ({n_stp})",
        f"⚪ Time-stopped ({n_tim})",
    ])
    with tabs[0]:
        st.caption(
            "Open trades: neither the stop loss nor the 2R target has been hit "
            "yet (time stop: 20 sessions). 'Awaiting entry' = confirmed on the "
            "last session; the entry is the NEXT session's open. These are "
            "TRACKED positions, not fresh entries — the validated entry was "
            "the open after the signal day."
        )
        _render_table(sort_rows(groups[STATUS_ACTIVE], sort_name),
                      active=True, key="gap_tbl_active")
    with tabs[1]:
        _render_table(sort_rows(groups[STATUS_TARGET], "Most recent first"),
                      active=False, key="gap_tbl_target")
    with tabs[2]:
        _render_table(sort_rows(groups[STATUS_STOP], "Most recent first"),
                      active=False, key="gap_tbl_stop")
    with tabs[3]:
        _render_table(sort_rows(groups[STATUS_TIME], "Most recent first"),
                      active=False, key="gap_tbl_time")

    st.caption(
        "Read the tab counts together: stop-loss hits normally OUTNUMBER "
        "target hits (≈60 vs ≈23 in 100 historically) — the rule's edge comes "
        "from targets paying 2R, not from winning often. Validated on two "
        "independent years (+0.41R in-sample, +0.34R out-of-sample). Research "
        "classification — not a buy/sell recommendation."
    )
