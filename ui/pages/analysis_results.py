"""Analysis results page (design screenshot 2).

Reached from "Run analysis". Previously the results grid was rendered inside
the dashboard; it now has its own page so the dashboard can be a market view.

The scan itself is unchanged — this page calls ``dashboard.run_scan``, which
is the same loop that used to live in ``render_dashboard``.

**Placeholders.** Risk/Reward, Entry Zone, Stop Loss and Targets need Phase 2
(M1); HTF Trend needs Phase 3. Those appear with their real labels and an
explicit pending marker rather than being omitted, so the layout is settled
and the missing inputs stay visible.
"""

from __future__ import annotations

import html

import streamlit as st

from storage.database import get_all_alerts
from ui.components.stock_card import build_detail_url
from ui.components.panels import (
    bias_pill,
    filter_chip,
    kv_row,
    page_title,
    section_title,
    spacer,
    stat_card,
)
from ui.pages.dashboard import (
    _do_export_excel,
    _do_export_pdf,
    _passes_screener,
    run_scan,
    scan_context,
)
from utils.logger import get_logger

logger = get_logger(__name__)

_PAGE_SIZES = [10, 25, 50, 100]

# Tradeability buckets on the scan-summary row. A setup counts as "valid" only
# when the trend-alignment rule passes AND the zone clears the display floor;
# anything confirmed or merely close is "watch"; the rest is "avoid".
_VALID_SETUP_SCORE = 5.0
_WATCH_SETUP_SCORE = 3.5

_STATUS_OPTIONS = ["All statuses", "Bullish", "Bearish", "Neutral"]
_STRENGTH_OPTIONS = ["All strengths", "Strong", "Medium", "Weak"]
_SORT_OPTIONS = [
    "ODD Score (High to Low)",
    "ODD Score (Low to High)",
    "Symbol (A-Z)",
    "Price (High to Low)",
]

# The screener filters shown as removable chips. Each entry maps a session key
# to its chip label and the value that means "off".
#
# These live in the sidebar Screener, which this batch does not touch, so the
# chips READ and CLEAR them rather than duplicating the widgets — one set of
# keys with one place to set them and one place to remove them.
_CHIP_FILTERS: list[tuple[str, str, object]] = [
    ("screener_confirmation", "Zone Confirmation", False),
    ("screener_proximity", "Proximity", "All"),
    ("screener_min_score", "ODD Score", "All"),
    ("screener_confirm_score", "Confirmed Score", "All"),
    ("screener_zone_strength", "Zone Strength", []),
]


def render_analysis_results() -> None:
    """Render the scan-results page, running the scan first when requested."""
    ctx = scan_context()
    if ctx is None:
        _back_button()
        return

    # The header is drawn BEFORE the scan so this page's title and Back button
    # are on screen while it works. Rendering it after meant the progress bar
    # appeared under the previous page's content, which read as the scan
    # running on the dashboard.
    _render_header(ctx)

    # A pending scan runs here rather than on the dashboard — "Run analysis"
    # navigates to this page and sets the flag, so the progress bar appears
    # where the results will land.
    if st.session_state.get("analysing"):
        produced = run_scan(ctx)
        if produced is not None:
            st.session_state["_last_scan_label"] = _now_label()

    results: dict[str, dict] = st.session_state.get("analysis_results", {}) or {}

    _render_filter_strip(ctx)

    if not results:
        spacer()
        st.info(
            "No results yet. Click **▶ Run analysis** in the sidebar to scan "
            "this watchlist."
        )
        return

    # Explicit gaps between the horizontal bands. Without them the filter
    # strip, the metric rows and the table panel stacked at the same rhythm as
    # rows inside a single panel, so the bands ran together.
    spacer(16)
    _render_scan_cards(results)
    spacer(6)
    _render_summary_cards(results)
    spacer(16)

    with st.container(border=True):
        _render_filter_bar()
    spacer(12)

    main, side = st.columns([3, 1])
    with main:
        with st.container(border=True):
            _render_ranked_table(results)
    with side:
        _render_legend(results)
        _render_insights(results)

    spacer(16)
    _render_detail_strip(results)


