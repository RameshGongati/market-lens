"""Dashboard landing page — market-wide overview (design screenshot 1).

Replaces the old dashboard, which was an empty shell until a scan ran and then
became the results grid. Results now live on their own page
(``ui/pages/analysis_results.py``); this page answers "what is the market
doing and what did my last scan find".

**Cold start.** Every setup-derived panel here needs a scan to have run. Rather
than showing an empty page on launch, this reads whatever is already in
``st.session_state.analysis_results`` — so after the first scan of a session
the dashboard stays populated while the user moves between pages. Before any
scan the setup panels show a "run a scan" hint; the market strip still renders,
because index data does not depend on a scan.

**Placeholders.** Sector strength, the sector heatmap, and the HTF/ITF trend
columns are drawn as labelled but empty boxes — see ui/components/panels.py
for why they are explicit rather than omitted or faked.
"""

from __future__ import annotations

import html

import streamlit as st

from data.market_indices import fetch_all_indices, market_bias
from storage.database import get_all_alerts
from ui.components.panels import (
    bias_pill,
    kv_row,
    page_title,
    pending_panel,
    section_title,
    stat_card,
)
from utils.logger import get_logger

logger = get_logger(__name__)

# ODD score at or above which a setup is counted "high probability" on the
# summary strip. Matches the documented "no trade below 5" cutoff's top band.
_HIGH_ODD = 7.0


@st.cache_data(ttl=300, show_spinner=False)
def indices_cached() -> list[dict]:
    """Index snapshots, refreshed every 5 minutes.

    Cached here rather than in ``data.market_indices`` so that module stays
    Streamlit-free and testable.
    """
    return [dict(s) for s in fetch_all_indices()]


def _sparkline(values: list[float], up: bool) -> str:
    """Inline SVG sparkline — small enough to sit inside a metric row.

    Hand-built rather than a Plotly figure: a 60x20 chart per index would cost
    two full chart objects for decoration.
    """
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = 60 / (len(values) - 1)
    pts = " ".join(
        f"{i * step:.1f},{18 - (v - lo) / span * 16:.1f}"
        for i, v in enumerate(values)
    )
    colour = "#1e7e34" if up else "#c92a2a"
    return (
        f"<svg width='60' height='20' viewBox='0 0 60 20' "
        f"style='vertical-align:middle;'>"
        f"<polyline points='{pts}' fill='none' stroke='{colour}' "
        f"stroke-width='1.4'/></svg>"
    )


def _zone_state_counts(results: dict[str, dict]) -> dict[str, int]:
    """Bucket every scanned stock into the four states the design shows.

    The buckets are mutually exclusive and derived from data the engine
    already produces:

    * **Near Demand / Near Supply** — a displayed zone within
      :data:`_NEAR_PCT` of price on that side.
    * **Waiting for Confirmation** — price has entered a zone but never
      closed back out (``activation_touch`` set, ``times_tested`` zero). This
      state has always existed in the engine and has never been surfaced.
    * **Avoid / Weak** — scanned, but nothing tradeable found.
    """
    near_pct = 3.0
    counts = {"demand": 0, "supply": 0, "waiting": 0, "avoid": 0}

    for res in results.values():
        price = res.get("current_price") or 0.0
        if price <= 0:
            counts["avoid"] += 1
            continue

        zones = [*(res.get("demand_zones") or []), *(res.get("supply_zones") or [])]
        waiting = any(
            z.get("activation_touch") and not z.get("times_tested")
            for z in zones
        )
        near_demand = any(
            z.get("category") == "demand"
            and abs(price - z.get("proximal", 0)) / price * 100 <= near_pct
            for z in zones
        )
        near_supply = any(
            z.get("category") == "supply"
            and abs(price - z.get("proximal", 0)) / price * 100 <= near_pct
            for z in zones
        )

        if near_demand:
            counts["demand"] += 1
        elif near_supply:
            counts["supply"] += 1
        elif waiting:
            counts["waiting"] += 1
        else:
            counts["avoid"] += 1
    return counts


def _best_by_bias(results: dict[str, dict], bias: str, limit: int = 3) -> list[str]:
    """Top symbols on one side, ranked by their best zone score.

    Bias is taken from the category of the stock's best-scoring zone, NOT from
    ``result["status"]``. Status is "neutral" for the large majority of
    stocks, so filtering on it returned a single name where the table below
    was showing thirty — the two panels described the same scan and disagreed.
    """
    side = "demand" if bias == "bullish" else "supply"
    scored: list[tuple[float, str]] = []
    for sym, res in results.items():
        zones = [*(res.get("demand_zones") or []), *(res.get("supply_zones") or [])]
        if not zones:
            continue
        best = max(zones, key=lambda z: z.get("odd_score", 0))
        if best.get("category") == side:
            scored.append((best.get("odd_score", 0), sym))
    scored.sort(reverse=True)
    return [s for _, s in scored[:limit]]


