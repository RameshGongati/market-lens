"""Shared surface primitives for the dashboard and analysis-results pages.

Both pages are built from the same three shapes — a stat card, a section
container and a labelled value row — so they live here rather than being
written twice with drifting padding.

**Placeholders are a first-class case.** Large parts of both page designs
depend on analysis that does not exist yet: entry/stop/target and risk-reward
need Phase 2 (M1), the HTF/ITF trend columns need Phase 3, and everything
sector-related needs Phase 7. Those boxes are rendered with their real label
and an explicit "pending" value naming the phase that will fill them, so an
empty cell reads as *not built yet* rather than as *no setup found* — on a
trading screen those two mean opposite things, and a fabricated number would
be worse than either.

Streamlit's markdown sanitiser strips ``class`` and ``id`` but preserves
inline ``style``, so everything here is styled inline (see Gotcha 20 in
CLAUDE.md).
"""

from __future__ import annotations

import html
from typing import Literal

import streamlit as st

from config.preferences import SCAN_PROGRESS_STYLE_OPTIONS, load_preferences

# Phase labels shown under a pending value. Keyed by the roadmap phase that
# supplies the data — see docs/requirements.md.
PENDING_PHASES: dict[str, str] = {
    "p2": "Phase 2 · entry/stop/target",
    "p3": "Phase 3 · multi-timeframe",
    "p7": "Phase 7 · sector context",
    "new": "not yet built",
}

_TONES: dict[str, tuple[str, str, str, str]] = {
    # (value colour, card tint, soft chip fill, SOLID badge fill)
    "neutral": ("#26313F", "#FFFFFF", "#EEF0F3", "#5B6472"),
    "bullish": ("#16794A", "#F1FAF4", "#D6F2E0", "#22A55B"),
    "bearish": ("#C23B33", "#FEF3F2", "#FBDDD8", "#EB5757"),
    "warning": ("#B4791A", "#FFF9EF", "#FBEBC8", "#F2A93B"),
    "info": ("#1F6FD0", "#F1F6FE", "#DCE8FB", "#2F80ED"),
    "purple": ("#5A47B8", "#F6F4FD", "#E4DEF8", "#7B61E3"),
    "muted": ("#8A8A82", "#FAFAF9", "#EFEFEA", "#B6B6AE"),
}

Tone = Literal[
    "neutral", "bullish", "bearish", "warning", "info", "purple", "muted"
]

_PENDING_COLOR = "#A8A8A0"
_BORDER = "#E7E9ED"

