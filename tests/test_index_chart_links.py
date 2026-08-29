"""Index-tile chart deep links: URL building, ticker passthrough, naming.

The dashboard's option-index tiles open the stock-detail chart in a new tab.
Index tickers are already fully qualified (^NSEI, BSE-BANK.BO), so the two
symbol-suffix helpers must pass them through untouched while still suffixing
plain equity symbols, and the links must pin src=Yahoo Finance because Jugaad
fetches equity history only.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.market_indices import INDEX_NAME_BY_TICKER, INDEX_TICKERS  # noqa: E402
from ui.components.stock_detail import _rangebreaks_for_interval, _source_symbol  # noqa: E402
from ui.pages.dashboard import _make_symbol  # noqa: E402
from ui.pages.market_overview import _index_chart_url  # noqa: E402


# ---------------------------------------------------------------------------
# Ticker -> display name map
# ---------------------------------------------------------------------------

def test_index_name_by_ticker_covers_only_chartable_indices():
    assert INDEX_NAME_BY_TICKER["^NSEI"] == "NIFTY 50"
    assert INDEX_NAME_BY_TICKER["^NSMIDCP"] == "NIFTY NEXT 50"
    assert INDEX_NAME_BY_TICKER["NIFTY_FIN_SERVICE.NS"] == "FINNIFTY"
    assert INDEX_NAME_BY_TICKER["NIFTY_MID_SELECT.NS"] == "MIDCPNIFTY"
    assert INDEX_NAME_BY_TICKER["BSE-BANK.BO"] == "BSE BANKEX"
    assert "" not in INDEX_NAME_BY_TICKER
    # The quote-only index (no Yahoo symbol exists) must not appear as a name.
    assert "NIFTY INDIA FPI 150" not in set(INDEX_NAME_BY_TICKER.values())
    assert len(INDEX_NAME_BY_TICKER) == sum(1 for t in INDEX_TICKERS.values() if t)


# ---------------------------------------------------------------------------
# Tile deep links
# ---------------------------------------------------------------------------

def test_index_tile_url_pins_yahoo_source_and_encodes_ticker():
    url = _index_chart_url({"name": "NIFTY 50", "ticker": "^NSEI"})
    assert url is not None
    assert url.startswith("?")
    assert "stock=%5ENSEI" in url          # ^ must be URL-encoded
    assert "src=Yahoo+Finance" in url      # pinned regardless of sidebar source
    assert "exchange=NSE" in url


def test_bse_index_tile_url_carries_bse_exchange():
    url = _index_chart_url({"name": "BSE SENSEX", "ticker": "^BSESN"})
    assert url is not None
    assert "stock=%5EBSESN" in url
    assert "exchange=BSE" in url


def test_thin_history_index_tiles_still_link():
    # FINNIFTY / MIDCPNIFTY / BANKEX: real Yahoo tickers whose history is
    # only starting to accumulate. The tile links anyway — the detail page
    # renders whatever price history exists plus the live quote, with a
    # warning that zone analysis needs more bars.
    assert _index_chart_url({"name": "FINNIFTY", "ticker": "NIFTY_FIN_SERVICE.NS",
                             "ema20": None}) is not None
    assert _index_chart_url({"name": "MIDCPNIFTY", "ticker": "NIFTY_MID_SELECT.NS",
                             "ema20": None}) is not None
    url = _index_chart_url({"name": "BSE BANKEX", "ticker": "BSE-BANK.BO",
                            "ema20": None})
    assert url is not None and "stock=BSE-BANK.BO" in url and "exchange=BSE" in url


def test_quote_only_index_tile_has_no_link():
    # No Yahoo symbol exists for FPI 150 and no other history source works
    # (jugaad's index-history endpoint no longer parses; NSE's historical API
    # is bot-blocked) — a link would open an empty page.
    assert _index_chart_url({"name": "NIFTY INDIA FPI 150", "ticker": ""}) is None
    assert _index_chart_url({"name": "NIFTY INDIA FPI 150"}) is None


# ---------------------------------------------------------------------------
# Symbol suffixing — both code paths, kept in step
# ---------------------------------------------------------------------------

def test_chart_fetch_symbol_passes_index_tickers_through():
    assert _source_symbol("^NSEI", "NSE", "Yahoo Finance") == "^NSEI"
    assert _source_symbol("^BSESN", "BSE", "Yahoo Finance") == "^BSESN"
    assert _source_symbol("BSE-BANK.BO", "BSE", "Yahoo Finance") == "BSE-BANK.BO"
    assert _source_symbol("NIFTY_FIN_SERVICE.NS", "NSE",
                          "Yahoo Finance") == "NIFTY_FIN_SERVICE.NS"


def test_chart_fetch_symbol_still_suffixes_equities():
    assert _source_symbol("RELIANCE", "NSE", "Yahoo Finance") == "RELIANCE.NS"
    assert _source_symbol("RELIANCE", "BSE", "Yahoo Finance") == "RELIANCE.BO"
    assert _source_symbol("RELIANCE", "NSE", "Jugaad Data") == "RELIANCE"


def test_analysis_fetch_symbol_matches_chart_fetch_rules():
    for symbol, exchange in [("^NSEI", "NSE"), ("BSE-BANK.BO", "BSE"),
                             ("RELIANCE", "NSE"), ("RELIANCE", "BSE")]:
        assert _make_symbol(symbol, exchange, "Yahoo Finance") == \
            _source_symbol(symbol, exchange, "Yahoo Finance")


# ---------------------------------------------------------------------------
# Live daily index display candle
# ---------------------------------------------------------------------------

def _daily_frame(days: list[str], closes: list[float]):
    import pandas as pd

    index = pd.DatetimeIndex(pd.to_datetime(days)).tz_localize("Asia/Kolkata")
    return pd.DataFrame({
        "Open": closes,
        "High": [value + 2 for value in closes],
        "Low": [value - 2 for value in closes],
        "Close": closes,
        "Volume": [0] * len(closes),
    }, index=index)


def test_today_index_display_bar_is_appended_without_mutating_analysis_frame():
    from datetime import date
    from ui.components.stock_detail import _append_today_display_bar

    completed = _daily_frame(["2026-08-27"], [24090.85])
    today = _daily_frame(["2026-08-28"], [24154.15])

    display, appended = _append_today_display_bar(completed, today, date(2026, 8, 28))

    assert appended is True
    assert len(display) == 2
    assert display.iloc[-1]["Close"] == 24154.15
    assert len(completed) == 1


def test_display_bar_is_not_appended_when_completed_frame_already_has_today():
    from datetime import date
    from ui.components.stock_detail import _append_today_display_bar

    completed = _daily_frame(["2026-08-27", "2026-08-28"], [24090.85, 24154.15])
    today = _daily_frame(["2026-08-28"], [24154.15])

    display, appended = _append_today_display_bar(completed, today, date(2026, 8, 28))

    assert appended is False
    assert display is completed


# ---------------------------------------------------------------------------
# Daily synthesis from hourly bars (newborn Yahoo index series)
# ---------------------------------------------------------------------------

def _hourly_frame(sessions: int = 20, bars_per_session: int = 6):
    """Tz-aware IST hourly frame: session d bar h opens at 100 + d*10 + h."""
    import pandas as pd

    idx, opens = [], []
    base = pd.Timestamp("2026-08-03 09:15", tz="Asia/Kolkata")
    for d in range(sessions):
        for h in range(bars_per_session):
            idx.append(base + pd.Timedelta(days=d, hours=h))
            opens.append(100.0 + d * 10 + h)
    return pd.DataFrame({
        "Open": opens,
        "High": [o + 5 for o in opens],
        "Low": [o - 5 for o in opens],
        "Close": [o + 1 for o in opens],
        "Volume": [0] * len(opens),   # indices trade no volume — must survive
        "Adj Close": opens,
    }, index=pd.DatetimeIndex(idx))


class _FakeTicker:
    """yf.Ticker stand-in serving distinct frames per requested interval."""

    def __init__(self, frames: dict):
        self._frames = frames

    def history(self, period=None, interval=None, **_kw):
        import pandas as pd

        frame = self._frames.get(interval)
        if frame is None:
            raise AssertionError(f"unexpected interval fetch: {interval}")
        return frame.copy() if hasattr(frame, "copy") else pd.DataFrame()


def test_synthesize_daily_resamples_each_session(monkeypatch):
    from data.sources import yahoo_finance as yfin

    hourly = _hourly_frame()
    monkeypatch.setattr(yfin.yf, "Ticker",
                        lambda _s: _FakeTicker({"60m": hourly}))
    daily = yfin.synthesize_daily_from_hourly("NIFTY_FIN_SERVICE.NS")

    assert daily is not None and len(daily) == 20
    first = daily.iloc[0]
    # Session 0 bars open 100..105: first open, max high, min low, last close.
    assert first["Open"] == 100.0
    assert first["High"] == 110.0
    assert first["Low"] == 95.0
    assert first["Close"] == 106.0
    assert first["Volume"] == 0
    assert str(daily.index.tz) == "Asia/Kolkata"


def test_synthesis_refuses_thin_hourly_history(monkeypatch):
    from data.sources import yahoo_finance as yfin

    thin = _hourly_frame(sessions=3)  # 18 bars < _SYNTH_MIN_HOURLY_ROWS
    monkeypatch.setattr(yfin.yf, "Ticker", lambda _s: _FakeTicker({"60m": thin}))
    assert yfin.synthesize_daily_from_hourly("NIFTY_FIN_SERVICE.NS") is None


def test_fetch_history_synthesizes_for_newborn_daily_series(monkeypatch):
    import pandas as pd
    from data.sources import yahoo_finance as yfin

    one_day = _hourly_frame(sessions=1, bars_per_session=1)
    monkeypatch.setattr(yfin.yf, "Ticker", lambda _s: _FakeTicker(
        {"1d": one_day, "60m": _hourly_frame()}))
    monkeypatch.setattr(yfin, "_repair_last_bar", lambda df, s: df)
    monkeypatch.setattr(yfin, "fill_missing_sessions", lambda df, s: df)

    df = yfin.YahooFinanceSource().fetch_history(
        "NIFTY_FIN_SERVICE.NS", period="5y", interval="1d")
    # One lone daily bar -> the 20-session synthesized series wins, and its
    # zero-volume rows survive (the volume>0 filter must not apply to it).
    assert len(df) == 20
    assert (df["Volume"] == 0).all()


def test_failed_daily_fetch_does_not_trigger_synthesis(monkeypatch):
    import pandas as pd
    from data.sources import yahoo_finance as yfin

    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume",
                                  "Adj Close"])
    # No "60m" entry: a synthesis attempt would raise inside _FakeTicker.
    # An empty daily response means the fetch failed or was rate-limited —
    # firing an extra hourly request per failed symbol slowed whole scans.
    monkeypatch.setattr(yfin.yf, "Ticker", lambda _s: _FakeTicker({"1d": empty}))
    monkeypatch.setattr(yfin, "_repair_last_bar", lambda df, s: df)
    monkeypatch.setattr(yfin, "fill_missing_sessions", lambda df, s: df)

    df = yfin.YahooFinanceSource().fetch_history("RELIANCE.NS",
                                                 period="1y", interval="1d")
    assert df.empty


def test_fetch_history_leaves_deep_daily_series_alone(monkeypatch):
    import pandas as pd
    from data.sources import yahoo_finance as yfin

    idx = pd.date_range("2026-01-01", periods=30, freq="D", tz="Asia/Kolkata")
    deep = pd.DataFrame({
        "Open": 100.0, "High": 105.0, "Low": 95.0, "Close": 101.0,
        "Volume": 1000, "Adj Close": 101.0,
    }, index=idx)
    # No "60m" entry: a synthesis attempt would raise inside _FakeTicker.
    monkeypatch.setattr(yfin.yf, "Ticker", lambda _s: _FakeTicker({"1d": deep}))
    monkeypatch.setattr(yfin, "_repair_last_bar", lambda df, s: df)
    monkeypatch.setattr(yfin, "fill_missing_sessions", lambda df, s: df)

    df = yfin.YahooFinanceSource().fetch_history("RELIANCE.NS",
                                                 period="1y", interval="1d")
    assert len(df) == 30


def test_fetch_history_keeps_zero_volume_intraday_bars_for_supported_index(monkeypatch):
    from data.sources import yahoo_finance as yfin

    intraday = _hourly_frame()
    monkeypatch.setattr(yfin.yf, "Ticker", lambda _s: _FakeTicker({"60m": intraday}))

    df = yfin.YahooFinanceSource().fetch_history("^NSEI", period="1y", interval="60m")

    assert len(df) == len(intraday)
    assert (df["Volume"] == 0).all()


def test_fetch_history_still_filters_zero_volume_intraday_equity_bars(monkeypatch):
    import pandas as pd
    from data.sources import yahoo_finance as yfin

    intraday = _hourly_frame()
    monkeypatch.setattr(yfin.yf, "Ticker", lambda _s: _FakeTicker({"60m": intraday}))

    df = yfin.YahooFinanceSource().fetch_history("RELIANCE.NS", period="1y", interval="60m")

    assert df.empty


def test_intraday_charts_hide_closed_market_hours():
    breaks = _rangebreaks_for_interval("15m")

    assert dict(bounds=["sat", "mon"]) in breaks
    assert dict(bounds=[15.5, 9.0], pattern="hour") in breaks


def test_hourly_charts_keep_room_between_sessions():
    assert dict(bounds=[15.5, 8.25], pattern="hour") in _rangebreaks_for_interval("60m")
    assert dict(bounds=[15.5, 8.0], pattern="hour") in _rangebreaks_for_interval("75m")


def test_daily_charts_do_not_hide_weekday_hours():
    assert _rangebreaks_for_interval("Daily") == [dict(bounds=["sat", "mon"])]
