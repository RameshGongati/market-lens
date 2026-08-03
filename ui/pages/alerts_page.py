"""Alerts page — one feed of zone-proximity matches and Telegram deliveries.

Restores the alert surface that lived on the old dashboard above the results
grid. That banner was rendered by ``dashboard._render_filter_sort_bar``, which
lost its only caller when the results grid moved to its own page, so the
alerts silently stopped appearing.

Two kinds of alert share one deduplicated, sortable feed:

* **Near now** — live matches recomputed from the current scan against
  ``config/alert_config.json`` every render. What *would* fire, present
  whether or not alerting is switched on.
* **Sent** — what ``alert_monitor.py`` actually delivered to Telegram, read
  from that same config file. What *did* fire.

One row per symbol. See :func:`_feed_rows` for which copy survives.

**Where "Sent" comes from, and why not the database.** The ``alerts`` table is
permanently empty for index and F&O scans: ``create_alert`` needs a real
``stock_id``, foreign keys are enforced, and predefined-watchlist stocks carry
id 0. The monitor runs outside Streamlit and records deliveries as cooldown
keys in the config file, never touching the table. So the feed reads the
config — see :func:`telegram_sent_alerts`.

**Freshness.** ``load_alert_config()`` reads from disk on every call and the
monitor writes the moment it delivers, so nothing is cached between them: any
rerun shows new alerts. Re-running the scan is not required and would not
help — the sent history does not come from the scan. The Refresh button
exists only because an idle page does not re-render on its own.
"""

from __future__ import annotations

import html
import re

import streamlit as st

from alerts.zone_alert_checker import check_zone_alerts
from config.alert_settings import load_alert_config
from storage.database import get_unread_alerts
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
        # The newest delivery time doubles as a freshness indicator: if a
        # Telegram alert just arrived and this still shows an older time,
        # Refresh has not been pressed yet.
        _newest = max((s["sent_at"] for s in telegram_sent_alerts()),
                      default="")
        if _newest:
            st.caption(f"Most recent Telegram alert: {_fmt_stamp(_newest)}")
    with right:
        b1, b2 = st.columns(2)
        with b1:
            # A rerun is all that is needed: load_alert_config() reads the
            # file on every call and the monitor writes to it the moment it
            # delivers, so nothing is cached between the two. Re-running the
            # scan is NOT required — the sent history does not come from the
            # scan. This button exists because an idle page does not re-render
            # on its own, not because anything needs invalidating.
            #
            # It replaces a "Mark all read" button that acted on the alerts
            # DB table, which is permanently empty here (see
            # telegram_sent_alerts) — it could never do anything.
            if st.button("Refresh", icon=":material/refresh:",
                         use_container_width=True, key="al_refresh"):
                st.rerun()
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
    """One row per SYMBOL, holding both kinds of alert.

    A stock alerts repeatedly — once per zone, and again each day the cooldown
    resets — so the raw history carries 209 entries for 112 symbols. Listing
    every one buries the stocks you have not seen under repeats of the ones
    you have. The feed therefore collapses to one row per symbol.

    Which copy survives:

    * the **latest** ``sent_at`` wins among sent entries, so the row shows the
      most recent delivery and the zone level that was alerted then;
    * a **live match takes precedence for the market data** — category, price,
      distance and ODD score come from the current scan when the symbol is in
      it, because a level recorded days ago is not where the zone sits now;
    * the latest delivery time is kept either way, so a symbol that is both
      near a zone now and alerted earlier shows both facts in one row.

    Distance is computed for sent-only rows whenever the symbol is in the
    current scan: without it, sorting by proximity would order half the feed
    and clump the rest arbitrarily at the end.
    """
    results = st.session_state.get("analysis_results", {}) or {}
    merged: dict[str, dict] = {}

    for m in matches:
        zone = getattr(m, "zone", {}) or {}
        symbol = str(getattr(m, "symbol", ""))
        if not symbol:
            continue
        merged[symbol] = {
            "symbol": symbol,
            "origin": "Near now",
            "category": zone.get("category", ""),
            "proximal": float(zone.get("proximal", 0) or 0),
            "score": float(zone.get("odd_score", 0) or 0),
            "price": float(getattr(m, "current_price", 0) or 0),
            "distance": float(getattr(m, "distance_pct", 0) or 0),
            "sent_at": "",
        }

    for s in sent:
        symbol = s["symbol"]
        existing = merged.get(symbol)

        # A live match already holds the current market data for this symbol.
        # Take only the delivery time from the sent entry — the level recorded
        # when the alert fired is not where the zone sits now.
        if existing is not None and existing["origin"] != "Sent":
            if s["sent_at"] > existing["sent_at"]:
                existing["sent_at"] = s["sent_at"]
                existing["origin"] = "Near now + Sent"
            continue

        # Sent-only symbol. A newer delivery replaces the row WHOLESALE, level
        # included — keeping the old level beside a new timestamp would show a
        # zone that was not the one most recently alerted.
        if existing is not None and existing["sent_at"] >= s["sent_at"]:
            continue

        price = float((results.get(symbol) or {}).get("current_price") or 0)
        proximal = s["proximal"]
        merged[symbol] = {
            "symbol": symbol,
            "origin": "Sent",
            "category": "",
            "proximal": proximal,
            "score": 0.0,
            "price": price,
            "distance": (
                abs(price - proximal) / price * 100
                if price and proximal else None
            ),
            "sent_at": s["sent_at"],
        }

    return list(merged.values())


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


def _fmt_stamp(stamp: str) -> str:
    """An ISO timestamp as "03 Aug 06:42", or the raw head if unparseable."""
    try:
        from datetime import datetime
        return datetime.fromisoformat(stamp).strftime("%d %b %H:%M")
    except Exception:
        return stamp[:16]


def _when(stamp: str) -> str:
    """Readable delivery time, or an em dash for live rows."""
    if not stamp:
        return "<span style='color:#A8A8A0;'>&mdash;</span>"
    return html.escape(_fmt_stamp(stamp))