# Stroked line-icon paths on a 24x24 grid, drawn white inside a solid circle.
#
# Emoji cannot do this. A glyph like 🎯 carries its own colours and its own
# optical size, so on a coloured badge it reads as a sticker rather than an
# icon and lands differently on every platform. These are single-colour
# strokes, so they inherit the badge treatment and stay identical everywhere.
_ICON_PATHS: dict[str, str] = {
    "trend_up": ("<polyline points='3 16.5 9.5 10 13.5 14 21 6.5'/>"
                 "<polyline points='15 6.5 21 6.5 21 12.5'/>"),
    "trophy": ("<path d='M8 21h8M12 17.5V21M6.5 4h11v5.5a5.5 5.5 0 0 1-11 0V4z'/>"
               "<path d='M17.5 6H21v1.5a3.5 3.5 0 0 1-3.5 3.5'/>"
               "<path d='M6.5 6H3v1.5A3.5 3.5 0 0 0 6.5 11'/>"),
    "target": ("<circle cx='12' cy='12' r='8.5'/><circle cx='12' cy='12' r='4.5'/>"
               "<circle cx='12' cy='12' r='1' fill='currentColor'/>"),
    "star": ("<polygon points='12 3.5 14.5 9.2 20.7 9.8 16 13.9 17.4 20 12 16.8 "
             "6.6 20 8 13.9 3.3 9.8 9.5 9.2'/>"),
    "bell": ("<path d='M18 8.5a6 6 0 1 0-12 0c0 6.5-2.5 8.5-2.5 8.5h17S18 15 18 8.5'/>"
             "<path d='M13.7 20.5a2 2 0 0 1-3.4 0'/>"),
    "check_circle": ("<circle cx='12' cy='12' r='8.5'/>"
                     "<polyline points='8.3 12.3 10.9 14.9 15.9 9.6'/>"),
    "search": "<circle cx='11' cy='11' r='7'/><line x1='20.5' y1='20.5' x2='16' y2='16'/>",
    "trend_down": ("<polyline points='3 7.5 9.5 14 13.5 10 21 17.5'/>"
                   "<polyline points='15 17.5 21 17.5 21 11.5'/>"),
    "pause": ("<circle cx='12' cy='12' r='8.5'/><line x1='10' y1='9' x2='10' y2='15'/>"
              "<line x1='14' y1='9' x2='14' y2='15'/>"),
    "shield": "<path d='M12 3.2 19 6v5.5c0 4.4-3 7.4-7 9.3-4-1.9-7-4.9-7-9.3V6z'/>",
    "layers": ("<polygon points='12 3 21 7.7 12 12.4 3 7.7'/>"
               "<polyline points='3 12.3 12 17 21 12.3'/>"),
    "calendar": ("<rect x='3.5' y='5' width='17' height='15.5' rx='2.5'/>"
                 "<line x1='3.5' y1='10' x2='20.5' y2='10'/>"
                 "<line x1='8' y1='3' x2='8' y2='6.5'/>"
                 "<line x1='16' y1='3' x2='16' y2='6.5'/>"),
    "clock": ("<circle cx='12' cy='12' r='8.5'/>"
              "<polyline points='12 7 12 12 15.5 14'/>"),
    "check": "<polyline points='4.5 12.5 9.5 17.5 19.5 6.5'/>",
    "logo": ("<circle cx='12' cy='12' r='8.6'/>"
             "<polyline points='7.5 14.2 10.6 10.8 13.2 13 16.6 8.9'/>"),
    "settings": (
        "<circle cx='12' cy='12' r='3.2'/>"
        "<path d='M19.1 14.7a1.6 1.6 0 0 0 .32 1.77l.06.06a1.94 1.94 0 1 1-2.75 2.75l-.06-.06a"
        "1.6 1.6 0 0 0-1.77-.32 1.6 1.6 0 0 0-.97 1.47v.16a1.94 1.94 0 0 1-3.88 0v-.09a1.6 1.6 "
        "0 0 0-1.05-1.46 1.6 1.6 0 0 0-1.77.32l-.06.06a1.94 1.94 0 1 1-2.75-2.75l.06-.06a1.6 "
        "1.6 0 0 0 .32-1.77 1.6 1.6 0 0 0-1.47-.97h-.16a1.94 1.94 0 0 1 0-3.88h.09a1.6 1.6 0 0 "
        "0 1.46-1.05 1.6 1.6 0 0 0-.32-1.77l-.06-.06a1.94 1.94 0 1 1 2.75-2.75l.06.06a1.6 1.6 "
        "0 0 0 1.77.32h.08a1.6 1.6 0 0 0 .97-1.47v-.16a1.94 1.94 0 0 1 3.88 0v.09a1.6 1.6 0 0 "
        "0 .97 1.46 1.6 1.6 0 0 0 1.77-.32l.06-.06a1.94 1.94 0 1 1 2.75 2.75l-.06.06a1.6 1.6 0 "
        "0 0-.32 1.77v.08a1.6 1.6 0 0 0 1.47.97h.16a1.94 1.94 0 0 1 0 3.88h-.09a1.6 1.6 0 0 "
        "0-1.46.97z'/>"
    ),
    "database": ("<ellipse cx='12' cy='5.5' rx='7.5' ry='2.8'/>"
                 "<path d='M4.5 5.5v13c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8v-13'/>"
                 "<path d='M4.5 12c0 1.55 3.36 2.8 7.5 2.8s7.5-1.25 7.5-2.8'/>"),
    "activity": "<polyline points='2.5 12.5 7 12.5 9.5 6 14 18 16.5 12.5 21.5 12.5'/>",
    "palette": ("<path d='M12 3a9 9 0 1 0 0 18c1.1 0 2-.9 2-2 0-.5-.2-.95-.5-1.3-.3-.35-.5-.8"
                "-.5-1.2 0-1.1.9-2 2-2h2.2A3.8 3.8 0 0 0 21 10.8C21 6.5 16.9 3 12 3z'/>"
                "<circle cx='7.4' cy='11.6' r='1.1' fill='currentColor' stroke='none'/>"
                "<circle cx='9.9' cy='7.6' r='1.1' fill='currentColor' stroke='none'/>"
                "<circle cx='15' cy='7.9' r='1.1' fill='currentColor' stroke='none'/>"),
    "monitor": ("<rect x='2.5' y='4' width='19' height='12.5' rx='2'/>"
                "<line x1='8' y1='20.5' x2='16' y2='20.5'/>"
                "<line x1='12' y1='16.5' x2='12' y2='20.5'/>"),
    "sliders": ("<line x1='3.5' y1='8' x2='20.5' y2='8'/><circle cx='9' cy='8' r='2.3'/>"
                "<line x1='3.5' y1='16' x2='20.5' y2='16'/><circle cx='15' cy='16' r='2.3'/>"),
    "send": ("<line x1='21' y1='3' x2='10.5' y2='13.5'/>"
             "<polygon points='21 3 14.6 21 10.5 13.5 3 9.4'/>"),
    "rocket": ("<path d='M12 2.6c3 2.1 4.9 5.6 4.9 9.4L14.4 15h-4.8L7.1 12c0-3.8 1.9-7.3 "
               "4.9-9.4z'/><circle cx='12' cy='9.9' r='1.8'/>"
               "<path d='M9.6 16.4 7 19l1.6 1.6M14.4 16.4 17 19l-1.6 1.6'/>"),
    "info": ("<circle cx='12' cy='12' r='8.5'/><line x1='12' y1='11.2' x2='12' y2='16.6'/>"
             "<circle cx='12' cy='7.9' r='1' fill='currentColor' stroke='none'/>"),
    "download": ("<polyline points='7.5 11 12 15.5 16.5 11'/>"
                 "<line x1='12' y1='4' x2='12' y2='15.5'/>"
                 "<path d='M4.5 17.5V19a1.5 1.5 0 0 0 1.5 1.5h12a1.5 1.5 0 0 0 1.5-1.5v-1.5'/>"),
}


def _icon_svg(name: str, size: int = 19, colour: str = "#FFFFFF") -> str:
    """Inline SVG for one icon, or empty string when the name is unknown."""
    path = _ICON_PATHS.get(name)
    if not path:
        return ""
    return (
        f"<svg width='{size}' height='{size}' viewBox='0 0 24 24' fill='none' "
        f"stroke='{colour}' stroke-width='1.9' stroke-linecap='round' "
        f"stroke-linejoin='round' style='display:block;'>{path}</svg>"
    )


def pending_value(phase: str = "new") -> str:
    """The canonical 'no data yet' value — an em dash, never a zero.

    A zero would be indistinguishable from a real count of nothing.
    """
    return f"<span style='color:{_PENDING_COLOR};font-weight:400;'>&mdash;</span>"


