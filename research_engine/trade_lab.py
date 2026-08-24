"""Options Trade Lab — pure analysis logic (no Streamlit, no harness imports).

Answers two separate questions for ONE bought CE/PE idea at a time:
  Layer 1 — can the stock move in the expected direction? (point-in-time,
            frame cut at the research/buy date; read-only zone detection)
  Layer 2 — is the selected option contract suitable for that move?

Everything here is educational research output. Verdict vocabulary is fixed
(TAKE candidate / WAIT / WATCH / AVOID / ... ), never buy/sell
recommendations. Missing inputs reduce DATA COVERAGE — they never add risk
points and never silently become guesses.

Boundaries (enforced by tests): no zone-engine mutation, no ODD/GTF changes,
no research_engine.harness imports, historical stats arrive as plain dicts
read from the research store by the caller.
"""
from __future__ import annotations

import calendar
import datetime as dt
import math
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from analysis.zone_engine.patterns import detect_zones  # read-only

# ---------------------------------------------------------------- constants
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})

ATM_BAND_PCT = 1.0          # |strike-spot| <= 1% of spot -> ATM
FAR_OTM_PCT = 5.0
NEAR_EXPIRY_DAYS = 10
ZONE_NEAR_PCT = 5.0         # zone considered "near" within 5% of spot
STALE_TOUCH_BARS = 10       # app's confirmation-recency rule
HABITATION_LIMIT = 4        # engine kills a zone at 4 closes inside

EXPIRY_CONFIRMED = "Confirmed by NSE"
EXPIRY_SUGGESTION = "Unverified suggestion"
EXPIRY_MANUAL = "Manual"

SIDEWAYS_BANDS = [(20, "Low sideways risk"), (40, "Moderate sideways risk"),
                  (60, "Caution"), (80, "High sideways risk"),
                  (100, "Avoid for option buying")]
TRAP_BANDS = [(20, "Low trap risk"), (40, "Moderate trap risk"),
              (60, "Caution"), (80, "High trap risk"), (100, "Avoid")]

BURDEN_CAPTION_PROXY = ("Premium Burden is approximate: IV/theta data is "
                        "missing, so the rating compares the required move "
                        "with an ATR-based expected move.")
BURDEN_CAPTION_IV = ("Premium Burden uses the live ATM implied volatility for "
                     "the expected move; it is still an approximation (no IV "
                     "history/rank available).")


# ---------------------------------------------------------------- dataclasses
@dataclass
class ZoneView:
    category: str
    proximal: float
    distal: float
    odd_score: float
    is_fresh: bool
    times_tested: int
    strength: str
    num_base: int
    width_pct: float
    created_index: int


@dataclass
class StockSetup:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    demand: ZoneView | None = None
    supply: ZoneView | None = None
    support_level: float | None = None
    resistance_level: float | None = None
    invalidation_level: float | None = None
    target_level: float | None = None
    rr_to_obstacle: float | None = None
    confirmation_status: str = "unknown"
    closes_inside: int | None = None
    days_since_touch: int | None = None
    price_vs_zone: str = "n/a"
    above_ema20: bool | None = None
    above_sma50: bool | None = None
    above_sma200: bool | None = None
    momentum_20d_pct: float | None = None
    bounce_volume_ratio: float | None = None
    atr_contracting: bool | None = None
    market_line: str = "market context unavailable"
    sector_line: str = "sector context unavailable"
    history_line: str = "no historical stats in the research store"
    flags: dict[str, bool | None] = field(default_factory=dict)


@dataclass
class OptionAssessment:
    verdict: str
    reasons: list[str] = field(default_factory=list)
    moneyness: str = "n/a"
    strike_distance_pct: float | None = None
    breakeven: float | None = None
    required_move_pct: float | None = None
    expected_move_pct: float | None = None
    premium_pct_of_spot: float | None = None
    days_to_expiry: int | None = None
    burden_rating: str | None = None
    burden_caption: str = BURDEN_CAPTION_PROXY
    total_quantity: int | None = None
    total_premium: float | None = None
    iv: float | None = None
    oi: int | None = None
    chain_volume: int | None = None
    bid: float | None = None
    ask: float | None = None
    spread_pct: float | None = None


@dataclass
class Decomposition:
    kind: str                     # exact | daily_close_proxy | descriptive
    caption: str
    rows: list[dict] = field(default_factory=list)
    text: str = ""


@dataclass
class LabReport:
    mode: str
    stock: StockSetup
    option: OptionAssessment
    sideways_score: int = 0
    sideways_band: str = ""
    sideways_components: list[dict] = field(default_factory=list)
    trap_score: int = 0
    trap_band: str = ""
    trap_components: list[dict] = field(default_factory=list)
    coverage_available: int = 0
    coverage_total: int = 0
    coverage_missing: list[str] = field(default_factory=list)
    final_verdict: str = ""
    final_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    decomposition: Decomposition | None = None
    learning: list[str] = field(default_factory=list)
    chart_levels: dict[str, float | None] = field(default_factory=dict)