def _now_label() -> str:
    from datetime import datetime
    return datetime.now().strftime("Today, %I:%M %p")


def _back_button() -> None:
    if st.button("Back to Dashboard", icon=":material/arrow_back:", key="ar_back"):
        st.session_state.active_page = "dashboard"
        st.rerun()


def _render_header(ctx: dict) -> None:
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            "<div style='font-size:0.78rem;color:#9AA0A8;margin-bottom:2px;'>"
            "Dashboard &nbsp;&rsaquo;&nbsp; "
            "<span style='color:#4A5361;font-weight:600;'>Analysis Results"
            "</span></div>",
            unsafe_allow_html=True,
        )
        page_title(
            f"Market Lens — {ctx['wl_name']} Results",
            f"Last scan: {st.session_state.get('_last_scan_label', '—')}",
            icon="target",
        )
    with right:
        c1, c2, c3 = st.columns(3)
        results = st.session_state.get("analysis_results", {}) or {}
        with c1:
            if st.button("Re-run scan", icon=":material/refresh:",
                         use_container_width=True, key="ar_rerun"):
                st.session_state.analysing = True
                st.rerun()
        with c2:
            # Export collapses into one control, as in the design, rather than
            # two buttons competing with Back for header width.
            with st.popover("Export", icon=":material/download:",
                            use_container_width=True, disabled=not results):
                if st.button("Excel (.xlsx)", key="ar_xls",
                             use_container_width=True):
                    _do_export_excel(results, ctx["analysis_type"],
                                     ctx["wl_name"])
                if st.button("PDF", key="ar_pdf", use_container_width=True):
                    _do_export_pdf(results, ctx["analysis_type"],
                                   ctx["wl_name"])
        with c3:
            _back_button()


def _render_filter_strip(ctx: dict) -> None:
    """Read-only echo of the sidebar selections the scan ran under.

    The design puts these controls in the page header. They stay read-only
    here for now: they are live widgets in the sidebar, and duplicating them
    as a second set of writable controls would give two sources of truth for
    the same session-state keys.
    """
    cols = st.columns(5)
    confirm = (
        "On" if st.session_state.get("screener_confirmation") else "Off"
    )
    fields = [
        ("Watchlist", ctx["wl_name"], "layers"),
        ("Timeframe", st.session_state.get("_used_tf_label", ctx["tf_label"]), "calendar"),
        ("Strategy", ctx["trading_type"], "target"),
        ("Zone Confirmation", confirm, "check"),
        ("Scan Status",
         "Completed" if st.session_state.get("analysis_results") else "Not run", "clock"),
    ]
    for col, (label, value, icon) in zip(cols, fields):
        with col:
            filter_chip(label, str(value), icon)


def _render_scan_cards(results: dict[str, dict]) -> None:
    """The five scan-summary cards from the design.

    "Valid setups" / "Watch or confirm" / "Avoid or weak" are the three
    tradeability buckets, and they partition the scanned set — the three
    always sum to Stocks Scanned, so the row can be read as a breakdown
    rather than as four unrelated counters.
    """
    total = len(results)
    rows = _row_data(results)

    valid = watch = 0
    for r in rows:
        if r["tradeable"] and r["odd"] >= _VALID_SETUP_SCORE:
            valid += 1
        elif r["confirmation"] != "None" or r["odd"] >= _WATCH_SETUP_SCORE:
            watch += 1
    avoid = total - valid - watch

    src = st.session_state.get("selected_data_source", "Yahoo Finance")
    tf = st.session_state.get("_used_tf_label", "Daily")
    scan_time = st.session_state.get("_last_scan_label", "—")

    cols = st.columns(5)
    with cols[0]:
        stat_card(f"Scan summary ({tf})", str(total), "Stocks scanned",
                  "search", tone="info")
    with cols[1]:
        stat_card("Valid setups", str(valid), "High probability",
                  "target", tone="bullish")
    with cols[2]:
        stat_card("Watch / confirm", str(watch), "Needs confirmation",
                  "clock", tone="warning")
    with cols[3]:
        stat_card("Avoid / weak", str(avoid), "Low probability",
                  "pause", tone="bearish" if avoid else "muted")
    with cols[4]:
        stat_card("Scan time", scan_time, f"Data source: {src}",
                  "calendar", tone="purple")


