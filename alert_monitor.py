"""Background alert monitor — checks zone proximity every 5 minutes during market hours.

Usage:
    cd /home/gongati/projects/market-lens
    source venv/bin/activate
    python alert_monitor.py

Runs standalone (not inside Streamlit). Reuses the same zone engine
code as the app — no detection or scoring logic is duplicated.
"""

import os
import signal
import sys
import time
from datetime import date, datetime, timedelta

from analysis.demand_supply import DemandSupplyAnalysis
from alerts import gap_alerts
from alerts.telegram import format_zone_alert, send_to_all_recipients
from alerts.zone_alert_checker import AlertMatch
from config.alert_settings import load_alert_config, save_alert_config
from data.manager import DataSourceManager, fetch_for_trading_type
from utils.helpers import get_nse_batch_stocks, load_predefined_watchlists
from utils.market_hours import get_current_ist_time, is_market_open, is_trading_day
from watchlist.manager import get_all_watchlists, get_stocks

_CHECK_INTERVAL_SEC = 300  # 5 minutes
_shutdown = False


def _handle_shutdown(signum, frame):
    """Handle Ctrl+C gracefully."""
    global _shutdown
    _shutdown = True
    _log("Shutdown requested, finishing current cycle...")


def _log(msg: str) -> None:
    """Print a timestamped log line."""
    now = get_current_ist_time()
    ts = now.strftime("%H:%M IST")
    print(f"[{ts}] {msg}")


def _resolve_stock_list(config: dict) -> list[str]:
    """Build the list of stock symbols to monitor from config."""
    source = config.get("conditions", {}).get("stocks_source", "watchlist")

    if source == "custom":
        return config.get("conditions", {}).get("custom_stocks", [])

    if source == "fno":
        wls = load_predefined_watchlists()
        fno = next((w for w in wls if "F&O" in w.get("name", "")), None)
        return fno["symbols"] if fno else []

    if source == "all_nse":
        return [s["symbol"] for s in get_nse_batch_stocks(0, 200)]

    # Default: "watchlist" — use all stocks from all user watchlists
    symbols: list[str] = []
    try:
        for wl in get_all_watchlists():
            for stock in get_stocks(wl.id):
                if stock.symbol not in symbols:
                    symbols.append(stock.symbol)
    except Exception:
        pass
    return symbols


def _check_cooldown(
    symbol: str, proximal: float, cooldown: str, history: dict
) -> bool:
    """Return True if this zone has already been alerted (should skip)."""
    if cooldown == "every_approach":
        return False
    if cooldown == "once_per_zone_per_day":
        key = f"{symbol}_{proximal}_{date.today().isoformat()}"
    else:
        key = f"{symbol}_{proximal}"
    return key in history


def _record_alert(
    symbol: str, proximal: float, cooldown: str, history: dict
) -> None:
    """Mark this zone as alerted in the history dict."""
    if cooldown == "every_approach":
        return
    if cooldown == "once_per_zone_per_day":
        key = f"{symbol}_{proximal}_{date.today().isoformat()}"
    else:
        key = f"{symbol}_{proximal}"
    history[key] = datetime.now().isoformat()


def _flush_history(history: dict) -> None:
    """Write the alert history to disk, MERGING rather than overwriting.

    Re-reads the config first and merges, instead of saving the copy this
    process loaded at cycle start. Two monitors running at once would
    otherwise each write back their own stale snapshot, and whichever
    finished last would erase the other's alerts entirely — silent history
    loss with no error anywhere. The single-instance lock in :func:`main`
    should prevent that pairing, but a merge costs nothing and means a
    concurrent writer can only ever add.
    """
    try:
        current = load_alert_config() or {}
        merged = dict(current.get("alert_history") or {})
        merged.update(history)
        current["alert_history"] = merged
        save_alert_config(current)
        # Keep the in-memory copy in step so the cooldown check sees whatever
        # the other writer added.
        history.update(merged)
    except Exception as exc:
        _log(f"  Could not persist alert history: {exc}")


