"""Alerts page — zone-proximity matches and the triggered alert history.

Restores the alert surface that lived on the old dashboard above the results
grid. That banner was rendered by ``dashboard._render_filter_sort_bar``, which
lost its only caller when the results grid moved to its own page, so the
alerts silently stopped appearing. Rather than putting the banner back on a
page that no longer lists stocks, it becomes a page of its own.

Two different things are shown, and they answer different questions:

* **Near zones now** — live matches from the current scan, recomputed against
  ``config/alert_config.json`` every render. This is what *would* fire, and it
  exists whether or not alerting is switched on.
* **Alert history** — rows written to the ``alerts`` table by
  ``alerts.manager.check_and_trigger_alerts``, which is also what was
  delivered to Telegram. This is what *did* fire.

A stock can appear in the first and not the second: alerts only persist when
the stock came from a saved watchlist (``stock.id`` is set) and alerting was
enabled at scan time.
"""

from __future__ import annotations

import html
import re

import streamlit as st

from alerts.zone_alert_checker import check_zone_alerts
from config.alert_settings import load_alert_config
from storage.database import get_unread_alerts, mark_all_alerts_read
from ui.components.panels import (
    bias_pill,
    page_title,
    section_title,
    spacer,
    stat_card,
)
from ui.components.stock_card import build_detail_url
from utils.logger import get_logger

logger = get_logger(__name__)

# Cooldown keys are "SYMBOL_proximal" or "SYMBOL_proximal_YYYY-MM-DD".
_KEY_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_SORTS = (
    "Latest first",
    "Nearest to zone first",
    "Highest ODD score first",
    "Symbol A-Z",
)


def telegram_sent_alerts() -> list[dict]:
    """Alerts the background monitor actually delivered to Telegram.

    Read from ``config/alert_config.json``'s ``alert_history`` rather than the
    ``alerts`` database table, because that is where they are: the monitor
    (``alert_monitor.py``) runs outside Streamlit and records what it sent as
    cooldown keys, never touching the table. The table stays empty for index
    and F&O scans anyway — ``create_alert`` needs a real ``stock_id`` and
    foreign keys are enforced, so predefined-watchlist stocks (id 0) cannot be
    inserted.

    The key carries everything a row needs. It is parsed from the RIGHT so a
    symbol containing ``-`` or ``&`` survives: an optional trailing date, then
    the zone level, and whatever precedes both is the symbol.
    """
    out: list[dict] = []
    try:
        cfg = load_alert_config() or {}
        for key, sent_at in (cfg.get("alert_history") or {}).items():
            parts = key.split("_")
            if parts and _KEY_DATE.match(parts[-1]):
                parts.pop()
            try:
                proximal = float(parts.pop())
            except (ValueError, IndexError):
                continue
            symbol = "_".join(parts)
            if not symbol:
                continue
            out.append({
                "symbol": symbol,
                "proximal": proximal,
                "sent_at": str(sent_at),
                "origin": "Sent",
            })
    except Exception as exc:
        logger.warning("Could not read Telegram alert history: %s", exc)
    return out


def zone_alert_matches() -> list:
    """Live zone-proximity matches for the current scan, or an empty list.

    Shared with the sidebar so the nav badge and this page always show the
    same number — a badge that disagrees with the page it points at is worse
    than no badge.
    """
    try:
        results = st.session_state.get("analysis_results", {}) or {}
        if not results:
            return []
        cfg = load_alert_config()
        return list(check_zone_alerts(results, cfg) or [])
    except Exception as exc:
        logger.warning("Zone alert check failed: %s", exc)
        return []


def unread_alert_count() -> int:
    """Unread rows in the alerts table. Never raises — it feeds a badge."""
    try:
        return len(get_unread_alerts() or [])
    except Exception:
        return 0