def stat_card(
    label: str,
    value: str | None = None,
    sub: str = "",
    icon: str = "",
    tone: Tone = "neutral",
    pending: str = "",
) -> None:
    """One summary tile — icon badge, small-caps label, large value, caption.

    The icon sits in a filled circle rather than inline with the label. A bare
    emoji next to text renders at a different optical size on every platform
    and drifts off the baseline; a fixed-size badge keeps every card's header
    row identical no matter what glyph is used.

    Cards are given a fixed minimum height so a row of them lines up even when
    one has a two-line caption and its neighbour has none.

    Args:
        label: The caption above the value (rendered upper-case).
        value: The value. Ignored when ``pending`` is set.
        sub: Small text below the value.
        icon: Emoji shown in the badge.
        tone: Colour family — drives the value colour, tint and badge.
        pending: A key from :data:`PENDING_PHASES`. When set, the card shows
            the placeholder value and names the phase instead of ``value``.
    """
    accent, tint, _soft, solid = _TONES["muted" if pending else tone]
    if pending:
        shown = pending_value(pending)
        sub_text = PENDING_PHASES.get(pending, PENDING_PHASES["new"])
        sub_style = f"color:{_PENDING_COLOR};font-style:italic;"
    else:
        shown = html.escape(str(value if value is not None else ""))
        sub_text = sub
        sub_style = "color:#71757C;"

    glyph = _icon_svg(icon) if icon else ""
    badge_html = (
        f"<span style='display:inline-flex;align-items:center;"
        f"justify-content:center;width:40px;height:40px;border-radius:50%;"
        f"background:{solid};flex:0 0 40px;'>{glyph}</span>"
    ) if glyph else ""

    # Built as ONE line with no newlines. A multi-line f-string here produced a
    # whitespace-only line whenever `icon` was empty, and a blank line
    # terminates a markdown HTML block — the indented lines after it were then
    # parsed as an indented code block, so cards without an icon rendered
    # their own source as text on the page. Keep every helper in this module
    # newline-free.
    #
    # margin-bottom lives on the card, not on an external spacer: the card
    # renders slightly taller than the container Streamlit allocates for it,
    # so a sibling spacer overlapped the row above instead of separating it.
    st.markdown(
        f"<div style='background:{tint};border:1px solid {_BORDER};"
        f"border-radius:14px;padding:14px 16px;min-height:96px;"
        f"margin-bottom:10px;box-sizing:border-box;display:flex;"
        f"align-items:center;gap:13px;'>{badge_html}"
        f"<div style='min-width:0;'>"
        f"<div style='font-size:0.68rem;letter-spacing:0.6px;color:#8A8F98;"
        f"text-transform:uppercase;font-weight:600;line-height:1.25;'>"
        f"{html.escape(label)}</div>"
        f"<div style='font-size:1.5rem;font-weight:700;color:{accent};"
        f"line-height:1.3;margin-top:1px;'>{shown}</div>"
        f"<div style='font-size:0.75rem;margin-top:1px;{sub_style}'>"
        f"{html.escape(sub_text)}</div></div></div>",
        unsafe_allow_html=True,
    )


def page_title(title: str, subtitle: str = "", icon: str = "logo") -> None:
    """Page heading — mark, bold title, subtitle beneath.

    The title previously rendered as plain markdown at Streamlit's default
    weight and colour, which read as body copy rather than a page heading.
    """
    glyph = _icon_svg(icon, size=23, colour="#2F5FE0")
    mark = (
        f"<span style='display:inline-flex;align-items:center;"
        f"justify-content:center;width:40px;height:40px;flex:0 0 40px;'>"
        f"{glyph}</span>" if glyph else ""
    )
    sub = (
        f"<div style='font-size:0.9rem;color:#6B7280;margin-top:1px;'>"
        f"{html.escape(subtitle)}</div>" if subtitle else ""
    )
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:11px;"
        f"margin:0 0 4px 0;'>{mark}<div>"
        f"<div style='font-size:1.75rem;font-weight:800;color:#16233A;"
        f"line-height:1.15;letter-spacing:-0.2px;'>{html.escape(title)}</div>"
        f"{sub}</div></div>",
        unsafe_allow_html=True,
    )


def filter_chip(
    label: str, value: str = "", icon: str = "", pending: str = "", sub: str = "",
) -> None:
    """Compact read-out box for the results page's filter strip.

    Shorter than :func:`stat_card` on purpose: these echo the scan's settings
    and sat at the same 96px height as the metric cards below them, so two
    visually identical rows of boxes competed for attention when only the
    second carries numbers.

    ``pending`` takes a key from :data:`PENDING_PHASES` and swaps the value for
    the placeholder, the same contract as :func:`stat_card`.
    """
    glyph = _icon_svg(icon, size=14, colour="#6B7280") if icon else ""
    badge_html = (
        f"<span style='display:inline-flex;align-items:center;"
        f"justify-content:center;width:22px;height:22px;border-radius:6px;"
        f"background:#F0F2F5;flex:0 0 22px;'>{glyph}</span>" if glyph else ""
    )
    if pending:
        shown = pending_value(pending)
        note_text = PENDING_PHASES.get(pending, PENDING_PHASES["new"])
        note_style = f"color:{_PENDING_COLOR};font-style:italic;"
    else:
        shown = html.escape(str(value))
        note_text = sub
        note_style = "color:#8A8F98;"
    note = (
        f"<div style='font-size:0.66rem;margin-top:1px;line-height:1.25;"
        f"{note_style}'>{html.escape(note_text)}</div>" if note_text else ""
    )
    st.markdown(
        f"<div style='background:#FFFFFF;border:1px solid {_BORDER};"
        f"border-radius:10px;padding:9px 12px;min-height:58px;"
        f"margin-bottom:10px;box-sizing:border-box;'>"
        f"<div style='display:flex;align-items:center;gap:6px;min-height:20px;'>"
        f"{badge_html}<span style='font-size:0.62rem;letter-spacing:0.6px;"
        f"color:#7A7A72;text-transform:uppercase;font-weight:700;'>"
        f"{html.escape(label)}</span></div>"
        f"<div style='font-size:0.95rem;font-weight:700;color:#26262B;"
        f"margin-top:3px;line-height:1.2;'>{shown}</div>{note}"
        f"</div>",
        unsafe_allow_html=True,
    )


