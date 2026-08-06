"""Pattern Scanner results page."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
import html
from typing import Any

import pandas as pd
import streamlit as st

from analysis.pattern_models import PatternMatch
from analysis.pattern_scanner import (
    DETECTION_STAGES,
    PATTERN_FAMILIES,
    SUGGESTED_FILTERS,
    TIMEFRAMES,
    TRIANGLE_TYPES,
    apply_result_filters,
    default_settings,
    matches_to_export_rows,
    run_pattern_scan,
)
from storage.database import save_pattern_scan
from ui.components.panels import (
    bias_pill,
    page_slice,
    pagination_bar,
    page_title,
    section_title,
    spacer,
    stat_card,
)
from ui.pages.pattern_common import (
    build_pattern_detail_url,
    pattern_counts,
    resolve_pattern_universe,
)

_PAGE_SIZES = (10, 20, 30, 50, 75, 100)
_SORT_OPTIONS = ["Freshest First", "Confidence", "Apex Proximity", "Breakout Candidates", "Symbol"]


def render_pattern_results() -> None:
    """Render the latest saved Pattern Scanner results."""
    settings = st.session_state.get("pattern_scan_settings") or default_settings()
    _seed_result_filters(settings)
    _render_header(settings)

    if st.session_state.get("pattern_scanning"):
        _run_pending_scan(settings)
        st.rerun()

    matches: list[PatternMatch] = st.session_state.get("pattern_scan_results", []) or []
    if not matches:
        spacer(10)
        st.info("No pattern results yet. Open Pattern Scanner and start a scan.")
        if st.button("Open Pattern Scanner", icon=":material/query_stats:"):
            st.session_state.active_page = "pattern_scanner"
            st.rerun()
        return
    _ensure_pattern_scan_cached(settings, matches)

    _render_summary_cards(matches)
    spacer(8)
    with st.container(border=True):
        _render_filter_row()

    selected_filters = _selected_result_filters()
    filtered = apply_result_filters(
        matches,
        pattern_family=st.session_state.get("pr_family", "All Families"),
        pattern_type=st.session_state.get("pr_type", "All Triangle Patterns"),
        stages=st.session_state.get("pr_stages", list(DETECTION_STAGES)),
        timeframe=st.session_state.get("pr_timeframe", "All Timeframes"),
        recent_only=st.session_state.get("pr_recent_only", True),
        freshness_window=int(settings.get("freshness_window", 5) or 5),
        sort_by=st.session_state.get("pr_sort", "Freshest First"),
        selected_filters=selected_filters,
    )

    spacer(12)
    main, side = st.columns([3.35, 1])
    with main:
        with st.container(border=True):
            _render_results_table(filtered)
    with side:
        _render_result_helpers(filtered)

    spacer(10)
    _render_export_bar(filtered)


def _seed_result_filters(settings: dict[str, Any]) -> None:
    st.session_state.setdefault("pr_family", "All Families")
    st.session_state.setdefault("pr_type", settings.get("pattern_type", "All Triangle Patterns"))
    st.session_state.setdefault("pr_stages", settings.get("detection_stages", list(DETECTION_STAGES)))
    st.session_state.setdefault("pr_timeframe", settings.get("timeframe", "Daily"))
    st.session_state.setdefault("pr_recent_only", settings.get("recent_only", True))
    st.session_state.setdefault("pr_sort", "Freshest First")
    selected = set(settings.get("selected_filters") or [])
    for label in SUGGESTED_FILTERS:
        st.session_state.setdefault(_filter_key(label), label in selected)


def _render_header(settings: dict[str, Any]) -> None:
    left, right = st.columns([2.2, 1.65])
    with left:
        family = settings.get("pattern_family", "Triangle Patterns")
        title = "Triangle Pattern Results" if family == "Triangle Patterns" else f"{family} Results"
        page_title(
            title,
            "Recent pattern appearances detected across your selected watchlist.",
            icon="target",
        )
    with right:
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("Re-Scan", icon=":material/refresh:", use_container_width=True):
                st.session_state["pattern_scanning"] = True
                st.rerun()
        with c2:
            if st.button("Back to Scanner", icon=":material/query_stats:", use_container_width=True):
                st.session_state.active_page = "pattern_scanner"
                st.rerun()
        with c3:
            if st.button("Back to Dashboard", icon=":material/arrow_back:", use_container_width=True):
                st.session_state.active_page = "dashboard"
                st.rerun()
        st.caption(f"Last Scan: {st.session_state.get('_pattern_last_scan_label', 'Not run')}")


def _run_pending_scan(settings: dict[str, Any]) -> None:
    universe_label, stocks = resolve_pattern_universe(settings.get("scope", "Current Watchlist"))
    st.session_state["pattern_scan_universe_label"] = universe_label
    if not stocks:
        st.session_state["pattern_scanning"] = False
        st.warning("The selected pattern scan universe is empty.")
        return

    placeholder = st.empty()

    def _progress(symbol: str, done: int, total: int) -> None:
        placeholder.markdown(
            _progress_card(universe_label, symbol, done, total),
            unsafe_allow_html=True,
        )

    source_name = st.session_state.get("selected_data_source", "Yahoo Finance")
    credentials = st.session_state.get("credentials", {}).get(source_name, {})
    output = run_pattern_scan(
        settings=settings,
        stocks=stocks,
        source_name=source_name,
        credentials=credentials,
        progress_callback=_progress,
    )
    placeholder.empty()
    st.session_state["pattern_scan_results"] = output.matches
    st.session_state["pattern_chart_data"] = output.chart_data
    st.session_state["pattern_zone_results"] = output.zone_results
    st.session_state["pattern_scan_errors"] = output.errors
    st.session_state["pattern_scan_fallback_symbols"] = output.fallback_symbols
    st.session_state["_pattern_last_scan_label"] = output.completed_at.strftime("%d %b %Y, %I:%M %p")
    st.session_state["pattern_scan_source_name"] = source_name
    st.session_state["pattern_scan_id"] = ""
    if output.matches:
        try:
            st.session_state["pattern_scan_id"] = save_pattern_scan(
                settings=settings,
                universe_label=universe_label,
                source_name=source_name,
                matches=output.matches,
            )
        except Exception as exc:
            st.session_state["pattern_scan_errors"]["cache"] = str(exc)
    st.session_state["pattern_scanning"] = False


def _ensure_pattern_scan_cached(
    settings: dict[str, Any], matches: list[PatternMatch]
) -> None:
    if st.session_state.get("pattern_scan_id") or not matches:
        return
    try:
        st.session_state["pattern_scan_id"] = save_pattern_scan(
            settings=settings,
            universe_label=st.session_state.get("pattern_scan_universe_label", ""),
            source_name=st.session_state.get("pattern_scan_source_name")
            or st.session_state.get("selected_data_source", "Yahoo Finance"),
            matches=matches,
        )
    except Exception as exc:
        st.session_state.setdefault("pattern_scan_errors", {})["cache"] = str(exc)


def _render_summary_cards(matches: list[PatternMatch]) -> None:
    counts = pattern_counts(matches)
    total = max(counts["total"], 1)
    cols = st.columns(7)
    cards = [
        ("Total Matches", counts["total"], "Across scanned symbols", "search", "purple"),
        ("Forming", counts["forming"], "Patterns forming", "activity", "bullish"),
        ("Near Apex", counts["near_apex"], "Approaching apex", "target", "warning"),
        ("Breakout Confirmed", counts["breakout"], "Breakouts detected", "trend_up", "info"),
        ("Symmetrical", counts["symmetrical"], f"{counts['symmetrical'] / total:.0%} of matches", "layers", "purple"),
        ("Ascending", counts["ascending"], f"{counts['ascending'] / total:.0%} of matches", "trend_up", "bullish"),
        ("Descending", counts["descending"], f"{counts['descending'] / total:.0%} of matches", "trend_down", "bearish"),
    ]
    for col, (label, value, sub, icon, tone) in zip(cols, cards):
        with col:
            stat_card(label, str(value), sub, icon, tone=tone)  # type: ignore[arg-type]


def _render_filter_row() -> None:
    top = st.columns([1.25, 1.25, 1.9, 1.6, 0.95, 1.15, 0.8])
    with top[0]:
        st.selectbox("Pattern Family", ["All Families", *PATTERN_FAMILIES], key="pr_family")
    with top[1]:
        st.selectbox("Pattern Type", TRIANGLE_TYPES, key="pr_type")
    with top[2]:
        st.multiselect("Detection Stage", DETECTION_STAGES, key="pr_stages")
    with top[3]:
        st.radio("Timeframe", ["All Timeframes", *TIMEFRAMES], horizontal=True, key="pr_timeframe")
    with top[4]:
        st.toggle("Recent only", key="pr_recent_only")
    with top[5]:
        _render_sort_control()
    with top[6]:
        st.markdown("<div style='height:26px;'></div>", unsafe_allow_html=True)
        if st.button("Clear All", icon=":material/close:", use_container_width=True):
            _clear_result_filters()
            st.rerun()

    st.caption("Advanced Filters")
    chip_cols = st.columns(6)
    for idx, label in enumerate(SUGGESTED_FILTERS):
        with chip_cols[idx % 6]:
            st.checkbox(label, key=_filter_key(label))
    if st.button("Add Filter", icon=":material/add:", key="pr_add_filter"):
        st.info("Additional pattern filters can be added here as more detectors are introduced.")


def _render_sort_control() -> None:
    current = st.session_state.get("pr_sort", "Freshest First")
    if current not in _SORT_OPTIONS:
        current = "Freshest First"
        st.session_state["pr_sort"] = current
    with st.popover(f"Sort: {current}", icon=":material/sort:", use_container_width=True):
        for option in _SORT_OPTIONS:
            button_type = "primary" if option == current else "secondary"
            if st.button(
                option,
                key=f"pr_sort_option_{_filter_key(option)}",
                type=button_type,
                use_container_width=True,
            ):
                st.session_state["pr_sort"] = option
                st.rerun()


def _render_results_table(matches: list[PatternMatch]) -> None:
    section_title(f"Recent Pattern Matches ({len(matches)})")
    if not matches:
        st.caption("No symbols match the current result filters.")
        return

    start, end = page_slice(len(matches), "pr", default_size=10)
    rows = matches[start:end]
    scan_id = st.session_state.get("pattern_scan_id", "")
    headers = [
        "Rank",
        "Watch",
        "Symbol",
        "Pattern",
        "Stage",
        "Fresh",
        "Confidence",
        "Apex",
        "Breakout Bias",
        "Zone Context",
        "Volume",
        "Action",
    ]
    widths = [0.5, 0.55, 1.25, 1.55, 1.25, 0.72, 0.95, 0.72, 1.35, 1.35, 0.85, 1.1]
    _render_results_table_styles()

    header_cols = st.columns(widths)
    for col, label in zip(header_cols, headers):
        with col:
            st.markdown(
                f"<div class='pr-table-head'>{html.escape(label)}</div>",
                unsafe_allow_html=True,
            )

    watch_symbols = st.session_state.get("pattern_watch_symbols", set())
    for rank, match in enumerate(rows, start + 1):
        volume_label = "High" if match.volume_contraction else "Normal"
        volume_tone = "bullish" if match.volume_contraction else "muted"
        watched = match.symbol in watch_symbols
        watch_label = "Remove from pattern watch list" if watched else "Add to pattern watch list"
        detail_link = (
            f"<a href='{html.escape(build_pattern_detail_url(scan_id, match.symbol))}' "
            f"target='_blank' class='pr-action-link'>View Pattern</a>"
            if scan_id
            else "<span class='pr-muted'>Cache unavailable</span>"
        )
        cols = st.columns(widths)
        with cols[0]:
            st.markdown(f"<div class='pr-cell pr-rank'>{rank}</div>", unsafe_allow_html=True)
        with cols[1]:
            if st.button(
                " ",
                icon=":material/star:" if watched else ":material/star_border:",
                key=f"pr_watch_{match.symbol}_{rank}",
                help=watch_label,
                type="primary" if watched else "secondary",
                use_container_width=True,
            ):
                _toggle_pattern_watch(match.symbol)
                st.rerun()
        with cols[2]:
            st.markdown(
                f"<div class='pr-cell pr-stack'><b>{html.escape(match.symbol)}</b>"
                f"<span>{html.escape(match.company_name[:28])}</span></div>",
                unsafe_allow_html=True,
            )
        with cols[3]:
            st.markdown(
                f"<div class='pr-cell pr-pattern'>{_pattern_cell(match.pattern_type)}</div>",
                unsafe_allow_html=True,
            )
        with cols[4]:
            st.markdown(
                f"<div class='pr-cell'>{_stage_pill(match.stage)}</div>",
                unsafe_allow_html=True,
            )
        with cols[5]:
            st.markdown(
                f"<div class='pr-cell pr-nowrap'>{match.freshness_candles}c ago</div>",
                unsafe_allow_html=True,
            )
        with cols[6]:
            st.markdown(
                f"<div class='pr-cell'>{_confidence_ring(match.confidence_score)}</div>",
                unsafe_allow_html=True,
            )
        with cols[7]:
            st.markdown(
                f"<div class='pr-cell pr-nowrap'>{match.apex_proximity:.0f}%</div>",
                unsafe_allow_html=True,
            )
        with cols[8]:
            st.markdown(
                f"<div class='pr-cell'>{bias_pill(match.breakout_bias, _bias_tone(match.breakout_bias))}</div>",
                unsafe_allow_html=True,
            )
        with cols[9]:
            st.markdown(
                f"<div class='pr-cell'>{bias_pill(match.zone_context, _zone_tone(match.zone_context))}</div>",
                unsafe_allow_html=True,
            )
        with cols[10]:
            st.markdown(
                f"<div class='pr-cell'>{bias_pill(volume_label, volume_tone)}</div>",
                unsafe_allow_html=True,
            )
        with cols[11]:
            st.markdown(
                f"<div class='pr-cell'>{detail_link}</div>",
                unsafe_allow_html=True,
            )
        st.markdown("<div class='pr-row-divider'></div>", unsafe_allow_html=True)

    pagination_bar(len(matches), "pr", _PAGE_SIZES, 10)


def _render_results_table_styles() -> None:
    st.markdown(
        """
        <style>
        .pr-table-head {
            min-height: 30px;
            display: flex;
            align-items: center;
            color: #8A8F98;
            font-size: 0.72rem;
            font-weight: 700;
            white-space: nowrap;
            border-bottom: 1px solid #E7E9ED;
        }
        .pr-cell {
            min-height: 48px;
            display: flex;
            align-items: center;
            color: #26313F;
            font-size: 0.82rem;
            line-height: 1.25;
        }
        .pr-stack {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            justify-content: center;
        }
        .pr-stack span {
            margin-top: 3px;
            color: #71757C;
            font-size: 0.72rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            max-width: 100%;
        }
        .pr-rank {
            font-weight: 800;
        }
        .pr-nowrap {
            white-space: nowrap;
        }
        .pr-pattern {
            gap: 6px;
            flex-wrap: wrap;
        }
        .pr-muted {
            color: #9AA0A8;
            font-size: 0.76rem;
        }
        .pr-action-link {
            display: inline-block;
            color: #2F5FE0;
            font-weight: 700;
            text-decoration: none;
            border: 1px solid #DCE6FA;
            border-radius: 8px;
            padding: 6px 9px;
            background: #F4F7FE;
            white-space: nowrap;
        }
        .pr-action-link:hover {
            border-color: #2F5FE0;
            background: #EEF4FF;
        }
        .pr-row-divider {
            height: 1px;
            background: #F1F2F4;
            margin: 2px 0 4px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _toggle_pattern_watch(symbol: str) -> None:
    watch_symbols = set(st.session_state.get("pattern_watch_symbols", set()))
    normalized = symbol.upper()
    if normalized in watch_symbols:
        watch_symbols.remove(normalized)
    else:
        watch_symbols.add(normalized)
    st.session_state["pattern_watch_symbols"] = watch_symbols


def _render_result_helpers(matches: list[PatternMatch]) -> None:
    with st.container(border=True):
        section_title("How to read these results")
        lines = [
            ("Triangles show contraction", "Price is consolidating between converging trendlines."),
            ("Direction on breakout only", "Bias is pressure, not confirmed direction."),
            ("Check zone context", "Demand/supply zones improve pattern usefulness."),
            ("Watch volume contraction", "Lower volume during contraction supports cleaner breaks."),
        ]
        for title, caption in lines:
            st.markdown(
                f"<div style='font-size:0.78rem;padding:5px 0;'>"
                f"<b>{html.escape(title)}</b><br><span style='color:#71757C;'>"
                f"{html.escape(caption)}</span></div>",
                unsafe_allow_html=True,
            )

    spacer(8)
    with st.container(border=True):
        section_title("Pattern Mix", hint=f"Based on {len(matches)} matches")
        counts = pattern_counts(matches)
        total = max(len(matches), 1)
        for label, count, colour in [
            ("Symmetrical", counts["symmetrical"], "#5A47B8"),
            ("Ascending", counts["ascending"], "#16794A"),
            ("Descending", counts["descending"], "#C23B33"),
        ]:
            pct = count / total * 100
            st.markdown(
                f"<div style='font-size:0.76rem;margin:7px 0 2px 0;'>"
                f"{html.escape(label)} <span style='float:right;'>{count} ({pct:.0f}%)</span></div>"
                f"<div style='height:5px;background:#EEF0F3;border-radius:999px;'>"
                f"<div style='height:5px;width:{pct:.0f}%;background:{colour};border-radius:999px;'></div></div>",
                unsafe_allow_html=True,
            )

    spacer(8)
    with st.container(border=True):
        section_title("Scan Insights")
        if not matches:
            st.caption("No filtered matches.")
            return
        best = max(matches, key=lambda m: m.confidence_score)
        near = sum(1 for m in matches if m.stage == "Near Apex")
        breakout = sum(1 for m in matches if m.stage == "Breakout Confirmed")
        for line in [
            f"Top confidence: {best.symbol} at {best.confidence_score:.0f}%.",
            f"{near} setups are near apex.",
            f"{breakout} setups already have a confirmed break.",
        ]:
            st.markdown(
                f"<div style='font-size:0.78rem;padding:3px 0;color:#4A5361;'>"
                f"&bull; {html.escape(line)}</div>",
                unsafe_allow_html=True,
            )


def _render_export_bar(matches: list[PatternMatch]) -> None:
    with st.container(border=True):
        left, right = st.columns([1.5, 2.5])
        with left:
            c1, c2 = st.columns(2)
            with c1:
                st.download_button(
                    "Export Excel",
                    data=_excel_bytes(matches),
                    file_name=_export_name("patterns", "xlsx"),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    icon=":material/download:",
                    disabled=not matches,
                    use_container_width=True,
                )
            with c2:
                st.download_button(
                    "Export PDF",
                    data=_pdf_bytes(matches),
                    file_name=_export_name("patterns", "pdf"),
                    mime="application/pdf",
                    icon=":material/picture_as_pdf:",
                    disabled=not matches,
                    use_container_width=True,
                )
        with right:
            if st.button("Open Full Pattern Report", icon=":material/open_in_new:", use_container_width=True):
                st.info("The full pattern report view will expand as more pattern families are added.")


def _clear_result_filters() -> None:
    st.session_state["pr_family"] = "All Families"
    st.session_state["pr_type"] = "All Triangle Patterns"
    st.session_state["pr_stages"] = list(DETECTION_STAGES)
    st.session_state["pr_timeframe"] = "All Timeframes"
    st.session_state["pr_recent_only"] = False
    st.session_state["pr_sort"] = "Freshest First"
    for label in SUGGESTED_FILTERS:
        st.session_state[_filter_key(label)] = False


def _selected_result_filters() -> list[str]:
    return [label for label in SUGGESTED_FILTERS if st.session_state.get(_filter_key(label))]


def _stage_pill(stage: str) -> str:
    tone = {
        "Forming": "bullish",
        "Near Apex": "warning",
        "Breakout Confirmed": "purple",
    }.get(stage, "neutral")
    return bias_pill(stage, tone)  # type: ignore[arg-type]


def _bias_tone(text: str) -> str:
    if "Bullish" in text or text == "Up Break":
        return "bullish"
    if "Bearish" in text or text == "Down Break":
        return "bearish"
    return "muted"


def _zone_tone(text: str) -> str:
    if text == "Near Demand Zone":
        return "bullish"
    if text == "Near Supply Zone":
        return "bearish"
    return "info"


def _pattern_cell(pattern_type: str) -> str:
    mini = {
        "Symmetrical Triangle": "SYM",
        "Ascending Triangle": "ASC",
        "Descending Triangle": "DSC",
    }.get(pattern_type, "Tri")
    return (
        f"<span style='display:inline-block;background:#EEF4FF;color:#2F5FE0;"
        f"border:1px solid #DCE6FA;border-radius:6px;padding:2px 6px;"
        f"font-size:0.68rem;font-weight:800;margin-right:6px;'>{html.escape(mini)}</span>"
        f"<span style='font-size:0.8rem;color:#26313F;'>{html.escape(pattern_type)}</span>"
    )


def _confidence_ring(score: float) -> str:
    colour = "#16794A" if score >= 75 else "#B4791A" if score >= 62 else "#8A8F98"
    return (
        f"<span style='display:inline-flex;align-items:center;justify-content:center;"
        f"width:34px;height:34px;border-radius:50%;border:2px solid {colour};"
        f"font-size:0.72rem;font-weight:800;color:{colour};'>{score:.0f}%</span>"
    )


def _progress_card(universe: str, symbol: str, done: int, total: int) -> str:
    pct = done / total * 100 if total else 0
    return (
        "<div style='display:flex;justify-content:center;padding:36px 16px;'>"
        "<div style='width:min(720px,100%);background:#FFFFFF;border:1px solid #E7E9ED;"
        "border-radius:16px;padding:28px 34px;'>"
        "<div style='font-size:1.35rem;font-weight:800;color:#16233A;'>Running Pattern Scan</div>"
        f"<div style='font-size:0.9rem;color:#6B7280;margin:4px 0 16px 0;'>"
        f"Scanning <b>{done}</b> of <b>{total}</b> symbols from "
        f"<b>{html.escape(universe)}</b></div>"
        "<div style='height:10px;background:#EEF0F3;border-radius:999px;overflow:hidden;'>"
        f"<div style='height:10px;width:{pct:.1f}%;background:#2F5FE0;border-radius:999px;'></div></div>"
        f"<div style='font-size:0.82rem;color:#4A5361;margin-top:12px;'>"
        f"Currently checking <b>{html.escape(symbol)}</b> for recent triangle structures.</div>"
        "</div></div>"
    )


def _excel_bytes(matches: list[PatternMatch]) -> bytes:
    rows = matches_to_export_rows(matches)
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name="Pattern Results")
    return bio.getvalue()


def _pdf_bytes(matches: list[PatternMatch]) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    except Exception:
        return b"ReportLab is not available."

    rows = matches_to_export_rows(matches)
    data = [["Rank", "Symbol", "Pattern", "Stage", "Confidence", "Apex", "Bias"]]
    for row in rows[:80]:
        data.append(
            [
                row["Rank"],
                row["Symbol"],
                row["Pattern"],
                row["Stage"],
                f"{row['Confidence %']}%",
                f"{row['Apex Proximity %']}%",
                row["Breakout Bias"],
            ]
        )
    bio = BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=landscape(letter))
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF4FF")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#16233A")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D8DCE2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    doc.build([table])
    return bio.getvalue()


def _export_name(prefix: str, ext: str) -> str:
    return f"market_lens_{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"


def _filter_key(label: str) -> str:
    return "pr_filter_" + label.lower().replace(" ", "_").replace("/", "_").replace("&", "and")
