"""In-app notification handler — stores and retrieves alert state."""

from storage.database import create_alert, get_unread_alerts, mark_alert_read, mark_all_alerts_read
from utils.logger import get_logger

logger = get_logger(__name__)


def add_notification(stock_id: int, analysis_type: str, message: str) -> None:
    """Persist a new in-app notification to the database.

    Args:
        stock_id: Database ID of the related stock.
        analysis_type: Analysis type that triggered the notification.
        message: Human-readable notification text.
    """
    try:
        create_alert(stock_id, analysis_type, message)
    except Exception as exc:
        logger.error("Failed to store notification: %s", exc)


def get_pending_notifications() -> list[dict]:
    """Return all unread notifications from the database.

    Returns:
        List of alert dicts with id, stock_id, message, created_at.
    """
    try:
        return get_unread_alerts()
    except Exception as exc:
        logger.error("Failed to retrieve notifications: %s", exc)
        return []


def dismiss_notification(alert_id: int) -> None:
    """Mark a single notification as read.

    Args:
        alert_id: Database ID of the alert to dismiss.
    """
    try:
        mark_alert_read(alert_id)
    except Exception as exc:
        logger.error("Failed to dismiss notification %d: %s", alert_id, exc)


def dismiss_all_notifications() -> None:
    """Mark all notifications as read."""
    try:
        mark_all_alerts_read()
    except Exception as exc:
        logger.error("Failed to dismiss all notifications: %s", exc)