def panel_head(title: str, sub: str = "", icon: str = "", tone: Tone = "info") -> None:
    """Panel heading with a tinted icon tile, title and caption beneath.

    :func:`section_title` is a bare line of bold text, which is right for a
    panel that sits inside another surface. These are the top-level cards on
    the settings page, where the icon tile is what separates one card from the
    next at a glance.
    """
    accent, _tint, soft, _solid = _TONES.get(tone, _TONES["info"])
    glyph = _icon_svg(icon, size=17, colour=accent) if icon else ""
    tile = (
        f"<span style='display:inline-flex;align-items:center;"
        f"justify-content:center;width:30px;height:30px;border-radius:8px;"
        f"background:{soft};flex:0 0 30px;'>{glyph}</span>" if glyph else ""
    )
    caption = (
        f"<div style='font-size:0.74rem;color:#8A8F98;margin-top:1px;"
        f"line-height:1.3;'>{html.escape(sub)}</div>" if sub else ""
    )
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:10px;"
        f"margin:0 0 10px 0;'>{tile}<div style='min-width:0;'>"
        f"<div style='font-size:0.95rem;font-weight:700;color:#1E1E23;"
        f"line-height:1.25;'>{html.escape(title)}</div>{caption}</div></div>",
        unsafe_allow_html=True,
    )


_PAGE_SIZES = (10, 20, 30, 50, 75, 100)


def _page_window(current: int, pages: int, span: int = 1) -> list[int | None]:
    """Page numbers to show, with ``None`` marking an ellipsis.

    Always includes the first and last page plus *span* either side of the
    current one, so the strip stays a fixed width whether there are 8 pages
    or 800 — rendering every page number would wrap the row and, on the F&O
    universe, produce more buttons than table rows.
    """
    if pages <= 7:
        return list(range(1, pages + 1))

    slots: list[int | None] = [1]
    lo, hi = max(2, current - span), min(pages - 1, current + span)
    if lo > 2:
        slots.append(None)
    slots.extend(range(lo, hi + 1))
    if hi < pages - 1:
        slots.append(None)
    slots.append(pages)
    return slots


def page_slice(
    total: int, key: str, default_size: int = 25,
) -> tuple[int, int]:
    """The ``(start, end)`` slice for the current page — renders nothing.

    Separate from :func:`pagination_bar` because the slice is needed BEFORE
    the table is drawn while the bar belongs BELOW it. Both read the same
    session keys, so the rows shown always match the caption underneath them.

    The page is CLAMPED here on every call rather than only when the arrows
    are pressed. Raising the page size, or filtering the table down, can leave
    the stored page past the end — the caller would then slice an empty window
    and show a blank table with no indication why.
    """
    page_key, size_key = f"{key}_page", f"{key}_size"
    st.session_state.setdefault(page_key, 1)
    per_page = st.session_state.get(size_key, default_size)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, int(st.session_state.get(page_key, 1))), pages)
    st.session_state[page_key] = page
    return (page - 1) * per_page, min(page * per_page, total)


def pagination_bar(
    total: int,
    key: str,
    sizes: tuple[int, ...] = _PAGE_SIZES,
    default_size: int = 25,
) -> None:
    """Footer pagination bar, drawn below a table.

    Layout follows the design: a "Showing X to Y of N results" caption on the
    left, numbered pages in the middle, and a rows-per-page selector on the
    right. Call :func:`page_slice` with the same ``key`` to get the rows.
    """
    page_key, size_key = f"{key}_page", f"{key}_size"
    per_page = st.session_state.get(size_key, default_size)
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(max(1, int(st.session_state.get(page_key, 1))), pages)

    # Clear the table's horizontal scrollbar, which sits directly above and
    # was being overlapped by the buttons.
    st.markdown("<div style='height:10px;'></div>", unsafe_allow_html=True)

    # Marker that lets app.py shrink the buttons inside this row only.
    # Streamlit's default button is sized for a text label and looked like a
    # row of large plain boxes at page-number size. A class would read better,
    # but the markdown sanitiser strips class and id while preserving inline
    # style, so the letter-spacing value is the hook (see Gotcha 20). Keep
    # this value in step with the selector in app.py.
    st.markdown(
        "<div style='letter-spacing:0.09px;height:0;margin:0;'></div>",
        unsafe_allow_html=True,
    )

    left, mid, right = st.columns([2.4, 4.4, 1.9])

    with left:
        first = (page - 1) * per_page + 1 if total else 0
        last = min(page * per_page, total)
        st.markdown(
            f"<div style='padding-top:9px;font-size:0.8rem;color:#71757C;'>"
            f"Showing <b>{first}</b> to <b>{last}</b> of <b>{total}</b> results"
            f"</div>",
            unsafe_allow_html=True,
        )

    with mid:
        slots = _page_window(page, pages)
        # Arrows share the strip so the control reads as one unit.
        cells = st.columns(len(slots) + 2, gap="small")
        with cells[0]:
            if st.button("‹", key=f"{key}_prev", disabled=page <= 1,
                         use_container_width=True, help="Previous page"):
                st.session_state[page_key] = page - 1
                st.rerun()
        for slot, col in zip(slots, cells[1:-1]):
            with col:
                if slot is None:
                    st.markdown(
                        "<div style='text-align:center;padding-top:9px;"
                        "color:#9AA0A8;'>…</div>", unsafe_allow_html=True,
                    )
                elif slot == page:
                    st.button(str(slot), key=f"{key}_p{slot}",
                              use_container_width=True, type="primary")
                elif st.button(str(slot), key=f"{key}_p{slot}",
                               use_container_width=True):
                    st.session_state[page_key] = slot
                    st.rerun()
        with cells[-1]:
            if st.button("›", key=f"{key}_next", disabled=page >= pages,
                         use_container_width=True, help="Next page"):
                st.session_state[page_key] = page + 1
                st.rerun()

    with right:
        lbl, sel = st.columns([1.1, 1])
        with lbl:
            st.markdown(
                "<div style='padding-top:9px;font-size:0.8rem;"
                "color:#71757C;text-align:right;'>Rows per page:</div>",
                unsafe_allow_html=True,
            )
        with sel:
            st.selectbox(
                "Rows per page", sizes,
                index=sizes.index(default_size) if default_size in sizes else 0,
                key=size_key, label_visibility="collapsed",
            )


