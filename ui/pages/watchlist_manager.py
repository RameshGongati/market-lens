"""Watchlist management page."""

import streamlit as st

from ui.components.watchlist_panel import render_watchlist_panel


def render_watchlist_manager() -> None:
    """Render the watchlist management page."""
    st.title("📋 Watchlist Manager")
    render_watchlist_panel()
