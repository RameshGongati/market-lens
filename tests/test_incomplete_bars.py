"""Tests for incomplete-bar handling and NSE bhavcopy repair.

A bar missing its close is the dangerous case: nothing raises, and
``classify_candle`` reports it as a boring doji because every comparison
against NaN is False. These tests pin both halves of the defence — dropping
such a bar, and completing it from NSE's end-of-day file when possible.

Real case behind them: BAJAJHLDNG on 2026-07-31, where Yahoo returned
O=11000, H=11467, L=10951, V=89956 and no close, while NSE's bhavcopy
carried the session's close of 11343.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from analysis.zone_engine.candles import classify_candle
from data import nse_bhavcopy
from data.nse_bhavcopy import _parse, fetch_eod_ohlc
from data.sources.base import drop_incomplete_bars


def _frame(rows: list[tuple]) -> pd.DataFrame:
    """Build a dated OHLCV frame from (open, high, low, close, volume)."""
    return pd.DataFrame(
        {
            "Open": [r[0] for r in rows],
            "High": [r[1] for r in rows],
            "Low": [r[2] for r in rows],
            "Close": [r[3] for r in rows],
            "Volume": [r[4] for r in rows],
        },
        index=pd.to_datetime(
            [f"2026-07-{27 + i}" for i in range(len(rows))]
        ),
    )


# ---------------------------------------------------------------------------
# Why this matters at all
# ---------------------------------------------------------------------------

def test_nan_close_is_silently_classified_as_a_boring_doji():
    """A NaN close does not raise — it produces a plausible-looking candle.

    This is the reason incomplete bars must be dropped rather than tolerated:
    the corruption is invisible. Pinning it here so the rationale survives.
    """
    info = classify_candle(11000.0, 11467.0, 10951.0, float("nan"))
    assert info["is_boring"] is True
    assert info["is_exciting"] is False
    assert info["direction"] == "doji"


# ---------------------------------------------------------------------------
# drop_incomplete_bars
# ---------------------------------------------------------------------------

def test_drop_incomplete_bars_removes_a_missing_close():
    df = _frame([
        (100.0, 105.0, 99.0, 104.0, 1000),
        (104.0, 110.0, 103.0, np.nan, 2000),   # the broken bar
    ])
    out = drop_incomplete_bars(df)
    assert len(out) == 1
    assert out["Close"].iloc[-1] == 104.0


@pytest.mark.parametrize("column", ["Open", "High", "Low", "Close"])
def test_drop_incomplete_bars_removes_any_missing_ohlc_field(column: str):
    """Not just the close — any absent price makes the bar unusable."""
    df = _frame([
        (100.0, 105.0, 99.0, 104.0, 1000),
        (104.0, 110.0, 103.0, 109.0, 2000),
    ])
    df.iloc[1, df.columns.get_loc(column)] = np.nan
    assert len(drop_incomplete_bars(df)) == 1


def test_drop_incomplete_bars_keeps_zero_volume_sessions():
    """Zero volume is a real if illiquid session, not an incomplete bar."""
    df = _frame([
        (100.0, 105.0, 99.0, 104.0, 1000),
        (104.0, 104.0, 104.0, 104.0, 0),
    ])
    assert len(drop_incomplete_bars(df)) == 2


def test_drop_incomplete_bars_passes_through_empty_and_foreign_frames():
    """Never raise on input it does not recognise."""
    assert drop_incomplete_bars(pd.DataFrame()).empty
    other = pd.DataFrame({"Something": [1, 2]})
    assert len(drop_incomplete_bars(other)) == 2


# ---------------------------------------------------------------------------
# Bhavcopy parsing — both NSE schemas
# ---------------------------------------------------------------------------

_MODERN_CSV = (
    "TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric\n"
    "2026-07-31,BAJAJHLDNG,EQ,11000.00,11467.00,10951.00,11343.00\n"
    "2026-07-31,RELIANCE,EQ,1500.00,1520.00,1490.00,1510.00\n"
    "2026-07-31,BAJAJHLDNG,BE,11000.00,11467.00,10951.00,99999.00\n"
)

_LEGACY_CSV = (
    "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE\n"
    "BAJAJHLDNG,EQ,11000.00,11467.00,10951.00,11343.00\n"
)


def test_parse_reads_the_modern_nse_schema():
    """NSE's current file uses TckrSymb/OpnPric/ClsPric."""
    table = _parse(_MODERN_CSV)
    assert table["BAJAJHLDNG"]["close"] == pytest.approx(11343.00)
    assert table["BAJAJHLDNG"]["open"] == pytest.approx(11000.00)
    assert "RELIANCE" in table


