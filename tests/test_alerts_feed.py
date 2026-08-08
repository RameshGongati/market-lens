"""Tests for the Alerts page feed: history parsing, dedupe and ordering.

The regression these exist for: "Latest first" used to sort on ORIGIN before
time, so a Telegram alert delivered seconds ago could not reach the top of the
feed while any live scan match existed. Every test below that names an
ordering is guarding that.
"""

from __future__ import annotations

import pytest

from ui.pages import alerts_page


@pytest.fixture
def no_session(monkeypatch):
    """Replace st.session_state with a plain dict — no Streamlit run here."""
    state: dict = {}
    monkeypatch.setattr(alerts_page.st, "session_state", state)
    return state


def _live(symbol: str, distance: float = 1.0) -> dict:
    return {"symbol": symbol, "origin": "Near now", "sent_at": "",
            "distance": distance, "score": 5.5, "proximal": 100.0, "price": 101.0,
            "category": "demand"}


def _sent(symbol: str, sent_at: str, distance: float | None = 2.0) -> dict:
    return {"symbol": symbol, "origin": "Sent", "sent_at": sent_at,
            "distance": distance, "score": 0.0, "proximal": 100.0, "price": 0.0,
            "category": ""}


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

def test_newest_delivery_leads_even_when_live_matches_exist():
    """The reported bug: new alerts hidden below every live match."""
    rows = [_live("INFY"), _live("SBIN"), _live("ITC"),
            _sent("PETRONET", "2026-08-04T07:40:20"),
            _sent("ADANIPORTS", "2026-08-04T07:34:18"),
            _sent("OLDSTOCK", "2026-07-01T09:00:00")]

    order = [r["symbol"] for r in alerts_page._sort_rows(rows, "Latest first")]

    assert order[0] == "PETRONET"
    assert order[1] == "ADANIPORTS"
    assert order[2] == "OLDSTOCK"
    # Live matches have no delivery event, so they follow — but they are still
    # all present. Nothing is dropped by the reordering.
    assert set(order[3:]) == {"INFY", "SBIN", "ITC"}


def test_a_single_live_match_cannot_displace_a_new_delivery():
    """One live match was enough to bury every delivery under the old key."""
    rows = [_live("INFY"), _sent("PETRONET", "2026-08-04T07:40:20")]
    order = [r["symbol"] for r in alerts_page._sort_rows(rows, "Latest first")]
    assert order == ["PETRONET", "INFY"]


def test_mixed_origin_row_sorts_by_its_delivery_time():
    """"Near now + Sent" carries a timestamp, so it interleaves by time."""
    rows = [_sent("A", "2026-08-04T09:00:00"),
            {**_live("B"), "origin": "Near now + Sent",
             "sent_at": "2026-08-04T10:00:00"},
            _sent("C", "2026-08-04T08:00:00")]
    order = [r["symbol"] for r in alerts_page._sort_rows(rows, "Latest first")]
    assert order == ["B", "A", "C"]


def test_live_rows_are_ordered_by_distance_not_left_arbitrary():
    rows = [_live("FAR", 4.0), _live("NEAR", 0.5), _live("MID", 2.0)]
    order = [r["symbol"] for r in alerts_page._sort_rows(rows, "Latest first")]
    assert order == ["NEAR", "MID", "FAR"]


def test_live_row_with_unknown_distance_goes_last():
    rows = [{**_live("UNKNOWN"), "distance": None}, _live("KNOWN", 3.0)]
    order = [r["symbol"] for r in alerts_page._sort_rows(rows, "Latest first")]
    assert order == ["KNOWN", "UNKNOWN"]


def test_other_sorts_are_unchanged():
    rows = [_sent("B", "2026-08-04T07:00:00", distance=5.0),
            _live("A", 1.0), _live("C", 3.0)]

    assert [r["symbol"] for r in
            alerts_page._sort_rows(rows, "Symbol A-Z")] == ["A", "B", "C"]
    assert [r["symbol"] for r in
            alerts_page._sort_rows(rows, "Nearest to zone first")][0] == "A"
    assert [r["symbol"] for r in
            alerts_page._sort_rows(rows, "Highest ODD score first")][0] in {"A", "C"}


