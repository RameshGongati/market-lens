"""Reports — F&O Results Monitor.

Earnings/results tracking for the selected watchlist, widening to the full
F&O universe on request.

**Nothing is fetched on page open.** Populating the 208-stock F&O calendar
costs ~164 seconds cold (~790ms per symbol for the two yfinance calls). The
page therefore renders from the disk cache and asks for an explicit refresh,
with the same standalone progress card the scan uses. Once refreshed, the
cache is good for the calendar day and later loads are a disk read.

**Phase 1 scope.** BMO/AMO session and OI/Vol spike are labelled but blank:
yfinance carries no reliable Indian session marker, and open interest needs a
derivatives pipeline that is deliberately deferred. Alert subscriptions show
the existing alert count rather than a new subscription system.
"""

from __future__ import annotations

import html
from datetime import date

import streamlit as st

from data.earnings_calendar import (
    cache_status,
    classify_impact,
    countdown_label,
    date_heading,
    days_until,
    fetch_price_reactions,
    fno_symbols,
    get_earnings,
    impact_rule_text,
    index_symbols,
    recent_releases,
    sector_map,
    status_label,
    upcoming_groups,
)
from storage.database import get_all_alerts
from ui.components.panels import (
    bias_pill,
    page_slice,
    pagination_bar,
    page_title,
    section_title,
    spacer,
    stat_card,
)
from ui.components.stock_card import build_detail_url
from utils.helpers import get_company_name
from utils.logger import get_logger

logger = get_logger(__name__)

_MAJOR_LISTS = ("Nifty 50", "Nifty Bank")
_ROWS_PER_PAGE = [10, 20, 30, 50, 75, 100]
_TIMING_OPTIONS = (
    "All",
    "Yet to release",
    "Due Today",
    "Tomorrow",
    "Next 7 Days",
    "Later",
    "Released Today",
    "Released Yesterday",
    "Released: Last 7 Days",
    "All Released",
)
_RELEASE_TIMINGS = {
    "Released Today",
    "Released Yesterday",
    "Released: Last 7 Days",
    "All Released",
}


def _universe() -> tuple[list[str], str]:
    """Symbols to show, and a label describing where they came from.

    Defaults to the sidebar's current watchlist so the page opens against
    something small; the F&O toggle and the full-refresh button widen it
    explicitly rather than by default.
    """
    if st.session_state.get("reports_only_fno"):
        return sorted(fno_symbols()), "F&O universe"

    name = st.session_state.get("selected_predefined_watchlist")
    if name:
        syms = sorted(index_symbols(name))
        if syms:
            return syms, name

    results = st.session_state.get("analysis_results", {}) or {}
    if results:
        return sorted(results), "Last scan"

    return sorted(index_symbols("Nifty 50")), "Nifty 50"


def render_reports_page() -> None:
    """Render the Reports page."""
    symbols, source_label = _universe()

    _render_header(source_label, symbols)

    # Perform any requested refresh HERE, at page level, so the progress card
    # gets the full page width. The buttons that ask for it live inside a
    # three-way split of the header's right-hand column, and a card created
    # there inherits that width.
    requested = st.session_state.pop("reports_refresh", None)
    if requested == "selected":
        _refresh(symbols, force=False)
    elif requested == "full":
        _refresh(sorted(fno_symbols()), force=False)

    # Read the disk cache for the CURRENT universe on every render.
    #
    # This used to be memoised in session state and only filled when empty,
    # which broke the "Only F&O" toggle: the first render cached the 50 Nifty
    # rows, and switching to the 208-stock universe found that dict already
    # populated, so it was never reloaded. The header correctly said "F&O
    # universe · 208 stocks" while the table showed 50 — all 208 were sitting
    # on disk, unread.
    #
    # There is nothing to memoise anyway. cache_only never touches the
    # network; it is a handful of small JSON reads, measured at ~1ms for 50
    # symbols, and the refresh path writes to the same disk cache, so a
    # session copy could only ever go stale.
    #
    # cache_only itself is load-bearing, not an optimisation: without it,
    # opening the page fetches every uncached symbol inline — 39s for Nifty
    # 50, ~164s for the F&O universe — the exact behaviour the refresh button
    # exists to avoid.
    rows = get_earnings(symbols, cache_only=True)

    fresh, total = cache_status(symbols)
    if fresh < total:
        st.info(
            f"**{fresh} of {total}** symbols have a cached calendar for today. "
            f"Click **Refresh calendar** to fetch the remaining "
            f"{total - fresh} — roughly "
            f"{(total - fresh) * 0.8:.0f}s."
        )

    known = {s: r for s, r in rows.items() if s in set(symbols)}
    if not known:
        spacer(12)
        st.warning(
            "No calendar data cached yet for this watchlist. "
            "Click **Refresh calendar** above to fetch it."
        )
        return

    sectors = sector_map()
    fno = fno_symbols()
    major = set()
    for name in _MAJOR_LISTS:
        major |= index_symbols(name)
    major |= {s for s in sectors}

    reactions = _reactions_for(known)
    timing = st.session_state["reports_timing"]
    table = _build_rows(known, sectors, fno, major, reactions, timing)

    spacer(12)
    _render_summary_cards(table)
    spacer(14)

    main, side = st.columns([3, 1])
    with main:
        with st.container(border=True):
            _render_table(table, timing)
    with side:
        _render_upcoming(known)
        _render_recent(known, reactions)
        _render_heatmap(table)

    spacer(14)
    _render_explainer()


