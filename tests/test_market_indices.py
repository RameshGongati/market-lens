"""Option-enabled index snapshot configuration and ordering."""

from __future__ import annotations

from data import market_indices


def test_index_set_contains_current_nse_and_bse_option_underlyings():
    # FINNIFTY/MIDCPNIFTY are Yahoo's long-form symbols: ^CNXFIN quotes a
    # different/stale series (~28,506 vs FINNIFTY's real ~26,280).
    assert market_indices.INDEX_TICKERS == {
        "NIFTY 50": "^NSEI",
        "BANK NIFTY": "^NSEBANK",
        "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
        "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
        "NIFTY NEXT 50": "^NSMIDCP",
        "NIFTY INDIA FPI 150": "",
        "BSE SENSEX": "^BSESN",
        "BSE BANKEX": "BSE-BANK.BO",
    }


def test_fetch_all_indices_preserves_display_order(monkeypatch):
    def fake_snapshot(name: str, ticker: str):
        return {
            "name": name,
            "ticker": ticker,
            "last": 1.0,
            "change": 0.0,
            "change_pct": 0.0,
            "spark": [],
            "ema20": 1.0,
            "above_ema20": False,
            "ok": True,
        }

    monkeypatch.setattr(market_indices, "fetch_index_snapshot", fake_snapshot)
    monkeypatch.setattr(market_indices, "fetch_nse_index_quotes", lambda: {})
    snapshots = market_indices.fetch_all_indices()

    assert [row["name"] for row in snapshots] == list(market_indices.INDEX_TICKERS)


def test_nse_quote_supplies_current_value_when_history_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        market_indices,
        "fetch_index_snapshot",
        lambda name, ticker: market_indices._empty(name, ticker),
    )
    monkeypatch.setattr(
        market_indices,
        "fetch_nse_index_quotes",
        lambda: {
            "NIFTY MIDCAP SELECT": {
                "last": 15000.0,
                "variation": 75.0,
                "percentChange": 0.5,
            }
        },
    )

    rows = {row["name"]: row for row in market_indices.fetch_all_indices()}
    assert rows["MIDCPNIFTY"]["ok"] is True
    assert rows["MIDCPNIFTY"]["last"] == 15000.0
    assert rows["MIDCPNIFTY"]["change_pct"] == 0.5
    assert rows["MIDCPNIFTY"]["ema20"] is None


def test_single_history_bar_uses_live_quote_and_does_not_claim_ema(monkeypatch):
    import pandas as pd

    frame = pd.DataFrame({
        "Open": [102.0],
        "High": [106.0],
        "Low": [101.0],
        "Close": [105.0],
    })

    class FakeTicker:
        fast_info = {"lastPrice": 105.0, "previousClose": 100.0}

        def history(self, **_kwargs):
            return frame

    monkeypatch.setattr(market_indices.yf, "Ticker", lambda _ticker: FakeTicker())

    snapshot = market_indices.fetch_index_snapshot("BSE BANKEX", "BSE-BANK.BO")

    assert snapshot["last"] == 105.0
    assert snapshot["change"] == 5.0
    assert snapshot["change_pct"] == 5.0
    assert snapshot["ema20"] is None
    assert snapshot["above_ema20"] is None
