"""Options Trade Lab: parser, expiry rules, option maths, Premium Burden,
scores + data coverage, decomposition rules, verdict mapping, the CDSL-shaped
fixture, store round-trip and zone-engine isolation.
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

from research_engine import trade_lab as tl  # noqa: E402


# --------------------------------------------------------------------------- #
# Smart text parsing
# --------------------------------------------------------------------------- #

def test_parse_compact_grammar():
    fields, warnings = tl.parse_smart_text("CDSL Aug 1380 CE premium 45",
                                           default_year=2026)
    assert fields["symbol"] == "CDSL"
    assert fields["option_type"] == "CE"
    assert fields["strike"] == 1380.0
    assert fields["premium"] == 45.0
    assert fields["expiry_month"] == 8
    assert warnings == []


def test_parse_post_trade_sentence():
    text = "CDSL August 1380 CE bought 23 July premium 45 sold 20 August premium 8"
    fields, _ = tl.parse_smart_text(text, default_year=2026)
    assert fields["buy_date"] == dt.date(2026, 7, 23)
    assert fields["sell_date"] == dt.date(2026, 8, 20)
    assert fields["premium"] == 45.0
    assert fields["sell_premium"] == 8.0
    assert fields["expiry_month"] == 8


def test_parse_key_value_grammar():
    text = ("Stock: CDSL, type: CE, action: Buy, month: August, strike: 1380, "
            "premium: 45")
    fields, warnings = tl.parse_smart_text(text, default_year=2026)
    assert fields == {"symbol": "CDSL", "option_type": "CE", "action": "buy",
                      "expiry_month": 8, "strike": 1380.0, "premium": 45.0}
    assert warnings == []


def test_parse_unclear_input_warns_never_guesses():
    fields, warnings = tl.parse_smart_text("something vague 42", default_year=2026)
    assert "option_type" not in fields
    assert any("CE/PE" in w for w in warnings)
    assert any("premium" in w for w in warnings)
    _, w2 = tl.parse_smart_text("", default_year=2026)
    assert w2 == ["empty input"]


# --------------------------------------------------------------------------- #
# Expiry rules
# --------------------------------------------------------------------------- #

def test_last_tuesday_of_month():
    assert tl.last_tuesday_of_month(2026, 8) == dt.date(2026, 8, 25)
    assert tl.last_tuesday_of_month(2026, 9) == dt.date(2026, 9, 29)


def test_resolve_expiry_precedence_and_badges():
    nse = [dt.date(2026, 8, 25), dt.date(2026, 9, 29)]
    d, badge, w = tl.resolve_expiry(8, 2026, nse, None)
    assert (d, badge) == (dt.date(2026, 8, 25), tl.EXPIRY_CONFIRMED) and not w

    d, badge, w = tl.resolve_expiry(8, 2026, None, None)
    assert badge == tl.EXPIRY_SUGGESTION and d == dt.date(2026, 8, 25)
    assert any("could not be confirmed" in x for x in w)

    d, badge, _ = tl.resolve_expiry(8, 2026, nse, dt.date(2026, 8, 24))
    assert (d, badge) == (dt.date(2026, 8, 24), tl.EXPIRY_MANUAL)  # manual wins


# --------------------------------------------------------------------------- #
# Option maths
# --------------------------------------------------------------------------- #

def test_breakeven_and_moneyness():
    assert tl.breakeven("CE", 1380, 45) == 1425
    assert tl.breakeven("PE", 1380, 45) == 1335
    assert tl.moneyness("CE", 1320, 1380) == "OTM"
    assert tl.moneyness("CE", 1320, 1250) == "ITM"
    assert tl.moneyness("CE", 1320, 1325) == "ATM"
    assert tl.moneyness("PE", 1320, 1380) == "ITM"
    assert tl.moneyness("PE", 1320, 1250) == "OTM"


def test_required_move_and_premium_burden_bands():
    be = tl.breakeven("CE", 1380, 45)
    req = tl.required_move_pct("CE", 1320, be)
    assert req == pytest.approx((1425 - 1320) / 1320 * 100, abs=0.01)
    assert tl.premium_burden(2.0, 8.0, 30)[0] == "Light"
    assert tl.premium_burden(6.0, 8.0, 30)[0] == "Moderate"
    assert tl.premium_burden(10.0, 8.0, 30)[0] == "Heavy"
    assert tl.premium_burden(20.0, 8.0, 30)[0] == "Very heavy"
    # near expiry bumps the band one level
    assert tl.premium_burden(6.0, 8.0, 5)[0] == "Heavy"
    assert tl.premium_burden(None, 8.0, 30) == (None, None)


def test_total_quantity_arithmetic():
    a = tl.assess_option("CE", "bullish", 1320, 1380, 45, 30, 2.0,
                         num_lots=2, lot_size=475)
    assert a.total_quantity == 950
    assert a.total_premium == pytest.approx(45 * 950)
    b = tl.assess_option("CE", "bullish", 1320, 1380, 45, 30, 2.0,
                         num_lots=2, lot_size=None)
    assert b.total_quantity is None and b.total_premium is None


def test_option_verdicts():
    # wrong side: PE for a bullish view
    a = tl.assess_option("PE", "bullish", 1320, 1300, 30, 30, 2.0)
    assert a.verdict == "Avoid option buying"
    # far OTM
    a = tl.assess_option("CE", "bullish", 1320, 1420, 10, 30, 2.0)
    assert a.verdict == "Strike too far OTM"
    # near expiry
    a = tl.assess_option("CE", "bullish", 1320, 1330, 20, 5, 2.0)
    assert a.verdict == "Expiry too near"
    # unconfirmed stock downgrades an otherwise fine option
    a = tl.assess_option("CE", "bullish", 1320, 1325, 20, 30, 3.0,
                         stock_needs_confirmation=True)
    assert a.verdict == "Stock setup valid but option not suitable"


# --------------------------------------------------------------------------- #
# Scores: observed flags only; missing -> coverage, never risk
# --------------------------------------------------------------------------- #

def test_score_ignores_unobserved_flags():
    flags = {"no_confirmation": True, "price_inside_or_between": None,
             "weak_bounce_volume": None, "stale_touch": False,
             "habitation": None, "atr_contracting": None, "no_momentum": None,
             "result_within_2d": None, "slow_stock": None}
    score, _, comps, missing = tl._score(flags, tl._SIDEWAYS_WEIGHTS)
    # observed: no_confirmation (20, True) + stale_touch (10, False) -> 20/30
    assert score == 67
    assert len(comps) == 2 and len(missing) == 7


def test_bands():
    assert tl._band(10, tl.SIDEWAYS_BANDS) == "Low sideways risk"
    assert tl._band(65, tl.SIDEWAYS_BANDS) == "High sideways risk"
    assert tl._band(95, tl.TRAP_BANDS) == "Avoid"


# --------------------------------------------------------------------------- #
# Decomposition rules
# --------------------------------------------------------------------------- #

def test_decomposition_exact_and_proxy_and_descriptive():
    d = tl.decompose("CE", 1380, 45, 8, 1323.0, 1346.0, "user", 475)
    assert d.kind == "exact"
    intrinsic = [r for r in d.rows if r["item"] == "Intrinsic value"][0]
    assert intrinsic["entry"] == 0 and intrinsic["exit"] == 0   # OTM both ends
    tv = [r for r in d.rows if "Time value" in r["item"]][0]
    assert tv["change"] == pytest.approx(-37.0)                  # all time value
    assert "cannot be separated" in d.caption
    total = [r for r in d.rows if "Total" in r["item"]][0]
    assert total["change"] == pytest.approx(-37 * 475)

    d2 = tl.decompose("CE", 1380, 45, 8, 1323.0, 1346.0, "daily_close", None)
    assert d2.kind == "daily_close_proxy" and "proxy" in d2.caption.lower()

    d3 = tl.decompose("CE", 1380, 45, None, 1323.0, None, "user", None)
    assert d3.kind == "descriptive" and d3.rows == []


# --------------------------------------------------------------------------- #
# CDSL-shaped fixture: fresh strong zone, price inside, no confirmation
# --------------------------------------------------------------------------- #

def _cdsl_like_frame() -> pd.DataFrame:
    rows = []
    price = 1250.0
    for k in range(60):                       # gentle drift with real ranges
        o = price
        c = price + 1.0
        rows.append((o, max(o, c) + 12, min(o, c) - 12, c))
        price = c
    # legin: bearish exciting  (price ~1310)
    rows.append((1330.0, 1335.0, 1295.0, 1300.0))
    # instant-reversal legout: strong bullish, clears the legin range
    rows.append((1300.0, 1390.0, 1298.0, 1385.0))
    rows.append((1385.0, 1402.0, 1380.0, 1398.0))
    # retrace back INSIDE the zone (proximal 1330, distal 1295), no exit close
    for o, h, l, c in [(1395, 1396, 1355, 1360), (1358, 1360, 1330, 1338),
                       (1336, 1340, 1315, 1322), (1323, 1330, 1312, 1318),
                       (1319, 1328, 1310, 1316)]:
        rows.append((float(o), float(h), float(l), float(c)))
    idx = pd.bdate_range(end="2026-07-23", periods=len(rows))
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=idx)
    df["Volume"] = 10_000
    return df


def test_cdsl_like_scenario_end_to_end():
    frame = _cdsl_like_frame()
    inputs = {"mode": "pre", "symbol": "CDSL", "direction": "bullish",
              "option_type": "CE", "strike": 1380.0, "premium": 45.0,
              "expiry_date": dt.date(2026, 8, 25), "expiry_badge": tl.EXPIRY_MANUAL,
              "research_date": dt.date(2026, 7, 23)}
    report = tl.analyse(inputs, frame, context={})
    assert report.stock.verdict == "Bullish setup valid but needs confirmation"
    assert report.stock.confirmation_status == "no confirmation close yet"
    assert report.stock.price_vs_zone == "inside zone"
    assert report.stock.closes_inside >= 2
    assert report.option.moneyness == "OTM"
    assert report.option.breakeven == 1425.0
    assert report.final_verdict.startswith("WAIT for confirmation")
    assert report.sideways_score >= 41            # at least "Caution"
    assert any("result date unknown" in m for m in report.coverage_missing)
    txt = tl.build_summary_text(report, inputs)
    assert "not a buy/sell recommendation" in txt


def test_anchor_prefers_zone_containing_price_over_nearest():
    """With overlapping zones (a fresh zone containing the spot and a nearer,
    already-tested sub-zone below it), the anchor must be the CONTAINING zone
    — that is where the trade thesis lives. Reproduces the real-CDSL case."""
    frame = _cdsl_like_frame()
    setup = tl.analyse_stock_setup(frame, "bullish", dt.date(2026, 7, 23), {})
    assert setup.demand is not None
    lo = min(setup.demand.proximal, setup.demand.distal)
    hi = max(setup.demand.proximal, setup.demand.distal)
    spot = float(frame.Close.iloc[-1])
    assert lo <= spot <= hi, "anchor zone must contain the spot"
    assert setup.verdict == "Bullish setup valid but needs confirmation"


def test_no_lookahead_frame_cut():
    """Bars after the research date must not change the analysis."""
    frame = _cdsl_like_frame()
    future = frame.copy()
    burst = pd.DataFrame([(1320.0, 1500.0, 1315.0, 1490.0)],
                         columns=["Open", "High", "Low", "Close"],
                         index=pd.bdate_range(start="2026-07-24", periods=1))
    burst["Volume"] = 99_000
    future = pd.concat([future, burst])
    s1 = tl.analyse_stock_setup(frame, "bullish", dt.date(2026, 7, 23), {})
    s2 = tl.analyse_stock_setup(future, "bullish", dt.date(2026, 7, 23), {})
    assert s1.verdict == s2.verdict
    assert s1.closes_inside == s2.closes_inside
    assert (s1.demand.proximal, s1.demand.distal) == (s2.demand.proximal, s2.demand.distal)


def test_post_mode_produces_lesson_and_decomposition():
    frame = _cdsl_like_frame()
    inputs = {"mode": "post", "symbol": "CDSL", "direction": "bullish",
              "option_type": "CE", "strike": 1380.0, "premium": 45.0,
              "sell_premium": 8.0, "expiry_date": dt.date(2026, 8, 25),
              "expiry_badge": tl.EXPIRY_MANUAL,
              "buy_date": dt.date(2026, 7, 23), "sell_date": dt.date(2026, 7, 23),
              "research_date": dt.date(2026, 7, 23)}
    report = tl.analyse(inputs, frame, context={})
    assert report.final_verdict == "POST-TRADE LESSON ONLY"
    assert report.decomposition is not None
    assert report.decomposition.kind == "daily_close_proxy"   # no spots given
    assert report.learning


# --------------------------------------------------------------------------- #
# Store round-trip + isolation
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Live-chain normalization (NSE v3 serves ONE expiry per request)
# --------------------------------------------------------------------------- #

def _chain_payload(record_expiry: str, strike: int, ltp: float, iv: float) -> dict:
    """One per-expiry NSE payload; record-level expiryDate uses %d-%m-%Y."""
    return {"records": {
        "expiryDates": ["25-Aug-2026", "29-Sep-2026", "27-Oct-2026"],
        "underlyingValue": 35480, "timestamp": "21-Aug-2026 15:40:00",
        "data": [{"strikePrice": strike,
                  "CE": {"expiryDate": record_expiry, "strikePrice": strike,
                         "lastPrice": ltp, "impliedVolatility": iv,
                         "buyPrice1": ltp, "sellPrice1": ltp + 5,
                         "openInterest": 34, "totalTradedVolume": 38}}],
    }}


def test_chain_normalize_merges_per_expiry_payloads():
    from data.nse_options import _normalize, strike_record

    aug = _chain_payload("25-08-2026", 35500, 400.0, 21.5)
    sep = _chain_payload("29-09-2026", 36000, 1003.0, 23.09)
    chain = _normalize([aug, sep])

    # The base payload lists all three expiries even though only two carry rows.
    assert chain["expiries"] == [dt.date(2026, 8, 25), dt.date(2026, 9, 29),
                                 dt.date(2026, 10, 27)]
    assert chain["covered_expiries"] == ["2026-08-25", "2026-09-29"]
    rec = strike_record(chain, dt.date(2026, 9, 29), 36000.0, "CE")
    assert rec is not None and rec["ltp"] == 1003.0 and rec["iv"] == 23.09
    assert strike_record(chain, dt.date(2026, 8, 25), 35500.0, "CE") is not None
    assert strike_record(chain, dt.date(2026, 10, 27), 36000.0, "CE") is None


def test_chain_normalize_keeps_partial_coverage_on_expiry_error():
    from data.nse_options import _normalize, strike_record

    aug = _chain_payload("25-08-2026", 35500, 400.0, 21.5)
    chain = _normalize([aug], fetch_errors=["29-Sep-2026: boom"])

    assert chain["ok"] is True
    assert chain["covered_expiries"] == ["2026-08-25"]
    assert chain["fetch_errors"] == ["29-Sep-2026: boom"]
    assert strike_record(chain, dt.date(2026, 8, 25), 35500.0, "CE") is not None
    assert strike_record(chain, dt.date(2026, 9, 29), 36000.0, "CE") is None


def test_trade_lab_store_round_trip(tmp_path):
    from research_engine import store

    db = tmp_path / "re.db"
    aid = store.save_trade_lab_analysis("CDSL", "pre", {"strike": 1380},
                                        {"final_verdict": "WAIT"}, db_path=db)
    got = store.get_trade_lab_analysis(aid, db_path=db)
    assert got["symbol"] == "CDSL" and got["report"]["final_verdict"] == "WAIT"
    listed = store.list_trade_lab_analyses(db_path=db)
    assert len(listed) == 1 and listed[0]["id"] == aid
    assert store.get_trade_lab_analysis("nope", db_path=db) is None


def test_trade_lab_modules_do_not_touch_zone_engine():
    from analysis.zone_engine import patterns, scoring

    before = patterns.score_zone
    importlib.import_module("research_engine.trade_lab")
    importlib.import_module("data.nse_options")
    assert patterns.score_zone is before
    assert patterns.score_zone is scoring.score_zone
    assert not any(m.startswith("research_engine.harness")
                   for m in sys.modules if "trade_lab" in m)