# ---------------------------------------------------------------------------
# Header and refresh
# ---------------------------------------------------------------------------

def _render_header(source_label: str, symbols: list[str]) -> None:
    # Both rows are created up front so the layout is fixed — title and
    # buttons on top, toggles beneath — but the TOGGLES are written into
    # first, and that ordering is load-bearing.
    #
    # Streamlit discards the state of any widget that was not instantiated
    # during a run. The buttons below can end the run early (a refresh used to
    # call st.rerun, and Dashboard still does), which meant the toggles were
    # never created on that pass and "Only F&O" was silently reset — the
    # universe fell back from 208 to the default watchlist the moment you
    # pressed Refresh.
    left, right = st.columns([3, 2])
    opts = st.columns([1, 1.6, 3.4])

    # Preserve the old switch's intent for the first render after this
    # upgrade, then use the more precise timing picker from then on.
    if "reports_timing" not in st.session_state:
        st.session_state["reports_timing"] = (
            "Yet to release"
            if st.session_state.get("reports_upcoming_only") else "All"
        )

    with opts[0]:
        st.toggle("Only F&O", key="reports_only_fno",
                  help="Widen the table to every F&O stock.")
    with opts[1]:
        st.selectbox(
            "Results timing",
            _TIMING_OPTIONS,
            key="reports_timing",
            help=("Choose scheduled results, a specific upcoming window, "
                  "or recently reported results."),
        )

    with left:
        st.markdown(
            "<div style='font-size:0.78rem;color:#9AA0A8;margin-bottom:2px;'>"
            "Dashboard &nbsp;&rsaquo;&nbsp; "
            "<span style='color:#4A5361;font-weight:600;'>Reports</span></div>",
            unsafe_allow_html=True,
        )
        page_title(
            "Reports — F&O Results Monitor",
            f"Earnings tracking · {source_label} · {len(symbols)} stocks",
            icon="layers",
        )
    with right:
        c1, c2, c3 = st.columns(3)
        # These only RECORD the request; render_reports_page performs it.
        #
        # Calling _refresh here ran it inside this column, and st.empty()
        # belongs to whatever container is open when it is created — so the
        # progress card was laid out in a strip a third of the button row
        # wide. It is built for min(720px, 100%), and 100% of ~110px wraps
        # every word to one character per line.
        with c1:
            if st.button("Refresh calendar", icon=":material/refresh:",
                         use_container_width=True, key="rp_refresh"):
                st.session_state["reports_refresh"] = "selected"
        with c2:
            with st.popover("Full F&O", icon=":material/download:",
                            use_container_width=True):
                st.caption(
                    f"Fetches all {len(fno_symbols())} F&O stocks. "
                    "Roughly 164s on a cold cache; a day-old cache makes it "
                    "much faster."
                )
                if st.button("Refresh full F&O calendar",
                             key="rp_refresh_fno",
                             use_container_width=True):
                    st.session_state["reports_refresh"] = "full"
        with c3:
            if st.button("Dashboard", icon=":material/arrow_back:",
                         use_container_width=True, key="rp_back"):
                st.session_state.active_page = "dashboard"
                st.rerun()


