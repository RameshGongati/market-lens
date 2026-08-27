"""Dashboard mover panels must not build disabled quote universes."""

from __future__ import annotations

from ui.pages import market_overview


def test_disabled_mover_panels_build_no_universes(monkeypatch):
    monkeypatch.setattr(
        market_overview,
        "_selected_watchlist_universe",
        lambda: (_ for _ in ()).throw(AssertionError("watchlist universe was built")),
    )
    monkeypatch.setattr(
        market_overview,
        "_all_market_universe",
        lambda: (_ for _ in ()).throw(AssertionError("All NSE universe was built")),
    )

    assert market_overview._dashboard_mover_universes({
        "dashboard_show_watchlist_movers": False,
        "dashboard_show_all_nse_movers": False,
    }) == []


def test_each_mover_universe_is_built_only_when_enabled(monkeypatch):
    calls: list[str] = []

    def watchlist():
        calls.append("watchlist")
        return "F&O Stocks Movers", ("AAA",)

    def all_nse():
        calls.append("all_nse")
        return "All NSE Market Movers", ("AAA", "BBB")

    monkeypatch.setattr(market_overview, "_selected_watchlist_universe", watchlist)
    monkeypatch.setattr(market_overview, "_all_market_universe", all_nse)

    assert market_overview._dashboard_mover_universes({
        "dashboard_show_watchlist_movers": True,
        "dashboard_show_all_nse_movers": False,
    }) == [("F&O Stocks Movers", ("AAA",))]
    assert calls == ["watchlist"]

    calls.clear()
    assert market_overview._dashboard_mover_universes({
        "dashboard_show_watchlist_movers": False,
        "dashboard_show_all_nse_movers": True,
    }) == [("All NSE Market Movers", ("AAA", "BBB"))]
    assert calls == ["all_nse"]


def test_scan_breadth_counts_best_side_and_tradeable_stocks():
    results = {
        "LONG": {
            "demand_zones": [{"odd_score": 7, "category": "demand", "is_tradeable": True}],
            "supply_zones": [{"odd_score": 5, "category": "supply", "is_tradeable": False}],
        },
        "SHORT": {
            "demand_zones": [],
            "supply_zones": [{"odd_score": 6, "category": "supply", "is_tradeable": True}],
        },
        "EMPTY": {"demand_zones": [], "supply_zones": []},
    }

    assert market_overview._scan_breadth(results) == {
        "scanned": 3,
        "tradeable": 2,
        "bullish": 1,
        "bearish": 1,
    }