def spacer(px: int = 14) -> None:
    """Explicit vertical gap between major sections.

    Streamlit's block gap is uniform, so a card row and the panel beneath it
    sat the same distance apart as two rows inside one panel, which made the
    page read as one undifferentiated stack.
    """
    st.markdown(
        f"<div style='height:{px}px;'></div>", unsafe_allow_html=True,
    )


def section_title(title: str, hint: str = "") -> None:
    """Header line for a panel, with an optional right-aligned hint."""
    right = (
        f"<span style='float:right;font-size:0.75rem;color:#8A8A82;"
        f"font-weight:400;'>{html.escape(hint)}</span>" if hint else ""
    )
    st.markdown(
        f"<div style='font-size:0.95rem;font-weight:700;color:#1E1E23;"
        f"margin:0 0 6px 0;'>{html.escape(title)}{right}</div>",
        unsafe_allow_html=True,
    )


def kv_row(label: str, value: str = "", pending: str = "", tone: str = "") -> None:
    """A label-on-the-left, value-on-the-right row for detail panels."""
    if pending:
        shown = pending_value(pending)
        note = (
            f"<div style='font-size:0.66rem;color:{_PENDING_COLOR};"
            f"font-style:italic;'>{PENDING_PHASES.get(pending, '')}</div>"
        )
    else:
        colour = tone or "#1E1E23"
        shown = (
            f"<span style='color:{colour};font-weight:600;'>"
            f"{html.escape(str(value))}</span>"
        )
        note = ""
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:baseline;padding:4px 0;"
        f"border-bottom:1px solid #F0F0EC;'>"
        f"<span style='font-size:0.8rem;color:#5F5E5A;'>"
        f"{html.escape(label)}</span>"
        f"<span style='font-size:0.85rem;text-align:right;'>{shown}{note}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


# Scan progress is determinate: the loop knows the scanned and total stock
# counts, so the UI shows only that real progress plus the current symbol.
def _speedometer(pct: float, width: int = 178, height: int = 104) -> str:
    """Semi-circular scan progress gauge matching the original sample style."""
    clamped = max(0.0, min(pct, 100.0))
    visible_start = 90.0
    filled_stop = visible_start + (clamped / 100.0 * 180.0)
    green_stop = visible_start + min(clamped / 100.0 * 180.0, 110.0)
    inner_x = max(12, int(width * 0.124))
    inner_h = max(42, int(height * 0.596))
    font_size = max(22, int(width * 0.157))
    bottom = max(10, int(height * 0.135))
    if filled_stop <= visible_start:
        fill = f"#E8EDF5 0deg {visible_start:.1f}deg,#E8EDF5 {visible_start:.1f}deg 360deg"
    elif filled_stop <= green_stop:
        fill = (
            f"#E8EDF5 0deg {visible_start:.1f}deg,"
            f"#1EA663 {visible_start:.1f}deg {filled_stop:.1f}deg,"
            f"#E8EDF5 {filled_stop:.1f}deg 360deg"
        )
    else:
        fill = (
            f"#E8EDF5 0deg {visible_start:.1f}deg,"
            f"#1EA663 {visible_start:.1f}deg {green_stop:.1f}deg,"
            f"#2F80ED {green_stop:.1f}deg {filled_stop:.1f}deg,"
            f"#E8EDF5 {filled_stop:.1f}deg 360deg"
        )
    return (
        f"<div style='width:{width}px;height:{height}px;border-radius:{width}px "
        f"{width}px 0 0;background:conic-gradient(from 180deg at 50% 100%,"
        f"{fill});position:relative;box-shadow:0 20px 36px "
        f"rgba(47,128,237,0.17),inset 0 5px 8px rgba(255,255,255,0.48);"
        f"overflow:hidden;'>"
        f"<div style='position:absolute;left:{inner_x}px;right:{inner_x}px;"
        f"bottom:0;height:{inner_h}px;border-radius:120px 120px 0 0;"
        f"background:#FFFFFF;box-shadow:inset 0 6px 16px "
        f"rgba(16,24,40,0.08);'></div>"
        f"<strong style='position:absolute;left:0;right:0;bottom:{bottom}px;"
        f"text-align:center;font-size:{font_size}px;font-weight:950;"
        f"z-index:1;color:#142033;'>{clamped:.0f}%</strong>"
        f"</div>"
    )