def _top_opportunities(results: dict[str, dict], limit: int = 10) -> list[dict]:
    """Rows for the Top Opportunities table, best score first."""
    rows: list[dict] = []
    for sym, res in results.items():
        zones = [*(res.get("demand_zones") or []), *(res.get("supply_zones") or [])]
        if not zones:
            continue
        best = max(zones, key=lambda z: z.get("odd_score", 0))
        rows.append({
            "symbol": sym,
            "bias": "Bullish" if best.get("category") == "demand" else "Bearish",
            "zone": (best.get("category") or "").title(),
            "freshness": "Fresh" if best.get("is_fresh") else "Tested",
            "odd": best.get("odd_score", 0.0),
            "action": "BUY" if best.get("category") == "demand" else "SELL",
            "tradeable": bool(best.get("is_tradeable")),
            "reason": res.get("summary", "")[:60],
        })
    rows.sort(key=lambda r: r["odd"], reverse=True)
    return rows[:limit]


def render_market_overview() -> None:
    """Render the dashboard landing page."""
    results: dict[str, dict] = st.session_state.get("analysis_results", {}) or {}
    scanned = len(results)

    # ---- Header -----------------------------------------------------------
    head_l, head_r = st.columns([3, 2])
    with head_l:
        page_title(
            "Market Lens Dashboard",
            "Smart Analysis. Better Decisions. Stronger Trades.",
        )
    with head_r:
        # A single "Analysis Results" button, not Run analysis + View results.
        # Running a scan already has one home — the sidebar — and putting a
        # second trigger here made the dashboard offer two doors to the same
        # place, one of which silently discarded the scan you already had.
        # This one only navigates; the results page decides whether it needs
        # to scan.
        cols = st.columns(2)
        with cols[0]:
            st.button(
                "Analysis Results", icon=":material/table_view:",
                use_container_width=True, key="mo_results", type="primary",
                on_click=_go_results,
                help="Open the latest scan results.",
            )
        with cols[1]:
            st.button(
                "Schedule scan", icon=":material/event:",
                use_container_width=True, key="mo_sched", disabled=True,
                help="Not yet built.",
            )

    # ---- Top summary cards -------------------------------------------------
    indices = indices_cached()
    bias, bias_reason = market_bias(indices)  # type: ignore[arg-type]

    high_odd = sum(
        1 for r in results.values()
        if any(
            z.get("odd_score", 0) >= _HIGH_ODD
            for z in [*(r.get("demand_zones") or []), *(r.get("supply_zones") or [])]
        )
    )
    valid_setups = sum(
        1 for r in results.values()
        if (r.get("demand_zones") or r.get("supply_zones"))
    )
    try:
        alerts_today = len(get_all_alerts() or [])
    except Exception:
        alerts_today = 0

    cards = st.columns(6)
    with cards[0]:
        stat_card(
            "Market bias", bias, bias_reason, "trend_up",
            tone="bullish" if bias == "BULLISH" else
                 "bearish" if bias == "BEARISH" else "muted",
        )
    with cards[1]:
        stat_card("Strong sectors", icon="trophy", pending="p7")
    with cards[2]:
        stat_card("Valid setups", str(valid_setups), "Zones detected", "target", tone="info")
    with cards[3]:
        stat_card(
            "High ODD setups", str(high_odd),
            f"ODD score ≥ {_HIGH_ODD:g}", "star", tone="warning",
        )
    with cards[4]:
        stat_card("Alerts today", str(alerts_today), "Needs attention", "bell",
                  tone="bearish" if alerts_today else "muted")
    with cards[5]:
        last = st.session_state.get("_last_scan_label", "")
        stat_card(
            "Scan status", "Completed" if scanned else "Not run",
            last or f"{scanned} stocks", "check_circle",
            tone="purple" if scanned else "muted",
        )

    # ---- Market overview + heatmap ----------------------------------------
    left, right = st.columns([3, 2])
    with left:
        with st.container(border=True):
            section_title("Today's Market Overview")
            cols = st.columns(len(indices) + 2)
            for col, snap in zip(cols, indices):
                with col:
                    up = snap["change"] >= 0
                    colour = "#1e7e34" if up else "#c92a2a"
                    val = f"{snap['last']:,.2f}" if snap["ok"] else "—"
                    delta = (
                        f"{snap['change']:+,.2f} ({snap['change_pct']:+.2f}%)"
                        if snap["ok"] else "unavailable"
                    )
                    st.markdown(
                        f"<div style='font-size:0.7rem;color:#6B6B63;"
                        f"font-weight:700;'>{html.escape(snap['name'])}</div>"
                        f"<div style='font-size:1.1rem;font-weight:700;'>{val}</div>"
                        f"<div style='font-size:0.72rem;color:{colour};'>{delta}</div>"
                        f"{_sparkline(snap['spark'], up)}",
                        unsafe_allow_html=True,
                    )
            with cols[-2]:
                st.markdown(
                    "<div style='font-size:0.7rem;color:#6B6B63;font-weight:700;'>"
                    "BEST LONG</div>", unsafe_allow_html=True)
                longs = _best_by_bias(results, "bullish")
                st.markdown(
                    f"<div style='font-size:0.82rem;color:#1e7e34;font-weight:600;'>"
                    f"{html.escape(', '.join(longs)) if longs else '—'}</div>",
                    unsafe_allow_html=True)
            with cols[-1]:
                st.markdown(
                    "<div style='font-size:0.7rem;color:#6B6B63;font-weight:700;'>"
                    "BEST SHORT</div>", unsafe_allow_html=True)
                shorts = _best_by_bias(results, "bearish")
                st.markdown(
                    f"<div style='font-size:0.82rem;color:#c92a2a;font-weight:600;'>"
                    f"{html.escape(', '.join(shorts)) if shorts else '—'}</div>",
                    unsafe_allow_html=True)

            st.markdown("<div style='height:6px;'></div>", unsafe_allow_html=True)
            counts = _zone_state_counts(results)
            pills = st.columns(4)
            for col, (label, key, tone) in zip(pills, [
                ("Near Demand", "demand", "bullish"),
                ("Near Supply", "supply", "bearish"),
                ("Waiting for Confirmation", "waiting", "warning"),
                ("Avoid / Weak Setup", "avoid", "muted"),
            ]):
                with col:
                    stat_card(label, f"{counts[key]}", "stocks", tone=tone)

    with right:
        pending_panel(
            "Market Heatmap", "p7",
            "Sector performance tiles need sector index data.",
        )

    # ---- Opportunities + sector strength ----------------------------------
    opp_col, sect_col = st.columns([3, 2])
    with opp_col:
        with st.container(border=True):
            rows = _top_opportunities(results)
            section_title(f"Top Opportunities ({len(rows)})")
            if not rows:
                st.caption("Run a scan to populate this table.")
            else:
                _render_opportunity_table(rows)
    with sect_col:
        pending_panel(
            "Sector Strength", "p7",
            "Per-sector strength bars need sector index data.",
        )

    # ---- Selected stock + alerts + quick tools -----------------------------
    a, b, c = st.columns([2, 1, 1])
    with a:
        with st.container(border=True):
            section_title("Selected Stock Analysis")
            _render_selected_stock(results)
    with b:
        with st.container(border=True):
            section_title("Recent Alerts")
            _render_recent_alerts()
    with c:
        with st.container(border=True):
            section_title("Quick Tools")
            _render_quick_tools()