def render_alerts_page() -> None:
    """Render the Alerts page."""
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            "<div style='font-size:0.78rem;color:#9AA0A8;margin-bottom:2px;'>"
            "Dashboard &nbsp;&rsaquo;&nbsp; "
            "<span style='color:#4A5361;font-weight:600;'>Alerts</span></div>",
            unsafe_allow_html=True,
        )
        page_title("Alerts", "Zone proximity matches and delivery history",
                   icon="bell")
    with right:
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Mark all read", icon=":material/mark_email_read:",
                         use_container_width=True, key="al_mark_read"):
                try:
                    mark_all_alerts_read()
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not mark alerts read: {exc}")
        with b2:
            if st.button("Back to Dashboard", icon=":material/arrow_back:",
                         use_container_width=True, key="al_back"):
                st.session_state.active_page = "dashboard"
                st.rerun()

    cfg = _safe_config()
    matches = zone_alert_matches()
    sent = telegram_sent_alerts()

    spacer(12)
    cols = st.columns(4)
    with cols[0]:
        stat_card("Near zones now", str(len(matches)), "From the last scan",
                  "target", tone="warning" if matches else "muted")
    with cols[1]:
        stat_card("Sent to Telegram", str(len(sent)),
                  "Delivered by the monitor", "bell",
                  tone="info" if sent else "muted")
    with cols[2]:
        stat_card("Symbols alerted", str(len({s["symbol"] for s in sent})),
                  "Distinct stocks", "layers", tone="purple" if sent else "muted")
    with cols[3]:
        on = bool(cfg.get("enabled"))
        stat_card("Alerting", "On" if on else "Off",
                  "Telegram delivery", "check_circle",
                  tone="bullish" if on else "muted")

    if not cfg.get("enabled"):
        st.info(
            "Alerting is off, so nothing is being delivered to Telegram. "
            "Matches below are still computed from the last scan. "
            "Turn it on in **Settings**."
        )

    spacer(12)
    with st.container(border=True):
        _render_feed(matches, sent)


def _safe_config() -> dict:
    try:
        return load_alert_config() or {}
    except Exception as exc:
        logger.warning("Could not load alert config: %s", exc)
        return {}



def _feed_rows(matches: list, sent: list[dict]) -> list[dict]:
    """One list holding both kinds of alert.

    They answer different questions — a live match is what WOULD fire from
    the last scan, a sent row is what the monitor DID deliver — so each row
    keeps an origin badge rather than being silently merged into one meaning.

    Distance is computed for sent rows too, whenever the symbol is in the
    current scan: without it, sorting by proximity would only order half the
    feed and the other half would clump arbitrarily at the end.
    """
    results = st.session_state.get("analysis_results", {}) or {}
    rows: list[dict] = []

    for m in matches:
        zone = getattr(m, "zone", {}) or {}
        rows.append({
            "symbol": str(getattr(m, "symbol", "")),
            "origin": "Near now",
            "category": zone.get("category", ""),
            "proximal": float(zone.get("proximal", 0) or 0),
            "score": float(zone.get("odd_score", 0) or 0),
            "price": float(getattr(m, "current_price", 0) or 0),
            "distance": float(getattr(m, "distance_pct", 0) or 0),
            "sent_at": "",
        })

    for s in sent:
        sym = s["symbol"]
        res = results.get(sym) or {}
        price = float(res.get("current_price") or 0)
        proximal = s["proximal"]
        distance = (
            abs(price - proximal) / price * 100 if price and proximal else None
        )
        rows.append({
            "symbol": sym,
            "origin": "Sent",
            "category": "",
            "proximal": proximal,
            "score": 0.0,
            "price": price,
            "distance": distance,
            "sent_at": s["sent_at"],
        })
    return rows


def _sort_rows(rows: list[dict], how: str) -> list[dict]:
    """Apply the chosen ordering.

    Rows missing the sort key go LAST rather than sorting as zero — a sent
    alert with no current price is unknown-distance, not zero-distance, and
    putting it first would be a lie.
    """
    if how == "Nearest to zone first":
        return sorted(rows, key=lambda r: (r["distance"] is None,
                                           r["distance"] or 0.0))
    if how == "Highest ODD score first":
        return sorted(rows, key=lambda r: -r["score"])
    if how == "Symbol A-Z":
        return sorted(rows, key=lambda r: r["symbol"])
    # Latest first: live matches are from the current scan, so they lead;
    # sent rows follow, newest delivery first.
    return sorted(rows, key=lambda r: (r["origin"] == "Sent",
                                       _neg_time(r["sent_at"])))