def _refresh(symbols: list[str], force: bool) -> None:
    """Fetch calendars with the standalone progress card."""
    from ui.components.panels import scan_progress

    holder = st.empty()

    def _tick(done: int, total: int, symbol: str) -> None:
        holder.markdown(
            scan_progress("results calendar", symbol, done, total),
            unsafe_allow_html=True,
        )

    # get_earnings writes every fetched row to the disk cache, which is what
    # the page reads on the next render — so nothing needs storing in session
    # here. Keeping a session copy is what let the table fall out of step with
    # the selected universe.
    get_earnings(symbols, force=force, progress=_tick)
    holder.empty()
    st.session_state["reports_last_refresh"] = date.today().isoformat()
    # Deliberately NO st.rerun(). The page reads the disk cache further down
    # this same run, so the freshly fetched rows are picked up without one —
    # and a rerun here ended the script before the toggles below were
    # instantiated, which reset the F&O selection every time you refreshed.


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------

def _reactions_for(rows: dict) -> dict[str, float]:
    """Price reaction only for stocks that reported in the last few days.

    Batched, and scoped to recent reporters — computing it for every symbol
    would be a second full-universe download for a column that is blank on
    upcoming rows anyway.
    """
    recent = [s for s, _r in recent_releases(rows, within_days=5)]
    if not recent:
        return {}
    key = "reports_reactions_" + ",".join(sorted(recent))[:200]
    cached = st.session_state.get(key)
    if cached is not None:
        return cached
    out = fetch_price_reactions(recent)
    st.session_state[key] = out
    return out


def _reported_days(row: dict) -> int | None:
    return days_until(row.get("last_result_date"))


def _scheduled_result_is_reported(row: dict) -> bool:
    """Whether today's displayed scheduled result has already reported.

    The provider can briefly expose the same date as both the next calendar
    event and the latest reported quarter. Treating that row as pending makes
    a completed result appear under "Due Today", which is misleading.
    """
    scheduled = (row.get("result_date") or "")[:10]
    reported = (row.get("last_result_date") or "")[:10]
    reported_days = _reported_days(row)
    return bool(scheduled and scheduled == reported and reported_days is not None
                and reported_days <= 0)


def _matches_timing(row: dict, timing: str) -> bool:
    """Return whether an earnings row belongs in the selected time window."""
    if timing == "All":
        return True

    scheduled_days = days_until(row.get("result_date"))
    reported_days = _reported_days(row)
    pending = (
        scheduled_days is not None
        and scheduled_days >= 0
        and not _scheduled_result_is_reported(row)
    )

    if timing == "Yet to release":
        return pending
    if timing == "Due Today":
        return pending and scheduled_days == 0
    if timing == "Tomorrow":
        return pending and scheduled_days == 1
    if timing == "Next 7 Days":
        return pending and scheduled_days is not None and 0 <= scheduled_days <= 7
    if timing == "Later":
        return pending and scheduled_days is not None and scheduled_days > 7
    if timing == "Released Today":
        return reported_days == 0
    if timing == "Released Yesterday":
        return reported_days == -1
    if timing == "Released: Last 7 Days":
        return reported_days is not None and -6 <= reported_days <= 0
    if timing == "All Released":
        return reported_days is not None and reported_days <= 0
    return True


def _reported_countdown_label(days: int | None) -> str:
    if days is None:
        return "—"
    if days == 0:
        return "TODAY"
    if days == -1:
        return "YESTERDAY"
    return f"{abs(days)} DAYS AGO" if days < 0 else "—"


def _reported_status_label(days: int | None) -> str:
    if days == 0:
        return "Released Today"
    if days == -1:
        return "Released Yesterday"
    return "Released"