# =============================================================== smart parsing
_KV_KEYS = {
    "stock": "symbol", "symbol": "symbol", "type": "option_type",
    "option type": "option_type", "action": "action", "month": "expiry_month",
    "expiry month": "expiry_month", "strike": "strike", "strike price": "strike",
    "premium": "premium", "buy premium": "premium", "sell premium": "sell_premium",
    "buy date": "buy_date", "sell date": "sell_date", "quantity": "num_lots",
    "lots": "num_lots", "lot size": "lot_size",
}
_DATE_RE = re.compile(r"(\d{1,2})\s*(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?(?:\s+(\d{4}))?", re.I)
_NOISE_WORDS = {"ce", "pe", "buy", "sell", "bought", "sold", "premium", "strike",
                "expiry", "month", "lot", "lots", "with", "huge", "loss", "profit",
                "and", "on", "at", "the", "a", "i"}


def _parse_date_token(daystr: str, monstr: str, yearstr: str | None,
                      default_year: int) -> dt.date | None:
    mon = MONTHS.get(monstr.lower())
    if not mon:
        return None
    try:
        return dt.date(int(yearstr) if yearstr else default_year, mon, int(daystr))
    except ValueError:
        return None


def parse_smart_text(text: str, default_year: int | None = None) -> tuple[dict, list[str]]:
    """Deterministic parser for the documented grammars. Returns
    (fields, warnings) — anything unclear becomes a warning, never a guess."""
    default_year = default_year or dt.date.today().year
    fields: dict[str, Any] = {}
    warnings: list[str] = []
    raw = text.strip()
    if not raw:
        return {}, ["empty input"]

    # key: value grammar (comma/newline separated) takes precedence
    if ":" in raw:
        for part in re.split(r"[,\n]", raw):
            if ":" not in part:
                continue
            key, _, val = part.partition(":")
            mapped = _KV_KEYS.get(key.strip().lower())
            val = val.strip()
            if not mapped:
                warnings.append(f"unrecognised field: '{key.strip()}'")
                continue
            if mapped in ("strike", "premium", "sell_premium", "num_lots", "lot_size"):
                m = re.search(r"\d+(?:\.\d+)?", val)
                if m:
                    fields[mapped] = float(m.group())
                else:
                    warnings.append(f"could not read a number for '{key.strip()}'")
            elif mapped in ("buy_date", "sell_date"):
                dm = _DATE_RE.search(val)
                parsed = _parse_date_token(*dm.groups(), default_year) if dm else None
                if parsed:
                    fields[mapped] = parsed
                else:
                    warnings.append(f"could not read date for '{key.strip()}'")
            elif mapped == "option_type":
                v = val.upper()
                if v in ("CE", "PE"):
                    fields[mapped] = v
                else:
                    warnings.append(f"option type must be CE or PE, got '{val}'")
            elif mapped == "action":
                fields[mapped] = "buy" if val.lower().startswith("b") else "sell"
            elif mapped == "expiry_month":
                mon = MONTHS.get(val.lower().strip())
                if mon:
                    fields[mapped] = mon
                else:
                    warnings.append(f"unrecognised month '{val}'")
            else:
                fields[mapped] = val.upper() if mapped == "symbol" else val

    # free-token grammar fills whatever key:value did not provide
    tokens = raw.replace(",", " ").split()
    upper = raw.upper()
    if "option_type" not in fields:
        m = re.search(r"\b(CE|PE)\b", upper)
        if m:
            fields["option_type"] = m.group(1)
    if "strike" not in fields:
        m = re.search(r"strike\s*:?\s*(\d+(?:\.\d+)?)", raw, re.I) or \
            re.search(r"(\d{3,6}(?:\.\d+)?)\s+(?:CE|PE)\b", raw, re.I)
        if m:
            fields["strike"] = float(m.group(1))
    premium_hits = re.findall(r"premium\s*:?\s*(\d+(?:\.\d+)?)", raw, re.I)
    if "premium" not in fields and premium_hits:
        fields["premium"] = float(premium_hits[0])
    # "bought ... premium A ... sold ... premium B" -> second number is the exit
    if ("sell_premium" not in fields and len(premium_hits) >= 2
            and re.search(r"\bsold\b", raw, re.I)):
        fields["sell_premium"] = float(premium_hits[1])
    if "expiry_month" not in fields:
        m = re.search(r"([a-z]{3,9})\.?\s+expiry", raw, re.I) or \
            re.search(r"expiry\s+([a-z]{3,9})", raw, re.I)
        if m and MONTHS.get(m.group(1).lower()):
            fields["expiry_month"] = MONTHS[m.group(1).lower()]
        else:
            # a bare month token counts only when it is NOT part of a
            # day-month date like "23 July"
            date_spans = [mm.span() for mm in _DATE_RE.finditer(raw)]
            for mm in re.finditer(r"[a-z]{3,9}", raw, re.I):
                mon = MONTHS.get(mm.group().lower())
                inside_date = any(s <= mm.start() < e for s, e in date_spans)
                if mon and not inside_date:
                    fields["expiry_month"] = mon
                    break
    if "buy_date" not in fields:
        m = re.search(r"(?:bought|buy)\s+(?:on\s+)?" + _DATE_RE.pattern, raw, re.I)
        if m:
            parsed = _parse_date_token(m.group(1), m.group(2), m.group(3), default_year)
            if parsed:
                fields["buy_date"] = parsed
    if "sell_date" not in fields:
        m = re.search(r"(?:sold|sell)\s+(?:on\s+)?" + _DATE_RE.pattern, raw, re.I)
        if m:
            parsed = _parse_date_token(m.group(1), m.group(2), m.group(3), default_year)
            if parsed:
                fields["sell_date"] = parsed
    if "symbol" not in fields:
        for tok in tokens:
            t = tok.strip(".,:").upper()
            if (t.isalpha() and 2 <= len(t) <= 12 and t.lower() not in _NOISE_WORDS
                    and t.lower() not in MONTHS):
                fields["symbol"] = t
                break
        if "symbol" not in fields:
            warnings.append("could not identify the stock symbol")

    if "option_type" not in fields:
        warnings.append("could not identify CE/PE")
    if "premium" not in fields:
        warnings.append("could not identify the premium")
    return fields, warnings


