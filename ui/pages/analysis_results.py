"""Analysis results page (design screenshot 2).

Reached from "Run analysis". Previously the results grid was rendered inside
the dashboard; it now has its own page so the dashboard can be a market view.

The scan itself is unchanged — this page calls ``dashboard.run_scan``, which
is the same loop that used to live in ``render_dashboard``.

**Placeholders.** Risk/Reward needs Phase 2 (M1), so the RR column is kept
but shows a muted placeholder until entry, stop and target logic exists.
"""

from __future__ import annotations

import html

import streamlit as st

from storage.database import save_latest_analysis_snapshot
from ui.components.stock_card import build_detail_url
from ui.components.panels import (
    bias_pill,
    page_slice,
    pagination_bar,
    filter_chip,
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
from utils.helpers import get_company_name
from utils.logger import get_logger

logger = get_logger(__name__)

_PAGE_SIZES = [10, 20, 30, 50, 75, 100]

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
            try:
                save_latest_analysis_snapshot(
                    produced,
                    {
                        "last_scan_label": st.session_state["_last_scan_label"],
                        "used_tf_label": st.session_state.get("_used_tf_label", ""),
                        "fallback_symbols": st.session_state.get(
                            "_fetch_fallback_symbols", []
                        ),
                    },
                )
            except Exception as exc:
                logger.warning("Could not save latest analysis snapshot: %s", exc)
        # Rerun once the scan finishes. app.main() renders the sidebar BEFORE
        # routing here, so the Alerts badge was computed against the previous
        # (usually empty) results and stayed stale until some later
        # interaction forced a redraw — the scan produced alerts the nav could
        # not show. One extra pass is cheap: run_scan has already cleared the
        # analysing flag, so this cannot loop.
        st.rerun()

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

    with st.container(border=True):
        _render_ranked_table(results)


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
    floor). It still belongs in the results table as a neutral / wait row.
    """
    return {
        "symbol": sym,
        "company": get_company_name(sym),
        "exchange": res.get("exchange", "NSE"),
        "price": float(res.get("current_price") or 0.0),
        "trend": (res.get("trend") or "—").upper(),
        "bias": "Neutral",
        "zone": "—",
        "freshness": "—",
        "odd": 0.0,
        "strength": "—",
        "confirmation": "None",
        "rr": None,
        "tradeable": False,
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
            "company": get_company_name(sym),
            "exchange": res.get("exchange", "NSE"),
            "price": float(res.get("current_price") or 0.0),
            "trend": (res.get("trend") or "—").upper(),
            "bias": "Bullish" if best.get("category") == "demand" else "Bearish",
            "zone": (best.get("category") or "").title(),
            "freshness": "Fresh" if best.get("is_fresh") else "Tested",
            "odd": best.get("odd_score", 0.0),
            "strength": best.get("zone_strength", "Normal"),
            "confirmation": conf,
            "rr": res.get("risk_reward") or best.get("risk_reward"),
            "tradeable": bool(best.get("is_tradeable")),
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

    top = st.columns([3, 1])
    with top[0]:
        section_title(f"Ranked Opportunities ({len(rows)})")
    with top[1]:
        query = st.text_input("Search", key="ar_search", placeholder="Search symbol…",
                              label_visibility="collapsed")

    if query:
        rows = [r for r in rows if query.strip().upper() in r["symbol"].upper()]
    if not rows:
        st.caption("No stocks match the current filters.")
        return

    # Slice computed here, bar drawn after the table — both share the "ar"
    # key so the rows and the "Showing X to Y" caption cannot disagree.
    start, end = page_slice(len(rows), "ar", default_size=20)
    window = rows[start:end]

    headers = ["#", "Stock", "Price", "Trend", "Zone", "Zone Status",
               "ODD", "Strength", "Confirmation", "RR (Min)", "Action",
               "View"]
    head = "<tr>" + "".join(
        f"<th style='text-align:left;padding:10px 10px;font-size:0.68rem;"
        f"color:#7A8290;font-weight:800;white-space:nowrap;"
        f"text-transform:uppercase;letter-spacing:0.35px;"
        f"border-bottom:1px solid #E3E8F0;background:#F8FAFD;'>{h}</th>"
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
        # View opens the chart in a NEW TAB via the shared deep link, so the
        # analysis context (source, timeframe, strategy, confirmation mode)
        # travels with it — a new tab is a separate Streamlit session.
        view = (
            f"<a href='{build_detail_url(r['symbol'], r['exchange'])}' "
            f"target='_blank' style='display:inline-flex;align-items:center;"
            f"justify-content:center;border:1px solid #D6E4FF;background:#F3F7FF;"
            f"color:#1F6FD0;font-weight:800;text-decoration:none;"
            f"white-space:nowrap;border-radius:7px;padding:5px 9px;'>View &rarr;</a>"
        )
        row_bg = "#FFFFFF" if i % 2 else "#FCFDFF"
        cells = [
            f"<span style='display:inline-flex;align-items:center;"
            f"justify-content:center;width:24px;height:24px;border-radius:50%;"
            f"background:#F1F4F8;color:#596273;font-weight:800;'>{i}</span>",
            _stock_cell(r),
            f"<span style='font-weight:700;color:#26313F;'>₹{r['price']:,.2f}</span>"
            if r["price"] else dash,
            bias_pill(r["trend"], _trend_tone(r["trend"])),
            bias_pill(r["zone"], _zone_tone(r["zone"])) if r["zone"] != "—" else dash,
            bias_pill(r["freshness"],
                      "info" if r["freshness"] == "Fresh" else "muted"),
            _odd_score_cell(float(r["odd"] or 0.0)) if r["odd"] else dash,
            bias_pill(str(r["strength"]), _strength_tone(str(r["strength"]))),
            bias_pill(r["confirmation"], conf_tone),
            _rr_cell(r.get("rr")),
            bias_pill(act if r["tradeable"] else "WAIT",
                      ("bullish" if act == "BUY" else "bearish")
                      if r["tradeable"] else "warning"),
            view,
        ]
        body += f"<tr style='background:{row_bg};'>" + "".join(
            f"<td style='padding:10px 10px;font-size:0.79rem;vertical-align:middle;"
            f"border-bottom:1px solid #EDF0F5;'>{c}</td>" for c in cells
        ) + "</tr>"

    st.markdown(
        f"<div style='overflow-x:auto;border:1px solid #EEF1F5;"
        f"border-radius:10px;'><table style='width:100%;min-width:1050px;"
        f"border-collapse:collapse;background:#FFFFFF;'>{head}{body}</table></div>",
        unsafe_allow_html=True,
    )

    pagination_bar(len(rows), "ar", tuple(_PAGE_SIZES), 20)


def _stock_cell(row: dict) -> str:
    symbol = html.escape(str(row.get("symbol") or ""))
    company = html.escape(str(row.get("company") or ""))
    href = html.escape(build_detail_url(row["symbol"], row["exchange"]), quote=True)
    return (
        f"<div style='min-width:148px;'>"
        f"<a href='{href}' target='_blank' style='color:#16233A;"
        f"text-decoration:none;font-weight:900;letter-spacing:0;'>"
        f"{symbol}</a>"
        f"<div style='font-size:0.68rem;color:#7A8290;margin-top:2px;"
        f"white-space:nowrap;max-width:170px;overflow:hidden;"
        f"text-overflow:ellipsis;'>{company}</div></div>"
    )


def _odd_score_cell(score: float) -> str:
    width = max(0, min(100, score / 7 * 100))
    colour = "#22A55B" if score >= 5 else "#F2A93B" if score >= 3.5 else "#B6B6AE"
    return (
        f"<div style='min-width:70px;'>"
        f"<div style='font-weight:900;color:#26313F;'>{score:.1f}</div>"
        f"<div style='height:5px;background:#EEF1F5;border-radius:999px;"
        f"overflow:hidden;margin-top:4px;'>"
        f"<div style='height:100%;width:{width:.0f}%;background:{colour};"
        f"border-radius:999px;'></div></div></div>"
    )


def _rr_cell(value: object) -> str:
    try:
        rr = float(value)
    except (TypeError, ValueError):
        rr = 0.0
    if rr > 0:
        return f"<b style='color:#26313F;'>{rr:.2f}:1</b>"
    return "<span style='color:#A8A8A0;font-weight:700;'>&mdash;</span>"


def _trend_tone(trend: str) -> str:
    return {"UP": "bullish", "DOWN": "bearish"}.get(trend, "muted")


def _zone_tone(zone: str) -> str:
    return {"Demand": "bullish", "Supply": "bearish"}.get(zone, "muted")


def _strength_tone(strength: str) -> str:
    if strength == "Strong":
        return "bullish"
    if strength == "Weak":
        return "bearish"
    if strength == "Very Strong":
        return "purple"
    if strength == "Normal":
        return "neutral"
    return "muted"
