"""Tests that confirmed zones actually reach the chart.

The selection logic being right is not enough — the confirmed zones were
correct in ``result["confirmation_zones"]`` for several rounds while nothing
appeared on screen, because the overlay read a different flag than the
screener did. These tests exercise ``_add_zone_overlays`` itself so the
question "is it drawn" is answered by the drawing code, not by inspection.
"""

from __future__ import annotations

import types

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pytest

from ui.components import stock_detail


@pytest.fixture
def fake_session(monkeypatch):
    """Replace the module's ``st`` with a stand-in exposing session_state.

    ``_add_zone_overlays`` touches Streamlit only for ``st.session_state``, so
    a plain dict is a faithful substitute and keeps the test free of a script
    run context.
    """
    state: dict = {}
    monkeypatch.setattr(stock_detail, "st", types.SimpleNamespace(session_state=state))
    return state


def _df() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=20, freq="D")
    return pd.DataFrame(
        {"Open": 240.0, "High": 245.0, "Low": 235.0, "Close": 242.0, "Volume": 1000},
        index=idx,
    )


def _zone_dict(proximal: float, distal: float, category: str, score: float) -> dict:
    """A zone dict shaped like demand_supply._zone_dict output, including the
    legacy top/bottom aliases the overlay reads."""
    return {
        "zone_type": "DBR" if category == "demand" else "DBD",
        "category": category,
        "proximal": proximal,
        "distal": distal,
        "top": max(proximal, distal),
        "bottom": min(proximal, distal),
        "mid": (proximal + distal) / 2,
        "odd_score": score,
        "zone_strength": "Normal",
        "base_start_idx": 2,
        "num_base_candles": 2,
        "base_width_pct": 1.0,
        "times_tested": 1,
    }


def _fig() -> go.Figure:
    """The overlay draws with row/col, so it needs a subplot grid like the
    real chart's (price on row 1, volume on row 2)."""
    return make_subplots(rows=2, cols=1, shared_xaxes=True)


def _annotation_texts(fig: go.Figure) -> str:
    return " ".join(a.text or "" for a in fig.layout.annotations)


def test_confirmed_zones_are_drawn_when_the_mode_is_on(fake_session):
    """The ONGC case: two confirmed zones, neither in the display set."""
    fake_session["screener_confirmation"] = True
    result = {
        "demand_zones": [_zone_dict(226.66, 220.0, "demand", 7.0)],
        "supply_zones": [_zone_dict(284.00, 290.0, "supply", 5.0)],
        "confirmation_zones": [
            _zone_dict(237.84, 232.0, "demand", 3.5),
            _zone_dict(250.05, 256.0, "supply", 4.0),
        ],
    }
    fig = _fig()
    stock_detail._add_zone_overlays(fig, result, _df())

    text = _annotation_texts(fig)
    assert text.count("CONFIRMED") == 2, "both confirmed zones need a label"
    assert "Score 3.5" in text and "Score 4" in text
    # The ordinary zones must still be drawn alongside them.
    assert "Score 7" in text and "Score 5" in text


def test_nothing_extra_is_drawn_when_the_mode_is_off(fake_session):
    """Unticking must leave the chart byte-identical to the ordinary view."""
    fake_session["screener_confirmation"] = False
    result = {
        "demand_zones": [_zone_dict(226.66, 220.0, "demand", 7.0)],
        "supply_zones": [],
        "confirmation_zones": [_zone_dict(237.84, 232.0, "demand", 3.5)],
    }
    fig = _fig()
    stock_detail._add_zone_overlays(fig, result, _df())

    text = _annotation_texts(fig)
    assert "CONFIRMED" not in text
    assert "Score 3.5" not in text


def test_a_confirmed_zone_already_displayed_is_not_drawn_twice(fake_session):
    """A zone can qualify for both lists; it must not get two rectangles."""
    fake_session["screener_confirmation"] = True
    shared = _zone_dict(226.66, 220.0, "demand", 5.5)
    result = {
        "demand_zones": [shared],
        "supply_zones": [],
        "confirmation_zones": [dict(shared)],
    }
    fig = _fig()
    stock_detail._add_zone_overlays(fig, result, _df())

    assert len(fig.layout.annotations) == 1
    assert "CONFIRMED" not in _annotation_texts(fig)