def _run_cycle(config: dict) -> int:
    """Run one check cycle. Returns number of alerts sent."""
    tg = config.get("telegram", {})
    bot_token = tg.get("bot_token", "")
    recipients = tg.get("recipients", [])
    cond = config.get("conditions", {})
    proximity_pct = cond.get("proximity_pct", 1.0)
    min_score = cond.get("min_score", 6.0)
    zone_type_filter = cond.get("zone_type", "both")
    cooldown = cond.get("cooldown", "once_per_zone_per_day")
    history = config.get("alert_history", {})

    symbols = _resolve_stock_list(config)
    if not symbols:
        _log("No stocks to monitor.")
        return 0

    _log(f"Checking {len(symbols)} stocks...")

    ds = DataSourceManager()
    ds.switch_source("Yahoo Finance")
    analyser = DemandSupplyAnalysis()
    alerts_sent = 0

    for symbol in symbols:
        if _shutdown:
            break
        try:
            fetch_symbol = f"{symbol}.NS"
            hist, _ = fetch_for_trading_type(
                fetch_symbol, "Short-term Trading", fetch_fn=ds.get_history
            )
            if hist is None or hist.empty:
                continue

            result = analyser.analyse(fetch_symbol, hist, use_fibonacci=False)
            price = result.get("current_price", 0)
            if not price or price <= 0:
                continue
            trend = result.get("trend", "")

            for zone_key, category in (
                ("nearest_demand", "demand"),
                ("nearest_supply", "supply"),
            ):
                if zone_type_filter == "demand" and category != "demand":
                    continue
                if zone_type_filter == "supply" and category != "supply":
                    continue

                zone = result.get(zone_key)
                if not zone or not zone.get("proximal"):
                    continue

                score = zone.get("odd_score", 0)
                if score < min_score:
                    continue

                proximal = zone["proximal"]
                if category == "demand":
                    distance = (price - proximal) / proximal * 100
                else:
                    distance = (proximal - price) / proximal * 100

                if distance > proximity_pct:
                    continue

                if _check_cooldown(symbol, proximal, cooldown, history):
                    continue

                msg = format_zone_alert(
                    symbol, price, zone,
                    distance_pct=round(max(distance, 0), 2),
                    trend=trend,
                )
                result_send = send_to_all_recipients(bot_token, recipients, msg)
                if result_send["sent"]:
                    _record_alert(symbol, proximal, cooldown, history)
                    # Persist immediately, not at the end of the cycle. The
                    # Telegram message is already on the user's phone; leaving
                    # the record in memory until the loop finishes meant the
                    # Alerts page could not show it for minutes — observed as
                    # a 6-minute gap between an APLAPOLLO alert at 07:23 and
                    # the file being written at 07:29.
                    _flush_history(history)
                    alerts_sent += 1
                    _log(f"  Alert: {symbol} {category} zone (Score {score}, "
                         f"{max(distance, 0):.1f}% away)")

        except Exception as exc:
            _log(f"  Error checking {symbol}: {exc}")

    # Persist updated alert history
    config["alert_history"] = history
    save_alert_config(config)

    return alerts_sent


def _gap_send(messages: list[tuple[str, dict]], bot_token: str,
              recipients: list) -> tuple[int, int]:
    """Send (message, history_updates) pairs; flush each key on success only,
    so a failed send stays unrecorded and is retried next cycle."""
    sent = failed = 0
    for msg, updates in messages:
        if _shutdown:
            break
        outcome = send_to_all_recipients(bot_token, recipients, msg)
        if outcome.get("sent"):
            gap_alerts.apply_state(updates)
            sent += 1
        else:
            failed += 1
    return sent, failed


def _gap_eod_pass() -> int:
    """Confirmed-signal + resolution alerts for any unscanned completed
    session (incl. catch-up after downtime). Cheap no-op when nothing pends."""
    config = load_alert_config()
    if not gap_alerts.gap_alerts_enabled(config):
        return 0
    now = get_current_ist_time()
    if not gap_alerts.pending_sessions(config.get("alert_history", {}), now):
        return 0
    tg = config.get("telegram", {})
    bot_token, recipients = tg.get("bot_token", ""), tg.get("recipients", [])
    if not bot_token or not recipients:
        return 0
    symbols = _resolve_stock_list(config)
    if not symbols:
        return 0
    _log(f"Gap signals: end-of-day pass over {len(symbols)} stocks...")
    ds = DataSourceManager()
    ds.switch_source("Yahoo Finance")
    try:
        from data.earnings_calendar import get_earnings
        earnings = get_earnings(symbols, cache_only=True)
    except Exception:  # noqa: BLE001
        earnings = {}
    result = gap_alerts.run_eod_pass(config, ds, symbols, now=now, earnings=earnings)
    if result is None:
        return 0
    sent, failed = _gap_send(result.messages, bot_token, recipients)
    # scan guards persist only when every alert went out (or none existed);
    # otherwise the next cycle rescans and resends just the unrecorded ones
    if failed == 0:
        gap_alerts.apply_state(result.guard_updates, result.registry)
        _log(f"Gap signals: {sent} alert(s) sent, "
             f"{len(result.registry)} active signal(s) in the registry.")
    else:
        gap_alerts.apply_state({}, result.registry)
        _log(f"Gap signals: {failed} send(s) failed — pass will retry.")
    return sent