def _neg_time(stamp: str) -> str:
    """Sort key that puts the newest ISO timestamp first."""
    return "" if not stamp else "".join(chr(255 - ord(c)) for c in stamp[:19])


def _render_feed(matches: list, sent: list[dict]) -> None:
    """The merged, sortable, clickable alert feed."""
    rows = _feed_rows(matches, sent)

    head = st.columns([2, 1.4, 1])
    with head[0]:
        section_title(f"Alerts ({len(rows)})", hint="Click a row to open its chart")
    with head[1]:
        how = st.selectbox("Sort", _SORTS, key="al_sort",
                           label_visibility="collapsed")
    with head[2]:
        query = st.text_input("Search", key="al_search",
                              placeholder="Symbol…", label_visibility="collapsed")

    if query:
        rows = [r for r in rows if query.strip().upper() in r["symbol"].upper()]
    if not rows:
        st.caption(
            "Nothing yet. Live matches appear after a scan; sent alerts appear "
            "once the background monitor delivers one."
        )
        return

    rows = _sort_rows(rows, how)
    dash = "<span style='color:#A8A8A0;'>&mdash;</span>"
    body = ""
    for r in rows:
        tone = ("warning" if r["origin"] == "Near now"
                else "info")
        cat = (bias_pill(r["category"].title(),
                         "bullish" if r["category"] == "demand" else "bearish")
               if r["category"] else dash)
        dist = (f"{r['distance']:.1f}%" if r["distance"] is not None else dash)
        when = _when(r["sent_at"])
        # Streamlit's markdown sanitiser strips event handlers, so a row-level
        # onclick silently does nothing (it also strips class and id — see
        # Gotcha 20). The SYMBOL and the trailing Chart link are therefore both
        # real anchors, giving two obvious targets that survive sanitising.
        href = build_detail_url(r["symbol"], "NSE")
        body += (
            f"<tr>"
            f"<td style='padding:7px 8px;'>"
            f"<a href='{href}' target='_blank' style='color:#16233A;"
            f"font-weight:700;text-decoration:none;'>"
            f"{html.escape(r['symbol'])}</a></td>"
            f"<td style='padding:7px 8px;'>{bias_pill(r['origin'], tone)}</td>"
            f"<td style='padding:7px 8px;'>{cat}</td>"
            f"<td style='padding:7px 8px;'>{r['proximal']:,.2f}</td>"
            f"<td style='padding:7px 8px;'>"
            f"{('&#8377;%s' % format(r['price'], ',.2f')) if r['price'] else dash}</td>"
            f"<td style='padding:7px 8px;'>{dist}</td>"
            f"<td style='padding:7px 8px;'>"
            f"{('%.1f' % r['score']) if r['score'] else dash}</td>"
            f"<td style='padding:7px 8px;color:#71757C;'>{when}</td>"
            f"<td style='padding:7px 8px;'>"
            f"<a href='{href}' target='_blank' "
            f"style='color:#2F80ED;font-weight:600;text-decoration:none;'>"
            f"Chart &rarr;</a></td></tr>"
        )

    headers = ["Symbol", "Source", "Zone", "Level", "Price", "Distance",
               "ODD", "Sent", ""]
    head_html = "<tr>" + "".join(
        f"<th style='text-align:left;padding:7px 8px;font-size:0.68rem;"
        f"color:#8A8F98;font-weight:600;white-space:nowrap;"
        f"border-bottom:1px solid #E7E9ED;'>{h}</th>" for h in headers
    ) + "</tr>"
    st.markdown(
        f"<div style='overflow-x:auto;'><table style='width:100%;"
        f"border-collapse:collapse;font-size:0.79rem;'>{head_html}{body}"
        f"</table></div>",
        unsafe_allow_html=True,
    )


def _when(stamp: str) -> str:
    """Readable delivery time, or an em dash for live rows."""
    if not stamp:
        return "<span style='color:#A8A8A0;'>&mdash;</span>"
    try:
        from datetime import datetime
        return html.escape(
            datetime.fromisoformat(stamp).strftime("%d %b %H:%M")
        )
    except Exception:
        return html.escape(stamp[:16])