def _render_summary_cards(results: dict[str, dict]) -> None:
    """Scan-wide totals.

    Bias is counted from the same derivation the table uses — the category of
    the highest-scoring zone — NOT from ``result["status"]``. The engine's
    status is a broader verdict that is "neutral" for most stocks, so counting
    it here produced "1 bullish" above a table whose rows were nearly all
    marked Bullish. Two numbers describing the same scan must agree.
    """
    total = len(results)
    rows = _row_data(results)
    bull = sum(1 for r in rows if r["bias"] == "Bullish")
    bear = sum(1 for r in rows if r["bias"] == "Bearish")
    # Counted from the rows, NOT as total - bull - bear. The subtraction was a
    # second, independent derivation of the same number, and when zoneless
    # stocks were missing from the rows it kept reporting 2 that the table
    # could not show. One source, so the card and the table cannot disagree.
    neutral = sum(1 for r in rows if r["bias"] == "Neutral")

    scores = [r["odd"] for r in rows]
    best_sym, best_score = "—", 0.0
    if rows:
        best = max(rows, key=lambda r: r["odd"])
        best_sym, best_score = best["symbol"], best["odd"]
    avg = sum(scores) / len(scores) if scores else 0.0

    def pct(n: int) -> str:
        return f"{n / total * 100:.0f}%" if total else "0%"

    cols = st.columns(7)
    with cols[0]:
        stat_card("Total scanned", str(total), "Stocks", "search", tone="info")
    with cols[1]:
        stat_card("Bullish setups", str(bull), pct(bull), "trend_up", tone="bullish")
    with cols[2]:
        stat_card("Bearish setups", str(bear), pct(bear), "trend_down", tone="bearish")
    with cols[3]:
        stat_card("Neutral / wait", str(neutral), pct(neutral), "pause", tone="muted")
    with cols[4]:
        stat_card("Avg ODD score", f"{avg:.2f}", "out of 7", "target", tone="warning")
    with cols[5]:
        stat_card("Best opportunity", f"{best_score:.1f}", best_sym, "star",
                  tone="bullish" if best_score else "muted")
    with cols[6]:
        quality = ("High" if avg >= 5.5 else "Medium" if avg >= 4 else "Low")
        stat_card("Scan quality", quality if scores else "—",
                  "Signal strength", "shield",
                  tone="bullish" if quality == "High" else "warning")


def _zoneless_row(sym: str, res: dict) -> dict:
    """A row for a stock the scan reached but found no tradeable zone in.

    Not an error — these usually detected zones that ``filter_zones`` then
    dropped (price sitting between them, or every zone below the 5.0 display
    floor). The stock's own summary already says so, so it is carried through
    as the reason rather than inventing one.
    """
    return {
        "symbol": sym,
        "exchange": res.get("exchange", "NSE"),
        "price": float(res.get("current_price") or 0.0),
        "trend": (res.get("trend") or "—").upper(),
        "bias": "Neutral",
        "zone": "—",
        "freshness": "—",
        "odd": 0.0,
        "strength": "—",
        "confirmation": "None",
        "tradeable": False,
        "notes": (res.get("summary") or "No tradeable zone found")[:70],
    }


