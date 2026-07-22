"""Alerts on/off toggle component."""

import streamlit as st


def render_alerts_toggle() -> None:
    """Render an alerts toggle and persist its state to session state.

    Updates ``st.session_state.alerts_on`` on every interaction.
    """
    current = st.session_state.get("alerts_on", False)
    # Render the toggle first so its returned value drives the label —
    # reading session state before the widget renders shows stale state.
    toggled = st.toggle(
        "Enable alerts",
        value=current,
        key="alerts_toggle_widget",
        help="When enabled, Market Lens will save alerts for bullish/bearish signals.",
    )
    st.session_state.alerts_on = toggled

    label = "🔔 Alerts: ON" if toggled else "🔕 Alerts: OFF"
    color = "green" if toggled else "gray"
    st.markdown(
        f"<span style='color:{color}; font-weight:bold;'>{label}</span>",
        unsafe_allow_html=True,
    )
