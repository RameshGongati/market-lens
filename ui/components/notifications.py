"""In-app notification component using Streamlit toast messages."""

import streamlit as st

from storage.database import get_unread_alerts, mark_all_alerts_read


def render_notifications() -> None:
    """Display unread alert notifications as Streamlit toast messages.

    Reads unread alerts from the database, shows each as a toast, and
    marks all as read. Also displays a notification count badge in the
    sidebar when there are pending alerts.
    """
    try:
        unread = get_unread_alerts()
    except Exception:
        return

    if not unread:
        return

    count = len(unread)
    st.sidebar.markdown(
        f"<span style='background:#e74c3c;color:white;padding:2px 8px;"
        f"border-radius:10px;font-size:0.8rem;'>🔔 {count} new</span>",
        unsafe_allow_html=True,
    )

    # Show the most recent 3 as toasts to avoid notification flood
    for alert in unread[:3]:
        st.toast(alert["message"], icon="📢")

    if count > 3:
        st.toast(f"...and {count - 3} more alerts. Check the dashboard.", icon="ℹ️")

    try:
        mark_all_alerts_read()
    except Exception:
        pass
