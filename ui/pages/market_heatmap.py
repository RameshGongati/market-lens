"""Market heatmap page and dashboard widget."""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlencode

import streamlit as st

from data import market_heatmap as mh
from ui.components.panels import page_title, section_title, spacer
from ui.components.stock_card import build_detail_url


def _credentials_for(source_name: str) -> dict[str, str]:
    creds = st.session_state.get("credentials", {}) or {}
    return dict(creds.get(source_name, {}) or {})


@st.cache_data(ttl=900, show_spinner=False)
def _cached_group_tiles() -> list[dict[str, Any]]:
    return mh.group_tiles({}, allow_basket_fallback=True)


@st.cache_data(ttl=300, show_spinner=False)
def _cached_symbol_stock_tiles(
    symbols: tuple[str, ...],
    source_name: str,
    credentials: tuple[tuple[str, str], ...],
) -> list[dict[str, Any]]:
    return mh.stock_tiles_for_symbols(
        symbols,
        source_name=source_name,
        credentials=dict(credentials),
        results={},
    )


def _overlay_setup_counts(
    tiles: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tile in tiles:
        group = mh.group_by_id(str(tile["id"]))
        symbols = mh.symbols_for_group(group) if group else []
        item = dict(tile)
        item["setup_count"] = mh.setup_count(symbols, results)
        item["symbol_count"] = len(symbols)
        out.append(item)
    return out


def _overlay_stock_setups(
    rows: list[dict[str, Any]],
    results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        res = results.get(str(item["symbol"]), {})
        item["has_setup"] = mh.has_setup(res)
        item["setup"] = _setup_label(res)
        out.append(item)
    return out


def _setup_label(result: dict[str, Any]) -> str:
    if result.get("demand_zones"):
        return "Demand setup"
    if result.get("supply_zones"):
        return "Supply setup"
    if result.get("confirmation_zones"):
        return "Confirmed zone"
    return "No scan setup"


def _tile_style(change_pct: float, ok: bool) -> tuple[str, str, str, str]:
    if not ok:
        return "#F5F7FA", "#DDE3EC", "#667085", "#344054"
    strength = min(abs(change_pct), 2.5) / 2.5
    if change_pct >= 0:
        lightness = 92 - int(strength * 32)
        bg = f"hsl(151 43% {lightness}%)"
        border = "#78CBA0"
        value = "#0F6B45" if strength < 0.65 else "#FFFFFF"
        text = "#142033" if strength < 0.65 else "#FFFFFF"
    else:
        lightness = 93 - int(strength * 34)
        bg = f"hsl(3 58% {lightness}%)"
        border = "#EF9B95"
        value = "#B42318" if strength < 0.65 else "#FFFFFF"
        text = "#142033" if strength < 0.65 else "#FFFFFF"
    return bg, border, value, text


def _change_text(row: dict[str, Any]) -> str:
    return f"{float(row.get('change_pct', 0.0)):+.2f}%" if row.get("ok") else "-"


def _setup_text(row: dict[str, Any]) -> str:
    count = int(row.get("setup_count", 0) or 0)
    if count:
        return f"{count} scan setup{'s' if count != 1 else ''}"
    return str(row.get("setup") or "No scan setup")


def _compact_setup_text(row: dict[str, Any]) -> str:
    count = int(row.get("setup_count", 0) or 0)
    if count:
        return f"{count} setup{'s' if count != 1 else ''}"
    return "No setup"


def _group_tile_html(
    row: dict[str, Any],
    *,
    compact: bool = False,
    joined: bool = False,
    href: str = "",
    selected: bool = False,
) -> str:
    bg, border, value, text = _tile_style(float(row.get("change_pct", 0.0)), bool(row.get("ok")))
    if compact and joined:
        label_size, value_size, pad, min_h = "0.76rem", "1.16rem", "9px 10px", "76px"
    elif compact:
        label_size, value_size, pad, min_h = "0.68rem", "0.98rem", "7px 9px", "58px"
    else:
        label_size, value_size, pad, min_h = "0.92rem", "1.65rem", "14px 16px", "122px"
    radius = "0" if joined else ("8px" if compact else "10px")
    shadow = "none" if joined else "0 6px 14px rgba(16,24,40,0.055)"
    selected_ring = f"box-shadow:{shadow};"
    source = row.get("source", "")
    note = "" if compact else row.get("note") or ("Basket avg" if source == "basket" else "")
    meta = _compact_setup_text(row) if compact else _setup_text(row)
    if note:
        meta = f"{meta} - {html.escape(str(note))}"
    tile = (
        f"<div style='min-height:{min_h};border-radius:{radius};padding:{pad};"
        f"background:{bg};border:1px solid {border};"
        f"{selected_ring}box-sizing:border-box;height:100%;"
        f"{'cursor:pointer;transition:filter 140ms ease, transform 140ms ease;' if href else ''}'>"
        f"<div style='font-size:{label_size};font-weight:850;color:{text};"
        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
        f"{html.escape(str(row['label']))}</div>"
        f"<div style='font-size:{value_size};font-weight:950;color:{value};"
        f"line-height:1.05;margin-top:{'4px' if compact else '8px'};'>"
        f"{_change_text(row)}</div>"
        f"<div style='font-size:{'0.62rem' if compact else '0.72rem'};"
        f"color:{text};opacity:0.82;margin-top:{'5px' if compact else '9px'};"
        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>{meta}</div>"
        f"</div>"
    )
    if not href:
        return tile
    return (
        f"<a href='{html.escape(href, quote=True)}' target='_self' "
        f"style='display:block;height:100%;text-decoration:none;color:inherit;'>"
        f"{tile}</a>"
    )


def _stock_tile_html(
    row: dict[str, Any],
    *,
    compact: bool = False,
    joined: bool = False,
) -> str:
    bg, border, value, text = _tile_style(float(row.get("change_pct", 0.0)), bool(row.get("ok")))
    url = html.escape(build_detail_url(str(row["symbol"]), "NSE"), quote=True)
    price = f"Rs {float(row.get('price', 0.0)):,.2f}" if row.get("ok") else "Price unavailable"
    name = html.escape(str(row.get("name") or row["symbol"]))
    setup = html.escape(str(row.get("setup") or "No setup").replace("No scan setup", "No setup"))
    radius = "0" if joined else "10px"
    shadow = "none" if joined else "0 10px 22px rgba(16,24,40,0.07)"
    min_h = "82px" if compact else "128px"
    pad = "9px 10px" if compact else "14px 16px"
    symbol_size = "0.8rem" if compact else "0.95rem"
    value_size = "0.98rem" if compact else "1.18rem"
    name_size = "0.6rem" if compact else "0.68rem"
    price_size = "0.68rem" if compact else "0.78rem"
    setup_size = "0.62rem" if compact else "0.72rem"
    return (
        f"<a href='{url}' target='_blank' style='text-decoration:none;color:inherit;'>"
        f"<div style='min-height:{min_h};border-radius:{radius};padding:{pad};"
        f"background:{bg};border:1px solid {border};"
        f"box-shadow:{shadow};height:100%;box-sizing:border-box;'>"
        f"<div style='display:flex;justify-content:space-between;gap:10px;'>"
        f"<div style='min-width:0;'><div style='font-size:{symbol_size};font-weight:900;"
        f"color:{text};white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
        f"{html.escape(str(row['symbol']))}</div>"
        f"<div style='font-size:{name_size};color:{text};opacity:0.72;"
        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>{name}</div></div>"
        f"<div style='font-size:{value_size};font-weight:950;color:{value};white-space:nowrap;'>"
        f"{_change_text(row)}</div></div>"
        f"<div style='margin-top:{'6px' if compact else '13px'};font-size:{price_size};"
        f"color:{text};opacity:0.86;'>"
        f"{price}</div>"
        f"<div style='margin-top:{'5px' if compact else '9px'};font-size:{setup_size};"
        f"color:{text};opacity:0.82;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
        f"{setup}</div>"
        f"</div></a>"
    )


def dashboard_heatmap_tiles() -> list[dict[str, Any]]:
    """Cached market heatmap rows for dashboard summary cards."""
    return _cached_group_tiles()


def strong_sector_count(tiles: list[dict[str, Any]]) -> int:
    """Count sectors with positive available change."""
    return sum(
        1 for row in tiles
        if row.get("kind") == "sector" and row.get("ok") and row.get("change_pct", 0.0) > 0
    )


def render_dashboard_heatmap_card(results: dict[str, dict[str, Any]]) -> None:
    """Compact non-clickable dashboard heatmap with a full-page link."""
    tiles = _overlay_setup_counts(_cached_group_tiles(), results)
    up = sum(1 for row in tiles if row.get("ok") and row.get("change_pct", 0.0) > 0)
    down = sum(1 for row in tiles if row.get("ok") and row.get("change_pct", 0.0) < 0)
    flat = len(tiles) - up - down

    with st.container(border=True):
        title, action = st.columns([2, 1])
        with title:
            section_title("Market Heatmap")
        with action:
            if st.button(
                "View Full Heatmap",
                icon=":material/open_in_new:",
                use_container_width=True,
                key="mo_full_heatmap",
            ):
                st.session_state.active_page = "market_heatmap"
                st.rerun()

        cells = "".join(_group_tile_html(row, compact=True) for row in tiles)
        st.markdown(
            f"<div style='display:grid;grid-template-columns:repeat(4,minmax(0,1fr));"
            f"gap:5px;align-items:stretch;margin-top:2px;'>{cells}</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='font-size:0.78rem;color:#667085;text-align:center;margin-top:8px;'>"
            f"<b style='color:#16794A;'>{up}</b> up &nbsp;-&nbsp; "
            f"<b style='color:#C23B33;'>{down}</b> down &nbsp;-&nbsp; "
            f"{flat} flat/unavailable</div>",
            unsafe_allow_html=True,
        )


def render_dashboard_sector_strength_card(results: dict[str, dict[str, Any]]) -> None:
    """Dashboard sector-strength bars from the same heatmap data."""
    tiles = _overlay_setup_counts(_cached_group_tiles(), results)
    sectors = [
        row for row in tiles
        if row.get("kind") == "sector" and row.get("ok")
    ]
    sectors.sort(key=lambda row: float(row.get("change_pct", 0.0)), reverse=True)
    max_abs = max([abs(float(row.get("change_pct", 0.0))) for row in sectors] or [1.0])

    with st.container(border=True):
        section_title("Sector Strength")
        if not sectors:
            st.caption("Sector quote data is unavailable right now.")
            return
        rows = "".join(_sector_strength_row(row, max_abs) for row in sectors)
        st.markdown(
            f"<div style='display:flex;flex-direction:column;gap:7px;'>{rows}</div>",
            unsafe_allow_html=True,
        )


def _sector_strength_row(row: dict[str, Any], max_abs: float) -> str:
    change = float(row.get("change_pct", 0.0))
    width = min(abs(change) / max(max_abs, 0.01) * 100.0, 100.0)
    positive = change >= 0
    colour = "#1EA663" if positive else "#D84D45"
    pale = "#E7F5EE" if positive else "#FDECEC"
    setups = int(row.get("setup_count", 0) or 0)
    setup_text = f"{setups} setup{'s' if setups != 1 else ''}" if setups else "No setup"
    fill = (
        f"<div style='height:100%;width:{width:.1f}%;border-radius:999px;"
        f"background:{colour};box-shadow:0 6px 12px {pale};'></div>"
    )
    return (
        f"<div style='display:grid;grid-template-columns:92px 1fr 54px;"
        f"align-items:center;gap:8px;'>"
        f"<div style='font-size:0.76rem;font-weight:850;color:#142033;"
        f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
        f"{html.escape(str(row['label']))}</div>"
        f"<div style='min-width:0;'>"
        f"<div style='height:9px;border-radius:999px;background:#EEF2F7;"
        f"overflow:hidden;box-shadow:inset 0 1px 2px rgba(16,24,40,0.08);'>"
        f"{fill}"
        f"</div><div style='font-size:0.62rem;color:#667085;margin-top:2px;'>"
        f"{setup_text}</div></div>"
        f"<div style='font-size:0.78rem;font-weight:900;color:{colour};"
        f"text-align:right;'>{change:+.2f}%</div></div>"
    )


def _heatmap_group_url(group_id: str) -> str:
    return "?" + urlencode({"heatmap_group": group_id, "heatmap_view": "Group Stocks"})


def _render_joined_grid(cells: str, *, columns: int) -> None:
    st.markdown(
        f"<div style='display:grid;grid-template-columns:repeat({columns},minmax(0,1fr));"
        f"gap:0;border:1px solid #D7DEE8;border-radius:10px;overflow:hidden;"
        f"background:#FFFFFF;'>{cells}</div>",
        unsafe_allow_html=True,
    )


def _custom_watchlist_options() -> list[tuple[str, str]]:
    try:
        from watchlist.manager import get_all_watchlists

        return [(f"custom:{wl.id}", f"My Watchlist - {wl.name}") for wl in get_all_watchlists()]
    except Exception:
        return []


def _stock_universe_options() -> list[tuple[str, str]]:
    options = [(f"group:{group.id}", f"{group.label} Group") for group in mh.GROUPS]
    options.extend(
        (f"watchlist:{item['name']}", f"Watchlist - {item['name']}")
        for item in mh.predefined_watchlists()
        if item.get("name")
    )
    options.extend(_custom_watchlist_options())
    return options


def _resolve_stock_universe(option_key: str) -> tuple[str, tuple[str, ...]]:
    kind, _, value = option_key.partition(":")
    if kind == "watchlist":
        return value, tuple(mh.symbols_for_watchlist(value))
    if kind == "custom":
        try:
            from watchlist.manager import get_all_watchlists, get_stocks

            watchlist_id = int(value)
            watchlist = next((wl for wl in get_all_watchlists() if wl.id == watchlist_id), None)
            label = watchlist.name if watchlist else "My Watchlist"
            return label, tuple(stock.symbol for stock in get_stocks(watchlist_id))
        except Exception:
            return "My Watchlist", ()
    group = mh.group_by_id(value)
    if group is None:
        group = mh.group_by_id("banks")
    if group is None:
        return "Stocks", ()
    return group.label, tuple(mh.symbols_for_group(group))


def _selected_universe_key(selected_group_id: str) -> str:
    default_key = f"group:{selected_group_id}"
    options = dict(_stock_universe_options())
    widget_value = st.session_state.get("hm_stock_universe_select")
    current = str(widget_value or st.session_state.get("heatmap_stock_universe", default_key))
    if current not in options:
        current = default_key if default_key in options else next(iter(options), default_key)
    st.session_state["heatmap_stock_universe"] = current
    return current


def render_market_heatmap() -> None:
    """Render the full market heatmap page."""
    results: dict[str, dict[str, Any]] = st.session_state.get("analysis_results", {}) or {}
    source_name = st.session_state.get("selected_data_source", "Yahoo Finance")
    credentials = tuple(sorted(_credentials_for(source_name).items()))

    head_l, head_r = st.columns([3, 2])
    with head_l:
        st.markdown(
            "<div style='font-size:0.78rem;color:#9AA0A8;margin-bottom:2px;'>"
            "Dashboard &nbsp;&rsaquo;&nbsp; "
            "<span style='color:#4A5361;font-weight:600;'>Market Heatmap</span></div>",
            unsafe_allow_html=True,
        )
        page_title(
            "Market Heatmap",
            f"Indices, sectors and stock movers - quotes from {source_name}.",
            icon="target",
        )
    with head_r:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Refresh Heatmap", icon=":material/refresh:",
                         use_container_width=True, key="hm_refresh"):
                _cached_group_tiles.clear()
                _cached_symbol_stock_tiles.clear()
                st.rerun()
        with c2:
            if st.button("Back to Dashboard", icon=":material/arrow_back:",
                         use_container_width=True, key="hm_back"):
                st.session_state.active_page = "dashboard"
                st.rerun()

    spacer(12)
    group_rows = _overlay_setup_counts(_cached_group_tiles(), results)
    st.session_state.setdefault("heatmap_selected_group", "banks")
    selected_id = st.session_state.get("heatmap_selected_group", "banks")

    selected_group = mh.group_by_id(str(st.session_state.get("heatmap_selected_group", selected_id)))
    if selected_group is None:
        selected_group = mh.group_by_id("banks")
    selected_id = selected_group.id if selected_group else "banks"

    pending_view = st.session_state.pop("hm_view_pending", None)
    if pending_view in {"Heatmap", "Group Stocks"}:
        st.session_state["hm_view_widget"] = pending_view
    if st.session_state.get("hm_view_widget") not in {None, "Heatmap", "Group Stocks"}:
        st.session_state["hm_view_widget"] = "Heatmap"
    st.session_state.setdefault("hm_view_widget", "Heatmap")
    view = st.segmented_control(
        "Heatmap view",
        ["Heatmap", "Group Stocks"],
        key="hm_view_widget",
        label_visibility="collapsed",
    ) or "Heatmap"

    if view == "Heatmap":
        _render_group_grid(group_rows, selected_id)
    else:
        _render_group_detail(selected_id, source_name, credentials, results)


def _render_group_grid(rows: list[dict[str, Any]], selected_id: str) -> None:
    with st.container(border=True):
        title_col, sort_col = st.columns([2, 1])
        with title_col:
            section_title("Heatmap")
            st.caption("Select a tile to inspect its stocks.")
        with sort_col:
            sort_mode = st.selectbox(
                "Sort heatmap",
                ["% Change: High to Low", "% Change: Low to High", "Setups First", "Name"],
                key="hm_sort",
                label_visibility="collapsed",
            )
        mode = st.radio(
            "Heatmap filter",
            ["All", "Gainers", "Losers", "With Setups"],
            horizontal=True,
            label_visibility="collapsed",
            key="hm_filter",
        )

        shown = mh.sort_tiles(mh.filter_tiles(rows, mode), sort_mode)
        if not shown:
            st.info("No groups match the current filter.")
            return
        cells = "".join(
            _group_tile_html(
                row,
                compact=True,
                joined=True,
                href=_heatmap_group_url(str(row["id"])),
                selected=row["id"] == selected_id,
            )
            for row in shown
        )
        _render_joined_grid(cells, columns=5)


def _render_group_detail(
    selected_group_id: str,
    source_name: str,
    credentials: tuple[tuple[str, str], ...],
    results: dict[str, dict[str, Any]],
) -> None:
    universe_key = _selected_universe_key(selected_group_id)
    options = _stock_universe_options()
    option_keys = [key for key, _ in options]
    labels = {key: label for key, label in options}

    with st.container(border=True):
        head, universe_col, sort_col = st.columns([1.8, 1.25, 1])
        with universe_col:
            chosen_key = st.selectbox(
                "Universe",
                option_keys,
                index=option_keys.index(universe_key) if universe_key in option_keys else 0,
                format_func=lambda key: labels.get(key, key),
                key="hm_stock_universe_select",
                label_visibility="collapsed",
            )
            st.session_state["heatmap_stock_universe"] = chosen_key
        title, symbols = _resolve_stock_universe(chosen_key)
        if chosen_key.startswith("group:"):
            st.session_state["heatmap_selected_group"] = chosen_key.partition(":")[2]
        rows = _overlay_stock_setups(
            _cached_symbol_stock_tiles(symbols, source_name, credentials),
            results,
        )
        gainers = sum(1 for row in rows if row.get("ok") and row.get("change_pct", 0.0) > 0)
        losers = sum(1 for row in rows if row.get("ok") and row.get("change_pct", 0.0) < 0)
        setups = sum(1 for row in rows if row.get("has_setup"))

        with head:
            section_title(f"{title} Stocks")
            st.caption(
                f"{len(rows)} stocks - {gainers} gainers - {losers} losers - {setups} with setup"
            )
        with sort_col:
            stock_sort = st.selectbox(
                "Sort stocks",
                ["% Change: High to Low", "% Change: Low to High", "Setups First", "Name"],
                key="hm_stock_sort",
                label_visibility="collapsed",
            )
        stock_mode = st.radio(
            "Stock filter",
            ["All", "Gainers", "Losers", "With Setups"],
            horizontal=True,
            label_visibility="collapsed",
            key="hm_stock_filter",
        )
        shown = mh.sort_tiles(mh.filter_tiles(rows, stock_mode), stock_sort)
        if not shown:
            st.info("No stocks match the current filter.")
            return
        cells = "".join(_stock_tile_html(row, compact=True, joined=True) for row in shown)
        _render_joined_grid(cells, columns=6)


def render_dashboard_movers_card(
    title: str,
    symbols: tuple[str, ...],
    source_name: str,
    credentials: tuple[tuple[str, str], ...],
    results: dict[str, dict[str, Any]],
    *,
    limit: int = 5,
    compact: bool = False,
) -> None:
    rows = _overlay_stock_setups(
        _cached_symbol_stock_tiles(symbols, source_name, credentials),
        results,
    )
    valid = [r for r in rows if r.get("ok")]
    gainers = sorted(
        [r for r in valid if float(r.get("change_pct", 0.0)) > 0],
        key=lambda r: r.get("change_pct", 0.0),
        reverse=True,
    )[:limit]
    losers = sorted(
        [r for r in valid if float(r.get("change_pct", 0.0)) < 0],
        key=lambda r: r.get("change_pct", 0.0),
    )[:limit]
    volume = sorted(
        [r for r in valid if int(r.get("volume", 0) or 0) > 0],
        key=lambda r: int(r.get("volume", 0) or 0),
        reverse=True,
    )[:limit]

    with st.container(border=True):
        if compact:
            st.markdown(
                f"<div style='font-size:0.94rem;font-weight:900;color:#142033;"
                f"margin-bottom:2px;'>{html.escape(title)}</div>",
                unsafe_allow_html=True,
            )
        else:
            section_title(title)
            st.caption(f"Top {limit} from this universe.")
        left, mid, right = st.columns(3)
        with left:
            _render_mover_list("Top Gainers", gainers, "change", compact=compact)
        with mid:
            _render_mover_list("Top Losers", losers, "change", compact=compact)
        with right:
            _render_mover_list("Top Volume", volume, "volume", compact=compact)


def _render_mover_list(
    title: str,
    rows: list[dict[str, Any]],
    metric: str,
    *,
    compact: bool = False,
) -> None:
    title_size = "0.72rem" if compact else "0.84rem"
    row_height = "28px" if compact else "38px"
    row_pad = "3px 0" if compact else "6px 0"
    symbol_size = "0.68rem" if compact else "0.78rem"
    price_size = "0.56rem" if compact else "0.62rem"
    value_size = "0.68rem" if compact else "0.8rem"
    if not rows:
        st.markdown(
            f"<div style='font-size:{title_size};font-weight:900;color:#142033;"
            f"margin-bottom:6px;'>{html.escape(title)}</div>",
            unsafe_allow_html=True,
        )
        st.caption("No available quote data.")
        return
    items: list[str] = []
    for idx, row in enumerate(rows, 1):
        fg = "#16794A" if row.get("change_pct", 0.0) >= 0 else "#C23B33"
        url = html.escape(build_detail_url(str(row["symbol"]), "NSE"), quote=True)
        price = f"Rs {float(row.get('price', 0.0)):,.2f}" if row.get("price") else "-"
        value = _change_text(row) if metric == "change" else _format_volume(row.get("volume", 0))
        value_colour = fg if metric == "change" else "#175CD3"
        items.append(
            f"<a href='{url}' target='_blank' style='text-decoration:none;color:inherit;'>"
            f"<div style='display:flex;align-items:center;gap:8px;"
            f"min-height:{row_height};padding:{row_pad};border-bottom:1px solid #EEF0F3;'>"
            f"<div style='width:24px;flex:0 0 24px;font-size:0.72rem;"
            f"color:#98A2B3;font-weight:850;'>{idx}</div>"
            f"<div style='min-width:0;flex:1 1 auto;'>"
            f"<div style='font-size:{symbol_size};font-weight:900;color:#142033;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
            f"{html.escape(str(row['symbol']))}</div>"
            f"<div style='font-size:{price_size};color:#667085;line-height:1.15;"
            f"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>{price}</div>"
            f"</div>"
            f"<div style='flex:0 0 auto;text-align:right;font-size:{value_size};"
            f"font-weight:900;color:{value_colour};white-space:nowrap;'>{value}</div>"
            f"</div></a>"
        )
    st.markdown(
        f"<div style='font-size:{title_size};font-weight:900;color:#142033;"
        f"margin-bottom:{'2px' if compact else '6px'};'>{html.escape(title)}</div>"
        f"<div style='display:flex;flex-direction:column;gap:0;'>"
        f"{''.join(items)}</div>",
        unsafe_allow_html=True,
    )


def _format_volume(value: Any) -> str:
    volume = int(value or 0)
    if volume >= 10_000_000:
        return f"{volume / 10_000_000:.1f}Cr"
    if volume >= 100_000:
        return f"{volume / 100_000:.1f}L"
    if volume >= 1_000:
        return f"{volume / 1_000:.1f}K"
    return str(volume)
