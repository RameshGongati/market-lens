"""Tests for rebuilding sessions a data source dropped.

Both working sources lose whole trading days, for different reasons. Yahoo
omits sessions for individual symbols and carries on, leaving a hole in the
middle — 70 of the 208 F&O stocks were missing 2026-07-31. Jugaad splits a
5-year range into 61 parallel chunks and concatenates whatever survives NSE
rate limiting, so the newest chunk is routinely lost.

The dangerous part is the SILENCE: nothing raises, so a chart quietly skips a
day and zone detection reads a base that never existed. These tests pin which
dates are expected (the NSE calendar, excluding a session still in progress)
and that only genuinely absent ones are rebuilt.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
import pytz

from data.sources.base import fill_missing_sessions
from utils.market_hours import last_completed_session, recent_trading_sessions

_IST = pytz.timezone("Asia/Kolkata")


def _ist(y: int, m: int, d: int, hh: int = 12, mm: int = 0) -> datetime:
    return _IST.localize(datetime(y, m, d, hh, mm))


def _frame(dates: list[str]) -> pd.DataFrame:
    idx = pd.to_datetime(dates).tz_localize("Asia/Kolkata")
    return pd.DataFrame(
        {"Open": 100.0, "High": 105.0, "Low": 99.0, "Close": 104.0,
         "Volume": 1000},
        index=idx,
    )


# ---------------------------------------------------------------------------
# last_completed_session
# ---------------------------------------------------------------------------

def test_after_close_todays_session_counts():
    """2026-07-31 is a Friday; 16:00 IST is past the 15:30 close."""
    assert last_completed_session(_ist(2026, 7, 31, 16, 0)).isoformat() == "2026-07-31"


def test_during_market_hours_todays_session_does_not_count():
    """Mid-session the day's candle is still forming.

    Treating it as complete would make every frame look stale and fire a
    refetch on every symbol, every render, all day.
    """
    assert last_completed_session(_ist(2026, 7, 31, 11, 0)).isoformat() == "2026-07-30"


def test_before_open_falls_back_to_the_previous_day():
    assert last_completed_session(_ist(2026, 7, 31, 8, 0)).isoformat() == "2026-07-30"


def test_weekend_walks_back_to_friday():
    """Saturday and Sunday both resolve to Friday 2026-07-31."""
    assert last_completed_session(_ist(2026, 8, 1, 12)).isoformat() == "2026-07-31"
    assert last_completed_session(_ist(2026, 8, 2, 12)).isoformat() == "2026-07-31"


def test_monday_morning_resolves_to_friday():
    """The BAJAJHLDNG case: Monday 10:07, so Friday is the last session."""
    assert last_completed_session(_ist(2026, 8, 3, 10, 7)).isoformat() == "2026-07-31"


# ---------------------------------------------------------------------------
# recent_trading_sessions
# ---------------------------------------------------------------------------

def test_recent_sessions_skip_the_weekend(monkeypatch):
    """Monday 2026-08-03 back five sessions lands on Mon 27 July.

    28 July–31 July are Tue–Fri; the 1st and 2nd are a weekend and must not
    appear, or every Monday would report two phantom missing sessions.
    """
    monkeypatch.setattr(
        "utils.market_hours.get_current_ist_time", lambda: _ist(2026, 8, 3, 10),
    )
    got = [d.isoformat() for d in recent_trading_sessions(5)]
    assert got == ["2026-07-27", "2026-07-28", "2026-07-29",
                   "2026-07-30", "2026-07-31"]


# ---------------------------------------------------------------------------
# fill_missing_sessions — the interior hole
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_bhavcopy(monkeypatch):
    """Stand in for NSE, recording which dates were asked for."""
    asked: list = []

    def _fetch(symbol, day):
        asked.append((symbol, day.isoformat()))
        if day.isoformat() == "2026-07-31":
            return {"open": 11000.0, "high": 11467.0,
                    "low": 10951.0, "close": 11343.0}
        return None

    monkeypatch.setattr("data.nse_bhavcopy.fetch_eod_ohlc", _fetch)
    monkeypatch.setattr(
        "utils.market_hours.get_current_ist_time", lambda: _ist(2026, 8, 3, 10),
    )
    return asked


def test_interior_hole_is_rebuilt(fake_bhavcopy):
    """The BAJAJHLDNG case: 07-31 absent between 07-30 and 08-03.

    The tail is current (08-03), so any check that looks only at the newest
    bar sees nothing wrong. The hole is behind it.
    """
    df = _frame(["2026-07-29", "2026-07-30", "2026-08-03"])
    out = fill_missing_sessions(df, "BAJAJHLDNG.NS")

    dates = [i.date().isoformat() for i in out.index]
    assert "2026-07-31" in dates
    assert dates == sorted(dates), "rebuilt bar must be inserted in order"
    row = out.loc[out.index[dates.index("2026-07-31")]]
    assert row["Close"] == 11343.0
    # Volume is unavailable from the bhavcopy parse and must be an explicit
    # zero rather than a guess.
    assert row["Volume"] == 0


def test_symbol_suffix_is_stripped_before_the_lookup(fake_bhavcopy):
    """NSE keys on the bare symbol; ".NS" would never match."""
    fill_missing_sessions(_frame(["2026-07-30", "2026-08-03"]), "BAJAJHLDNG.NS")
    assert all(sym == "BAJAJHLDNG" for sym, _ in fake_bhavcopy)


def test_complete_frame_asks_nse_nothing(fake_bhavcopy):
    """No holes means no lookups — the common case must stay free."""
    df = _frame([d.isoformat() for d in recent_trading_sessions(6)])
    out = fill_missing_sessions(df, "TEST")
    assert fake_bhavcopy == []
    assert len(out) == len(df)


def test_dates_before_the_frame_starts_are_not_holes(fake_bhavcopy):
    """A frame beginning after a session is a listing boundary, not a gap."""
    df = _frame(["2026-08-03"])
    fill_missing_sessions(df, "TEST")
    assert fake_bhavcopy == [], "must not try to fill before the first bar"


def test_a_session_nse_lacks_leaves_the_frame_alone(fake_bhavcopy):
    """The stub returns None for 07-30, so that hole cannot be filled."""
    df = _frame(["2026-07-29", "2026-07-31", "2026-08-03"])
    out = fill_missing_sessions(df, "TEST")
    dates = [i.date().isoformat() for i in out.index]
    assert "2026-07-30" not in dates
    assert len(out) == 3


def test_a_raising_bhavcopy_never_breaks_the_fetch(monkeypatch):
    def _boom(symbol, day):
        raise RuntimeError("NSE said no")

    monkeypatch.setattr("data.nse_bhavcopy.fetch_eod_ohlc", _boom)
    monkeypatch.setattr(
        "utils.market_hours.get_current_ist_time", lambda: _ist(2026, 8, 3, 10),
    )
    df = _frame(["2026-07-29", "2026-07-30", "2026-08-03"])
    assert len(fill_missing_sessions(df, "TEST")) == 3