def test_parse_reads_the_legacy_nse_schema():
    """Older archives use SYMBOL/OPEN/CLOSE — reading only one schema would
    silently find nothing rather than fail loudly."""
    table = _parse(_LEGACY_CSV)
    assert table["BAJAJHLDNG"]["close"] == pytest.approx(11343.00)


def test_parse_keeps_only_the_eq_series():
    """The file carries BE/BZ rows on the same ticker; taking those would
    overwrite the EQ price with an unrelated one."""
    table = _parse(_MODERN_CSV)
    assert table["BAJAJHLDNG"]["close"] == pytest.approx(11343.00)


def test_parse_survives_junk_input():
    assert _parse("") == {}
    assert _parse("not,a,bhavcopy\n1,2,3\n") == {}


# ---------------------------------------------------------------------------
# fetch_eod_ohlc — symbol normalisation and caching, without network
# ---------------------------------------------------------------------------

def test_fetch_eod_ohlc_strips_exchange_suffixes(monkeypatch):
    """Callers pass Yahoo-style tickers; the bhavcopy uses bare NSE symbols."""
    monkeypatch.setattr(nse_bhavcopy, "_cache", {date(2026, 7, 31): _parse(_MODERN_CSV)})
    for ticker in ("BAJAJHLDNG", "BAJAJHLDNG.NS", "bajajhldng.ns"):
        got = fetch_eod_ohlc(ticker, date(2026, 7, 31))
        assert got is not None, ticker
        assert got["close"] == pytest.approx(11343.00)


def test_fetch_eod_ohlc_returns_none_when_unavailable(monkeypatch):
    """A holiday or unpublished session caches as None and must not raise."""
    monkeypatch.setattr(nse_bhavcopy, "_cache", {date(2026, 8, 1): None})
    assert fetch_eod_ohlc("BAJAJHLDNG", date(2026, 8, 1)) is None


def test_fetch_eod_ohlc_returns_none_for_an_unlisted_symbol(monkeypatch):
    monkeypatch.setattr(nse_bhavcopy, "_cache", {date(2026, 7, 31): _parse(_MODERN_CSV)})
    assert fetch_eod_ohlc("NOSUCHSTOCK", date(2026, 7, 31)) is None


# ---------------------------------------------------------------------------
# End to end: the Yahoo repair path
# ---------------------------------------------------------------------------

def test_repair_last_bar_fills_a_missing_close(monkeypatch):
    """The BAJAJHLDNG case: Yahoo gives O/H/L/V, NSE supplies the close."""
    from data.sources import yahoo_finance

    monkeypatch.setattr(
        yahoo_finance, "fetch_eod_ohlc",
        lambda sym, dt: {"open": 11000.0, "high": 11467.0,
                         "low": 10951.0, "close": 11343.0},
    )
    df = _frame([
        (10875.0, 10990.0, 10875.0, 10970.0, 39575),
        (11000.0, 11467.0, 10951.0, np.nan, 89956),
    ])
    out = yahoo_finance._repair_last_bar(df, "BAJAJHLDNG.NS")

    assert out["Close"].iloc[-1] == pytest.approx(11343.0)
    # Values Yahoo did return are left alone.
    assert out["Open"].iloc[-1] == pytest.approx(11000.0)
    assert out["High"].iloc[-1] == pytest.approx(11467.0)
    # And the repaired bar now survives the guard.
    assert len(drop_incomplete_bars(out)) == 2


def test_repair_last_bar_leaves_a_complete_frame_untouched(monkeypatch):
    from data.sources import yahoo_finance

    def _boom(sym, dt):  # pragma: no cover — must never be reached
        raise AssertionError("bhavcopy should not be consulted for a complete bar")

    monkeypatch.setattr(yahoo_finance, "fetch_eod_ohlc", _boom)
    df = _frame([
        (100.0, 105.0, 99.0, 104.0, 1000),
        (104.0, 110.0, 103.0, 109.0, 2000),
    ])
    pd.testing.assert_frame_equal(yahoo_finance._repair_last_bar(df, "X.NS"), df)


def test_repair_last_bar_drops_the_bar_when_nse_has_nothing(monkeypatch):
    """No bhavcopy entry means the bar stays incomplete and is then removed,
    rather than being invented."""
    from data.sources import yahoo_finance

    monkeypatch.setattr(yahoo_finance, "fetch_eod_ohlc", lambda sym, dt: None)
    df = _frame([
        (100.0, 105.0, 99.0, 104.0, 1000),
        (104.0, 110.0, 103.0, np.nan, 2000),
    ])
    out = yahoo_finance._repair_last_bar(df, "X.NS")
    assert pd.isna(out["Close"].iloc[-1])
    assert len(drop_incomplete_bars(out)) == 1