def _row_data(results: dict[str, dict]) -> list[dict]:
    """One row per scanned stock — including those with no zones.

    Stocks that produced no displayable zone used to be skipped here, which
    silently broke the page's arithmetic: the summary card derived its neutral
    count as ``total - bullish - bearish`` and reported 2, while the table had
    no rows for them, so the count could never be reconciled and filtering
    Status to "Neutral" returned nothing. A scanned stock is a result even
    when the result is "nothing found" — that is exactly what the Neutral
    filter is for.
    """
    rows: list[dict] = []
    for sym, res in results.items():
        zones = [*(res.get("demand_zones") or []), *(res.get("supply_zones") or [])]
        if not zones:
            rows.append(_zoneless_row(sym, res))
            continue
        best = max(zones, key=lambda z: z.get("odd_score", 0))
        confirmed = res.get("confirmation_zones") or []
        # Confirmation strength is read off the confirmed zone's own score,
        # which is the only graded signal that exists for it today.
        if confirmed:
            top_conf = max(c.get("odd_score", 0) for c in confirmed)
            conf = "Strong" if top_conf >= 4.5 else "Moderate"
        else:
            conf = "None"
        rows.append({
            "symbol": sym,
            "exchange": res.get("exchange", "NSE"),
            "price": float(res.get("current_price") or 0.0),
            "trend": (res.get("trend") or "—").upper(),
            "bias": "Bullish" if best.get("category") == "demand" else "Bearish",
            "zone": (best.get("category") or "").title(),
            "freshness": "Fresh" if best.get("is_fresh") else "Tested",
            "odd": best.get("odd_score", 0.0),
            "strength": best.get("zone_strength", "Normal"),
            "confirmation": conf,
            "tradeable": bool(best.get("is_tradeable")),
            "notes": (res.get("summary") or "")[:70],
        })
    rows.sort(key=lambda r: r["odd"], reverse=True)
    return rows


def _active_chips() -> list[tuple[str, str]]:
    """(session key, chip label) for every screener filter currently on."""
    out: list[tuple[str, str]] = []
    for key, label, off in _CHIP_FILTERS:
        val = st.session_state.get(key, off)
        if val == off or val in (None, "", [], False):
            continue
        text = label if isinstance(val, bool) else f"{label}: {_fmt(val)}"
        out.append((key, text))
    return out


def _fmt(val: object) -> str:
    return ", ".join(val) if isinstance(val, list) else str(val)


def _clear_filter(key: str) -> None:
    """Reset one screener filter to its off value.

    Writes the sidebar widget key too where one exists, or Streamlit restores
    the old value from the widget on the next render and the chip reappears.
    """
    off = next((o for k, _l, o in _CHIP_FILTERS if k == key), None)
    st.session_state[key] = off
    widget_key = f"sidebar_{key}"
    if widget_key in st.session_state:
        st.session_state[widget_key] = off


def _clear_all_filters() -> None:
    for key, _label, _off in _CHIP_FILTERS:
        _clear_filter(key)


def _render_filter_bar() -> None:
    """Status / Strength / Sort by, plus the removable screener chips.

    These three own their state outright — they have no sidebar equivalent, so
    the results page is their single source of truth.
    """
    cols = st.columns([1.4, 1.4, 1.6, 0.9, 0.8])
    with cols[0]:
        st.selectbox("Status", _STATUS_OPTIONS, key="ar_status")
    with cols[1]:
        st.selectbox("Strength", _STRENGTH_OPTIONS, key="ar_strength")
    with cols[2]:
        st.selectbox("Sort by", _SORT_OPTIONS, key="ar_sort")

    chips = _active_chips()
    with cols[3]:
        st.markdown("<div style='height:26px;'></div>", unsafe_allow_html=True)
        st.markdown(
            f"<div style='font-size:0.8rem;color:#1F6FD0;font-weight:600;"
            f"padding-top:8px;'>Active filters ({len(chips)})</div>",
            unsafe_allow_html=True,
        )
    with cols[4]:
        st.markdown("<div style='height:26px;'></div>", unsafe_allow_html=True)
        st.button("Clear all", key="ar_clear_all", use_container_width=True,
                  disabled=not chips, on_click=_clear_all_filters)

    if chips:
        chip_cols = st.columns(min(len(chips), 5))
        for col, (key, text) in zip(chip_cols, chips):
            with col:
                st.button(f"✕  {text}", key=f"ar_chip_{key}",
                          use_container_width=True,
                          on_click=_clear_filter, args=(key,),
                          help="Remove this screener filter")


