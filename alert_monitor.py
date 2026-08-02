"""Background alert monitor — checks zone proximity every 5 minutes during market hours.

Usage:
    cd /home/gongati/projects/market-lens
    source venv/bin/activate
    python alert_monitor.py

Runs standalone (not inside Streamlit). Reuses the same zone engine
code as the app — no detection or scoring logic is duplicated.
"""

import signal
import sys
import time
from datetime import date, datetime, timedelta

from analysis.demand_supply import DemandSupplyAnalysis
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
                    alerts_sent += 1
                    _log(f"  Alert: {symbol} {category} zone (Score {score}, "
                         f"{max(distance, 0):.1f}% away)")

        except Exception as exc:
            _log(f"  Error checking {symbol}: {exc}")

    # Persist updated alert history
    config["alert_history"] = history
    save_alert_config(config)

    return alerts_sent


def _wait_for_market_open() -> None:
    """Sleep until the next market open, checking every 60 seconds."""
    while not _shutdown:
        now = get_current_ist_time()
        if is_market_open(now):
            return
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


def main() -> None:
    """Entry point — run the alert monitor loop."""
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

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
