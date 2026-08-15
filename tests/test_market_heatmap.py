"""Tests for market heatmap data shaping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from data import market_heatmap as mh


def test_symbols_for_group_combines_watchlists(monkeypatch) -> None:
    monkeypatch.setattr(
        mh,
        "_watchlists_by_name",
        lambda: {
            "Nifty 50": ["AAA", "BBB"],
            "Nifty Next 50": ["BBB", "CCC"],
        },
    )

    assert mh.symbols_for_group("nifty100") == ["AAA", "BBB", "CCC"]


def test_symbols_for_watchlist_reads_predefined_watchlist(monkeypatch) -> None:
    monkeypatch.setattr(
        mh,
        "_watchlists_by_name",
        lambda: {
            "F&O Stocks": ["AAA", "BBB", "AAA.NS"],
        },
    )

    assert mh.symbols_for_watchlist("F&O Stocks") == ["AAA", "BBB"]


def test_group_tiles_use_basket_fallback_and_setup_counts(monkeypatch) -> None:
    monkeypatch.setattr(
        mh,
        "_watchlists_by_name",
        lambda: {
            "Nifty 50": ["AAA", "BBB"],
            "Nifty Bank": ["BANKA", "BANKB"],
        },
    )

    calls: list[list[str]] = []

    def fake_fetch(symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        calls.append(list(symbols))
        out: dict[str, dict[str, Any]] = {}
        for symbol in symbols:
            is_index = symbol.startswith("^")
            out[symbol] = {
                "symbol": symbol,
                "price": 100.0,
                "change": 1.0,
                "change_pct": 1.25,
                "volume": 1000,
                "ok": not is_index,
            }
        return out

    rows = mh.group_tiles(
        {"BANKA": {"demand_zones": [{"odd_score": 7.0}]}},
        quote_fetcher=fake_fetch,
        fallback_limit=2,
    )

    bank = next(row for row in rows if row["id"] == "banks")
    assert bank["ok"] is True
    assert bank["source"] == "basket"
    assert bank["setup_count"] == 1
    assert bank["volume"] == 2000
    assert any("BANKA" in call for call in calls)


def test_stock_tiles_overlay_quote_and_setup(monkeypatch) -> None:
    monkeypatch.setattr(
        mh,
        "_watchlists_by_name",
        lambda: {"Nifty Bank": ["BANKA", "BANKB"]},
    )

    def fake_fetch(symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        return {
            symbol: {
                "symbol": symbol,
                "price": 100.0,
                "change": 2.0,
                "change_pct": 2.0,
                "volume": 5000,
                "ok": True,
            }
            for symbol in symbols
        }

    rows = mh.stock_tiles(
        "banks",
        results={"BANKB": {"supply_zones": [{"odd_score": 6.0}]}},
        quote_fetcher=fake_fetch,
    )

    assert [row["symbol"] for row in rows] == ["BANKA", "BANKB"]
    assert rows[0]["change_pct"] == 2.0
    assert rows[0]["volume"] == 5000
    assert rows[1]["has_setup"] is True
    assert rows[1]["setup"] == "Supply setup"


def test_stock_tiles_for_symbols_supports_arbitrary_universe() -> None:
    def fake_fetch(symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
        return {
            symbol: {
                "symbol": symbol,
                "price": 100.0,
                "change": 2.0,
                "change_pct": 2.0,
                "volume": 3000,
                "ok": True,
            }
            for symbol in symbols
        }

    rows = mh.stock_tiles_for_symbols(
        ["AAA", "BBB", "AAA.NS"],
        results={"AAA": {"demand_zones": [{"odd_score": 7.0}]}},
        quote_fetcher=fake_fetch,
    )

    assert [row["symbol"] for row in rows] == ["AAA", "BBB"]
    assert rows[0]["has_setup"] is True
    assert rows[0]["volume"] == 3000
    assert rows[1]["setup"] == "No scan setup"


def test_filter_and_sort_tiles() -> None:
    rows = [
        {"label": "A", "ok": True, "change_pct": -1.0, "setup_count": 0},
        {"label": "B", "ok": True, "change_pct": 2.0, "setup_count": 1},
        {"label": "C", "ok": False, "change_pct": 0.0, "setup_count": 0},
    ]

    assert [r["label"] for r in mh.filter_tiles(rows, "Gainers")] == ["B"]
    assert [r["label"] for r in mh.filter_tiles(rows, "Losers")] == ["A"]
    assert [r["label"] for r in mh.filter_tiles(rows, "With Setups")] == ["B"]
    assert [r["label"] for r in mh.sort_tiles(rows, "% Change: High to Low")] == [
        "B",
        "A",
        "C",
    ]