def _build_rows(
    rows: dict,
    sectors: dict,
    fno: set,
    major: set,
    reactions: dict,
    timing: str,
) -> list[dict]:
    out: list[dict] = []
    for sym, row in rows.items():
        if not _matches_timing(row, timing):
            continue
        scheduled_days = days_until(row.get("result_date"))
        reported_days = _reported_days(row)
        show_reported = timing in _RELEASE_TIMINGS
        # In the all-results view, make a same-day reported result visibly
        # completed rather than leaving it labelled "Due Today".
        if timing == "All" and _scheduled_result_is_reported(row):
            show_reported = True
        display_days = reported_days if show_reported else scheduled_days
        reaction = reactions.get(sym)
        high, why = classify_impact(
            row, is_fno=sym in fno, in_major_list=sym in major,
            price_reaction=reaction,
        )
        out.append({
            "symbol": sym,
            "company": get_company_name(sym),
            "sector": sectors.get(sym, "—"),
            "result_date": (
                (row.get("last_result_date") if show_reported
                 else row.get("result_date")) or ""
            )[:10],
            "days": display_days,
            "scheduled_days": scheduled_days,
            "reported_days": reported_days,
            "is_reported_view": show_reported,
            "countdown": (
                _reported_countdown_label(display_days)
                if show_reported else countdown_label(display_days)
            ),
            "status": (
                _reported_status_label(display_days)
                if show_reported else status_label(display_days)
            ),
            "eps": row.get("eps_estimate"),
            "revenue_cr": row.get("revenue_estimate_cr"),
            "reported_eps": row.get("reported_eps"),
            "surprise": row.get("surprise_pct"),
            "reaction": reaction,
            "high_impact": high,
            "impact_why": why,
            "is_fno": sym in fno,
        })
    # Scheduled windows run soonest first; release windows run newest first.
    # Undated rows remain last in the unfiltered view.
    if timing in _RELEASE_TIMINGS:
        out.sort(key=lambda r: (
            r["days"] is None,
            -r["days"] if r["days"] is not None else 9999,
            r["symbol"],
        ))
    else:
        out.sort(key=lambda r: (
            r["days"] is None,
            r["days"] if r["days"] is not None else 9999,
            r["symbol"],
        ))
    return out


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

def _render_summary_cards(table: list[dict]) -> None:
    today = sum(1 for r in table if r["status"] in {
        "Due Today", "Released Today",
    })
    d1 = sum(1 for r in table if r["status"] == "Upcoming"
             and r["scheduled_days"] == 1)
    d2 = sum(1 for r in table if r["status"] == "Upcoming"
             and r["scheduled_days"] is not None
             and 0 <= r["scheduled_days"] <= 2)
    released = sum(1 for r in table if r["status"].startswith("Released"))
    high = sum(1 for r in table if r["high_impact"])
    try:
        alerts = len(get_all_alerts() or [])
    except Exception:
        alerts = 0

    cols = st.columns(6)
    with cols[0]:
        stat_card("Results today", str(today),
                  f"High impact: {sum(1 for r in table if r['days'] == 0 and r['high_impact'])}",
                  "calendar", tone="bullish" if today else "muted")
    with cols[1]:
        stat_card("Next 1 day", str(d1), "Tomorrow", "clock",
                  tone="warning" if d1 else "muted")
    with cols[2]:
        stat_card("Next 2 days", str(d2), "Within 2 sessions", "calendar",
                  tone="warning" if d2 else "muted")
    with cols[3]:
        stat_card("Released", str(released),
                  "Already reported", "check_circle", tone="info")
    with cols[4]:
        stat_card("High impact", str(high), "By the rule below", "star",
                  tone="bearish" if high else "muted")
    with cols[5]:
        stat_card("Alerts", str(alerts), "Existing zone alerts", "bell",
                  tone="purple" if alerts else "muted")