def _render_opportunity_table(rows: list[dict]) -> None:
    """The Top Opportunities table.

    HTF Trend, ITF Trend, Entry Zone and R:R are columns in the design that
    have no data behind them yet (Phases 3 and 2). They are rendered with
    their headers and an em dash so the layout is final and the missing
    inputs are visible, rather than silently dropped.
    """
    header = (
        "<tr>" + "".join(
            f"<th style='text-align:left;padding:4px 6px;font-size:0.7rem;"
            f"color:#6B6B63;border-bottom:1px solid #E7E7E1;'>{h}</th>"
            for h in ["#", "Symbol", "Bias", "Zone", "Freshness", "ODD",
                      "HTF Trend", "ITF Trend", "Entry Zone", "R:R",
                      "Action", "Reason"]
        ) + "</tr>"
    )
    dash = "<span style='color:#A8A8A0;'>&mdash;</span>"
    body = ""
    for i, r in enumerate(rows, 1):
        tone = "bullish" if r["bias"] == "Bullish" else "bearish"
        act_tone = "bullish" if r["action"] == "BUY" else "bearish"
        cells = [
            str(i),
            f"<b>{html.escape(r['symbol'])}</b>",
            bias_pill(r["bias"], tone),
            html.escape(r["zone"]),
            bias_pill(r["freshness"], "info" if r["freshness"] == "Fresh" else "muted"),
            f"<b>{r['odd']:.1f}</b>",
            dash, dash, dash, dash,
            bias_pill(r["action"] if r["tradeable"] else "WAIT",
                      act_tone if r["tradeable"] else "warning"),
            f"<span style='color:#6B6B63;'>{html.escape(r['reason'])}</span>",
        ]
        body += "<tr>" + "".join(
            f"<td style='padding:5px 6px;font-size:0.78rem;"
            f"border-bottom:1px solid #F4F4F1;'>{c}</td>" for c in cells
        ) + "</tr>"

    st.markdown(
        f"<div style='overflow-x:auto;'><table style='width:100%;"
        f"border-collapse:collapse;'>{header}{body}</table></div>",
        unsafe_allow_html=True,
    )