# ============================================================ expiry handling
def last_tuesday_of_month(year: int, month: int) -> dt.date:
    """NSE monthly F&O expiry day (last Tuesday, post-Sep-2025 regime),
    NOT holiday-adjusted — callers must present it as an unverified suggestion."""
    last_day = calendar.monthrange(year, month)[1]
    d = dt.date(year, month, last_day)
    while d.weekday() != 1:  # Tuesday
        d -= dt.timedelta(days=1)
    return d


def resolve_expiry(expiry_month: int | None, year: int,
                   nse_expiries: list[dt.date] | None,
                   manual_date: dt.date | None) -> tuple[dt.date | None, str, list[str]]:
    """(date, badge, warnings). Analysis may run only on Confirmed/Manual."""
    warnings: list[str] = []
    if manual_date is not None:
        return manual_date, EXPIRY_MANUAL, warnings
    if nse_expiries and expiry_month:
        for e in sorted(nse_expiries):
            if e.month == expiry_month and e.year == year:
                return e, EXPIRY_CONFIRMED, warnings
        warnings.append("no NSE expiry found for the selected month — select manually")
        return None, EXPIRY_SUGGESTION, warnings
    if expiry_month:
        warnings.append(
            "Expiry date could not be confirmed (no live NSE data; the holiday "
            "calendar cannot verify the calculated date). Please confirm or "
            "select the expiry manually.")
        return last_tuesday_of_month(year, expiry_month), EXPIRY_SUGGESTION, warnings
    warnings.append("no expiry month or date provided")
    return None, EXPIRY_SUGGESTION, warnings


# ============================================================== option maths
def moneyness(option_type: str, spot: float, strike: float) -> str:
    diff_pct = (strike - spot) / spot * 100
    if abs(diff_pct) <= ATM_BAND_PCT:
        return "ATM"
    if option_type == "CE":
        return "OTM" if diff_pct > 0 else "ITM"
    return "OTM" if diff_pct < 0 else "ITM"


def breakeven(option_type: str, strike: float, premium: float) -> float:
    return strike + premium if option_type == "CE" else strike - premium


def required_move_pct(option_type: str, spot: float, be: float) -> float:
    move = (be - spot) / spot * 100 if option_type == "CE" else (spot - be) / spot * 100
    return max(0.0, round(move, 2))


def expected_move_pct(atr_pct_daily: float | None, dte: int | None,
                      iv_annual_pct: float | None = None) -> float | None:
    """Approximate expected move to expiry. IV-based when a real IV exists,
    else ATR-based; both labelled approximate by the caller."""
    if not dte or dte <= 0:
        return None
    if iv_annual_pct and iv_annual_pct > 0:
        return round(iv_annual_pct * math.sqrt(dte / 365.0), 2)
    if atr_pct_daily and atr_pct_daily > 0:
        return round(atr_pct_daily * math.sqrt(dte), 2)
    return None


def premium_burden(required_pct: float | None, expected_pct: float | None,
                   dte: int | None) -> tuple[str | None, float | None]:
    """(rating, ratio). Burden = how much the trade must overcome — NOT a
    cheap/expensive valuation claim (no IV history exists to support one)."""
    if required_pct is None or not expected_pct:
        return None, None
    ratio = required_pct / expected_pct if expected_pct else None
    if ratio is None:
        return None, None
    if ratio <= 0.5:
        rating = "Light"
    elif ratio <= 1.0:
        rating = "Moderate"
    elif ratio <= 1.75:
        rating = "Heavy"
    else:
        rating = "Very heavy"
    if dte is not None and dte < NEAR_EXPIRY_DAYS and rating != "Very heavy":
        order = ["Light", "Moderate", "Heavy", "Very heavy"]
        rating = order[min(order.index(rating) + 1, 3)]
    return rating, round(ratio, 2)


# ======================================================== layer 1: stock setup
def _zone_view(z) -> ZoneView:
    return ZoneView(
        category=z.category, proximal=z.proximal, distal=z.distal,
        odd_score=z.odd_score, is_fresh=z.is_fresh, times_tested=z.times_tested,
        strength=z.zone_strength, num_base=z.num_base_candles,
        width_pct=abs(z.proximal - z.distal) / z.proximal * 100,
        created_index=z.created_at_index)


def _walk_zone_state(frame: pd.DataFrame, zone: ZoneView) -> tuple[int | None, int]:
    """(days since first touch of the proximal, consecutive closes inside now)."""
    h = frame["High"].to_numpy(float)
    l = frame["Low"].to_numpy(float)
    c = frame["Close"].to_numpy(float)
    demand = zone.category == "demand"
    start = min(zone.created_index + 1, len(frame))
    first_touch = None
    for i in range(start, len(frame)):
        touched = l[i] <= zone.proximal if demand else h[i] >= zone.proximal
        if touched:
            first_touch = i
            break
    lo, hi = min(zone.proximal, zone.distal), max(zone.proximal, zone.distal)
    inside_run = 0
    for close in c[::-1]:
        if lo <= close <= hi:
            inside_run += 1
        else:
            break
    days_since = (len(frame) - 1 - first_touch) if first_touch is not None else None
    return days_since, inside_run


