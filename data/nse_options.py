"""Live NSE option chain via jugaad-data's NSELive — explicit-fetch only.

Never called as a render side effect (the same principle as the earnings
calendar, Gotcha 27): the Options Trade Lab fetches this behind a button.
The call runs on a worker thread with a hard timeout because NSE endpoints
can stall, and failures degrade to {'ok': False} — callers show a warning
and fall back to proxy analysis.

Schema notes (verified against the live endpoint, 2026-08):
- NSE's option-chain-v3 endpoint serves ONE expiry per request. With no
  expiry argument jugaad defaults to the NEAREST expiry, while
  records.expiryDates still lists all of them — so a single call looks
  complete but only carries the front month's strikes. fetch_option_chain
  therefore requests each listed expiry (up to _MAX_EXPIRIES) on one warmed
  session and merges the rows; a per-expiry failure drops that expiry's
  strikes, never the whole chain.
- expiry dates appear as '25-Aug-2026' at records level and '25-08-2026'
  inside strike records — both parsed. The records-level string is what the
  endpoint's expiry parameter expects, so it is passed through verbatim.
- bid/ask are buyPrice1/sellPrice1.
- impliedVolatility is 0 on illiquid strikes -> treated as MISSING.
- lot size is NOT present in chain records (stays user-entered in the lab).
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from utils.logger import get_logger

logger = get_logger(__name__)

# One session warm-up plus up to _MAX_EXPIRIES sequential chain calls share
# this budget, so it is larger than a single-request timeout would be.
_TIMEOUT_SEC = 20
_MAX_EXPIRIES = 3  # NSE lists near/mid/far monthlies for stock options


def _parse_expiry(raw: str) -> dt.date | None:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(str(raw).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _payload_strikes(raw: dict) -> dict[tuple, dict]:
    """Normalized strike records from one per-expiry payload."""
    strikes: dict[tuple, dict] = {}
    for row in raw.get("records", {}).get("data", []):
        for side in ("CE", "PE"):
            rec = row.get(side)
            if not isinstance(rec, dict):
                continue
            expiry = _parse_expiry(rec.get("expiryDate", ""))
            strike = rec.get("strikePrice")
            if expiry is None or strike is None:
                continue
            iv = rec.get("impliedVolatility") or 0
            strikes[(expiry.isoformat(), float(strike), side)] = {
                "ltp": rec.get("lastPrice"),
                "iv": float(iv) if iv else None,   # 0 == NSE's "no quote" -> missing
                "oi": rec.get("openInterest"),
                "volume": rec.get("totalTradedVolume"),
                "bid": rec.get("buyPrice1"),
                "ask": rec.get("sellPrice1"),
            }
    return strikes


def _normalize(payloads: list[dict], fetch_errors: list[str] | None = None) -> dict:
    """Merge one-expiry-per-request payloads into a single chain dict.

    The first payload is the base (default/nearest-expiry request): it
    supplies spot, timestamp and the full expiry listing.
    """
    base_records = payloads[0].get("records", {}) if payloads else {}
    expiries = sorted(e for e in (_parse_expiry(x)
                                  for x in base_records.get("expiryDates", [])) if e)
    strikes: dict[tuple, dict] = {}
    for raw in payloads:
        strikes.update(_payload_strikes(raw))
    covered = sorted({key[0] for key in strikes})
    return {
        "ok": True,
        "error": "",
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "spot": base_records.get("underlyingValue"),
        "chain_timestamp": base_records.get("timestamp", ""),
        "expiries": expiries,
        "covered_expiries": covered,
        "fetch_errors": list(fetch_errors or []),
        "strikes": strikes,
    }


def fetch_option_chain(symbol: str, timeout: int = _TIMEOUT_SEC) -> dict:
    """Fetch and normalize the live chain across listed expiries. Never raises."""
    clean = symbol.upper().replace(".NS", "").strip()

    def _call() -> tuple[list[dict], list[str]]:
        from jugaad_data.nse import NSELive
        live = NSELive()
        base = live.equities_option_chain(clean)
        payloads = [base]
        errors: list[str] = []
        already = {key[0] for key in _payload_strikes(base)}
        for raw_expiry in base.get("records", {}).get("expiryDates", [])[:_MAX_EXPIRIES]:
            parsed = _parse_expiry(raw_expiry)
            if parsed is None or parsed.isoformat() in already:
                continue
            try:
                payloads.append(live.equities_option_chain(clean, expiry=raw_expiry))
            except Exception as exc:  # noqa: BLE001 — one bad expiry must not sink the rest
                errors.append(f"{raw_expiry}: {exc}")
                logger.warning("chain fetch failed for %s expiry %s: %s",
                               clean, raw_expiry, exc)
        return payloads, errors

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            payloads, errors = pool.submit(_call).result(timeout=timeout)
        out = _normalize(payloads, errors)
        if not out["strikes"]:
            return {"ok": False, "error": "empty chain returned", "expiries": [],
                    "strikes": {}, "spot": None}
        return out
    except FuturesTimeout:
        logger.warning("option chain fetch timed out for %s", clean)
        return {"ok": False, "error": f"NSE did not respond within {timeout}s",
                "expiries": [], "strikes": {}, "spot": None}
    except Exception as exc:  # noqa: BLE001
        logger.warning("option chain fetch failed for %s: %s", clean, exc)
        return {"ok": False, "error": str(exc), "expiries": [], "strikes": {},
                "spot": None}


def strike_record(chain: dict, expiry: dt.date, strike: float,
                  option_type: str) -> dict | None:
    """The normalized record for one contract, or None."""
    if not chain or not chain.get("ok"):
        return None
    return chain.get("strikes", {}).get((expiry.isoformat(), float(strike), option_type))