def _apply_view_filters(rows: list[dict]) -> list[dict]:
    """Status / Strength / Sort — the page's own view controls."""
    status = st.session_state.get("ar_status", _STATUS_OPTIONS[0])
    if status != _STATUS_OPTIONS[0]:
        # Every row carries an explicit bias of Bullish / Bearish / Neutral,
        # so this is a straight equality — no special case for Neutral.
        rows = [r for r in rows if r["bias"] == status]
    strength = st.session_state.get("ar_strength", _STRENGTH_OPTIONS[0])
    if strength != _STRENGTH_OPTIONS[0]:
        rows = [r for r in rows if r["strength"] == strength]

    sort = st.session_state.get("ar_sort", _SORT_OPTIONS[0])
    if sort == "ODD Score (Low to High)":
        rows.sort(key=lambda r: r["odd"])
    elif sort == "Symbol (A-Z)":
        rows.sort(key=lambda r: r["symbol"])
    elif sort == "Price (High to Low)":
        rows.sort(key=lambda r: r["price"], reverse=True)
    else:
        rows.sort(key=lambda r: r["odd"], reverse=True)
    return rows


def _render_ranked_table(results: dict[str, dict]) -> None:
    screened = {s: r for s, r in results.items() if _passes_screener(r)}
    rows = _apply_view_filters(_row_data(screened))

    top = st.columns([2, 1, 1])
    with top[0]:
        section_title(f"Ranked Opportunities ({len(rows)})")
    with top[1]:
        query = st.text_input("Search", key="ar_search", placeholder="Search symbol…",
                              label_visibility="collapsed")
    with top[2]:
        per_page = st.selectbox("Per page", _PAGE_SIZES, key="ar_pagesize",
                                label_visibility="collapsed")

    if query:
        rows = [r for r in rows if query.strip().upper() in r["symbol"].upper()]
    if not rows:
        st.caption("No stocks match the current filters.")
        return

    pages = max(1, (len(rows) + per_page - 1) // per_page)
    page = st.session_state.get("ar_page", 1)
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    window = rows[start:start + per_page]

    headers = ["#", "Symbol", "Price", "Trend", "Zone", "Zone Status",
               "ODD Score", "Strength", "Confirmation", "RR (Min)", "Action",
               "Reason", "View"]
    head = "<tr>" + "".join(
        f"<th style='text-align:left;padding:7px 8px;font-size:0.7rem;"
        f"color:#8A8F98;font-weight:600;white-space:nowrap;"
        f"border-bottom:1px solid #E7E9ED;'>{h}</th>"
        for h in headers) + "</tr>"

    dash = "<span style='color:#A8A8A0;'>&mdash;</span>"
    body = ""
    for i, r in enumerate(window, start + 1):
        conf_tone = {"Strong": "bullish", "Moderate": "warning"}.get(
            r["confirmation"], "muted")
        # A Neutral row has no side to take. Without the explicit mapping it
        # fell through the Bullish test and rendered as SELL, which would read
        # as a short call on a stock the scan found nothing in.
        act = {"Bullish": "BUY", "Bearish": "SELL"}.get(r["bias"], "WAIT")
        trend_colour = {"UP": "#16794A", "DOWN": "#C23B33"}.get(
            r["trend"], "#8A8F98")
        # View opens the chart in a NEW TAB via the shared deep link, so the
        # analysis context (source, timeframe, strategy, confirmation mode)
        # travels with it — a new tab is a separate Streamlit session.
        view = (
            f"<a href='{build_detail_url(r['symbol'], r['exchange'])}' "
            f"target='_blank' style='color:#2F80ED;font-weight:600;"
            f"text-decoration:none;white-space:nowrap;'>View &rarr;</a>"
        )
        cells = [
            f"<span style='color:#9AA0A8;'>{i}</span>",
            f"<b>{html.escape(r['symbol'])}</b>",
            f"₹{r['price']:,.2f}" if r["price"] else dash,
            f"<span style='color:{trend_colour};font-weight:600;'>"
            f"{html.escape(r['trend'])}</span>",
            html.escape(r["zone"]),
            bias_pill(r["freshness"],
                      "info" if r["freshness"] == "Fresh" else "muted"),
            f"<b>{r['odd']:.1f}</b>" if r["odd"] else dash,
            html.escape(str(r["strength"])),
            bias_pill(r["confirmation"], conf_tone),
            dash,
            bias_pill(act if r["tradeable"] else "WAIT",
                      ("bullish" if act == "BUY" else "bearish")
                      if r["tradeable"] else "warning"),
            f"<span style='color:#71757C;'>{html.escape(r['notes'])}</span>",
            view,
        ]
        body += "<tr>" + "".join(
            f"<td style='padding:7px 8px;font-size:0.78rem;vertical-align:middle;"
            f"border-bottom:1px solid #F1F2F4;'>{c}</td>" for c in cells
        ) + "</tr>"

    st.markdown(
        f"<div style='overflow-x:auto;'><table style='width:100%;"
        f"border-collapse:collapse;'>{head}{body}</table></div>",
        unsafe_allow_html=True,
    )

    nav = st.columns([3, 1, 1, 1])
    with nav[0]:
        st.caption(
            f"Showing {start + 1} to {min(start + per_page, len(rows))} "
            f"of {len(rows)} results"
        )
    with nav[1]:
        if st.button("Prev", key="ar_prev", disabled=page <= 1,
                     use_container_width=True):
            st.session_state["ar_page"] = page - 1
            st.rerun()
    with nav[2]:
        st.caption(f"Page {page} / {pages}")
    with nav[3]:
        if st.button("Next", key="ar_next", disabled=page >= pages,
                     use_container_width=True):
            st.session_state["ar_page"] = page + 1
            st.rerun()


def _render_legend(results: dict[str, dict]) -> None:
    """The Columns panel — counts of each confirmation state."""
    demand_conf = supply_conf = weak = strong = 0
    for r in results.values():
        confirmed = r.get("confirmation_zones") or []
        if not confirmed:
            weak += 1
            continue
        for z in confirmed:
            if z.get("category") == "demand":
                demand_conf += 1
            else:
                supply_conf += 1
        if max(z.get("odd_score", 0) for z in confirmed) >= 4.5:
            strong += 1

    with st.container(border=True):
        section_title("Columns")
        for label, count, colour in [
            ("Demand Zones Confirmed", demand_conf, "#1e7e34"),
            ("Supply Zones Confirmed", supply_conf, "#c92a2a"),
            ("Weak / No Confirmation", weak, "#8A8A82"),
            ("Strong & Above Setups", strong, "#1c6fb0"),
        ]:
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;"
                f"font-size:0.78rem;padding:2px 0;'>"
                f"<span><span style='color:{colour};'>●</span> "
                f"{html.escape(label)}</span><b>{count}</b></div>",
                unsafe_allow_html=True,
            )