def _render_table(table: list[dict], timing: str) -> None:
    top = st.columns([3, 1])
    with top[0]:
        section_title(f"Results ({len(table)})")
    with top[1]:
        query = st.text_input("Search", key="rp_search",
                              placeholder="Search symbol…",
                              label_visibility="collapsed")
    if query:
        q = query.strip().upper()
        table = [r for r in table if q in r["symbol"].upper()
                 or q in r["company"].upper()]
    if not table:
        st.caption(f"No results match the {timing.lower()} filter.")
        return

    # The bar is rendered AFTER the table but computes the slice first, so
    # the rows shown always match the "Showing X to Y" caption below them.
    start, end = page_slice(len(table), "rp", default_size=20)
    window = table[start:end]

    released_view = timing in _RELEASE_TIMINGS
    headers = ["Symbol", "Company", "Sector",
               "Reported Date" if released_view else "Result Date",
               "Session",
               "Reported" if released_view else "Countdown",
               "Impact",
               "Reported EPS" if released_view else "Expected EPS",
               "EPS Surprise" if released_view else "Expected Rev (Cr)",
               "Status", "Last Day Move", "OI / Vol Spike"]
    head = "<tr>" + "".join(
        f"<th style='text-align:left;padding:7px 8px;font-size:0.68rem;"
        f"color:#8A8F98;font-weight:600;white-space:nowrap;"
        f"border-bottom:1px solid #E7E9ED;'>{h}</th>" for h in headers
    ) + "</tr>"

    dash = "<span style='color:#A8A8A0;'>&mdash;</span>"
    body = ""
    for r in window:
        cd = r["countdown"]
        cd_tone = (
            "info" if r["is_reported_view"] else
            "bearish" if cd == "TODAY" else
            "warning" if cd in ("1 DAY", "2 DAYS") else "muted"
        )
        st_tone = {"Due Today": "bearish", "Upcoming": "warning",
                   "Released": "info", "Released Today": "info",
                   "Released Yesterday": "info"}.get(r["status"], "muted")
        # title= gives the exact rule that matched, so a flag is never opaque.
        impact = (
            f"<span title='{html.escape(r['impact_why'])}'>"
            f"{bias_pill('HIGH', 'bearish')}</span>"
            if r["high_impact"] else
            f"<span title='{html.escape(r['impact_why'])}' "
            f"style='color:#C9CDD3;'>&mdash;</span>"
        )
        reaction = dash
        if r["reaction"] is not None:
            col = "#16794A" if r["reaction"] >= 0 else "#C23B33"
            reaction = (f"<span style='color:{col};font-weight:600;'>"
                        f"{r['reaction']:+.2f}%</span>")
        eps = r["reported_eps"] if released_view else r["eps"]
        secondary = r["surprise"] if released_view else r["revenue_cr"]
        secondary_cell = (
            f"{secondary:+.1f}%" if released_view and secondary is not None
            else f"{secondary:,.0f}" if secondary is not None else dash
        )
        chart_url = html.escape(build_detail_url(r["symbol"], "NSE"), quote=True)
        cells = [
            f"<a href='{chart_url}' target='_blank' rel='noopener noreferrer' "
            f"title='Open {html.escape(r['symbol'], quote=True)} chart in a new tab' "
            "style='color:#175CD3;font-weight:800;text-decoration:none;'>"
            f"{html.escape(r['symbol'])}</a>",
            f"<span style='color:#71757C;'>{html.escape(r['company'])[:26]}</span>",
            html.escape(r["sector"]),
            html.escape(r["result_date"] or "—"),
            # yfinance has no reliable Indian session marker — see the module
            # docstring. Blank rather than guessed.
            dash,
            bias_pill(cd, cd_tone),
            impact,
            f"{eps:.2f}" if eps is not None else dash,
            secondary_cell,
            bias_pill(r["status"], st_tone),
            reaction,
            dash,
        ]
        body += "<tr>" + "".join(
            f"<td style='padding:7px 8px;font-size:0.78rem;"
            f"border-bottom:1px solid #F1F2F4;white-space:nowrap;'>{c}</td>"
            for c in cells
        ) + "</tr>"

    st.markdown(
        f"<div style='overflow-x:auto;'><table style='width:100%;"
        f"border-collapse:collapse;'>{head}{body}</table></div>",
        unsafe_allow_html=True,
    )

    pagination_bar(len(table), "rp", tuple(_ROWS_PER_PAGE), 20)


