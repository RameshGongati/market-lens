"""Gap-Up Continuation: detector rule fidelity, guards, page helpers, storage,
harness parity and zone-engine isolation.

The detector is the graduated, twice-validated rule — these tests pin its
exact thresholds (strict > 1.3% over the prior HIGH, close holds, prior-low
− 0.1 ATR stop) so any drift from the validated definition fails loudly.
"""
from __future__ import annotations

import datetime as dt
import importlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analysis.gap_signals import (  # noqa: E402
    ATR_BUFFER, GapSignal, detect_gap_up_continuation, evidence_rank,
    latest_confirmed_signal, risk_ok, volume_expansion_tag, wilder_atr,
    zone_context_tag,
)


def _df(rows, start="2026-01-01", volume=None):
    """rows = list of (O, H, L, C); business-day index."""
    idx = pd.bdate_range(start, periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = volume if volume is not None else [10_000] * len(rows)
    return df


def _flat(n, price=100.0):
    # gentle drift so ATR is finite and non-zero
    return [(price, price + 1.0, price - 1.0, price + 0.2) for _ in range(n)]


# --------------------------------------------------------------------------- #
# Detector rule fidelity
# --------------------------------------------------------------------------- #

def test_gap_exactly_at_threshold_is_not_a_signal():
    # prior high 101; open at exactly 101 * 1.013 — strict > means NO signal
    rows = _flat(20)
    open_at = 101.0 * 1.013
    rows.append((open_at, open_at + 2, open_at - 0.5, open_at + 1))
    assert detect_gap_up_continuation(_df(rows)) == []


def test_gap_just_above_threshold_signals():
    rows = _flat(20)
    open_above = 101.0 * 1.013 + 0.05
    rows.append((open_above, open_above + 2, open_above - 0.5, open_above + 1))
    sigs = detect_gap_up_continuation(_df(rows))
    assert len(sigs) == 1
    assert sigs[-1].i == 20
    assert sigs[-1].gap_pct == pytest.approx((open_above / 101.0 - 1) * 100, abs=0.01)


def test_gap_is_measured_from_prior_high_not_close():
    # prior bar: close 100.2, high 104. Open 102.5 clears close by 2.3% but
    # NOT the high — no signal.
    rows = _flat(20)
    rows[-1] = (100.0, 104.0, 99.0, 100.2)
    rows.append((102.5, 104.5, 102.0, 103.0))
    assert detect_gap_up_continuation(_df(rows)) == []


def test_close_must_hold_at_or_above_open():
    rows = _flat(20)
    o = 103.0
    rows.append((o, o + 2, o - 1, o - 0.1))          # close below open -> no
    assert detect_gap_up_continuation(_df(rows)) == []
    rows[-1] = (o, o + 2, o - 1, o)                   # close == open -> yes
    assert len(detect_gap_up_continuation(_df(rows))) == 1


def test_stop_is_prior_low_minus_atr_buffer():
    rows = _flat(20)
    rows.append((103.0, 105.0, 102.5, 104.0))
    df = _df(rows)
    sig = detect_gap_up_continuation(df)[-1]
    atr = wilder_atr(df).iloc[sig.i]
    assert sig.prior_low == pytest.approx(99.0)       # 100 - 1
    assert sig.stop == pytest.approx(99.0 - ATR_BUFFER * atr)


def test_consecutive_gap_days_deduped_by_default_but_not_at_dedupe_1():
    rows = _flat(20)
    rows.append((103.0, 105.0, 102.5, 104.5))         # day A: signal
    rows.append((106.5, 108.0, 106.0, 107.0))         # day B: also qualifies
    df = _df(rows)
    assert [s.i for s in detect_gap_up_continuation(df)] == [20]
    assert [s.i for s in detect_gap_up_continuation(df, dedupe_bars=1)] == [20, 21]


def test_latest_confirmed_signal_only_fires_on_last_bar():
    rows = _flat(20)
    rows.append((103.0, 105.0, 102.5, 104.0))         # signal bar
    rows.append((104.0, 104.5, 103.0, 103.5))         # quiet day after
    assert latest_confirmed_signal(_df(rows)) is None
    assert latest_confirmed_signal(_df(rows[:-1])) is not None


def test_short_frames_return_no_signals():
    assert detect_gap_up_continuation(_df(_flat(5))) == []
    assert detect_gap_up_continuation(None) == []


# --------------------------------------------------------------------------- #
# Guards and tags
# --------------------------------------------------------------------------- #

def _sig(close=104.0, stop=100.0):
    return GapSignal(i=1, date=pd.Timestamp("2026-01-05"), gap_pct=2.0,
                     open=103.0, close=close, prior_low=stop + 0.5,
                     atr=1.0, stop=stop)


def test_risk_ok_matches_backtest_guards():
    assert risk_ok(_sig(close=104.0, stop=100.0))                 # ~3.8% risk
    assert not risk_ok(_sig(close=104.0, stop=104.0 * (1 - 0.13)))  # >12%
    assert not risk_ok(_sig(close=104.0, stop=104.0 - 0.05))       # <0.1%


def test_target_2r_arithmetic():
    s = _sig(close=104.0, stop=100.0)
    assert s.target_2r() == pytest.approx(112.0)
    assert s.risk_pct_vs_close == pytest.approx(4 / 104 * 100)


def test_zone_context_tag_reads_result_dict_read_only():
    res = {"nearest_demand": {"proximal": 101.0, "distal": 98.0}}
    assert zone_context_tag(res, close=103.0)          # within 3% above
    assert zone_context_tag(res, close=100.0)          # inside the band
    assert not zone_context_tag(res, close=106.0)      # 5% away
    assert not zone_context_tag(None, close=103.0)
    assert not zone_context_tag({}, close=103.0)


def test_volume_expansion_tag():
    vols = [10_000] * 24 + [16_000]
    rows = _flat(24) + [(103.0, 105.0, 102.5, 104.0)]
    df = _df(rows, volume=vols)
    assert volume_expansion_tag(df, 24)
    df2 = _df(rows, volume=[10_000] * 25)
    assert not volume_expansion_tag(df2, 24)


def test_evidence_rank_has_no_t13_penalty():
    assert evidence_rank(False, False) == 70
    assert evidence_rank(False, True) == 75
    assert evidence_rank(True, False) == 80
    assert evidence_rank(True, True) == 85


# --------------------------------------------------------------------------- #
# Page pure helpers
# --------------------------------------------------------------------------- #

def test_drop_forming_bar_intraday_vs_after_close():
    from ui.pages.gap_signals import drop_forming_bar

    today = dt.datetime(2026, 8, 24, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    rows = _flat(5)
    idx = pd.bdate_range(end="2026-08-24", periods=5)
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    assert len(drop_forming_bar(df, now=today)) == 4        # 14:00 IST -> drop
    after = today.replace(hour=16, minute=5)
    assert len(drop_forming_bar(df, now=after)) == 5        # confirmed -> keep


def test_days_to_result_parsing():
    from ui.pages.gap_signals import days_to_result

    as_of = dt.date(2026, 8, 24)
    assert days_to_result({"result_date": "2026-08-25"}, as_of) == 1
    assert days_to_result({"result_date": "2026-08-24"}, as_of) == 0
    assert days_to_result({"result_date": "2026-10-30"}, as_of) is None   # >30d
    assert days_to_result({"result_date": "2026-08-20"}, as_of) is None   # past
    assert days_to_result({"result_date": None}, as_of) is None
    assert days_to_result(None, as_of) is None


def test_tag_filters_and_status_split_and_sort():
    from analysis.gap_signals import (STATUS_ACTIVE, STATUS_AWAITING,
                                      STATUS_STOP, STATUS_TARGET, STATUS_TIME)
    from ui.pages.gap_signals import apply_tag_filters, sort_rows, split_by_status

    rows = [
        {"symbol": "A", "status": STATUS_ACTIVE, "signal_date": "2026-08-10",
         "from_demand_zone": True, "volume_expansion": False,
         "result_in_days": None, "evidence_rank": 80, "days_active": 5,
         "r_multiple": 0.4},
        {"symbol": "B", "status": STATUS_AWAITING, "signal_date": "2026-08-21",
         "from_demand_zone": False, "volume_expansion": True,
         "result_in_days": 2, "evidence_rank": 75, "days_active": None,
         "r_multiple": None},
        {"symbol": "C", "status": STATUS_TARGET, "signal_date": "2026-08-01",
         "from_demand_zone": False, "volume_expansion": False,
         "result_in_days": None, "evidence_rank": 70, "days_active": 4,
         "r_multiple": 2.0},
        {"symbol": "D", "status": STATUS_STOP, "signal_date": "2026-08-05",
         "from_demand_zone": False, "volume_expansion": False,
         "result_in_days": None, "evidence_rank": 70, "days_active": 2,
         "r_multiple": -1.0},
        {"symbol": "E", "status": STATUS_TIME, "signal_date": "2026-07-01",
         "from_demand_zone": False, "volume_expansion": False,
         "result_in_days": None, "evidence_rank": 70, "days_active": 20,
         "r_multiple": 0.3},
    ]
    groups = split_by_status(rows)
    assert [r["symbol"] for r in groups[STATUS_ACTIVE]] == ["A", "B"]  # awaiting joins active
    assert [r["symbol"] for r in groups[STATUS_TARGET]] == ["C"]
    assert [r["symbol"] for r in groups[STATUS_STOP]] == ["D"]
    assert [r["symbol"] for r in groups[STATUS_TIME]] == ["E"]

    assert [r["symbol"] for r in apply_tag_filters(rows, zone_only=True,
            volume_only=False, hide_results_week=False)] == ["A"]
    assert [r["symbol"] for r in apply_tag_filters(rows, zone_only=False,
            volume_only=True, hide_results_week=False)] == ["B"]
    assert "B" not in [r["symbol"] for r in apply_tag_filters(
        rows, zone_only=False, volume_only=False, hide_results_week=True)]

    act = groups[STATUS_ACTIVE]
    assert [r["symbol"] for r in sort_rows(act, "Most recent first")] == ["B", "A"]
    assert [r["symbol"] for r in sort_rows(act, "Unrealized R")] == ["A", "B"]


# --------------------------------------------------------------------------- #
# Signal tracking: the exact backtest trade rules
# --------------------------------------------------------------------------- #

def _frame_with_signal(after: list[tuple]) -> tuple[pd.DataFrame, "object"]:
    """20 quiet bars, a signal bar, then `after` bars. Returns (df, signal)."""
    rows = _flat(20) + [(103.0, 105.0, 102.5, 104.0)] + after
    df = _df(rows)
    sigs = detect_gap_up_continuation(df)
    assert sigs and sigs[0].i == 20
    return df, sigs[0]


def test_track_awaiting_entry_when_signal_is_last_bar():
    from analysis.gap_signals import STATUS_AWAITING, track_signal

    df, sig = _frame_with_signal([])
    t = track_signal(df, sig)
    assert t.status == STATUS_AWAITING and t.entry_price is None


def test_track_target_hit_and_stop_loss_hit():
    from analysis.gap_signals import STATUS_STOP, STATUS_TARGET, track_signal

    # entry open 104.5 -> risk to stop; a later bar spikes through 2R
    df, sig = _frame_with_signal([(104.5, 105.0, 103.5, 104.0),
                                  (104.0, 125.0, 103.8, 120.0)])
    t = track_signal(df, sig)
    assert t.status == STATUS_TARGET and t.r_multiple == 2.0
    assert t.exit_price == t.target and t.entry_price == 104.5

    df2, sig2 = _frame_with_signal([(104.5, 105.0, 103.5, 104.0),
                                    (103.5, 104.0, 80.0, 82.0)])
    t2 = track_signal(df2, sig2)
    assert t2.status == STATUS_STOP and t2.r_multiple == -1.0
    assert t2.exit_price == pytest.approx(round(sig2.stop, 2))


def test_track_same_bar_both_touched_counts_as_stop_loss():
    from analysis.gap_signals import STATUS_STOP, track_signal

    df, sig = _frame_with_signal([(104.5, 150.0, 80.0, 100.0)])  # hits both
    t = track_signal(df, sig)
    assert t.status == STATUS_STOP, "both-touched bar must resolve as a loss"


def test_track_active_with_unrealized_r_and_time_stop_at_20():
    from analysis.gap_signals import (STATUS_ACTIVE, STATUS_TIME,
                                      TIME_STOP_BARS, track_signal)

    quiet = [(104.5, 105.5, 103.5, 105.0)] * 5
    df, sig = _frame_with_signal(quiet)
    t = track_signal(df, sig)
    assert t.status == STATUS_ACTIVE and t.days_active == 4
    entry, risk = 104.5, 104.5 - sig.stop
    assert t.r_multiple == pytest.approx(round((105.0 - entry) / risk, 2))

    quiet21 = [(104.5, 105.5, 103.5, 105.0)] * (TIME_STOP_BARS + 1)
    df2, sig2 = _frame_with_signal(quiet21)
    t2 = track_signal(df2, sig2)
    assert t2.status == STATUS_TIME and t2.days_active == TIME_STOP_BARS
    assert t2.exit_price == pytest.approx(105.0)


def test_saved_active_row_refreshes_to_target_hit():
    from analysis.gap_signals import STATUS_ACTIVE, STATUS_TARGET, track_signal
    from ui.pages.gap_signals import build_row, refresh_open_signal_rows

    # A saved scan before the second post-entry bar sees an open position.
    full, sig = _frame_with_signal([
        (104.5, 105.0, 103.5, 104.0),
        (104.0, 125.0, 103.8, 120.0),
        *[(104.0, 105.0, 103.5, 104.0)] * 7,
    ])
    before_target = full.iloc[:22]
    saved = track_signal(
        before_target, detect_gap_up_continuation(before_target)[0]
    )
    assert saved.status == STATUS_ACTIVE
    row = build_row("TEST", "Test Ltd", saved, from_zone=False,
                    volume_ok=False, result_days=None, market_extended=False)

    rows, transitions = refresh_open_signal_rows(
        [row],
        fetch_fn=lambda _symbol, _period, _interval: full,
        source_name="Yahoo Finance",
    )

    assert transitions == 1
    assert rows[0]["status"] == STATUS_TARGET
    assert rows[0]["r_multiple"] == 2.0


def test_gap_chart_overlay_uses_next_open_not_signal_close():
    from ui.components.stock_detail import _gap_overlay_levels

    df, sig = _frame_with_signal([
        (104.5, 105.0, 103.5, 104.0),
        (104.0, 125.0, 103.8, 120.0),
    ])

    entry, target = _gap_overlay_levels(df, sig)

    assert entry == pytest.approx(104.5)
    assert target == pytest.approx(115.94)
    assert target != sig.target_2r(sig.close)


def test_track_skips_trades_the_backtest_risk_guard_skipped():
    from analysis.gap_signals import track_signal

    # prior bar has an absurdly deep low -> risk > 12% of entry -> skipped
    rows = _flat(19) + [(100.0, 101.0, 60.0, 100.2)]
    rows += [(103.0, 105.0, 102.5, 104.0), (104.5, 105.0, 103.5, 104.0)]
    df = _df(rows)
    sig = detect_gap_up_continuation(df)[-1]
    assert track_signal(df, sig) is None


def test_tracker_matches_backtest_simulator():
    """The page's tracker and the research simulator must agree bar-for-bar."""
    from analysis.gap_signals import track_signal
    from research_engine.harness.detectors import Signal
    from research_engine.harness.simulate import simulate

    cases = [
        [(104.5, 105.0, 103.5, 104.0), (104.0, 125.0, 103.8, 120.0)],   # target
        [(104.5, 105.0, 103.5, 104.0), (103.5, 104.0, 80.0, 82.0)],     # stop loss
        [(104.5, 150.0, 80.0, 100.0)],                                   # both -> loss
        [(104.5, 105.5, 103.5, 105.0)] * 25,                             # time stop
    ]
    expect = {"win": "target_hit", "loss": "stop_loss_hit", "timeout": "time_stopped"}
    for after in cases:
        df, sig = _frame_with_signal(after)
        t = track_signal(df, sig)
        [trade] = simulate(df, [Signal(sig.i, "gap_up_go", 1, sig.stop)], "daily")
        assert expect[trade["result"]] == t.status
        assert trade["exit_price"] == pytest.approx(t.exit_price, abs=0.01)
        assert trade["holding_period"] == t.days_active


# --------------------------------------------------------------------------- #
# Storage round-trip
# --------------------------------------------------------------------------- #

def test_gap_scan_round_trip(monkeypatch, tmp_path):
    import storage.database as database

    monkeypatch.setattr(database, "_APP_DIR", tmp_path)
    monkeypatch.setattr(database, "_DB_PATH", tmp_path / "market_lens.db")
    database.init_db()
    rows = [{"symbol": "RELIANCE", "gap_pct": 2.1, "stop": 1280.0,
             "evidence_rank": 85, "from_demand_zone": True}]
    scan_id = database.save_gap_scan({"scope": "F&O Stocks"}, "F&O Stocks",
                                     "Yahoo Finance", rows)
    got = database.get_gap_scan(scan_id)
    assert got is not None
    assert got["settings"] == {"scope": "F&O Stocks"}
    assert got["rows"] == rows
    assert database.get_gap_scan("nope") is None


# --------------------------------------------------------------------------- #
# Harness parity: the research backtest now runs THIS detector
# --------------------------------------------------------------------------- #

def test_harness_emits_same_gap_signals_as_old_inline_rule():
    from research_engine.harness.indicators import compute
    from research_engine.harness.detectors import indicator_signals

    rows = _flat(70)
    rows.append((103.0, 105.0, 102.5, 104.0))          # signal at i=70
    rows += _flat(5, price=104.0)
    df = compute(_df(rows))
    got = [(s.i, round(s.stop, 6)) for s in indicator_signals(df, "daily")
           if s.setup == "gap_up_go"]
    # old inline rule, reimplemented verbatim for the parity pin
    o, h, l = df["Open"].to_numpy(), df["High"].to_numpy(), df["Low"].to_numpy()
    c, atr = df["Close"].to_numpy(), df["atr"].to_numpy()
    want = [(i, round(l[i - 1] - 0.1 * atr[i], 6)) for i in range(60, len(df))
            if o[i] > h[i - 1] * 1.013 and c[i] >= o[i]]
    assert got == want and len(got) == 1


# --------------------------------------------------------------------------- #
# Isolation: nothing here may touch the zone engine
# --------------------------------------------------------------------------- #

def test_gap_modules_do_not_patch_zone_engine():
    from analysis.zone_engine import patterns, scoring

    before = patterns.score_zone
    importlib.import_module("analysis.gap_signals")
    importlib.import_module("ui.pages.gap_signals")
    assert patterns.score_zone is before
    assert patterns.score_zone is scoring.score_zone