def _donut_gauge(pct: float) -> str:
    clamped = max(0.0, min(pct, 100.0))
    filled = clamped * 3.6
    if filled <= 210:
        fill = (
            f"#185BDB 0deg {filled:.1f}deg,"
            f"#E8EDF5 {filled:.1f}deg 360deg"
        )
    else:
        fill = (
            f"#185BDB 0deg 210deg,"
            f"#28C7A6 210deg {filled:.1f}deg,"
            f"#E8EDF5 {filled:.1f}deg 360deg"
        )
    return (
        f"<div style='width:160px;height:160px;border-radius:50%;display:grid;"
        f"place-items:center;background:conic-gradient(from -90deg,{fill});"
        f"box-shadow:0 22px 45px rgba(47,128,237,0.22),"
        f"inset 0 6px 10px rgba(255,255,255,0.62),"
        f"inset 0 -10px 18px rgba(18,69,157,0.18);'>"
        f"<div style='width:126px;height:126px;border-radius:50%;display:grid;"
        f"place-items:center;background:linear-gradient(145deg,#FFFFFF,#EEF5FF);"
        f"box-shadow:inset 0 7px 18px rgba(16,24,40,0.08),"
        f"0 1px 0 rgba(255,255,255,0.9);'>"
        f"<span style='font-size:34px;font-weight:850;color:#16233A;'>{clamped:.0f}%</span>"
        f"</div></div>"
    )


def _scan_fact(value: str, label: str, tone: str = "neutral") -> str:
    colours = {
        "blue": ("#1F6FD0", "#F2F7FF", "#D7E6FF"),
        "green": ("#16794A", "#F1FAF4", "#D6F2E0"),
        "purple": ("#5A47B8", "#F6F4FD", "#E4DEF8"),
        "orange": ("#B4791A", "#FFF9EF", "#FBEBC8"),
        "neutral": ("#26313F", "#FFFFFF", "#E7E9ED"),
    }
    accent, fill, border = colours.get(tone, colours["neutral"])
    return (
        f"<div style='background:{fill};border:1px solid {border};"
        f"border-radius:14px;padding:12px 13px;min-height:70px;"
        f"box-shadow:0 10px 22px rgba(16,24,40,0.05);'>"
        f"<div style='font-size:1.12rem;line-height:1.1;font-weight:800;"
        f"color:{accent};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
        f"{html.escape(value)}</div>"
        f"<div style='font-size:0.68rem;color:#6B7280;font-weight:700;"
        f"text-transform:uppercase;letter-spacing:0.35px;margin-top:5px;'>"
        f"{html.escape(label)}</div></div>"
    )


def _scan_chip(value: str, label: str) -> str:
    return (
        f"<span style='display:inline-flex;align-items:center;border-radius:999px;"
        f"padding:8px 11px;background:#F2F7FF;border:1px solid #D8E6FB;"
        f"color:#34445D;font-weight:800;font-size:0.76rem;white-space:nowrap;'>"
        f"{html.escape(label)}: {html.escape(value)}</span>"
    )


def _progress_bar(pct: float, height: int = 34) -> str:
    progress_width = max(0.0, min(pct, 100.0))
    return (
        f"<div style='height:{height}px;border-radius:999px;padding:5px;"
        f"background:linear-gradient(180deg,#D9E3F1,#F7F9FC);"
        f"box-shadow:inset 0 7px 11px rgba(31,42,68,0.13),"
        f"inset 0 -2px 0 rgba(255,255,255,0.86);'>"
        f"<div style='height:100%;width:{progress_width:.2f}%;border-radius:999px;"
        f"background:linear-gradient(180deg,rgba(255,255,255,0.62),transparent 48%),"
        f"linear-gradient(90deg,#185BDB,#2F80ED 45%,#28C7A6);"
        f"box-shadow:0 15px 30px rgba(47,128,237,0.28),"
        f"inset 0 2px 0 rgba(255,255,255,0.52),"
        f"inset 0 -6px 12px rgba(18,69,157,0.19);overflow:hidden;"
        f"position:relative;'>"
        f"<span style='position:absolute;inset:0;background:repeating-linear-gradient("
        f"115deg,rgba(255,255,255,0.26) 0 9px,transparent 9px 21px);'></span>"
        f"</div></div>"
    )


def _progress_data(
    watchlist: str,
    symbol: str,
    done: int,
    total: int,
) -> dict[str, str | float | int]:
    safe_total = max(int(total or 0), 0)
    safe_done = min(max(int(done or 0), 0), safe_total) if safe_total else 0
    remaining = max(safe_total - safe_done, 0)
    pct = (safe_done / safe_total * 100) if safe_total else 0.0
    return {
        "watchlist": watchlist,
        "symbol": symbol,
        "done": safe_done,
        "total": safe_total,
        "remaining": remaining,
        "pct": pct,
    }


def _progress_shell(body: str, width: int = 780) -> str:
    return (
        f"<div style='display:flex;justify-content:center;padding:34px 16px;'>"
        f"<div style='width:min({width}px,100%);background:linear-gradient(145deg,#FFFFFF,#F5F9FF);"
        f"border:1px solid #DDE6F3;border-radius:22px;padding:32px 36px;"
        f"box-shadow:0 24px 70px rgba(16,24,40,0.10),"
        f"inset 0 1px 0 rgba(255,255,255,0.94);'>{body}</div></div>"
    )


def _progress_footer() -> str:
    return (
        f"<div style='margin-top:20px;background:#F4F8FF;border:1px solid #D8E6FB;"
        f"border-radius:14px;padding:13px 16px;font-size:0.84rem;color:#35445D;"
        f"line-height:1.5;box-shadow:inset 0 1px 0 rgba(255,255,255,0.86);'>"
        f"Results will open automatically when this scan finishes."
        f"</div>"
    )