def _render_upcoming(rows: dict) -> None:
    with st.container(border=True):
        section_title("Upcoming Calendar", hint="Next 7 days")
        groups = upcoming_groups(rows, horizon_days=7)
        if not groups:
            st.caption("No results scheduled in the next 7 days.")
            return
        for iso, syms in groups:
            st.markdown(
                f"<div style='padding:5px 0;border-bottom:1px solid #F1F2F4;'>"
                f"<div style='display:flex;justify-content:space-between;'>"
                f"<span style='font-size:0.78rem;font-weight:600;"
                f"color:#26313F;'>{html.escape(date_heading(iso))}</span>"
                f"<span style='font-size:0.72rem;color:#8A8F98;'>"
                f"{len(syms)}</span></div>"
                f"<div style='font-size:0.72rem;color:#71757C;'>"
                f"{html.escape(', '.join(sorted(syms)[:6]))}"
                f"{'…' if len(syms) > 6 else ''}</div></div>",
                unsafe_allow_html=True,
            )


def _render_recent(rows: dict, reactions: dict) -> None:
    with st.container(border=True):
        section_title("Recent Releases", hint="Last 7 days")
        recents = recent_releases(rows, within_days=7)
        if not recents:
            st.caption("Nothing reported in the last 7 days.")
            return
        for sym, row in recents[:8]:
            surprise = row.get("surprise_pct")
            reaction = reactions.get(sym)
            verdict = "—"
            tone = "muted"
            if surprise is not None:
                verdict = "Positive" if surprise >= 0 else "Negative"
                tone = "bullish" if surprise >= 0 else "bearish"
            move = (f"{reaction:+.2f}%" if reaction is not None else "")
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;"
                f"align-items:center;padding:4px 0;"
                f"border-bottom:1px solid #F1F2F4;'>"
                f"<span style='font-size:0.78rem;'><b>{html.escape(sym)}</b>"
                f"<span style='color:#9AA0A8;font-size:0.7rem;'> "
                f"{html.escape((row.get('last_result_date') or '')[:10])}</span>"
                f"</span><span>{bias_pill(verdict, tone)}"
                f"<span style='font-size:0.72rem;color:#71757C;'> "
                f"{move}</span></span></div>",
                unsafe_allow_html=True,
            )


def _render_heatmap(table: list[dict]) -> None:
    """Average post-result price reaction per sector."""
    with st.container(border=True):
        section_title("Result Reaction Heatmap", hint="Avg move")
        buckets: dict[str, list[float]] = {}
        for r in table:
            if r["reaction"] is None or r["sector"] == "—":
                continue
            buckets.setdefault(r["sector"], []).append(r["reaction"])
        if not buckets:
            st.caption(
                "No sector has a reported result with a price reaction yet."
            )
            return
        cells = ""
        for sector, values in sorted(buckets.items()):
            avg = sum(values) / len(values)
            bg = "#EAF6EE" if avg >= 0 else "#FDECEC"
            fg = "#16794A" if avg >= 0 else "#C23B33"
            cells += (
                f"<div style='flex:1 1 30%;background:{bg};border-radius:8px;"
                f"padding:8px;text-align:center;'>"
                f"<div style='font-size:0.72rem;color:#4A4A44;'>"
                f"{html.escape(sector)}</div>"
                f"<div style='font-size:0.9rem;font-weight:700;color:{fg};'>"
                f"{avg:+.2f}%</div></div>"
            )
        st.markdown(
            f"<div style='display:flex;flex-wrap:wrap;gap:6px;'>{cells}</div>",
            unsafe_allow_html=True,
        )


def _render_explainer() -> None:
    a, b = st.columns(2)
    with a:
        with st.container(border=True):
            section_title("How High Impact is decided")
            st.markdown(impact_rule_text())
    with b:
        with st.container(border=True):
            section_title("Important notes")
            st.markdown(
                "- Result dates are company-announced estimates from the data "
                "provider and can move.\n"
                "- **Session (BMO/AMO) is blank**: yfinance carries no "
                "reliable Indian pre/post-market marker, so it is not "
                "guessed.\n"
                "- **OI / Vol Spike is blank**: open interest needs a "
                "derivatives feed, deferred to Phase 2.\n"
                "- Price reaction is the close-to-close move on the most "
                "recent session for stocks that reported in the last 5 days.\n"
                "- The calendar is cached to disk for the day; refresh to "
                "re-fetch."
            )
