"""Blank pages for nav items whose functionality is not yet specified.

Reports and Trade Journal are reachable from the sidebar so the navigation is
complete, but neither has been specified yet. They render a heading and an
explicit "not built" notice rather than inventing content — a page that looks
finished but shows nothing real is harder to spot than an obviously empty one.

Replace the body of each when the requirements are known; the routing and nav
entry already exist.
"""

from __future__ import annotations

import streamlit as st

from ui.components.panels import page_title, spacer


def _blank_page(title: str, subtitle: str, note: str, icon: str) -> None:
    left, right = st.columns([3, 1])
    with left:
        page_title(title, subtitle, icon=icon)
    with right:
        if st.button("Back to Dashboard", icon=":material/arrow_back:",
                     use_container_width=True, key=f"blank_back_{title}"):
            st.session_state.active_page = "dashboard"
            st.rerun()

    spacer(20)
    with st.container(border=True):
        st.markdown(
            "<div style='padding:44px 20px;text-align:center;'>"
            "<div style='font-size:2rem;line-height:1;color:#C9CDD3;'>"
            "&mdash;</div>"
            "<div style='font-size:1rem;font-weight:600;color:#4A5361;"
            "margin-top:10px;'>Not built yet</div>"
            f"<div style='font-size:0.85rem;color:#8A8F98;margin-top:4px;'>"
            f"{note}</div></div>",
            unsafe_allow_html=True,
        )


def render_reports_page() -> None:
    """Reports — placeholder pending requirements."""
    _blank_page(
        "Reports",
        "Scan and performance reporting",
        "Tell me what this page should show and I will build it.",
        icon="layers",
    )


def render_trade_journal_page() -> None:
    """Trade Journal — placeholder pending requirements."""
    _blank_page(
        "Trade Journal",
        "Logged trades and outcomes",
        "Tell me what this page should show and I will build it.",
        icon="calendar",
    )
