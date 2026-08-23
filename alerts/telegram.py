"""Telegram delivery — send zone proximity alerts via the Bot API."""

import datetime as dt
import zoneinfo
from typing import Any

import requests

from utils.logger import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_IST = zoneinfo.ZoneInfo("Asia/Kolkata")


def send_telegram_message(bot_token: str, chat_id: str, message: str) -> bool:
    """Send a single HTML-formatted message via the Telegram Bot API.

    Returns True on success, False on any failure (network, auth, invalid
    chat_id). Errors are logged but never raised.
    """
    if not bot_token or not chat_id:
        logger.warning("Cannot send — bot_token or chat_id is empty")
        return False
    try:
        resp = requests.post(
            _API_BASE.format(token=bot_token),
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if resp.ok:
            return True
        logger.warning(
            "Telegram API error for chat %s: %s %s",
            chat_id, resp.status_code, resp.text[:200],
        )
        return False
    except requests.RequestException as exc:
        logger.error("Telegram network error for chat %s: %s", chat_id, exc)
        return False


def send_to_all_recipients(
    bot_token: str,
    recipients: list[dict[str, str]],
    message: str,
) -> dict[str, list[str]]:
    """Send a message to every configured recipient.

    Returns ``{"sent": [...labels], "failed": [...labels]}``.
    """
    sent: list[str] = []
    failed: list[str] = []
    for recip in recipients:
        label = recip.get("label", recip.get("chat_id", "unknown"))
        ok = send_telegram_message(bot_token, recip.get("chat_id", ""), message)
        (sent if ok else failed).append(label)
    return {"sent": sent, "failed": failed}


def format_zone_alert(
    symbol: str,
    current_price: float,
    zone: dict[str, Any],
    distance_pct: float,
    trend: str = "",
) -> str:
    """Build an HTML-formatted Telegram alert message for a zone approach.

    Uses 📈 for demand alerts and 📉 for supply alerts.
    """
    is_demand = zone.get("category", "demand") == "demand"
    icon = "\U0001f4c8" if is_demand else "\U0001f4c9"
    zone_label = "Demand" if is_demand else "Supply"
    zone_type = zone.get("zone_type", "")
    score = zone.get("odd_score", 0)
    closing = zone.get("closing_quality", "unchecked")
    closing_label = f" | {closing.title()} Close" if closing != "unchecked" else ""
    marking = zone.get("marking", "Normal")
    proximal = zone.get("proximal", 0)
    distal = zone.get("distal", 0)
    trend_line = f"Trend: {trend}\n\n" if trend else "\n"

    now_ist = dt.datetime.now(_IST)
    timestamp = now_ist.strftime("%I:%M %p IST | %b %d, %Y")

    return (
        f"{icon} <b>{symbol}</b> approaching {zone_label} Zone\n\n"
        f"Price: ₹{current_price:,.2f} ({distance_pct:.1f}% from zone)\n"
        f"Zone: {zone_type} | Score {score}{closing_label}\n"
        f"Marking: {marking}\n"
        f"Proximal: ₹{proximal:,.2f}\n"
        f"Distal: ₹{distal:,.2f}\n"
        f"{trend_line}"
        f"⏰ {timestamp}"
    )


def format_test_message() -> str:
    """Build a test message to verify bot connectivity."""
    now_ist = dt.datetime.now(_IST)
    timestamp = now_ist.strftime("%I:%M %p IST | %b %d, %Y")
    return (
        "✅ Market Lens alerts are working!\n\n"
        f"Bot: connected\n"
        f"Time: {timestamp}"
    )


# --------------------------------------------------------------------------- #
# Gap-Up Continuation (Signals) alerts — always "stop loss", never "stop";
# always research-classification language, never buy/sell recommendations.
# --------------------------------------------------------------------------- #

_GAP_DISCLAIMER = "Research classification: TAKE candidate — not a buy/sell recommendation."


def _ist_stamp() -> str:
    return dt.datetime.now(_IST).strftime("%I:%M %p IST | %b %d, %Y")


def format_gap_signal_alert(symbol: str, sig, tracked, late: bool,
                            volume_ok: bool = False,
                            result_days: int | None = None) -> str:
    """Confirmed Gap-Up Continuation signal (fresh after the close, or a
    late catch-up once the entry session has already begun)."""
    sig_date = str(getattr(sig, "date", ""))[:10]
    tags = []
    if volume_ok:
        tags.append("volume ✓")
    if result_days is not None:
        tags.append(f"results in {result_days}d ⚠")
    tags_line = f"Tags: {' · '.join(tags)}\n" if tags else ""
    target = tracked.target if tracked.target is not None else sig.target_2r()

    if late and tracked.entry_price is not None:
        entry_line = (f"Entry (already occurred, {str(tracked.entry_date)[:10]} open): "
                      f"₹{tracked.entry_price:,.2f}\n")
        header = f"📈 <b>Gap-Up Continuation — LATE ALERT</b> (signal {sig_date})"
        status_line = (f"Current status: {tracked.status.replace('_', ' ')}"
                       + (f" ({tracked.r_multiple:+.2f}R)" if tracked.r_multiple is not None else "")
                       + "\n")
    elif late:
        entry_line = "Entry window (the open after the signal day) has passed.\n"
        header = f"📈 <b>Gap-Up Continuation — LATE ALERT</b> (signal {sig_date})"
        status_line = ""
    else:
        entry_line = f"Candidate entry: next session's open (~₹{sig.close:,.2f})\n"
        header = "📈 <b>Gap-Up Continuation (confirmed)</b>"
        status_line = ""

    return (
        f"{header}\n\n"
        f"<b>{symbol}</b> gapped +{sig.gap_pct:.2f}% over the prior high "
        f"and held into the close ({sig_date}).\n\n"
        f"{entry_line}"
        f"Stop loss: ₹{sig.stop:,.2f} (prior-day low − 0.1 ATR)\n"
        f"2R target: ₹{target:,.2f}\n"
        f"{status_line}{tags_line}\n"
        f"{_GAP_DISCLAIMER}\n"
        f"{_ist_stamp()}"
    )


def format_gap_touch_alert(entry: dict, event: str, price: float) -> str:
    """Provisional intraday touch of the stop loss or the 2R target."""
    symbol = entry.get("symbol", "")
    if event == "stop_loss":
        head = "🔴 <b>Stop loss touched (intraday, provisional)</b>"
        level = f"Stop loss: ₹{entry.get('stop', 0):,.2f}"
    else:
        head = "🟢 <b>2R target touched (intraday, provisional)</b>"
        level = f"2R target: ₹{entry.get('target', 0):,.2f}"
    return (
        f"{head}\n\n"
        f"<b>{symbol}</b> (gap signal {entry.get('signal_date', '')}) "
        f"traded at ₹{price:,.2f}.\n"
        f"{level} · entry was ₹{entry.get('entry_price') or 0:,.2f}\n\n"
        f"End-of-day confirmation follows (5-minute polling can lag brief "
        f"moves). Research tracking — not a buy/sell recommendation.\n"
        f"{_ist_stamp()}"
    )


def format_gap_resolution_alert(symbol: str, sig, tracked) -> str:
    """Authoritative end-of-day resolution from the tracker walk."""
    labels = {
        "target_hit": ("🟢", "2R target hit"),
        "stop_loss_hit": ("🔴", "Stop loss hit"),
        "time_stopped": ("⚪", "Time-stopped (20 sessions)"),
    }
    icon, label = labels.get(tracked.status, ("ℹ️", tracked.status))
    r = f"{tracked.r_multiple:+.2f}R" if tracked.r_multiple is not None else ""
    return (
        f"{icon} <b>Gap signal resolved: {label}</b>\n\n"
        f"<b>{symbol}</b> (signal {str(getattr(sig, 'date', ''))[:10]}, "
        f"entry ₹{tracked.entry_price or 0:,.2f} on {str(tracked.entry_date)[:10]})\n"
        f"Exit: ₹{tracked.exit_price or 0:,.2f} on {str(tracked.exit_date)[:10]} "
        f"→ <b>{r}</b> in {tracked.days_active} session(s).\n\n"
        f"Confirmed on completed bars (matches the Signals page and the "
        f"backtest exactly). Research tracking — not a buy/sell recommendation.\n"
        f"{_ist_stamp()}"
    )
