"""Tests for market heatmap data shaping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pandas as pd

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


def test_unavailable_yahoo_sector_indices_are_not_requested() -> None:
    assert "oilgas" not in mh._GROUP_TICKERS
    assert "healthcare" not in mh._GROUP_TICKERS


def test_fno_watchlist_excludes_non_equity_fpi_index_label() -> None:
    fno = next(item for item in mh.predefined_watchlists()
               if item.get("name") == "F&O Stocks")
    assert "NIFTYFPI" not in fno["symbols"]


def test_stale_yahoo_daily_symbol_detects_missing_prior_session() -> None:
    dates = {
        "DIXON": (pd.Timestamp("2026-08-20"), pd.Timestamp("2026-08-19")),
        "PAGEIND": (pd.Timestamp("2026-08-20"), pd.Timestamp("2026-08-18")),
        "RELIANCE": (pd.Timestamp("2026-08-20"), pd.Timestamp("2026-08-19")),
    }

    assert mh._stale_yahoo_daily_symbols(dates) == {"PAGEIND"}


def test_stale_yahoo_daily_symbol_allows_market_wide_holiday_gap() -> None:
    dates = {
        "DIXON": (pd.Timestamp("2026-08-20"), pd.Timestamp("2026-08-18")),
        "PAGEIND": (pd.Timestamp("2026-08-20"), pd.Timestamp("2026-08-18")),
        "RELIANCE": (pd.Timestamp("2026-08-20"), pd.Timestamp("2026-08-18")),
    }

    assert mh._stale_yahoo_daily_symbols(dates) == set()


def test_mover_yahoo_daily_symbols_selects_top_gainers_and_losers() -> None:
    quotes = {
        "AAA": {"ok": True, "change_pct": 4.0},
        "BBB": {"ok": True, "change_pct": 1.0},
        "CCC": {"ok": True, "change_pct": -3.0},
        "DDD": {"ok": True, "change_pct": -0.5},
        "EEE": {"ok": True, "change_pct": 0.0},
        "FFF": {"ok": False, "change_pct": 9.0},
    }

    assert mh._mover_yahoo_daily_symbols(quotes, limit_per_side=1) == {"AAA", "CCC"}


def test_repair_yahoo_quotes_prefers_nse_live_quote(monkeypatch) -> None:
    quotes = {
        "PAYTM": {
            "symbol": "PAYTM",
            "price": 1000.0,
            "change": 30.0,
            "change_pct": 3.0,
            "volume": 100,
            "ok": True,
            "error": "",
        }
    }

    monkeypatch.setattr(mh, "_create_nse_quote_session", lambda: object())
    monkeypatch.setattr(
        mh,
        "_fetch_nse_equity_quote",
        lambda symbol, session: mh._quote_from_price_prev(
            symbol,
            940.0,
            1000.0,
            volume=200,
            source="nse",
        ),
    )

    mh._repair_yahoo_quotes(
        quotes,
        {"PAYTM": "PAYTM.NS"},
        {"PAYTM": "mover validation"},
        yf=object(),
    )

    assert quotes["PAYTM"]["price"] == 940.0
    assert quotes["PAYTM"]["change_pct"] == -6.0
    assert quotes["PAYTM"]["source"] == "nse"


def test_intraday_batch_completes_missing_closes_in_one_call() -> None:
    calls: list[tuple[str, str]] = []

    class _FakeYF:
        @staticmethod
        def download(tickers: str, period: str, interval: str, **_kw) -> pd.DataFrame:
            calls.append((interval, tickers))
            idx = pd.date_range("2026-09-01 09:15", periods=3, freq="5min")
            if interval == "5m":
                cols = pd.MultiIndex.from_product([["AAA.NS"], ["Close", "Volume"]])
                return pd.DataFrame(
                    [[100.0, 10], [101.0, 10], [102.0, 10]], index=idx, columns=cols)
            # The 15m retry serves only what the 5m batch missed.
            cols = pd.MultiIndex.from_product([["BBB.NS"], ["Close", "Volume"]])
            return pd.DataFrame(
                [[50.0, 5], [49.0, 5], [48.5, 5]], index=idx, columns=cols)

    got = mh._fetch_yahoo_intraday_quotes_batch(
        _FakeYF,
        {"AAA": (100.0, 111), "BBB": (50.0, 222)},
        {"AAA": "AAA.NS", "BBB": "BBB.NS"},
    )

    assert got["AAA"]["price"] == 102.0 and got["AAA"]["change_pct"] == 2.0
    assert got["AAA"]["source"] == "yahoo_intraday"
    assert got["AAA"]["volume"] == 30            # intraday volume summed
    assert got["BBB"]["price"] == 48.5 and got["BBB"]["change_pct"] == -3.0
    # One batched request per interval — and the retry asked ONLY for the
    # symbol the first batch missed, never re-fetching resolved ones.
    assert [interval for interval, _ in calls] == ["5m", "15m"]
    assert calls[1][1] == "BBB.NS"


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