def test_unknown_distance_sorts_last_under_nearest_first():
    """A missing price is unknown distance, not zero distance."""
    rows = [{**_sent("NODATA", "2026-08-04T07:00:00", distance=None)},
            _live("CLOSE", 0.2)]
    order = [r["symbol"] for r in
             alerts_page._sort_rows(rows, "Nearest to zone first")]
    assert order == ["CLOSE", "NODATA"]


# ---------------------------------------------------------------------------
# History parsing
# ---------------------------------------------------------------------------

def _with_history(monkeypatch, history: dict) -> None:
    monkeypatch.setattr(alerts_page, "load_alert_config",
                        lambda: {"alert_history": history})


def test_history_key_with_trailing_date_is_parsed(monkeypatch):
    _with_history(monkeypatch, {
        "ADANIPORTS_1752.0999755859375_2026-08-04": "2026-08-04T07:34:18",
    })
    (row,) = alerts_page.telegram_sent_alerts()
    assert row["symbol"] == "ADANIPORTS"
    assert row["proximal"] == pytest.approx(1752.0999755859375)
    assert row["origin"] == "Sent"


def test_history_key_without_a_date_is_parsed(monkeypatch):
    """The "once per zone ever" cooldown writes no date component."""
    _with_history(monkeypatch, {"TCS_2415.1": "2026-08-04T07:23:28"})
    (row,) = alerts_page.telegram_sent_alerts()
    assert row["symbol"] == "TCS"
    assert row["proximal"] == pytest.approx(2415.1)


def test_symbol_containing_an_underscore_survives(monkeypatch):
    """The key is parsed from the RIGHT so the symbol can hold separators."""
    _with_history(monkeypatch, {"M_M_1234.5_2026-08-04": "2026-08-04T07:00:00"})
    (row,) = alerts_page.telegram_sent_alerts()
    assert row["symbol"] == "M_M"
    assert row["proximal"] == pytest.approx(1234.5)


def test_malformed_keys_are_skipped_not_raised(monkeypatch):
    _with_history(monkeypatch, {
        "GARBAGE": "2026-08-04T07:00:00",              # no level
        "_500.0_2026-08-04": "2026-08-04T07:00:00",    # no symbol
        "GOOD_100.0_2026-08-04": "2026-08-04T07:00:00",
    })
    rows = alerts_page.telegram_sent_alerts()
    assert [r["symbol"] for r in rows] == ["GOOD"]


def test_unreadable_config_yields_no_rows_rather_than_raising(monkeypatch):
    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr(alerts_page, "load_alert_config", boom)
    assert alerts_page.telegram_sent_alerts() == []


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------

def test_repeat_deliveries_collapse_to_the_newest(no_session):
    """One row per symbol, holding the most recent delivery and ITS level."""
    sent = [
        {"symbol": "TCS", "proximal": 2400.0, "sent_at": "2026-08-01T09:00:00",
         "origin": "Sent"},
        {"symbol": "TCS", "proximal": 2415.1, "sent_at": "2026-08-04T07:23:28",
         "origin": "Sent"},
    ]
    (row,) = alerts_page._feed_rows([], sent)
    assert row["sent_at"] == "2026-08-04T07:23:28"
    # The level moves with the timestamp — an old level beside a new time
    # would name a zone that was not the one most recently alerted.
    assert row["proximal"] == pytest.approx(2415.1)


def test_out_of_order_history_still_keeps_the_newest(no_session):
    """History is a dict; insertion order must not decide which copy wins."""
    sent = [
        {"symbol": "TCS", "proximal": 2415.1, "sent_at": "2026-08-04T07:23:28",
         "origin": "Sent"},
        {"symbol": "TCS", "proximal": 2400.0, "sent_at": "2026-08-01T09:00:00",
         "origin": "Sent"},
    ]
    (row,) = alerts_page._feed_rows([], sent)
    assert row["sent_at"] == "2026-08-04T07:23:28"
    assert row["proximal"] == pytest.approx(2415.1)
