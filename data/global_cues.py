"""Pre-open global cues for the dashboard's Global Cues card.

Fetches the small set of foreign series the global-influence study proved
useful (see research_engine/output/global_influence/GLOBAL_INFLUENCE_REPORT.md
and commit bead996), and interprets them with the STUDY'S OWN historical hit
rates — never as predictions. Two layers, same discipline as the rest of the
app: `fetch_global_cues()` does network and never raises; everything below it
is pure and testable.

Evidence constants below quote the 10-year study (2016-08..2026-08, ~2,465
NIFTY sessions). The central finding they encode: these cues price the
OPENING GAP and stop predicting after 09:15 (all open-to-close correlations
were within -0.10..+0.05), and domestic events override them.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# Daily series: name -> (yahoo ticker, display label, group)
_DAILY_TICKERS: dict[str, tuple[str, str, str]] = {
    "SP500": ("^GSPC", "S&P 500", "us"),
    "NASDAQ100": ("^NDX", "Nasdaq 100", "us"),
    "US_VIX": ("^VIX", "US VIX", "us"),
    "MSCI_INDIA": ("INDA", "MSCI India (NY)", "us"),
    "NIKKEI": ("^N225", "Nikkei", "asia"),
    "HANGSENG": ("^HSI", "Hang Seng", "asia"),
    "KOSPI": ("^KS11", "Kospi", "asia"),
    "BRENT": ("BZ=F", "Brent", "cmdty"),
    "COPPER": ("HG=F", "Copper", "cmdty"),
    "GOLD": ("GC=F", "Gold", "cmdty"),
    "USDINR": ("USDINR=X", "USD/INR", "cmdty"),
    "DXY": ("DX-Y.NYB", "DXY", "cmdty"),
    "US_10Y": ("^TNX", "US 10Y", "cmdty"),
}

# Asian markets open hours before the NSE; their same-day OPENING print was
# the study's strongest measurable gap signal (corr 0.45-0.58). Their
# previous close is NOT used — the study measured it as noise.
_ASIA_OPEN_GAP = {"NIKKEI", "HANGSENG", "KOSPI"}


# --------------------------------------------------------------------------- #
# Fetch (network — never raises)
# --------------------------------------------------------------------------- #

def fetch_global_cues() -> dict[str, Any]:
    """Raw cue values. Every field can be None; callers must tolerate that."""
    out: dict[str, Any] = {
        "ok": False,
        "as_of": dt.datetime.now(_IST).strftime("%d %b %Y · %H:%M IST"),
        "values": {},
    }
    try:
        import yfinance as yf

        tickers = [t for t, _, _ in _DAILY_TICKERS.values()]
        raw = yf.download(
            tickers=" ".join(tickers), period="5d", interval="1d",
            auto_adjust=False, group_by="ticker", threads=True, progress=False,
        )
        today = dt.datetime.now(_IST).date()
        for name, (ticker, _label, _grp) in _DAILY_TICKERS.items():
            try:
                df = raw[ticker].dropna(subset=["Close"])
            except (KeyError, TypeError):
                continue
            if len(df) < 2:
                continue
            closes = df["Close"]
            entry: dict[str, Any] = {}
            if name == "US_10Y":
                # ^TNX quotes the yield in percent; report basis points.
                entry["prev_change"] = float((closes.iloc[-1] - closes.iloc[-2]) * 100)
                entry["unit"] = "bp"
            else:
                entry["prev_change"] = float(
                    (closes.iloc[-1] / closes.iloc[-2] - 1) * 100)
                entry["unit"] = "%"
            entry["prev_date"] = str(df.index[-1].date())
            if name in _ASIA_OPEN_GAP:
                last = df.iloc[-1]
                if df.index[-1].date() == today and len(df) >= 2:
                    entry["open_gap"] = float(
                        (last["Open"] / closes.iloc[-2] - 1) * 100)
                    # The latest row is TODAY's live session, so prev_change
                    # must come from the two rows before it.
                    if len(closes) >= 3:
                        entry["prev_change"] = float(
                            (closes.iloc[-2] / closes.iloc[-3] - 1) * 100)
                else:
                    entry["open_gap"] = None
            out["values"][name] = entry
        out["ok"] = bool(out["values"])
    except Exception as exc:  # noqa: BLE001 — the card degrades, never breaks
        logger.warning("Global cues fetch failed: %s", exc)
        out["error"] = str(exc)

    # ES futures drift since the US close: the study measured corr ~0.40 with
    # the gap component the US close does not explain.
    try:
        import yfinance as yf

        es = yf.Ticker("ES=F").history(period="5d", interval="60m",
                                       auto_adjust=False)
        sp = out["values"].get("SP500", {})
        if len(es) and sp.get("prev_change") is not None:
            spx = yf.Ticker("^GSPC").history(period="5d", interval="1d",
                                             auto_adjust=False)["Close"]
            if len(spx):
                out["values"]["ES_DRIFT"] = {
                    "prev_change": float((es["Close"].iloc[-1] / spx.iloc[-1] - 1) * 100),
                    "unit": "%",
                    "prev_date": str(es.index[-1]),
                }
    except Exception as exc:  # noqa: BLE001
        logger.debug("ES drift unavailable: %s", exc)
    return out


# --------------------------------------------------------------------------- #
# Interpretation (pure — evidence constants from the 10y study)
# --------------------------------------------------------------------------- #

def _val(raw: dict, name: str, key: str = "prev_change") -> float | None:
    v = (raw.get("values", {}).get(name) or {}).get(key)
    return float(v) if v is not None else None


def classify_gap_bias(sp500: float | None, nikkei_gap: float | None,
                      usvix: float | None) -> tuple[str, str]:
    """(bias, evidence line). Bias is a research classification, not advice.

    Priority order mirrors the study's strength ranking: a VIX panic
    outranks the S&P direction; a fresh Asian print can veto a stale US one.
    """
    if usvix is not None and usvix >= 20:
        return ("bearish", "US VIX spiked 20%+: NIFTY gapped down on 80.7% of "
                           "such days (n=57, avg −0.98%) — and panic gaps are "
                           "the least reliable of all")
    if sp500 is None:
        return ("unknown", "US data unavailable — no gap read")
    if sp500 >= 1.0:
        return ("bullish", "S&P closed ≥ +1%: NIFTY gapped up on 86.8% of such "
                           "days (n=318, avg +0.53%)")
    if sp500 <= -1.0:
        return ("bearish", "S&P closed ≤ −1%: NIFTY gapped down on 71.9% of "
                           "such days (n=263, avg −0.57%)")
    if sp500 >= 0.5 and nikkei_gap is not None and nikkei_gap <= -0.3:
        return ("mixed", "US up but Asia opened weak: gap-up rate fell to "
                         "63.6% on such days (n=33) — fresh Asia can veto a "
                         "stale US close")
    if sp500 >= 0.5:
        if nikkei_gap is not None and nikkei_gap >= 0.3:
            return ("bullish", "US up AND Asia opened up: NIFTY gapped up on "
                               "85.8% of such days (n=471)")
        return ("bullish", "S&P closed ≥ +0.5%: NIFTY gapped up on 81.5% of "
                           "such days (n=702, avg +0.41%)")
    if sp500 <= -0.5:
        return ("bearish", "S&P closed ≤ −0.5%: NIFTY gapped down on 57.7% of "
                           "such days (n=520, avg −0.32%)")
    return ("mixed", "No strong overnight cue — base rates apply (gap-up "
                     "55.8%, gap-down 24.5% of all days)")


def sector_flags(raw: dict) -> list[str]:
    """Sector-level flags at the thresholds the study validated."""
    flags: list[str] = []
    brent = _val(raw, "BRENT")
    if brent is not None and brent >= 3.0:
        flags.append("Crude +3%: OMCs opened lower on 61% of such days "
                     "(avg −0.42%, full day −0.61%); paints and airlines weak; "
                     "energy producers historically flat")
    if brent is not None and brent <= -3.0:
        flags.append("Crude −3%: OMCs gapped up on 82.2% of such days "
                     "(avg +0.77%)")
    copper = _val(raw, "COPPER")
    if copper is not None and copper >= 2.0:
        flags.append("Copper +2%: metal stocks gapped up on 80.6% of such days "
                     "(avg +0.51%)")
    if copper is not None and copper <= -2.0:
        flags.append("Copper −2%: metals opened weak AND historically kept "
                     "falling intraday (avg open-to-close −0.50%)")
    gold = _val(raw, "GOLD")
    if gold is not None and gold >= 2.0:
        flags.append("Gold +2%: jewellery/gold-finance names gapped up on "
                     "67.4% of such days (avg +0.30%)")
    ndx = _val(raw, "NASDAQ100")
    if ndx is not None and ndx <= -1.5:
        flags.append("Nasdaq −1.5%: NIFTY IT opened lower on 68.3% of such "
                     "days (avg −0.63%)")
    if ndx is not None and ndx >= 1.5:
        flags.append("Nasdaq +1.5%: NIFTY IT gapped up on 85.2% of such days "
                     "(avg +0.57%) — the one cue with mild follow-through "
                     "after the open")
    return flags


def risk_flags(raw: dict) -> list[str]:
    flags: list[str] = []
    usvix = _val(raw, "US_VIX")
    if usvix is not None and usvix >= 10:
        flags.append(f"Global risk-off: US VIX +{usvix:.0f}% — India VIX rose "
                     "the same day on 62% of such days; gaps get unreliable, "
                     "ranges widen")
    dxy = _val(raw, "DXY")
    tnx = _val(raw, "US_10Y")
    if dxy is not None and tnx is not None and dxy >= 0.3 and tnx >= 3.0:
        flags.append("DXY and US yields rising together: gap-up rate fell to "
                     "41.5% (n=234) and the FULL DAY tilted negative — the "
                     "one combination the study found with post-open effect")
    return flags


def build_cue_report(raw: dict) -> dict[str, Any]:
    """Everything the card renders, from one raw fetch. Pure."""
    bias, evidence = classify_gap_bias(
        _val(raw, "SP500"),
        _val(raw, "NIKKEI", "open_gap"),
        _val(raw, "US_VIX"),
    )
    groups: dict[str, list[dict]] = {"us": [], "asia": [], "cmdty": []}
    for name, (_t, label, grp) in _DAILY_TICKERS.items():
        entry = raw.get("values", {}).get(name)
        if not entry:
            continue
        row = {"name": name, "label": label,
               "change": entry.get("prev_change"), "unit": entry.get("unit", "%")}
        if name in _ASIA_OPEN_GAP:
            row["open_gap"] = entry.get("open_gap")
        groups[grp].append(row)
    es = raw.get("values", {}).get("ES_DRIFT")
    if es:
        groups["us"].append({"name": "ES_DRIFT", "label": "S&P fut. since close",
                             "change": es.get("prev_change"), "unit": "%"})
    return {
        "ok": bool(raw.get("ok")),
        "as_of": raw.get("as_of", ""),
        "bias": bias,
        "evidence": evidence,
        "groups": groups,
        "sector_flags": sector_flags(raw),
        "risk_flags": risk_flags(raw),
        # The study's central honesty finding, rendered on the card verbatim.
        "caption": ("These cues price the OPEN — after 09:15 they stop "
                    "predicting (10y open-to-close correlations ≈ 0). Domestic "
                    "events (RBI, budget, results, expiry) override global "
                    "cues. Research classifications, not trade advice."),
    }
