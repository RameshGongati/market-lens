"""F&O refresh writer: index-underlying labels never reach the stock list.

NSE's OI-spurts payload mixes index derivatives (NIFTY, NIFTYFPI...) among
stock symbols with no instrument-type field, so the writer filters by label.
The substring rule exists because the fixed name set drifted the day NSE
launched FPI 150 derivatives — NIFTYFPI landed in the F&O Stocks watchlist
as if it were an equity.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.nse_indices import _fetch_fno_stocks, _is_index_label  # noqa: E402


def test_known_and_future_index_labels_are_recognised():
    for label in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY",
                  "NIFTYNXT50", "NIFTYFPI", "NIFTYSOMETHINGNEW"]:
        assert _is_index_label(label), label


def test_equity_symbols_are_not_index_labels():
    # Includes the recently listed F&O stocks the same refresh added.
    for symbol in ["RELIANCE", "ATHERENERG", "MAHABANK", "SAGILITY",
                   "M&M", "BAJAJ-AUTO"]:
        assert not _is_index_label(symbol), symbol


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload):
        self._payload = payload

    def get(self, _url, timeout=None):
        return _FakeResponse(self._payload)


def test_fetch_fno_stocks_drops_index_rows_keeps_equities():
    payload = {"data": [
        {"symbol": "NIFTY"},
        {"symbol": "NIFTYFPI"},
        {"symbol": "BANKNIFTY"},
        {"symbol": "NIFTY 50"},       # space-separated variant
        {"symbol": "RELIANCE"},
        {"symbol": "ATHERENERG"},
        {"latestOI": 1},              # row without a symbol key
    ]}
    assert _fetch_fno_stocks(_FakeSession(payload)) == ["ATHERENERG", "RELIANCE"]