def _render_insights(results: dict[str, dict]) -> None:
    """Derived observations about the scan as a whole."""
    total = len(results)
    bull = sum(1 for r in results.values() if r.get("status") == "bullish")
    confirmed = sum(1 for r in results.values() if r.get("confirmation_zones"))
    lines = []
    if total:
        if bull / total > 0.5:
            lines.append("Market breadth is bullish across this watchlist.")
        elif bull / total < 0.25:
            lines.append("Market breadth is weak across this watchlist.")
        else:
            lines.append("Market breadth is mixed across this watchlist.")
    if confirmed:
        lines.append(f"{confirmed} stocks have at least one confirmed zone.")
    else:
        lines.append("No confirmed zones in this scan.")
    lines.append("Avoid untested zones scoring below 5.")

    with st.container(border=True):
        section_title("Scan Insights")
        for line in lines:
            st.markdown(
                f"<div style='font-size:0.78rem;padding:2px 0;'>"
                f"✓ {html.escape(line)}</div>", unsafe_allow_html=True)


def _render_detail_strip(results: dict[str, dict]) -> None:
    """Selected-stock detail, setup logic, key levels and scan alerts."""
    st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)
    cols = st.columns([1, 1, 1, 1])

    symbols = sorted(results)
    with cols[0]:
        with st.container(border=True):
            section_title("Selected Stock Details")
            pick = st.selectbox("Stock", symbols, key="ar_selected",
                                label_visibility="collapsed")
            res = results.get(pick, {})
            zones = [*(res.get("demand_zones") or []),
                     *(res.get("supply_zones") or [])]
            best = max(zones, key=lambda z: z.get("odd_score", 0)) if zones else {}
            kv_row("Setup Type",
                   f"{(best.get('category') or '—').title()} Zone")
            kv_row("Zone Status",
                   "Fresh" if best.get("is_fresh") else "Tested" if best else "—")
            kv_row("ODD Score", f"{best.get('odd_score', 0):.1f} / 7" if best else "—")
            kv_row("HTF Trend", pending="p3")
            kv_row("Entry Zone", pending="p2")
            kv_row("Stop Loss", pending="p2")
            kv_row("Targets", pending="p2")
            kv_row("Risk / Reward", pending="p2")

    with cols[1]:
        with st.container(border=True):
            section_title("Setup Logic")
            res = results.get(st.session_state.get("ar_selected", ""), {})
            zones = [*(res.get("demand_zones") or []),
                     *(res.get("supply_zones") or [])]
            best = max(zones, key=lambda z: z.get("odd_score", 0)) if zones else {}
            checks = [
                ("Zone detected", bool(best)),
                ("Fresh (untested)", bool(best.get("is_fresh"))),
                ("EMA20 confluence", bool(best.get("ema20_enhancer"))),
                ("Trend aligned", bool(best.get("is_tradeable"))),
                ("Clean departure",
                 best.get("zone_quality") not in ("Weak Departure", None)),
            ]
            for label, ok in checks:
                mark = "✅" if ok else "⬜"
                st.markdown(
                    f"<div style='font-size:0.78rem;padding:2px 0;'>{mark} "
                    f"{html.escape(label)}</div>", unsafe_allow_html=True)

    with cols[2]:
        with st.container(border=True):
            section_title("Key Levels")
            res = results.get(st.session_state.get("ar_selected", ""), {})
            nd, ns = res.get("nearest_demand"), res.get("nearest_supply")
            # Support/resistance are read straight off the nearest zone
            # boundaries — the proximal is the level price meets first.
            kv_row("Support 1", f"{nd['proximal']:,.2f}" if nd else "—")
            kv_row("Support 2", f"{nd['distal']:,.2f}" if nd else "—")
            kv_row("Resistance 1", f"{ns['proximal']:,.2f}" if ns else "—")
            kv_row("Resistance 2", f"{ns['distal']:,.2f}" if ns else "—")

    with cols[3]:
        with st.container(border=True):
            section_title("Recent Scan Alerts")
            try:
                alerts = (get_all_alerts() or [])[:6]
            except Exception:
                alerts = []
            if not alerts:
                st.caption("No alerts.")
            for a in alerts:
                msg = getattr(a, "message", None) or str(a)
                st.markdown(
                    f"<div style='font-size:0.76rem;padding:2px 0;"
                    f"border-bottom:1px solid #F4F4F1;'>• "
                    f"{html.escape(str(msg))[:60]}</div>",
                    unsafe_allow_html=True)
