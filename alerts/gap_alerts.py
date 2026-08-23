"""Gap-Up Continuation Telegram alerts (Signals V1.1).

Four alert kinds, all driven by the production detector/tracker in
analysis.gap_signals (the single source shared with the Signals page and the
research harness):

  1. Confirmed-signal alerts after the close (EOD pass, 16:00 IST onwards).
  2. Session-keyed catch-up: if the machine was off at 16:00, the pass runs
     whenever the monitor next looks — evenings, next morning, or mid-session
     — for sessions up to CATCHUP_SESSIONS old, with late alerts labelled.
  3. Intraday stop-loss / 2R-target touch alerts (5-minute quote polling over
     the active-signal registry; provisional, deduped).
  4. Authoritative EOD resolutions: the exact tracker walk on completed bars
     reports status transitions (target hit / stop loss hit / time-stopped),
     catching anything intraday polling missed.

Today's bar is acquired through a three-source chain (Gotcha 17 spirit):
Yahoo daily -> synthesized from Yahoo intraday -> NSE bhavcopy.

State lives in config/alert_config.json: dated scan guards and per-signal
status keys in alert_history, plus the active-signal registry under
"gap_registry". Pure logic is kept in small testable functions; network and
persistence sit at the edges.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from analysis.gap_signals import (
    STATUS_ACTIVE, STATUS_AWAITING, STATUS_STOP, STATUS_TARGET, STATUS_TIME,
    EXCLUDED_SYMBOLS, detect_gap_up_continuation, risk_ok, track_signal,
    volume_expansion_tag,
)
from utils.logger import get_logger
from utils.market_hours import (
    get_current_ist_time, is_trading_day, last_completed_session,
    recent_trading_sessions,
)

logger = get_logger(__name__)

EOD_HOUR_IST = 16          # today's daily bar counts as complete from 16:00 IST
CATCHUP_SESSIONS = 2       # how many missed sessions get late alerts
SIGNAL_LOOKBACK_BARS = 60  # tracking window, same as the Signals page
REGISTRY_KEY = "gap_registry"

_TERMINAL = {STATUS_TARGET, STATUS_STOP, STATUS_TIME}


def gap_alerts_enabled(config: dict) -> bool:
    return bool(config.get("enabled")) and bool(
        config.get("conditions", {}).get("gap_alerts", True))


# --------------------------------------------------------------- pure helpers
def scan_guard_key(session: dt.date) -> str:
    return f"gapscan_{session.isoformat()}"


def signal_key(symbol: str, signal_date: str) -> str:
    return f"gapsig_{symbol}_{signal_date}"


def touch_key(symbol: str, signal_date: str, event: str) -> str:
    return f"gaptouch_{symbol}_{signal_date}_{event}"


def next_trading_session(session: dt.date) -> dt.date:
    cursor = session
    for _ in range(15):
        cursor = cursor + dt.timedelta(days=1)
        if is_trading_day(cursor):
            return cursor
    return cursor


def is_late(session: dt.date, now: dt.datetime) -> bool:
    """A signal alert is LATE once the entry session (next open) has begun."""
    nxt = next_trading_session(session)
    entry_open = dt.datetime.combine(nxt, dt.time(9, 15), tzinfo=now.tzinfo)
    return now >= entry_open


def pending_sessions(history: dict, now: dt.datetime) -> list[dt.date]:
    """Completed sessions (newest last) that still need an EOD pass.

    Today's session only qualifies from 16:00 IST (the bar is forming before
    that). Older unscanned sessions are capped at CATCHUP_SESSIONS.
    """
    last = last_completed_session(now)
    sessions = recent_trading_sessions(CATCHUP_SESSIONS + 1, end=last)
    out = []
    for s in sessions:
        if scan_guard_key(s) in history:
            continue
        if s == now.date() and now.hour < EOD_HOUR_IST:
            continue
        out.append(s)
    return out


# --------------------------------------------------------- bar acquisition
def _ist_date(ts) -> dt.date:
    ts = pd.Timestamp(ts)
    return (ts.tz_convert("Asia/Kolkata") if ts.tzinfo else ts).date()


def synthesize_daily_bar(intraday: pd.DataFrame, session: dt.date) -> dict | None:
    """Today's OHLC(V) from intraday bars (Yahoo intraday updates before daily)."""
    if intraday is None or intraday.empty:
        return None
    mask = [(_ist_date(ts) == session) for ts in intraday.index]
    day = intraday.loc[mask]
    if day.empty:
        return None
    return {
        "Open": float(day["Open"].iloc[0]),
        "High": float(day["High"].max()),
        "Low": float(day["Low"].min()),
        "Close": float(day["Close"].iloc[-1]),
        "Volume": float(day["Volume"].sum()) if "Volume" in day.columns else 0.0,
    }


