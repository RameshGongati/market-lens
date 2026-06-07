"""Alerts on/off toggle component."""

import streamlit as st


def render_alerts_toggle() -> None:
    """Render an alerts toggle and persist its state to session state.

    Updates ``st.session_state.alerts_on`` on every interaction.
    """
    current = st.session_state.get("alerts_on", False)
    label = "🔔 Alerts: ON" if current else "🔕 Alerts: OFF"
    color = "green" if current else "gray"

    st.markdown(
        f"<span style='color:{color}; font-weight:bold;'>{label}</span>",
        unsafe_allow_html=True,
    )
    toggled = st.toggle(
        "Enable alerts",
        value=current,
        key="alerts_toggle_widget",
        help="When enabled, Market Lens will save alerts for bullish/bearish signals.",
    )
    st.session_state.alerts_on = toggled
