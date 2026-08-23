"""Gap-Up Continuation Telegram alerts: session targeting, catch-up, the
three-source bar acquisition, EOD pass, intraday touches, wording, isolation.

All offline: fake managers, frozen clocks, monkeypatched bhavcopy.
"""
from __future__ import annotations

import datetime as dt
import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alerts import gap_alerts  # noqa: E402
from alerts.gap_alerts import (  # noqa: E402
    acquire_daily_frame, is_late, pending_sessions, run_eod_pass,
    run_touch_pass, scan_guard_key, signal_key, synthesize_daily_bar,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _ist(y, m, d, hh, mm=0):
    return dt.datetime(y, m, d, hh, mm, tzinfo=IST)


def _daily_frame(end: str, rows: list[tuple]) -> pd.DataFrame:
    idx = pd.bdate_range(end=end, periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 10_000
    return df


def _flat(n, price=100.0):
    return [(price, price + 1.0, price - 1.0, price + 0.2) for _ in range(n)]


class FakeManager:
    """get_history(symbol, period, interval) -> daily or intraday frame."""

    def __init__(self, daily: pd.DataFrame | None, intraday: pd.DataFrame | None = None):
        self.daily, self.intraday = daily, intraday

    def get_history(self, symbol, period, interval):
        if interval in ("1d", "1wk", "1mo"):
            return self.daily
        return self.intraday


# --------------------------------------------------------------------------- #
# Session targeting and lateness
# --------------------------------------------------------------------------- #

def test_pending_sessions_gates_today_until_16_ist():
    # Tue 2026-08-25: at 15:45 the session is complete (15:30) but the daily
    # bar is still treated as forming until 16:00 — today must NOT pend yet.
    hist: dict = {}
    at_1545 = pending_sessions(hist, _ist(2026, 8, 25, 15, 45))
    assert dt.date(2026, 8, 25) not in at_1545
    at_1605 = pending_sessions(hist, _ist(2026, 8, 25, 16, 5))
    assert dt.date(2026, 8, 25) in at_1605


def test_pending_sessions_catchup_and_guards():
    now = _ist(2026, 8, 25, 17, 0)
    sessions = pending_sessions({}, now)
    assert sessions == [dt.date(2026, 8, 21), dt.date(2026, 8, 24), dt.date(2026, 8, 25)]
    # scanned sessions drop out
    hist = {scan_guard_key(dt.date(2026, 8, 24)): "done",
            scan_guard_key(dt.date(2026, 8, 25)): "done"}
    assert pending_sessions(hist, now) == [dt.date(2026, 8, 21)]


def test_is_late_flips_at_next_session_open():
    session = dt.date(2026, 8, 21)                       # Friday
    assert not is_late(session, _ist(2026, 8, 21, 18, 0))   # same evening
    assert not is_late(session, _ist(2026, 8, 24, 9, 0))    # Monday pre-open
    assert is_late(session, _ist(2026, 8, 24, 10, 0))       # entry has begun


# --------------------------------------------------------------------------- #
# Three-source bar acquisition
# --------------------------------------------------------------------------- #

def test_synthesize_daily_bar_from_intraday():
    idx = pd.date_range("2026-08-25 09:15", periods=4, freq="15min", tz="Asia/Kolkata")
    intraday = pd.DataFrame({"Open": [100, 101, 102, 103], "High": [101, 104, 103, 104],
                             "Low": [99, 100, 101, 102], "Close": [101, 102, 103, 103.5],
                             "Volume": [10, 20, 30, 40]}, index=idx)
    bar = synthesize_daily_bar(intraday, dt.date(2026, 8, 25))
    assert bar == {"Open": 100.0, "High": 104.0, "Low": 99.0, "Close": 103.5, "Volume": 100.0}
    assert synthesize_daily_bar(intraday, dt.date(2026, 8, 24)) is None


def test_acquire_daily_frame_fallback_chain(monkeypatch):
    session = dt.date(2026, 8, 25)
    full_daily = _daily_frame("2026-08-25", _flat(40))
    stale_daily = _daily_frame("2026-08-24", _flat(40))
    idx = pd.date_range("2026-08-25 09:15", periods=3, freq="15min", tz="Asia/Kolkata")
    intraday = pd.DataFrame({"Open": [104, 104.5, 104.4], "High": [105, 105.5, 105],
                             "Low": [103.5, 104, 104], "Close": [104.5, 105, 104.8],
                             "Volume": [1, 1, 1]}, index=idx)

    df, source = acquire_daily_frame(FakeManager(full_daily), "TEST", session, "TEST.NS")
    assert source == "daily" and len(df) == 40

    df, source = acquire_daily_frame(FakeManager(stale_daily, intraday), "TEST", session, "TEST.NS")
    assert source == "intraday"
    assert df.index[-1].date() == session and df["Close"].iloc[-1] == 104.8

    monkeypatch.setattr("data.nse_bhavcopy.fetch_eod_ohlc",
                        lambda sym, d: {"open": 104.0, "high": 105.2, "low": 103.9, "close": 104.9})
    df, source = acquire_daily_frame(FakeManager(stale_daily, None), "TEST", session, "TEST.NS")
    assert source == "bhavcopy" and df["Close"].iloc[-1] == 104.9

    monkeypatch.setattr("data.nse_bhavcopy.fetch_eod_ohlc", lambda sym, d: None)
    assert acquire_daily_frame(FakeManager(stale_daily, None), "TEST", session, "TEST.NS") is None


# --------------------------------------------------------------------------- #
# EOD pass: fresh signals, late signals, resolutions, registry, guards
# --------------------------------------------------------------------------- #

def _config(history=None, registry=None):
    return {"enabled": True,
            "telegram": {"bot_token": "t", "recipients": [{"chat_id": "1"}]},
            "conditions": {"gap_alerts": True},
            "alert_history": history or {},
            "gap_registry": registry or []}


def test_eod_pass_fresh_signal_and_registry():
    # signal on Tue 2026-08-25 (last bar): gap over prior high, close held
    rows = _flat(40) + [(103.0, 105.0, 102.5, 104.0)]
    daily = _daily_frame("2026-08-25", rows)
    now = _ist(2026, 8, 25, 16, 30)
    result = run_eod_pass(_config(), FakeManager(daily), ["TEST"], now=now)
    assert result is not None and len(result.messages) == 1
    msg, updates = result.messages[0]
    assert "Gap-Up Continuation (confirmed)" in msg and "LATE" not in msg
    assert "Stop loss" in msg and "not a buy/sell recommendation" in msg
    assert list(updates) == [signal_key("TEST", "2026-08-25")]
    assert updates[signal_key("TEST", "2026-08-25")] == "awaiting_entry"
    assert result.registry and result.registry[0]["status"] == "awaiting_entry"
    assert scan_guard_key(dt.date(2026, 8, 25)) in result.guard_updates


def test_eod_pass_late_signal_next_day():
    # signal was Mon 2026-08-24; pass runs Tue 17:00 (entry already occurred)
    rows = _flat(40) + [(103.0, 105.0, 102.5, 104.0), (104.5, 106.0, 104.0, 105.0)]
    daily = _daily_frame("2026-08-25", rows)
    now = _ist(2026, 8, 25, 17, 0)
    result = run_eod_pass(_config(), FakeManager(daily), ["TEST"], now=now)
    late = [m for m, _u in result.messages if "LATE ALERT" in m]
    assert late, "next-day catch-up must be labelled late"
    assert "already occurred" in late[0]
    active = [r for r in result.registry if r["status"] == "active"]
    assert active and active[0]["entry_price"] == 104.5


def test_eod_pass_resolution_transition():
    # previously-alerted signal (recorded 'active') whose target later hit
    rows = _flat(40) + [(103.0, 105.0, 102.5, 104.0),      # signal bar
                        (104.5, 105.0, 103.5, 104.0),
                        (104.0, 125.0, 103.8, 120.0)]       # 2R spike
    daily = _daily_frame("2026-08-25", rows)
    sig_date = str(daily.index[-3].date())
    hist = {signal_key("TEST", sig_date): "active"}
    result = run_eod_pass(_config(history=hist), FakeManager(daily), ["TEST"],
                          now=_ist(2026, 8, 25, 16, 30))
    res = [m for m, _u in result.messages if "resolved" in m]
    assert res and "2R target hit" in res[0] and "+2.00R" in res[0]
    _msg, updates = [x for x in result.messages if "resolved" in x[0]][0]
    assert updates[signal_key("TEST", sig_date)] == "target_hit"
    assert not any(r["symbol"] == "TEST" for r in result.registry)


def test_eod_pass_dedupes_already_alerted_signal():
    rows = _flat(40) + [(103.0, 105.0, 102.5, 104.0)]
    daily = _daily_frame("2026-08-25", rows)
    hist = {signal_key("TEST", "2026-08-25"): "awaiting_entry"}
    result = run_eod_pass(_config(history=hist), FakeManager(daily), ["TEST"],
                          now=_ist(2026, 8, 25, 16, 30))
    assert result.messages == []          # no duplicate signal alert
    assert result.registry                # but it stays tracked


def test_eod_pass_no_data_leaves_sessions_unguarded():
    result = run_eod_pass(_config(), FakeManager(None), ["TEST"],
                          now=_ist(2026, 8, 25, 16, 30))
    assert result.bars_seen == 0
    assert result.guard_updates == {}     # retry next cycle


# --------------------------------------------------------------------------- #
# Intraday touch pass
# --------------------------------------------------------------------------- #

def _registry_entry(**kw):
    base = {"symbol": "TEST", "signal_date": "2026-08-22", "status": "active",
            "entry_price": 104.5, "stop": 99.0, "target": 115.5}
    base.update(kw)
    return base


def test_touch_pass_stop_loss_and_target_and_dedupe():
    cfg = _config(registry=[_registry_entry()])
    msgs = run_touch_pass(cfg, lambda s: {"price": 98.5}, now=_ist(2026, 8, 25, 11, 0))
    assert len(msgs) == 1 and "Stop loss touched" in msgs[0][0]
    key = list(msgs[0][1])[0]
    # once recorded, no repeat
    cfg2 = _config(history={key: "sent"}, registry=[_registry_entry()])
    assert run_touch_pass(cfg2, lambda s: {"price": 98.5}) == []
    # target side
    msgs = run_touch_pass(cfg, lambda s: {"price": 116.0})
    assert len(msgs) == 1 and "2R target touched" in msgs[0][0]
    # in-between price: nothing
    assert run_touch_pass(cfg, lambda s: {"price": 105.0}) == []


def test_touch_pass_skips_non_active_and_terminal():
    cfg = _config(registry=[_registry_entry(status="awaiting_entry")])
    assert run_touch_pass(cfg, lambda s: {"price": 90.0}) == []
    cfg = _config(history={signal_key("TEST", "2026-08-22"): "stop_loss_hit"},
                  registry=[_registry_entry()])
    assert run_touch_pass(cfg, lambda s: {"price": 90.0}) == []


# --------------------------------------------------------------------------- #
# Enabled flag and isolation
# --------------------------------------------------------------------------- #

def test_gap_alerts_enabled_defaults_true_for_older_configs():
    assert gap_alerts.gap_alerts_enabled({"enabled": True, "conditions": {}})
    assert not gap_alerts.gap_alerts_enabled({"enabled": False, "conditions": {}})
    assert not gap_alerts.gap_alerts_enabled(
        {"enabled": True, "conditions": {"gap_alerts": False}})


def test_gap_alert_modules_do_not_patch_zone_engine():
    from analysis.zone_engine import patterns, scoring

    before = patterns.score_zone
    importlib.import_module("alerts.gap_alerts")
    importlib.import_module("alerts.telegram")
    assert patterns.score_zone is before
    assert patterns.score_zone is scoring.score_zone