def acquire_daily_frame(manager, symbol: str, session: dt.date,
                        full_symbol: str) -> tuple[pd.DataFrame, str] | None:
    """Daily history ENDING AT `session`, via daily -> intraday -> bhavcopy."""
    from data.manager import fetch_by_interval

    df, _meta = fetch_by_interval(full_symbol, "Daily", fetch_fn=manager.get_history)
    if df is None or len(df) < 30:
        return None
    # trim anything after the target session (a forming bar next day, etc.)
    keep = [(_ist_date(ts) <= session) for ts in df.index]
    df = df.loc[keep]
    if df.empty:
        return None
    if _ist_date(df.index[-1]) == session:
        return df, "daily"
    # 2) synthesize from intraday
    try:
        intraday = manager.get_history(full_symbol, "5d", "15m")
        bar = synthesize_daily_bar(intraday, session)
    except Exception:  # noqa: BLE001
        bar = None
    source = "intraday"
    if bar is None:
        # 3) NSE bhavcopy (official EOD file; OHLC only)
        try:
            from data.nse_bhavcopy import fetch_eod_ohlc
            ohlc = fetch_eod_ohlc(symbol, session)
        except Exception:  # noqa: BLE001
            ohlc = None
        if not ohlc:
            return None
        bar = {"Open": ohlc["open"], "High": ohlc["high"], "Low": ohlc["low"],
               "Close": ohlc["close"], "Volume": 0.0}
        source = "bhavcopy"
    row = pd.DataFrame([bar], index=pd.DatetimeIndex(
        [pd.Timestamp(session)], name=df.index.name))
    if df.index.tz is not None:
        row.index = row.index.tz_localize(df.index.tz)
    return pd.concat([df, row]), source


# ------------------------------------------------------------------ EOD pass
@dataclass
class EodResult:
    # each message is paired with ITS OWN history keys so the caller can flush
    # per successful send (a failed send leaves its key unrecorded -> retried)
    messages: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    guard_updates: dict[str, str] = field(default_factory=dict)
    registry: list[dict] = field(default_factory=list)
    sessions_done: list[dt.date] = field(default_factory=list)
    bars_seen: int = 0
    symbols_scanned: int = 0