def analyse_stock_setup(frame: pd.DataFrame, direction: str, as_of: dt.date,
                        context: dict | None = None) -> StockSetup:
    """Point-in-time stock analysis. `frame` = daily OHLCV; it is CUT at
    `as_of` here so no future bar can leak in (the no-lookahead property the
    tests pin). `context` may carry: market_regime_up, market_ext_atr,
    sector_line, result_days (int|None), history (stock_findings row dict)."""
    context = context or {}
    cut = frame[[isinstance(ts, pd.Timestamp) and ts.date() <= as_of for ts in frame.index]]
    setup = StockSetup(verdict="Avoid stock setup")
    if cut is None or len(cut) < 60:
        setup.reasons.append("not enough price history at the research date")
        return setup
    c = cut["Close"]
    spot = float(c.iloc[-1])
    ema20 = c.ewm(span=20, adjust=False).mean()
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    tr = pd.concat([cut.High - cut.Low, (cut.High - c.shift()).abs(),
                    (cut.Low - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()

    setup.above_ema20 = bool(spot > ema20.iloc[-1])
    setup.above_sma50 = bool(spot > sma50.iloc[-1]) if np.isfinite(sma50.iloc[-1]) else None
    setup.above_sma200 = bool(spot > sma200.iloc[-1]) if np.isfinite(sma200.iloc[-1]) else None
    setup.momentum_20d_pct = round(float(spot / c.iloc[-21] - 1) * 100, 2) if len(c) > 21 else None
    setup.atr_contracting = bool(atr.iloc[-1] < atr.iloc[-20]) if len(atr) > 20 else None

    zones = detect_zones(cut[["Open", "High", "Low", "Close"]].reset_index(drop=True))

    def _pick_anchor(cands):
        """Anchor preference: (1) a zone CONTAINING the spot — that is the zone
        the trade thesis lives in (highest ODD score wins a tie) — else
        (2) the nearest zone by proximal distance."""
        if not cands:
            return None
        containing = [z for z in cands
                      if min(z.proximal, z.distal) <= spot <= max(z.proximal, z.distal)]
        if containing:
            return max(containing, key=lambda z: (z.odd_score, z.is_fresh))
        return min(cands, key=lambda z: abs(spot - z.proximal))

    demands = [z for z in zones if z.category == "demand"
               and z.distal <= spot * (1 + ZONE_NEAR_PCT / 100)]
    supplies = sorted((z for z in zones if z.category == "supply"
                       and z.proximal >= spot * (1 - ZONE_NEAR_PCT / 100)),
                      key=lambda z: z.proximal)
    picked_demand = _pick_anchor(demands)
    setup.demand = _zone_view(picked_demand) if picked_demand else None
    setup.supply = _zone_view(supplies[0]) if supplies else None

    bull = direction == "bullish"
    anchor = setup.demand if bull else setup.supply
    obstacle = setup.supply if bull else setup.demand

    if anchor:
        setup.days_since_touch, setup.closes_inside = _walk_zone_state(cut, anchor)
        lo, hi = min(anchor.proximal, anchor.distal), max(anchor.proximal, anchor.distal)
        if lo <= spot <= hi:
            setup.price_vs_zone = "inside zone"
        elif (bull and spot > hi) or (not bull and spot < lo):
            setup.price_vs_zone = "beyond proximal (zone behind price)"
        else:
            setup.price_vs_zone = "zone broken" if (
                (bull and spot < lo) or (not bull and spot > hi)) else "near zone"
        confirmed = (spot > anchor.proximal) if bull else (spot < anchor.proximal)
        setup.confirmation_status = ("confirmed close beyond proximal" if confirmed
                                     else "no confirmation close yet")
        setup.support_level = anchor.proximal
        setup.invalidation_level = anchor.distal
    if obstacle:
        setup.resistance_level = obstacle.proximal
        setup.target_level = obstacle.proximal
        if anchor:
            risk = abs(spot - anchor.distal)
            reward = abs(obstacle.proximal - spot)
            setup.rr_to_obstacle = round(reward / risk, 2) if risk > 0 else None

    # volume behaviour on recent zone-side bars
    vol = cut["Volume"]
    vma20 = vol.rolling(20).mean()
    recent_ratio = float((vol.iloc[-5:] / vma20.iloc[-5:]).max()) if np.isfinite(vma20.iloc[-1]) and vma20.iloc[-1] > 0 else None
    setup.bounce_volume_ratio = round(recent_ratio, 2) if recent_ratio else None

    # context lines (informational — never scored; see OOS validation)
    mreg = context.get("market_regime_up")
    mext = context.get("market_ext_atr")
    if mreg is not None:
        setup.market_line = (f"NIFTY {'above' if mreg else 'below'} its 20-EMA"
                             + (f", extension {mext:+.1f} ATR" if mext is not None else ""))
    if context.get("sector_line"):
        setup.sector_line = context["sector_line"]
    hist = context.get("history")
    if hist:
        setup.history_line = (
            f"historical (2y backtest): {hist.get('n_trades', '?')} setups, "
            f"overall {hist.get('overall_exp_r', '?')}R, best: {hist.get('best_setup', '?')}")

    # observed flags for the scores (None = unobserved -> coverage, not risk)
    result_days = context.get("result_days", None)
    setup.flags = {
        "no_confirmation": (setup.confirmation_status == "no confirmation close yet") if anchor else None,
        "price_inside_or_between": (setup.price_vs_zone == "inside zone"
                                    or (anchor is not None and obstacle is not None
                                        and setup.price_vs_zone != "zone broken")) if anchor else None,
        "weak_bounce_volume": (recent_ratio is not None and recent_ratio < 1.5) if recent_ratio is not None else None,
        "stale_touch": (setup.days_since_touch is not None and setup.days_since_touch > STALE_TOUCH_BARS) if anchor else None,
        "habitation": (setup.closes_inside is not None and setup.closes_inside >= 2) if anchor else None,
        "atr_contracting": setup.atr_contracting,
        "no_momentum": (abs(setup.momentum_20d_pct) < 2.0) if setup.momentum_20d_pct is not None else None,
        "result_within_2d": (result_days is not None and result_days <= 2) if result_days is not None else None,
        "slow_stock": (float(hist["overall_exp_r"]) < 0.05) if hist and hist.get("overall_exp_r") not in (None, "?") else None,
        "zone_tested_2plus": (anchor.times_tested >= 2) if anchor else None,
        "obstacle_within_1r": (setup.rr_to_obstacle is not None and setup.rr_to_obstacle < 1.0) if anchor and obstacle else None,
        "counter_ma_trend": (not setup.above_sma200 if bull else setup.above_sma200) if setup.above_sma200 is not None else None,
        "market_extended_for_longs": (bull and mext is not None and mext > 2) if mext is not None else None,
    }

    # ----- verdict (ordered rules) -----
    side = "Bullish" if bull else "Bearish"
    if anchor is None:
        setup.verdict = f"{side} setup weak"
        setup.reasons.append("no live zone found on the trade side within 5% of price")
        return setup
    if setup.price_vs_zone == "zone broken":
        setup.verdict = "Demand zone failed" if bull else f"{side} setup weak"
        setup.reasons.append("price has broken beyond the zone's distal")
        return setup
    if anchor.times_tested >= 2:
        setup.verdict = "Demand zone stale" if bull else f"{side} setup weak"
        setup.reasons.append(f"zone already tested {anchor.times_tested}x")
        return setup
    if setup.flags.get("obstacle_within_1r"):
        setup.verdict = "Supply overhead risk" if bull else "Avoid stock setup"
        setup.reasons.append("opposing zone closer than 1R")
        return setup
    if setup.confirmation_status == "no confirmation close yet":
        setup.verdict = f"{side} setup valid but needs confirmation"
        setup.reasons.append(
            f"price is {setup.price_vs_zone} (zone ODD {anchor.odd_score}, "
            f"{anchor.strength}); no close beyond the proximal yet")
        if setup.closes_inside and setup.closes_inside >= 2:
            setup.reasons.append(
                f"{setup.closes_inside} consecutive closes inside the zone "
                f"(engine invalidates at {HABITATION_LIMIT})")
        return setup
    if setup.flags.get("no_momentum") and setup.flags.get("weak_bounce_volume"):
        setup.verdict = "Sideways / no momentum"
        setup.reasons.append("confirmed, but flat 20-day momentum and no volume expansion")
        return setup
    setup.verdict = f"{side} setup valid"
    setup.reasons.append(
        f"fresh zone (ODD {anchor.odd_score}, {anchor.strength}), confirmation "
        f"close in place" + (f", RR to opposing zone 1:{setup.rr_to_obstacle}"
                             if setup.rr_to_obstacle else ""))
    return setup


# ======================================================= layer 2: option layer
def assess_option(option_type: str, direction: str, spot: float, strike: float,
                  premium: float, dte: int | None, atr_pct_daily: float | None,
                  num_lots: float | None = None, lot_size: float | None = None,
                  chain: dict | None = None,
                  stock_needs_confirmation: bool = False) -> OptionAssessment:
    a = OptionAssessment(verdict="Good option candidate")
    a.moneyness = moneyness(option_type, spot, strike)
    a.strike_distance_pct = round((strike - spot) / spot * 100, 2)
    a.breakeven = round(breakeven(option_type, strike, premium), 2)
    a.required_move_pct = required_move_pct(option_type, spot, a.breakeven)
    a.premium_pct_of_spot = round(premium / spot * 100, 2)
    a.days_to_expiry = dte
    if num_lots and lot_size:
        a.total_quantity = int(num_lots * lot_size)
        a.total_premium = round(premium * a.total_quantity, 2)

    iv = None
    if chain:
        rec = chain.get("strike_record") or {}
        iv = rec.get("iv")
        a.iv, a.oi = iv, rec.get("oi")
        a.chain_volume = rec.get("volume")
        a.bid, a.ask = rec.get("bid"), rec.get("ask")
        if a.bid and a.ask and a.ask > 0:
            a.spread_pct = round((a.ask - a.bid) / a.ask * 100, 1)
    a.expected_move_pct = expected_move_pct(atr_pct_daily, dte, iv)
    a.burden_rating, ratio = premium_burden(a.required_move_pct, a.expected_move_pct, dte)
    a.burden_caption = BURDEN_CAPTION_IV if iv else BURDEN_CAPTION_PROXY

    # ----- verdict (ordered) -----
    reasons = a.reasons
    wrong_side = (option_type == "CE") != (direction == "bullish")
    if wrong_side:
        a.verdict = "Avoid option buying"
        reasons.append(f"a bought {option_type} does not express a {direction} view")
        return a
    if dte is not None and dte <= 0:
        a.verdict = "Expiry too near"
        reasons.append("expiry has passed or is today")
        return a
    if a.moneyness == "OTM" and abs(a.strike_distance_pct) > FAR_OTM_PCT:
        a.verdict = "Strike too far OTM"
        reasons.append(f"strike {abs(a.strike_distance_pct):.1f}% from spot")
    elif dte is not None and dte < NEAR_EXPIRY_DAYS:
        a.verdict = "Expiry too near"
        reasons.append(f"only {dte} days to expiry")
    elif a.burden_rating == "Very heavy":
        a.verdict = "High time decay risk"
        reasons.append(f"required move {a.required_move_pct}% vs expected ~{a.expected_move_pct}%")
    elif a.burden_rating == "Heavy":
        a.verdict = "Acceptable option but needs fast move"
        reasons.append(f"required move {a.required_move_pct}% is close to the expected ~{a.expected_move_pct}%")
    if stock_needs_confirmation and a.verdict in (
            "Good option candidate", "Acceptable option but needs fast move"):
        a.verdict = "Stock setup valid but option not suitable"
        reasons.append("the stock has no confirmation yet — a bought option pays "
                       "time decay while waiting; better for equity/futures until "
                       "momentum confirms")
    if a.spread_pct is not None and a.spread_pct > 10:
        reasons.append(f"wide bid/ask spread ({a.spread_pct}%) — poor liquidity")
        if a.verdict == "Good option candidate":
            a.verdict = "Poor liquidity"
    if not reasons:
        reasons.append(f"{a.moneyness} strike, required move {a.required_move_pct}% "
                       f"vs expected ~{a.expected_move_pct}%, {dte} days to expiry")
    return a


# ================================================================= the scores
_SIDEWAYS_WEIGHTS = [
    ("no_confirmation", 20, "no confirmation close beyond the proximal"),
    ("price_inside_or_between", 15, "price inside the zone / between zones"),
    ("weak_bounce_volume", 15, "no volume expansion on recent bars"),
    ("stale_touch", 10, f"first zone touch more than {STALE_TOUCH_BARS} bars ago"),
    ("habitation", 10, "2+ consecutive closes inside the zone"),
    ("atr_contracting", 5, "ATR contracting"),
    ("no_momentum", 5, "flat 20-day momentum"),
    ("result_within_2d", 10, "results within 2 days"),
    ("slow_stock", 10, "stock historically slow/mediocre at setups"),
]
_TRAP_WEIGHTS = [
    ("zone_tested_2plus", 15, "zone already tested 2+ times", "heuristic"),
    ("obstacle_within_1r", 15, "opposing zone closer than 1R", "heuristic"),
    ("weak_bounce_volume", 10, "no volume on the bounce", "heuristic"),
    ("result_within_2d", 15, "results within 2 days", "validated (T9)"),
    ("market_extended_for_longs", 10, "NIFTY > 2 ATR extended for a long idea", "validated (T13)"),
    ("counter_ma_trend", 10, "against the 200-SMA side", "heuristic"),
    ("habitation", 10, "closes accumulating inside the zone", "heuristic"),
    ("stale_touch", 15, "stale zone touch", "heuristic"),
]


def _score(flags: dict, weights: list) -> tuple[int, str, list[dict], list[str]]:
    total_possible = 0
    earned = 0
    components: list[dict] = []
    missing: list[str] = []
    for row in weights:
        name, pts, label = row[0], row[1], row[2]
        tag = row[3] if len(row) > 3 else None
        observed = flags.get(name)
        if observed is None:
            missing.append(label)
            continue
        total_possible += pts
        if observed:
            earned += pts
        components.append({"flag": label, "observed": bool(observed), "points": pts,
                           **({"basis": tag} if tag else {})})
    score = int(round(earned / total_possible * 100)) if total_possible else 0
    return score, "", components, missing


def _band(score: int, bands: list) -> str:
    for limit, label in bands:
        if score <= limit:
            return label
    return bands[-1][1]


# ============================================================ decomposition
def decompose(option_type: str, strike: float, buy_premium: float,
              sell_premium: float | None, spot_entry: float | None,
              spot_exit: float | None, spot_source: str,
              total_qty: int | None) -> Decomposition:
    if sell_premium is None or spot_entry is None or spot_exit is None:
        return Decomposition(
            kind="descriptive",
            caption="Exact attribution unavailable: needs both premiums and "
                    "reference spot prices.",
            text="The premium change cannot be decomposed without entry/exit "
                 "spot references. Descriptively: if the stock did not move "
                 "beyond the strike, the entire premium change was time value "
                 "(theta and IV combined — these cannot be separated without "
                 "IV history).")
    def intrinsic(spot: float) -> float:
        return max(0.0, spot - strike) if option_type == "CE" else max(0.0, strike - spot)
    kind = "exact" if spot_source == "user" else "daily_close_proxy"
    caption = ("Exact decomposition (spot recorded at the trade timestamps)."
               if kind == "exact" else
               "Daily-close proxy: spot taken from the daily close, not the "
               "trade timestamp — intrinsic/time-value split is approximate.")
    caption += (" Time value combines theta and IV; they cannot be separated "
                "without IV history.")
    ie, ix = intrinsic(spot_entry), intrinsic(spot_exit)
    te, tx = buy_premium - ie, sell_premium - ix
    rows = [
        {"item": "Intrinsic value", "entry": round(ie, 2), "exit": round(ix, 2),
         "change": round(ix - ie, 2)},
        {"item": "Time value (theta + IV, combined)", "entry": round(te, 2),
         "exit": round(tx, 2), "change": round(tx - te, 2)},
        {"item": "Premium", "entry": round(buy_premium, 2),
         "exit": round(sell_premium, 2), "change": round(sell_premium - buy_premium, 2)},
    ]
    if total_qty:
        rows.append({"item": "Total P&L (₹)", "entry": "", "exit": "",
                     "change": round((sell_premium - buy_premium) * total_qty, 2)})
    warn_rows = [r for r in rows[:2] if isinstance(r["entry"], float) and r["entry"] < -0.01]
    if warn_rows:
        caption += " NOTE: a negative time value indicates inconsistent inputs."
    return Decomposition(kind=kind, caption=caption, rows=rows)


# ============================================================== final verdict
def final_verdict(stock: StockSetup, option: OptionAssessment,
                  sideways: int, trap: int, mode: str) -> tuple[str, str]:
    sv, ov = stock.verdict, option.verdict
    if mode == "post":
        return ("POST-TRADE LESSON ONLY",
                "Closed trade reviewed for learning; see the decomposition and "
                "learning sections.")
    if sv in ("Avoid stock setup", "Demand zone failed"):
        return "AVOID", f"stock setup: {sv.lower()}"
    if trap >= 61:
        return "AVOID", f"trap risk {trap}/100"
    if "needs confirmation" in sv:
        # WAIT outranks the generic sideways verdict: it names what would
        # unlock the trade (a confirmation close), which HIGH SIDEWAYS cannot
        return ("WAIT for confirmation — avoid option buying until momentum confirms",
                f"stock: {sv}; option: {ov}; sideways risk {sideways}/100")
    if sideways >= 61 and ov != "Good option candidate":
        return ("HIGH SIDEWAYS RISK — avoid option buying",
                f"sideways risk {sideways}/100 with option verdict: {ov}")
    if sv in ("Sideways / no momentum", "Supply overhead risk", "Demand zone stale"):
        return "WATCH only", f"stock: {sv}"
    if ov in ("Strike too far OTM", "Expiry too near", "High time decay risk",
              "Poor liquidity", "Avoid option buying",
              "Stock setup valid but option not suitable"):
        return ("VALID STOCK SETUP BUT BAD OPTION SETUP",
                f"stock: {sv}; option: {ov} — better for equity/futures than this option")
    if ov == "Acceptable option but needs fast move":
        return "TAKE candidate (needs a fast move)", f"stock: {sv}; option: {ov}"
    return "TAKE candidate", f"stock: {sv}; option: {ov}"


# ================================================================ orchestrator
def analyse(inputs: dict, frame: pd.DataFrame,
            context: dict | None = None) -> LabReport:
    """Pure orchestrator. `inputs` keys: mode, symbol, direction, option_type,
    strike, premium, expiry_date (date|None), expiry_badge, research_date,
    buy_date, sell_date, sell_premium, num_lots, lot_size, spot_at_entry,
    spot_at_exit. `context`: market_regime_up, market_ext_atr, sector_line,
    result_days, history, chain (normalized), result_date_known (bool)."""
    context = context or {}
    mode = inputs.get("mode", "pre")
    as_of = inputs.get("buy_date") or inputs.get("research_date") or dt.date.today()
    direction = inputs.get("direction", "bullish")
    warnings: list[str] = list(inputs.get("warnings") or [])

    stock = analyse_stock_setup(frame, direction, as_of, context)

    cut = frame[[ts.date() <= as_of for ts in frame.index]]
    spot = float(cut["Close"].iloc[-1]) if len(cut) else float("nan")
    tr = pd.concat([cut.High - cut.Low, (cut.High - cut.Close.shift()).abs(),
                    (cut.Low - cut.Close.shift()).abs()], axis=1).max(axis=1)
    atr_pct = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1] / spot * 100) if len(cut) > 15 else None

    expiry = inputs.get("expiry_date")
    dte = (expiry - as_of).days if expiry else None
    if inputs.get("expiry_badge") == EXPIRY_SUGGESTION:
        warnings.append("expiry is an unverified suggestion — analysis needs a "
                        "confirmed or manual expiry date")
    option = assess_option(
        inputs.get("option_type", "CE"), direction, spot,
        float(inputs.get("strike") or 0), float(inputs.get("premium") or 0),
        dte, atr_pct, inputs.get("num_lots"), inputs.get("lot_size"),
        chain=context.get("chain"),
        stock_needs_confirmation="needs confirmation" in stock.verdict,
    )

    # option-side observed flags feeding the scores
    flags = dict(stock.flags)
    flags["far_otm"] = (option.moneyness == "OTM"
                        and abs(option.strike_distance_pct or 0) > FAR_OTM_PCT)
    flags["expiry_near"] = (dte is not None and dte < NEAR_EXPIRY_DAYS) if dte is not None else None
    flags["heavy_burden"] = (option.burden_rating in ("Heavy", "Very heavy")) if option.burden_rating else None

    sideways_weights = _SIDEWAYS_WEIGHTS + [
        ("expiry_near", 5, "expiry within 10 days"),
        ("far_otm", 5, "strike far OTM"),
        ("heavy_burden", 5, "heavy premium burden"),
    ]
    s_score, _, s_comp, s_missing = _score(flags, sideways_weights)
    t_score, _, t_comp, t_missing = _score(flags, _TRAP_WEIGHTS)

    coverage_missing = sorted(set(s_missing) | set(t_missing))
    if not context.get("result_date_known", context.get("result_days") is not None):
        if "result date unknown" not in coverage_missing:
            coverage_missing.append("result date unknown")
            warnings.append("result date unknown — event risk cannot be assessed")
    if context.get("chain") is None:
        coverage_missing.append("live option chain not fetched (IV/OI/spread unavailable)")
    total_checks = len(sideways_weights) + len(_TRAP_WEIGHTS) + 2
    available = total_checks - len(coverage_missing)

    fv, freason = final_verdict(stock, option, s_score, t_score, mode)

    report = LabReport(
        mode=mode, stock=stock, option=option,
        sideways_score=s_score, sideways_band=_band(s_score, SIDEWAYS_BANDS),
        sideways_components=s_comp,
        trap_score=t_score, trap_band=_band(t_score, TRAP_BANDS),
        trap_components=t_comp,
        coverage_available=max(0, available), coverage_total=total_checks,
        coverage_missing=coverage_missing,
        final_verdict=fv, final_reason=freason, warnings=warnings,
        chart_levels={
            "support": stock.support_level, "resistance": stock.resistance_level,
            "invalidation": stock.invalidation_level, "target": stock.target_level,
            "breakeven": option.breakeven,
            "zone_lo": min(stock.demand.proximal, stock.demand.distal) if stock.demand else None,
            "zone_hi": max(stock.demand.proximal, stock.demand.distal) if stock.demand else None,
            "supply_lo": min(stock.supply.proximal, stock.supply.distal) if stock.supply else None,
            "supply_hi": max(stock.supply.proximal, stock.supply.distal) if stock.supply else None,
        },
    )

    if mode == "post":
        spot_entry = inputs.get("spot_at_entry")
        spot_exit = inputs.get("spot_at_exit")
        source = "user"
        if spot_entry is None or spot_exit is None:
            source = "daily_close"
            bd, sd = inputs.get("buy_date"), inputs.get("sell_date")
            if bd is not None:
                sub = frame[[ts.date() <= bd for ts in frame.index]]
                spot_entry = float(sub["Close"].iloc[-1]) if len(sub) else None
            if sd is not None:
                sub = frame[[ts.date() <= sd for ts in frame.index]]
                spot_exit = float(sub["Close"].iloc[-1]) if len(sub) else None
        report.decomposition = decompose(
            inputs.get("option_type", "CE"), float(inputs.get("strike") or 0),
            float(inputs.get("premium") or 0), inputs.get("sell_premium"),
            spot_entry, spot_exit, source, option.total_quantity)
        report.learning = _learning_notes(report, inputs)
    return report


def _learning_notes(report: LabReport, inputs: dict) -> list[str]:
    notes = []
    sv, ov = report.stock.verdict, report.option.verdict
    if "valid" in sv.lower():
        notes.append(f"What was reasonable: the stock setup ({sv}).")
    if "needs confirmation" in sv:
        notes.append("What was risky: entering a bought option before a "
                     "confirmation close — time decay runs while waiting.")
    if ov not in ("Good option candidate",):
        notes.append(f"Option selection issue: {ov}.")
    d = report.decomposition
    if d and d.kind != "descriptive" and len(d.rows) >= 2:
        tv = d.rows[1]["change"]
        iv_ = d.rows[0]["change"]
        notes.append(f"Attribution: intrinsic change {iv_:+.2f}, time value "
                     f"(theta+IV combined) {tv:+.2f} per share.")
    notes.append("Next time: confirmation close + volume before premium; a "
                 "time-stop on bought options (no move in 3-5 sessions = exit); "
                 "verify the result date; check RR to the opposing zone.")
    return notes


# ================================================================ summary text
def build_summary_text(report: LabReport, inputs: dict) -> str:
    lines = [
        f"Options Trade Lab — {inputs.get('symbol', '?')} "
        f"{inputs.get('strike', '?')} {inputs.get('option_type', '?')} "
        f"({report.mode}-trade)",
        f"Stock Setup Verdict: {report.stock.verdict}",
        f"Option Suitability Verdict: {report.option.verdict}",
        f"Final Research Verdict: {report.final_verdict}",
        f"Sideways risk {report.sideways_score}/100 ({report.sideways_band}) | "
        f"Trap risk {report.trap_score}/100 ({report.trap_band}) | "
        f"Premium burden: {report.option.burden_rating or 'n/a'}",
        f"Breakeven {report.option.breakeven} | required move "
        f"{report.option.required_move_pct}% | DTE {report.option.days_to_expiry}",
        f"Data coverage: {report.coverage_available}/{report.coverage_total} checks",
        "Research classification only — not a buy/sell recommendation.",
    ]
    return "\n".join(lines)
