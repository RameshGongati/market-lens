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

import streamlit as st

from alerts.zone_alert_checker import check_zone_alerts
from config.alert_settings import load_alert_config
from storage.database import (
    get_all_alerts,
    get_unread_alerts,
    mark_all_alerts_read,
)
from ui.components.panels import bias_pill, page_title, section_title, spacer, stat_card
from utils.helpers import format_timestamp
from utils.logger import get_logger

logger = get_logger(__name__)

_HISTORY_LIMIT = 50


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
    history = _safe_history()
    unread = unread_alert_count()

    spacer(12)
    cols = st.columns(4)
    with cols[0]:
        stat_card("Near zones now", str(len(matches)), "From the last scan",
                  "target", tone="warning" if matches else "muted")
    with cols[1]:
        stat_card("Unread", str(unread), "Not yet seen", "bell",
                  tone="bearish" if unread else "muted")
    with cols[2]:
        stat_card("Total logged", str(len(history)),
                  f"Last {_HISTORY_LIMIT} shown", "layers", tone="info")
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
    near_col, hist_col = st.columns(2)
    with near_col:
        with st.container(border=True):
            _render_near_zones(matches)
    with hist_col:
        with st.container(border=True):
            _render_history(history)


def _safe_config() -> dict:
    try:
        return load_alert_config() or {}
    except Exception as exc:
        logger.warning("Could not load alert config: %s", exc)
        return {}


def _safe_history() -> list[dict]:
    try:
        return list(get_all_alerts(limit=_HISTORY_LIMIT) or [])
    except Exception as exc:
        logger.warning("Could not load alert history: %s", exc)
        return []


def _render_near_zones(matches: list) -> None:
    """Stocks whose price is within the configured distance of a zone."""
    section_title("Near zones now", hint="Live, from the last scan")
    if not matches:
        st.caption(
            "No stocks are near a zone in the last scan — or no scan has run "
            "yet this session."
        )
        return
    for m in matches:
        zone = getattr(m, "zone", {}) or {}
        category = zone.get("category", "demand")
        tone = "bullish" if category == "demand" else "bearish"
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;padding:6px 0;"
            f"border-bottom:1px solid #F1F2F4;'>"
            f"<span><b>{html.escape(str(getattr(m, 'symbol', '')))}</b>"
            f"&nbsp;&nbsp;{bias_pill(category.title(), tone)}</span>"
            f"<span style='font-size:0.8rem;color:#4A4A44;'>"
            f"&#8377;{getattr(m, 'current_price', 0):,.2f} &nbsp;·&nbsp; "
            f"{getattr(m, 'distance_pct', 0):.1f}% away &nbsp;·&nbsp; "
            f"Score {zone.get('odd_score', 0):g}</span></div>",
            unsafe_allow_html=True,
        )


def _render_history(history: list[dict]) -> None:
    """Rows written by check_and_trigger_alerts — what Telegram was sent."""
    section_title("Alert history", hint=f"Last {_HISTORY_LIMIT}")
    if not history:
        st.caption(
            "Nothing logged yet. Alerts are recorded when a scan runs with "
            "alerting enabled, on stocks saved in one of your watchlists."
        )
        return
    for row in history:
        # Rows are sqlite3.Row converted to dict, NOT objects — read by key.
        message = str(row.get("message", ""))
        created = row.get("created_at", "")
        unread = not row.get("is_read", 0)
        dot = "#EB5757" if unread else "#C9CDD3"
        try:
            when = format_timestamp(created)
        except Exception:
            when = str(created)
        st.markdown(
            f"<div style='padding:6px 0;border-bottom:1px solid #F1F2F4;'>"
            f"<span style='color:{dot};'>&#9679;</span>&nbsp;"
            f"<span style='font-size:0.82rem;color:#26313F;'>"
            f"{html.escape(message)}</span>"
            f"<div style='font-size:0.7rem;color:#9AA0A8;margin-left:14px;'>"
            f"{html.escape(str(when))}</div></div>",
            unsafe_allow_html=True,
        )