def run_eod_pass(config: dict, manager, stocks: list, now: dt.datetime | None = None,
                 earnings: dict | None = None) -> EodResult | None:
    """Scan pending sessions, build alert messages and the new registry.

    Pure apart from data fetching: persistence and sending are the caller's.
    Returns None when nothing is pending.
    """
    from alerts.telegram import (format_gap_resolution_alert,
                                 format_gap_signal_alert)

    now = now or get_current_ist_time()
    history = config.get("alert_history", {})
    sessions = pending_sessions(history, now)
    if not sessions:
        return None
    result = EodResult()
    newest = sessions[-1]

    for stock in stocks:
        symbol = getattr(stock, "symbol", None) or str(stock)
        if symbol in EXCLUDED_SYMBOLS:
            continue
        result.symbols_scanned += 1
        full_symbol = f"{symbol}.NS"
        try:
            got = acquire_daily_frame(manager, symbol, newest, full_symbol)
            if got is None:
                continue
            df, _source = got
            result.bars_seen += 1
            keep_from = max(0, len(df) - SIGNAL_LOOKBACK_BARS)
            for sig in detect_gap_up_continuation(df):
                if sig.i < keep_from:
                    continue
                tracked = track_signal(df, sig)
                if tracked is None:
                    continue
                sig_date = pd.Timestamp(sig.date)
                sig_date_str = str(sig_date.date())
                skey = signal_key(symbol, sig_date_str)
                prev = history.get(skey) or result.guard_updates.get(skey)

                # (1) new confirmed signal on a pending session
                if prev is None and _ist_date(sig.date) in sessions:
                    late = is_late(_ist_date(sig.date), now)
                    result.messages.append((format_gap_signal_alert(
                        symbol=symbol, sig=sig, tracked=tracked, late=late,
                        volume_ok=volume_expansion_tag(df, sig.i),
                        result_days=_result_days(earnings, symbol, now.date()),
                    ), {skey: tracked.status}))
                # (2) authoritative resolution of a previously-alerted signal
                elif prev is not None and prev not in _TERMINAL and tracked.status in _TERMINAL:
                    result.messages.append((format_gap_resolution_alert(
                        symbol=symbol, sig=sig, tracked=tracked),
                        {skey: tracked.status}))
                elif prev is not None and prev != tracked.status and tracked.status not in _TERMINAL:
                    result.guard_updates[skey] = tracked.status  # silent update

                # registry: everything still open
                if tracked.status in (STATUS_ACTIVE, STATUS_AWAITING):
                    result.registry.append({
                        "symbol": symbol, "signal_date": sig_date_str,
                        "status": tracked.status,
                        "entry_price": tracked.entry_price,
                        "stop": round(sig.stop, 2),
                        "target": tracked.target if tracked.target is not None else sig.target_2r(),
                    })
        except Exception as exc:  # noqa: BLE001
            logger.warning("gap EOD pass failed for %s: %s", symbol, exc)

    # only mark sessions done when data actually arrived; otherwise the next
    # cycle retries (Yahoo lag -> intraday -> bhavcopy usually resolves fast)
    if result.bars_seen >= max(1, result.symbols_scanned // 4):
        for s in sessions:
            result.guard_updates[scan_guard_key(s)] = "done"
        result.sessions_done = sessions
    return result


def _result_days(earnings: dict | None, symbol: str, as_of: dt.date) -> int | None:
    if not earnings or symbol not in earnings:
        return None
    raw = (earnings.get(symbol) or {}).get("result_date")
    if not raw:
        return None
    try:
        delta = (dt.date.fromisoformat(str(raw)[:10]) - as_of).days
    except ValueError:
        return None
    return delta if 0 <= delta <= 30 else None


# -------------------------------------------------------------- intraday pass
def run_touch_pass(config: dict, quote_fn: Callable[[str], dict],
                   now: dt.datetime | None = None) -> list[tuple[str, dict[str, str]]]:
    """Provisional stop-loss / target touch alerts from live quotes.

    quote_fn(symbol) -> quote dict with a 'price' key (the monitor's manager
    provides this). Deduped per signal+event; skips signals already terminal.
    Returns (message, history_updates) pairs for per-send flushing.
    """
    from alerts.telegram import format_gap_touch_alert

    now = now or get_current_ist_time()
    history = config.get("alert_history", {})
    out: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()
    for entry in config.get(REGISTRY_KEY, []) or []:
        symbol = entry.get("symbol")
        sig_date = entry.get("signal_date")
        if not symbol or not sig_date or entry.get("status") != STATUS_ACTIVE:
            continue
        skey = signal_key(symbol, sig_date)
        if history.get(skey) in _TERMINAL:
            continue
        try:
            quote = quote_fn(symbol) or {}
            price = quote.get("price")
        except Exception:  # noqa: BLE001
            continue
        if not price or price <= 0:
            continue
        event = None
        if price <= entry.get("stop", 0):
            event = "stop_loss"
        elif entry.get("target") and price >= entry["target"]:
            event = "target"
        if event is None:
            continue
        tkey = touch_key(symbol, sig_date, event)
        if tkey in history or tkey in seen:
            continue
        seen.add(tkey)
        out.append((format_gap_touch_alert(entry=entry, event=event,
                                           price=float(price)),
                    {tkey: now.isoformat(timespec="seconds")}))
    return out


# ------------------------------------------------------------- persistence
def apply_state(history_updates: dict[str, str],
                registry: list[dict] | None = None) -> None:
    """Merge history keys (and optionally replace the registry) on disk.

    Mirrors the monitor's merge-then-save discipline so a concurrent writer
    can only add."""
    from config.alert_settings import load_alert_config, save_alert_config

    cfg = load_alert_config()
    cfg.setdefault("alert_history", {}).update(history_updates)
    if registry is not None:
        cfg[REGISTRY_KEY] = registry
    save_alert_config(cfg)