def _gap_touch_pass(config: dict) -> int:
    """Provisional intraday stop-loss / 2R-target touch alerts (market hours)."""
    if not gap_alerts.gap_alerts_enabled(config):
        return 0
    registry = config.get(gap_alerts.REGISTRY_KEY) or []
    if not registry:
        return 0
    tg = config.get("telegram", {})
    bot_token, recipients = tg.get("bot_token", ""), tg.get("recipients", [])
    if not bot_token or not recipients:
        return 0
    ds = DataSourceManager()
    ds.switch_source("Yahoo Finance")
    messages = gap_alerts.run_touch_pass(config, lambda s: ds.get_quote(f"{s}.NS"))
    sent, _failed = _gap_send(messages, bot_token, recipients)
    return sent


def _wait_for_market_open() -> None:
    """Sleep until the next market open, checking every 60 seconds.

    The gap-signal end-of-day pass runs from inside this loop: after the
    close (16:00 IST onwards) and as catch-up whenever the machine comes
    back from downtime. Its own guards make the call a cheap no-op."""
    while not _shutdown:
        now = get_current_ist_time()
        if is_market_open(now):
            return
        try:
            _gap_eod_pass()
        except Exception as exc:  # noqa: BLE001
            _log(f"Gap signals pass error: {exc}")
        # Find next market open for display
        if is_trading_day(now) and now.hour < 9:
            _log("Market opens today at 9:15 AM IST. Waiting...")
        elif is_trading_day(now) and now.hour >= 16:
            _log("Market closed for today. Waiting for next trading day...")
        else:
            next_day = now + timedelta(days=1)
            while not is_trading_day(next_day):
                next_day += timedelta(days=1)
            _log(f"Next trading day: {next_day.strftime('%A, %b %d')}. Waiting...")
        time.sleep(60)


def _acquire_single_instance_lock():
    """Exit if another monitor is already running.

    The crontab starts this script every weekday, but the script never exits
    on its own — it loops until killed. Each cron run therefore stacked
    another copy on top of the last: two were found running, three days old
    and twelve hours apart, each sending the user a duplicate Telegram
    message for every alert.

    An flock on a file in the app directory is released automatically when
    the process dies, however it dies, so a crashed monitor cannot leave a
    stale lock the way a bare pidfile would.

    Returns the open file handle, which the caller must keep referenced for
    the lifetime of the process — closing it releases the lock.
    """
    import fcntl
    from pathlib import Path

    lock_dir = Path.home() / ".market-lens"
    lock_dir.mkdir(parents=True, exist_ok=True)
    handle = open(lock_dir / "alert_monitor.lock", "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def main() -> None:
    """Entry point — run the alert monitor loop."""
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    lock = _acquire_single_instance_lock()
    if lock is None:
        _log("Another alert monitor is already running — exiting.")
        return

    _log("Market Lens Alert Monitor started")
    config = load_alert_config()

    if not config.get("enabled"):
        _log("Alerts are disabled in settings. Enable them and restart.")
        return
    if not config.get("telegram", {}).get("bot_token"):
        _log("No bot token configured. Add one in Settings and restart.")
        return
    if not config.get("telegram", {}).get("recipients"):
        _log("No recipients configured. Add at least one in Settings and restart.")
        return

    _log("Config loaded. Monitoring started.")

    while not _shutdown:
        now = get_current_ist_time()
        if not is_market_open(now):
            _wait_for_market_open()
            if _shutdown:
                break
            continue

        config = load_alert_config()
        sent = _run_cycle(config)
        # gap signals: intraday touch alerts + next-day catch-up during hours
        try:
            sent += _gap_touch_pass(config)
            sent += _gap_eod_pass()
        except Exception as exc:  # noqa: BLE001
            _log(f"Gap signals pass error: {exc}")

        next_check = get_current_ist_time() + timedelta(seconds=_CHECK_INTERVAL_SEC)
        _log(f"{sent} alert{'s' if sent != 1 else ''} sent. "
             f"Next check: {next_check.strftime('%H:%M')}")

        for _ in range(_CHECK_INTERVAL_SEC):
            if _shutdown:
                break
            time.sleep(1)

    _log("Alert monitor stopped.")


if __name__ == "__main__":
    main()
