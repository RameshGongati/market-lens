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