def _render_selected_stock(results: dict[str, dict]) -> None:
    """Compact read-out for one scanned stock, with a link to the full chart."""
    if not results:
        st.caption("Run a scan, then pick a stock here.")
        return
    symbols = sorted(results)
    pick = st.selectbox(
        "Stock", symbols, key="mo_selected_stock", label_visibility="collapsed",
    )
    res = results.get(pick, {})
    zones = [*(res.get("demand_zones") or []), *(res.get("supply_zones") or [])]
    best = max(zones, key=lambda z: z.get("odd_score", 0)) if zones else {}

    price = res.get("current_price", 0.0)
    st.markdown(
        f"<div style='font-size:1.2rem;font-weight:700;'>₹{price:,.2f}</div>"
        f"<div style='font-size:0.78rem;color:#6B6B63;'>"
        f"{html.escape(res.get('status', 'neutral').title())}</div>",
        unsafe_allow_html=True,
    )
    if best:
        kv_row("Zone", f"{best.get('zone_type', '')} "
                       f"({'Fresh' if best.get('is_fresh') else 'Tested'})")
        kv_row("ODD Score", f"{best.get('odd_score', 0):.1f} / 7")
        kv_row("EMA 20", "Confluence" if best.get("ema20_enhancer") else "None")
        kv_row("Setup Status", best.get("entry_recommendation", "—"))
    else:
        kv_row("Zone", "No zone detected")
    kv_row("R:R (Target 1)", pending="p2")

    if st.button("View full analysis", key="mo_view_full",
                 use_container_width=True):
        st.session_state.selected_stock_symbol = pick
        st.session_state.selected_stock_exchange = res.get("exchange", "NSE")
        st.session_state.active_page = "stock_detail"
        st.rerun()


def _render_recent_alerts() -> None:
    """The five most recent alerts from the database."""
    try:
        alerts = (get_all_alerts() or [])[:5]
    except Exception as exc:
        logger.warning("Could not load alerts: %s", exc)
        alerts = []
    if not alerts:
        st.caption("No alerts yet.")
        return
    for a in alerts:
        msg = getattr(a, "message", None) or str(a)
        st.markdown(
            f"<div style='font-size:0.78rem;padding:3px 0;"
            f"border-bottom:1px solid #F4F4F1;'>• {html.escape(str(msg))[:70]}</div>",
            unsafe_allow_html=True,
        )


def _render_quick_tools() -> None:
    """Shortcut grid. Tools that do not exist yet are present but disabled."""
    built = [
        ("Watchlist Manager", ":material/list:", "watchlist_manager"),
        ("Settings", ":material/settings:", "settings"),
    ]
    for label, icon, page in built:
        if st.button(label, icon=icon, use_container_width=True,
                     key=f"qt_{page}"):
            st.session_state.active_page = page
            st.rerun()
    for label, icon in [
        ("Risk Calculator", ":material/calculate:"),
        ("Trade Journal", ":material/book:"),
        ("Sector View", ":material/donut_small:"),
        ("Backtest", ":material/science:"),
    ]:
        st.button(label, icon=icon, use_container_width=True, disabled=True,
                  key=f"qt_{label}", help="Not yet built.")


def _go_results() -> None:
    """Open the results page without triggering a scan.

    Never sets ``analysing``: an existing scan must survive a trip to the
    dashboard and back. The results page prompts for a scan when it has
    nothing to show, and the sidebar's Run analysis remains the single way to
    start one.
    """
    st.session_state.active_page = "analysis_results"
