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


def filter_chip(label: str, value: str, icon: str = "") -> None:
    """Compact read-out box for the results page's filter strip.

    Shorter than :func:`stat_card` on purpose: these echo the scan's settings
    and sat at the same 96px height as the metric cards below them, so two
    visually identical rows of boxes competed for attention when only the
    second carries numbers.
    """
    glyph = _icon_svg(icon, size=14, colour="#6B7280") if icon else ""
    badge_html = (
        f"<span style='display:inline-flex;align-items:center;"
        f"justify-content:center;width:22px;height:22px;border-radius:6px;"
        f"background:#F0F2F5;flex:0 0 22px;'>{glyph}</span>" if glyph else ""
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
        f"margin-top:3px;line-height:1.2;'>{html.escape(str(value))}</div>"
        f"</div>",
        unsafe_allow_html=True,
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


# Presentation milestones for the scan page.
#
# These are NOT backend phases. Every one of them happens inside a single
# iteration of the scan loop, once per stock — so a checklist that ticked on
# real phase transitions would reset and re-tick fifty times and read as
# flicker. The measured quantities are the stock count and the percentage,
# which is what the headline shows; these rows are paced off that percentage
# and named to describe the run as a whole rather than to claim a phase
# boundary was observed.
_SCAN_MILESTONES: list[tuple[float, str]] = [
    (0.0, "Preparing market data"),
    (20.0, "Scanning watchlist"),
    (45.0, "Applying filters"),
    (70.0, "Calculating scores"),
    (90.0, "Building results"),
]


def _milestone_row(label: str, state: str) -> str:
    """One checklist row: done (tick), active (ring), or pending (hollow)."""
    if state == "done":
        mark = (
            "<span style='display:inline-flex;align-items:center;"
            "justify-content:center;width:18px;height:18px;border-radius:50%;"
            "background:#22A55B;flex:0 0 18px;'>"
            "<svg width='11' height='11' viewBox='0 0 24 24' fill='none' "
            "stroke='#fff' stroke-width='3.2' stroke-linecap='round' "
            "stroke-linejoin='round'><polyline points='4.5 12.5 9.5 17.5 "
            "19.5 6.5'/></svg></span>"
        )
        colour, weight = "#26313F", "500"
    elif state == "active":
        mark = (
            "<span style='display:inline-flex;width:18px;height:18px;"
            "border-radius:50%;border:2.5px solid #2F80ED;flex:0 0 18px;"
            "box-sizing:border-box;'></span>"
        )
        colour, weight = "#1F6FD0", "600"
    else:
        mark = (
            "<span style='display:inline-flex;width:18px;height:18px;"
            "border-radius:50%;border:2px solid #D8DCE2;flex:0 0 18px;"
            "box-sizing:border-box;'></span>"
        )
        colour, weight = "#9AA0A8", "400"
    return (
        f"<div style='display:flex;align-items:center;gap:10px;"
        f"padding:5px 0;'>{mark}"
        f"<span style='font-size:0.88rem;color:{colour};"
        f"font-weight:{weight};'>{html.escape(label)}</span></div>"
    )


def _donut(pct: float, size: int = 138) -> str:
    """Circular progress ring with the percentage in the middle."""
    r = 58.0
    circ = 2 * 3.14159265 * r
    filled = circ * max(0.0, min(pct, 100.0)) / 100.0
    return (
        f"<svg width='{size}' height='{size}' viewBox='0 0 140 140' "
        f"style='display:block;'>"
        f"<circle cx='70' cy='70' r='{r}' fill='none' stroke='#EAECEF' "
        f"stroke-width='13'/>"
        f"<circle cx='70' cy='70' r='{r}' fill='none' stroke='#2F80ED' "
        f"stroke-width='13' stroke-linecap='round' "
        f"stroke-dasharray='{filled:.2f} {circ:.2f}' "
        f"transform='rotate(-90 70 70)'/>"
        f"<text x='70' y='70' text-anchor='middle' dominant-baseline='central' "
        f"font-size='27' font-weight='700' fill='#16233A' "
        f"font-family='ui-sans-serif,system-ui,sans-serif'>{pct:.0f}%</text>"
        f"</svg>"
    )


def scan_progress(
    watchlist: str, symbol: str, done: int, total: int,
) -> str:
    """HTML for the standalone scan-progress page.

    Returned rather than written so the caller can pump it into a single
    ``st.empty()`` placeholder — Streamlit's own ``st.progress`` renders a
    thin bar wedged into whatever page is already on screen, which reads as
    the scan belonging to that page. This replaces the page content instead.
    """
    pct = (done / total * 100) if total else 0.0

    rows = ""
    for i, (threshold, label) in enumerate(_SCAN_MILESTONES):
        nxt = (
            _SCAN_MILESTONES[i + 1][0]
            if i + 1 < len(_SCAN_MILESTONES) else 101.0
        )
        state = "done" if pct >= nxt else "active" if pct >= threshold else "todo"
        rows += _milestone_row(label, state)

    return (
        f"<div style='display:flex;justify-content:center;padding:34px 16px;'>"
        f"<div style='width:min(720px,100%);background:#FFFFFF;"
        f"border:1px solid {_BORDER};border-radius:18px;padding:34px 38px;"
        f"box-shadow:0 1px 3px rgba(16,24,40,0.05);'>"
        f"<div style='display:flex;align-items:center;gap:34px;"
        f"flex-wrap:wrap;'>"
        f"<div style='flex:0 0 auto;'>{_donut(pct)}</div>"
        f"<div style='flex:1 1 260px;min-width:240px;'>"
        f"<div style='font-size:1.3rem;font-weight:700;color:#16233A;'>"
        f"Running Analysis&hellip;</div>"
        f"<div style='font-size:0.9rem;color:#6B7280;margin:2px 0 12px 0;'>"
        f"Analysing <b>{done}</b> of <b>{total}</b> stocks "
        f"&mdash; {pct:.0f}% complete</div>"
        f"{rows}</div></div>"
        f"<div style='margin-top:22px;background:#F4F7FE;"
        f"border:1px solid #DCE6FA;border-radius:11px;padding:13px 16px;"
        f"font-size:0.84rem;color:#3A4A63;'>"
        f"Currently scanning <b>{html.escape(symbol)}</b> from "
        f"<b>{html.escape(watchlist)}</b>. You will be taken to the results "
        f"automatically when the scan finishes."
        f"</div></div></div>"
    )


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
