"""Alert trigger logic — evaluates analysis results and saves alerts."""

from storage.database import create_alert
from utils.logger import get_logger

logger = get_logger(__name__)


def check_and_trigger_alerts(
    stock,
    analysis_result: dict,
    alerts_on: bool,
) -> None:
    """Evaluate an analysis result and create an alert if warranted.

    Only fires when alerts are enabled. Creates database alerts for
    bullish and bearish signals; ignores neutral results.

    Args:
        stock: Stock dataclass instance with id and symbol attributes.
        analysis_result: Dict returned by an analysis module.
        alerts_on: Whether the alerts toggle is enabled.
    """
    if not alerts_on:
        return

    status = analysis_result.get("status", "neutral")
    if status == "neutral":
        return

    symbol = analysis_result.get("symbol", stock.symbol)
    summary = analysis_result.get("summary", "")
    analysis_type = analysis_result.get("analysis_type", "")
    price = analysis_result.get("current_price", 0.0)

    if status == "bullish":
        message = f"BULLISH signal for {symbol} @ ₹{price:,.2f} — {summary}"
    else:
        message = f"BEARISH signal for {symbol} @ ₹{price:,.2f} — {summary}"

    try:
        create_alert(stock.id, analysis_type, message)
        logger.info("Alert created: %s", message)
    except Exception as exc:
        logger.error("Failed to create alert for %s: %s", symbol, exc)