def _progress_capsule(data: dict[str, str | float | int]) -> str:
    pct = float(data["pct"])
    return _progress_shell(
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;"
        f"gap:14px;margin-bottom:16px;'>"
        f"<div><div style='font-size:1.5rem;line-height:1.12;font-weight:850;color:#16233A;'>"
        f"Scanning {html.escape(str(data['watchlist']))}</div>"
        f"<div style='color:#6B7280;font-size:0.9rem;margin-top:5px;line-height:1.45;'>"
        f"Currently checking <b>{html.escape(str(data['symbol']))}</b>. "
        f"Results open when the scan finishes.</div></div>"
        f"<div style='font-size:2rem;font-weight:850;color:#1758D6;'>{pct:.0f}%</div>"
        f"</div>{_progress_bar(pct)}"
        f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));"
        f"gap:10px;margin-top:18px;'>"
        f"{_scan_fact(str(data['done']), 'Scanned', 'blue')}"
        f"{_scan_fact(str(data['total']), 'Total', 'purple')}"
        f"{_scan_fact(str(data['remaining']), 'Remaining', 'orange')}"
        f"{_scan_fact(str(data['symbol']), 'Current stock', 'green')}"
        f"</div>",
        width=780,
    )


def _progress_donut(data: dict[str, str | float | int]) -> str:
    pct = float(data["pct"])
    return _progress_shell(
        f"<div style='display:grid;grid-template-columns:170px 1fr;gap:24px;"
        f"align-items:center;'>"
        f"<div>{_donut_gauge(pct)}</div>"
        f"<div><div style='font-size:1.5rem;line-height:1.12;font-weight:850;color:#16233A;'>"
        f"Running Scan</div>"
        f"<div style='color:#6B7280;font-size:0.92rem;margin-top:6px;line-height:1.5;'>"
        f"<b>{data['done']}</b> of <b>{data['total']}</b> stocks scanned in "
        f"<b>{html.escape(str(data['watchlist']))}</b>.</div>"
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:16px;'>"
        f"{_scan_chip(str(data['symbol']), 'Current')}"
        f"{_scan_chip(str(data['remaining']), 'Remaining')}"
        f"{_scan_chip('Auto-open results', 'Next')}"
        f"</div></div></div>{_progress_footer()}",
        width=760,
    )


def _progress_speedometer(data: dict[str, str | float | int]) -> str:
    return _progress_shell(
        f"<div style='display:grid;grid-template-columns:180px 1fr;gap:20px;"
        f"align-items:center;'>"
        f"<div>{_speedometer(float(data['pct']), width=178, height=104)}</div>"
        f"<div><div style='font-size:1.5rem;line-height:1.12;font-weight:850;color:#16233A;'>"
        f"Scan Completion</div>"
        f"<div style='color:#6B7280;font-size:0.92rem;margin-top:6px;line-height:1.5;'>"
        f"<b>{html.escape(str(data['symbol']))}</b> is the current stock. "
        f"<b>{data['remaining']}</b> stocks remain in this watchlist.</div>"
        f"<div style='display:flex;gap:8px;flex-wrap:wrap;margin-top:16px;'>"
        f"{_scan_chip(str(data['done']), 'Scanned')}"
        f"{_scan_chip(str(data['total']), 'Total')}"
        f"{_scan_chip(str(data['watchlist']), 'Watchlist')}"
        f"</div></div></div>",
        width=720,
    )


def _pulse_bars(pct: float) -> str:
    clamped = max(0.0, min(pct, 100.0))
    heights = [22, 38, 51, 64, 74, 85, 78, 91]
    progress_units = clamped / 100.0 * len(heights)
    bars = ""
    for index, height in enumerate(heights):
        fill_ratio = max(0.0, min(progress_units - index, 1.0))
        fill_height = fill_ratio * 100.0
        is_current = 0.0 < fill_ratio < 1.0
        fill = ""
        if fill_ratio > 0.0:
            colour = (
                "linear-gradient(180deg,#7B61E3,#2F80ED)"
                if fill_ratio < 1.0 or index == len(heights) - 1
                else "linear-gradient(180deg,#2F80ED,#28C7A6)"
            )
            fill = (
                f"<span style='position:absolute;left:0;right:0;bottom:0;"
                f"height:{fill_height:.1f}%;background:{colour};"
                f"box-shadow:0 8px 16px rgba(47,128,237,0.14);'></span>"
            )
        border = "2px solid #2F80ED" if is_current else "1px solid #D8E6FB"
        shadow = (
            "0 10px 22px rgba(47,128,237,0.15)"
            if fill_ratio > 0.0 else "inset 0 1px 0 rgba(255,255,255,0.9)"
        )
        bars += (
            f"<span style='flex:1;height:{height}%;border-radius:999px 999px 6px 6px;"
            f"background:linear-gradient(180deg,#F5F8FC,#E8EDF5);"
            f"border:{border};min-height:18px;box-shadow:{shadow};"
            f"overflow:hidden;position:relative;box-sizing:border-box;'>{fill}</span>"
        )
    return (
        f"<div style='height:116px;display:flex;align-items:flex-end;gap:6px;"
        f"padding:14px;border-radius:16px;background:linear-gradient(180deg,#F8FBFF,#EEF6FF);"
        f"border:1px solid #D8E6FB;margin-top:18px;'>{bars}</div>"
    )


