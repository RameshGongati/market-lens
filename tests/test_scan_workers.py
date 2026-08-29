"""Scan worker functions behind the parallel Run Analysis / Signals scans.

The workers run on threads, so they must be pure with respect to Streamlit:
every contextual input arrives as an argument, and the tests exercise them
exactly as the pool does — one call per stock, no session state.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.pages.dashboard import _scan_stock_worker  # noqa: E402
from ui.pages.gap_signals import _scan_symbol_worker  # noqa: E402


def _daily_frame(rows: int = 80) -> pd.DataFrame:
    """Flat-ish daily frame: enough bars to analyse, no zones, no signals."""
    idx = pd.date_range("2026-04-01", periods=rows, freq="D", tz="Asia/Kolkata")
    base = np.linspace(100.0, 102.0, rows)
    return pd.DataFrame({
        "Open": base,
        "High": base + 0.4,
        "Low": base - 0.4,
        "Close": base + 0.1,
        "Volume": 10_000,
    }, index=idx)


class _FakeManager:
    """DataSourceManager stand-in recording what the worker asked for."""

    def __init__(self, frame: pd.DataFrame, price: float = 123.45):
        self.frame = frame
        self.price = price
        self.history_calls: list[tuple[str, str, str]] = []

    def get_quote(self, symbol: str) -> dict:
        return {"symbol": symbol, "current_price": self.price, "change_pct": 1.5}

    def get_history(self, symbol: str, period: str = "1y",
                    interval: str = "1d") -> pd.DataFrame:
        self.history_calls.append((symbol, period, interval))
        return self.frame.copy()


def test_dashboard_worker_merges_quote_and_reports_fallback():
    manager = _FakeManager(_daily_frame())
    stock = SimpleNamespace(symbol="TEST", exchange="NSE", id=7)

    result, fell_back = _scan_stock_worker(
        stock, "TEST.NS", "Options Trading", "Demand/Supply Zones",
        False, manager,
    )

    assert fell_back is False
    assert result["current_price"] == 123.45
    assert result["change_pct"] == 1.5
    # change is derived from the live quote, not the last close
    assert result["change"] == round(123.45 * 1.5 / 100, 2)
    assert result["stock_id"] == 7
    assert result["exchange"] == "NSE"
    # The daily fetch went through the trading type's timeframe (1y daily
    # for Options Trading), exactly as the sequential scan did.
    assert ("TEST.NS", "1y", "1d") in manager.history_calls


def test_signal_worker_fetches_one_year_daily_only():
    manager = _FakeManager(_daily_frame())
    rows = _scan_symbol_worker(
        "TEST", "Test Ltd", "TEST.NS", manager.get_history,
        zone_result=None, earnings_date=None, market_ext=False,
        today=pd.Timestamp("2026-08-28").date(),
    )
    # A flat frame produces no gap signals, and the fetch window is 1y daily —
    # not the Daily chart interval's 5-year window the page used to pull.
    assert rows == []
    assert manager.history_calls == [("TEST.NS", "1y", "1d")]


def test_signal_worker_skips_short_frames_without_detecting():
    manager = _FakeManager(_daily_frame(rows=10))
    rows = _scan_symbol_worker(
        "TEST", "Test Ltd", "TEST.NS", manager.get_history,
        zone_result=None, earnings_date=None, market_ext=False,
        today=pd.Timestamp("2026-08-28").date(),
    )
    assert rows == []
