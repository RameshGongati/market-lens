"""Export analysis results to Excel (.xlsx) and PDF (.pdf)."""

from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)

_EXPORTS_DIR = Path.home() / "market-lens-exports"


def _ensure_exports_dir() -> Path:
    """Create the exports directory if it does not exist."""
    _EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return _EXPORTS_DIR


def export_to_excel(
    results: dict[str, dict[str, Any]],
    watchlist_name: str,
    analysis_type: str,
    alerts: list[dict[str, Any]] | None = None,
) -> Path:
    """Export analysis results to an Excel workbook.

    Creates three sheets:
    - Summary: symbol, status, strength, price, change
    - Details: full per-stock analysis results
    - Alerts: alert history

    Args:
        results: Mapping of symbol → analysis result dict.
        watchlist_name: Name of the watchlist being exported.
        analysis_type: Analysis type that was run.
        alerts: Optional list of alert dicts from the database.

    Returns:
        Path to the generated .xlsx file.

    Raises:
        RuntimeError: If openpyxl is not installed.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError as exc:
        raise RuntimeError(
            "openpyxl not installed. Run: pip install openpyxl"
        ) from exc

    _ensure_exports_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = _EXPORTS_DIR / f"market_lens_{watchlist_name}_{ts}.xlsx"

    wb = openpyxl.Workbook()

    # --- Sheet 1: Summary ---
    ws_summary = wb.active
    ws_summary.title = "Summary"
    _style_header_row(ws_summary, ["Symbol", "Status", "Strength", "Price (₹)", "Change %", "Summary"], openpyxl)

    status_fills = {
        "bullish": PatternFill(start_color="D4EDDA", end_color="D4EDDA", fill_type="solid"),
        "bearish": PatternFill(start_color="F8D7DA", end_color="F8D7DA", fill_type="solid"),
        "neutral": PatternFill(start_color="FFF3CD", end_color="FFF3CD", fill_type="solid"),
    }

    for symbol, r in results.items():
        status = r.get("status", "neutral")
        row = [
            symbol,
            status.capitalize(),
            r.get("strength", "—"),
            r.get("current_price", 0.0),
            r.get("change_pct", 0.0),
            r.get("summary", ""),
        ]
        ws_summary.append(row)
        fill = status_fills.get(status)
        if fill:
            for cell in ws_summary[ws_summary.max_row]:
                cell.fill = fill

    _auto_width(ws_summary, get_column_letter)

    # --- Sheet 2: Details ---
    ws_detail = wb.create_sheet("Details")
    detail_keys = ["symbol", "status", "strength", "current_price", "change_pct",
                   "recommendation", "summary"]
    _style_header_row(ws_detail, [k.replace("_", " ").title() for k in detail_keys], openpyxl)
    for symbol, r in results.items():
        ws_detail.append([str(r.get(k, "")) for k in detail_keys])
    _auto_width(ws_detail, get_column_letter)

    # --- Sheet 3: Alerts ---
    ws_alerts = wb.create_sheet("Alerts")
    _style_header_row(ws_alerts, ["ID", "Stock ID", "Analysis Type", "Message", "Created At"], openpyxl)
    for alert in (alerts or []):
        ws_alerts.append([
            alert.get("id", ""),
            alert.get("stock_id", ""),
            alert.get("analysis_type", ""),
            alert.get("message", ""),
            alert.get("created_at", ""),
        ])
    _auto_width(ws_alerts, get_column_letter)

    wb.save(filename)
    logger.info("Excel exported to %s", filename)
    return filename


def export_to_pdf(
    results: dict[str, dict[str, Any]],
    watchlist_name: str,
    analysis_type: str,
    symbol_filter: str | None = None,
) -> Path:
    """Export analysis results to a PDF report.

    Args:
        results: Mapping of symbol → analysis result dict.
        watchlist_name: Name of the watchlist being exported.
        analysis_type: Analysis type that was run.
        symbol_filter: If given, export only this single stock.

    Returns:
        Path to the generated .pdf file.

    Raises:
        RuntimeError: If reportlab is not installed.
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle,
            Paragraph, Spacer, HRFlowable,
        )
    except ImportError as exc:
        raise RuntimeError(
            "reportlab not installed. Run: pip install reportlab"
        ) from exc

    _ensure_exports_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{symbol_filter}" if symbol_filter else ""
    filename = _EXPORTS_DIR / f"market_lens_{watchlist_name}{suffix}_{ts}.pdf"

    doc = SimpleDocTemplate(str(filename), pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    # Header
    title_style = ParagraphStyle("title", parent=styles["Title"], fontSize=18, spaceAfter=6)
    story.append(Paragraph("📈 Market Lens Analysis Report", title_style))
    story.append(Paragraph(
        f"Watchlist: <b>{watchlist_name}</b> &nbsp;|&nbsp; "
        f"Analysis: <b>{analysis_type}</b> &nbsp;|&nbsp; "
        f"Generated: {datetime.now().strftime('%d %b %Y %H:%M')}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.grey))
    story.append(Spacer(1, 4 * mm))

    filter_results = (
        {symbol_filter: results[symbol_filter]}
        if symbol_filter and symbol_filter in results
        else results
    )

    # Summary table
    if not symbol_filter:
        story.append(Paragraph("Summary", styles["Heading2"]))
        table_data = [["Symbol", "Status", "Strength", "Price (₹)", "Change %"]]
        for sym, r in filter_results.items():
            table_data.append([
                sym,
                r.get("status", "—").capitalize(),
                r.get("strength", "—"),
                f"₹{r.get('current_price', 0):,.2f}",
                f"{r.get('change_pct', 0):+.2f}%",
            ])
        t = Table(table_data, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#343a40")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9fa")]),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t)
        story.append(Spacer(1, 6 * mm))

    # Per-stock details
    for sym, r in filter_results.items():
        status = r.get("status", "neutral")
        strength = r.get("strength", "—")
        color_hex = {"bullish": "#155724", "bearish": "#721c24", "neutral": "#856404"}.get(status, "#000")
        story.append(Paragraph(
            f'<font color="{color_hex}"><b>{sym}</b></font> — {status.upper()} | {strength}',
            styles["Heading3"],
        ))
        price = r.get("current_price", 0.0)
        chg = r.get("change_pct", 0.0)
        story.append(Paragraph(
            f"Price: ₹{price:,.2f} &nbsp; Change: {chg:+.2f}%",
            styles["Normal"],
        ))
        recommendation = r.get("recommendation") or r.get("summary", "")
        if recommendation:
            for line in recommendation.split("\n"):
                if line.strip():
                    story.append(Paragraph(line.strip(), styles["Normal"]))
        story.append(Spacer(1, 4 * mm))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
        story.append(Spacer(1, 3 * mm))

    doc.build(story)
    logger.info("PDF exported to %s", filename)
    return filename


def exports_dir() -> Path:
    """Return the exports directory path."""
    return _EXPORTS_DIR


def _style_header_row(ws, headers: list[str], openpyxl_mod) -> None:
    """Append a bold, dark-background header row to an openpyxl worksheet."""
    from openpyxl.styles import Font, PatternFill, Alignment
    ws.append(headers)
    header_fill = PatternFill(start_color="343A40", end_color="343A40", fill_type="solid")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")


def _auto_width(ws, get_column_letter_fn) -> None:
    """Auto-size column widths based on content."""
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[get_column_letter_fn(col[0].column)].width = min(max_len + 4, 60)