def _progress_pulse(data: dict[str, str | float | int]) -> str:
    pct = float(data["pct"])
    return _progress_shell(
        f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:14px;'>"
        f"<div><div style='font-size:1.5rem;line-height:1.12;font-weight:850;color:#16233A;'>"
        f"Scan Pulse</div>"
        f"<div style='color:#6B7280;font-size:0.92rem;margin-top:6px;line-height:1.5;'>"
        f"Currently checking <b>{html.escape(str(data['symbol']))}</b> in "
        f"<b>{html.escape(str(data['watchlist']))}</b>.</div></div>"
        f"<div style='font-size:2rem;font-weight:850;color:#1758D6;'>{pct:.0f}%</div>"
        f"</div>{_pulse_bars(pct)}"
        f"<div style='margin-top:18px;background:#F4F8FF;border:1px solid #D8E6FB;"
        f"border-radius:14px;padding:13px 16px;font-size:0.84rem;color:#35445D;'>"
        f"<b>{data['done']}</b> scanned, <b>{data['remaining']}</b> remaining. "
        f"Current stock: <b>{html.escape(str(data['symbol']))}</b>.</div>",
        width=760,
    )


def _progress_ribbon(data: dict[str, str | float | int]) -> str:
    pct = max(0.0, min(float(data["pct"]), 100.0))
    pointer = max(2.5, min(pct, 96.0))
    fill_bg = (
        "#1EA663"
        if pct <= 35.0
        else "linear-gradient(90deg,#1EA663 0 35%,#2F80ED 100%)"
    )
    return _progress_shell(
        f"<div style='font-size:1.5rem;line-height:1.12;font-weight:850;color:#16233A;'>"
        f"Scanning {html.escape(str(data['watchlist']))}</div>"
        f"<div style='color:#6B7280;font-size:0.92rem;margin-top:6px;line-height:1.5;'>"
        f"Currently checking <b>{html.escape(str(data['symbol']))}</b>. "
        f"<b>{data['done']}</b> of <b>{data['total']}</b> stocks scanned.</div>"
        f"<div style='position:relative;margin:43px 0 22px;'>"
        f"<span style='position:absolute;left:calc({pointer:.2f}% - 22px);top:-36px;"
        f"padding:6px 10px;border-radius:10px;color:#FFFFFF;background:#1758D6;"
        f"font-weight:850;box-shadow:0 10px 22px rgba(23,88,214,0.25);'>{pct:.0f}%</span>"
        f"<div style='height:18px;border-radius:999px;background:#E8EDF5;"
        f"box-shadow:0 15px 28px rgba(47,128,237,0.18),"
        f"inset 0 1px 0 rgba(255,255,255,0.5);overflow:hidden;'>"
        f"<div style='height:100%;width:{pct:.2f}%;border-radius:999px;"
        f"background:{fill_bg};box-shadow:0 15px 28px rgba(47,128,237,0.18),"
        f"inset 0 1px 0 rgba(255,255,255,0.5);'></div>"
        f"</div>"
        f"</div>"
        f"<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));"
        f"gap:10px;margin-top:18px;'>"
        f"{_scan_fact(str(data['done']), 'Scanned', 'blue')}"
        f"{_scan_fact(str(data['total']), 'Total', 'purple')}"
        f"{_scan_fact(str(data['remaining']), 'Remaining', 'orange')}"
        f"{_scan_fact(str(data['symbol']), 'Current stock', 'green')}"
        f"</div>",
        width=760,
    )


_PROGRESS_RENDERERS = {
    "capsule": _progress_capsule,
    "donut": _progress_donut,
    "speedometer": _progress_speedometer,
    "pulse": _progress_pulse,
    "ribbon": _progress_ribbon,
}


def _progress_style() -> str:
    try:
        style = str(load_preferences().get("scan_progress_style", "speedometer"))
    except Exception:
        style = "speedometer"
    return style if style in SCAN_PROGRESS_STYLE_OPTIONS else "speedometer"


def scan_progress(
    watchlist: str, symbol: str, done: int, total: int,
) -> str:
    """HTML for the standalone scan-progress page.

    Returned rather than written so the caller can pump it into a single
    ``st.empty()`` placeholder — Streamlit's own ``st.progress`` renders a
    thin bar wedged into whatever page is already on screen, which reads as
    the scan belonging to that page. This replaces the page content instead.
    """
    data = _progress_data(watchlist, symbol, done, total)
    renderer = _PROGRESS_RENDERERS.get(_progress_style(), _progress_speedometer)
    return renderer(data)


def pending_panel(title: str, phase: str, note: str = "") -> None:
    """A whole panel whose data source does not exist yet.

    Used for the sector heatmap and sector-strength blocks: the design calls
    for them, so the box and title are drawn, but nothing inside is invented.
    """
    with st.container(border=True):
        section_title(title)
        st.markdown(
            f"<div style='padding:22px 12px;text-align:center;"
            f"color:{_PENDING_COLOR};'>"
            f"<div style='font-size:1.6rem;line-height:1;'>&mdash;</div>"
            f"<div style='font-size:0.78rem;font-style:italic;"
            f"margin-top:4px;'>"
            f"{html.escape(PENDING_PHASES.get(phase, PENDING_PHASES['new']))}"
            f"</div>"
            f"<div style='font-size:0.72rem;margin-top:2px;'>"
            f"{html.escape(note)}</div></div>",
            unsafe_allow_html=True,
        )


def bias_pill(text: str, tone: Tone = "neutral") -> str:
    """Inline coloured pill — returned as HTML for use inside tables/rows.

    Uses the badge fill rather than the card tint: the card tint is nearly
    white by design and a pill needs to read as a filled chip against a white
    table row.
    """
    accent, _tint, soft, _solid = _TONES.get(tone, _TONES["neutral"])
    return (
        f"<span style='background:{soft};color:{accent};border-radius:6px;"
        f"padding:2px 8px;font-size:0.72rem;font-weight:700;"
        f"white-space:nowrap;'>{html.escape(text)}</span>"
    )
