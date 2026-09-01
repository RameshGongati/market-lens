"""Settings — configuration, diagnostics and platform preferences.

Laid out as the design specifies: a status strip across the top, the recorded
sidebar selections beneath it, then paired panels for chart/alert/appearance
options, Telegram setup, the background monitor, data management and the
roadmap.

**Controls that are not wired to anything are rendered DISABLED, not hidden.**
The design calls for desktop notifications, sound alerts, a theme switcher and
a density control, none of which exist. Dropping them would misrepresent the
design; rendering them live would give the user a switch that silently does
nothing, which is worse than an obviously inert one. Each carries a help note
naming what it is waiting on, matching how ``panels.pending_*`` treats missing
analysis data elsewhere in the app.
"""

from __future__ import annotations

import html
from datetime import datetime

import streamlit as st

from alerts.monitor_control import is_running, monitor_pid, start_monitor, stop_monitor
from alerts.telegram import format_test_message, send_to_all_recipients
from config.alert_settings import load_alert_config, save_alert_config
from config.credentials import clear_credentials
from config.preferences import (
    SCAN_PROGRESS_STYLE_OPTIONS,
    load_preferences,
    reset_preferences,
    save_preferences,
)
from config.settings import APP_VERSION, SUPPORTED_DATA_SOURCES
from config.trading_config import get_timeframe
from storage.database import (
    clear_all_analysis_history,
    clear_all_notes,
    db_path,
    get_all_watchlists,
)
from ui.components.panels import (
    bias_pill,
    filter_chip,
    kv_row,
    page_title,
    panel_head,
    section_title,
    spacer,
)
from utils.export import exports_dir
from utils.system_info import clear_caches, format_bytes, storage_stats

# The two sources that actually return data. The other four in
# SUPPORTED_DATA_SOURCES are scaffolded against libraries or credentials that
# are not available (see Gotcha 8), so reporting them as "Live" because they
# happen to be selected would be a lie the user can only discover by scanning.
_WORKING_SOURCES = {"Yahoo Finance", "Jugaad Data (NSE)"}

_ROADMAP_VISIBLE = 9


def render_settings() -> None:
    """Render the application settings page."""
    prefs = _safe_prefs()
    cfg = load_alert_config()

    _render_header(prefs)
    spacer(12)
    _render_status_cards(cfg)
    spacer(14)
    _render_preferences_panel(prefs)
    spacer(14)
    _render_dashboard_settings(prefs)
    spacer(14)

    col_chart, col_alerts, col_look = st.columns(3)
    with col_chart:
        _render_chart_settings(prefs)
    with col_alerts:
        _render_alert_channels(cfg)
    with col_look:
        _render_appearance(prefs)

    spacer(14)
    col_tg, col_cond = st.columns([3, 2])
    with col_tg:
        _render_telegram_setup(cfg)
    with col_cond:
        _render_alert_conditions(cfg)

    spacer(14)
    col_mon, col_data, col_road = st.columns([1.15, 1.55, 1.3])
    with col_mon:
        _render_background_monitor()
    with col_data:
        _render_data_management()
    with col_road:
        _render_roadmap()

    spacer(16)
    _render_footer()


def _safe_prefs() -> dict:
    """Preferences, or an empty dict — a corrupt file must not kill the page."""
    try:
        return load_preferences()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _render_header(prefs: dict) -> None:
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            "<div style='font-size:0.78rem;color:#9AA0A8;margin-bottom:2px;'>"
            "Dashboard &nbsp;&rsaquo;&nbsp; "
            "<span style='color:#4A5361;font-weight:600;'>Settings</span></div>",
            unsafe_allow_html=True,
        )
        page_title(
            "Settings",
            "Configure data, alerts, notifications, storage and platform preferences.",
            icon="settings",
        )
    with right:
        saved_at = storage_stats().get("prefs_modified")
        cap, btn = st.columns([2, 1])
        with cap:
            st.markdown(
                f"<div style='text-align:right;padding-top:16px;font-size:0.8rem;"
                f"color:#71757C;'>Last saved: <b>{_when(saved_at)}</b></div>",
                unsafe_allow_html=True,
            )
        with btn:
            if st.button("Reset to Defaults", icon=":material/restart_alt:",
                         use_container_width=True, key="set_reset"):
                _reset_preferences()

    # Rendered outside the column so the message spans the page rather than
    # being squeezed into the button's narrow slot.
    msg = st.session_state.pop("settings_flash", None)
    if msg:
        kind, text = msg
        getattr(st, kind)(text)


def _reset_preferences() -> None:
    try:
        reset_preferences()
        st.session_state["settings_flash"] = ("success", "Preferences reset to defaults.")
    except Exception as exc:
        st.session_state["settings_flash"] = ("error", f"Failed to reset preferences: {exc}")
    st.rerun()


def _when(value: datetime | None) -> str:
    """Friendly timestamp — 'Today 09:38 AM' for today, a date otherwise."""
    if not isinstance(value, datetime):
        return "—"
    if value.date() == datetime.now().date():
        return f"Today {value.strftime('%I:%M %p').lstrip('0')}"
    return value.strftime("%d %b %Y, %I:%M %p").replace(" 0", " ")


# ---------------------------------------------------------------------------
# Status strip
# ---------------------------------------------------------------------------

def _render_status_cards(cfg: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)

    with c1:
        with st.container(border=True):
            panel_head("About", "Build and environment", icon="info", tone="info")
            kv_row("App Version", f"v{APP_VERSION}")
            # Build number and environment come from a release pipeline that
            # does not exist — this is run from a working tree.
            kv_row("Build", pending="new")
            kv_row("Environment", "Local")

    with c2:
        with st.container(border=True):
            panel_head("Active Data Source", "Where prices come from",
                       icon="database", tone="purple")
            source = st.session_state.get("selected_data_source", "Yahoo Finance")
            live = source in _WORKING_SOURCES
            _kv_html(
                "Provider",
                f"{html.escape(source)} &nbsp;"
                + bias_pill("Live" if live else "Not wired",
                            "bullish" if live else "muted"),
            )
            kv_row("Market", "NSE India")
            kv_row("Prices", "Unadjusted")

    with c3:
        with st.container(border=True):
            panel_head("Database & Storage", "Local footprint",
                       icon="layers", tone="info")
            stats = storage_stats()
            kv_row("Database", format_bytes(int(stats["db_bytes"])))
            kv_row("Cache", format_bytes(int(stats["cache_bytes"])))
            kv_row("Logs", format_bytes(int(stats["log_bytes"])))
            st.caption(f"`{db_path()}`")

    with c4:
        with st.container(border=True):
            panel_head("Quick Health", "At a glance", icon="activity",
                       tone="bullish" if cfg.get("enabled") else "muted")
            recipients = cfg.get("telegram", {}).get("recipients", []) or []
            _kv_html("Alerts Enabled", bias_pill(
                "Yes" if cfg.get("enabled") else "No",
                "bullish" if cfg.get("enabled") else "muted"))
            kv_row("Recipients", str(len(recipients)))
            running = is_running()
            _kv_html("Monitor", bias_pill(
                "Running" if running else "Stopped",
                "bullish" if running else "bearish"))
            kv_row("Last Run", _last_run_text())


def _kv_html(label: str, value_html: str) -> None:
    """A :func:`kv_row` whose value is pre-built HTML (a pill, a badge).

    ``kv_row`` escapes its value, which is right for every other caller — the
    pill markup has to bypass that, so the row is written out here instead.
    """
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;"
        f"align-items:center;padding:4px 0;border-bottom:1px solid #F0F0EC;'>"
        f"<span style='font-size:0.8rem;color:#5F5E5A;'>{html.escape(label)}</span>"
        f"<span style='font-size:0.85rem;text-align:right;'>{value_html}</span>"
        f"</div>",
        unsafe_allow_html=True,
    )


def _last_run_text() -> str:
    stamp = st.session_state.get("last_analysis_timestamp") or \
        _safe_prefs().get("last_analysis_timestamp")
    if not stamp:
        return "Never"
    try:
        return _when(datetime.fromisoformat(str(stamp)))
    except ValueError:
        return str(stamp)


# ---------------------------------------------------------------------------
# Last used selections
# ---------------------------------------------------------------------------

def _render_preferences_panel(prefs: dict) -> None:
    with st.container(border=True):
        head, action = st.columns([4, 1])
        with head:
            section_title("Last Used Selections & Preferences")
        with action:
            if st.button("Edit Preferences", icon=":material/edit:",
                         use_container_width=True, key="set_edit_prefs"):
                st.session_state["settings_show_prefs_hint"] = True

        # These are a RECORD of the last sidebar selections, not the settings
        # the app is currently running: init_session_state starts from fixed
        # defaults every launch (Gotcha 14), so this row can legitimately
        # disagree with the sidebar. Two different values for the same thing
        # on one page reads as a bug unless it is said out loud.
        st.caption(
            "Recorded automatically when you change the sidebar. They are **not** "
            "reapplied on startup, so they can differ from what is running now — "
            "the sidebar is always the live setting."
        )

        if st.session_state.get("settings_show_prefs_hint"):
            st.info("Change any of these from the sidebar on the left; the new "
                    "choice is recorded here automatically.")

        trading_type = prefs.get("trading_type", "Options Trading")
        enhancers = prefs.get("enhancers") or []
        timeframe = get_timeframe(trading_type)

        row = st.columns(9)
        with row[0]:
            filter_chip("Trading Type", trading_type, icon="target")
        with row[1]:
            filter_chip("Primary Strategy",
                        prefs.get("primary_strategy", "Demand/Supply Zones"),
                        icon="layers")
        with row[2]:
            filter_chip("Watchlist", _watchlist_label(prefs), icon="star")
        with row[3]:
            # No top-N control exists; the scan runs the whole watchlist.
            filter_chip("Top N Companies", icon="trophy", pending="new")
        with row[4]:
            filter_chip("Enhancers", ", ".join(enhancers) if enhancers else "None",
                        icon="sliders")
        with row[5]:
            filter_chip("Theme", icon="palette", pending="new")
        with row[6]:
            # Named for the sidebar switch it records, NOT "Alerts": the Quick
            # Health card above reports the Telegram config's own enabled
            # flag, and the two are independent. Both labelled "Alerts" they
            # read as one value contradicting itself.
            filter_chip("Sidebar Alerts", "On" if prefs.get("alerts_on") else "Off",
                        icon="bell")
        with row[7]:
            filter_chip("Timeframe",
                        f"{timeframe.get('interval', '1d')}",
                        icon="clock",
                        sub=f"period {timeframe.get('period', '—')}")
        with row[8]:
            filter_chip("Last Run", _last_run_text(), icon="check_circle")

        with st.expander("Raw preferences (JSON)"):
            st.json(prefs or {"note": "No saved preferences found."}, expanded=True)


def _watchlist_label(prefs: dict) -> str:
    """Name of the watchlist in use — custom by id, else the predefined one."""
    wl_id = st.session_state.get("selected_watchlist_id") or \
        prefs.get("selected_watchlist_id")
    if wl_id:
        try:
            for wl in get_all_watchlists():
                if wl.get("id") == wl_id:
                    return str(wl.get("name", "Custom"))
        except Exception:
            pass
    return str(st.session_state.get("selected_predefined_watchlist") or "Nifty 50")


# ---------------------------------------------------------------------------
# Dashboard content
# ---------------------------------------------------------------------------

def _render_dashboard_settings(prefs: dict) -> None:
    """Persist visibility for dashboard panels with non-trivial fetch cost."""
    with st.container(border=True):
        panel_head(
            "Dashboard Content",
            "Choose which overview and market-data panels load on the dashboard",
            icon="monitor",
            tone="info",
        )
        indices_col, scan_col = st.columns(2)
        current_indices = bool(prefs.get("dashboard_show_indices_overview", True))
        current_scan = bool(prefs.get("dashboard_show_scan_overview", True))
        current_watchlist = bool(prefs.get("dashboard_show_watchlist_movers", True))
        current_market = bool(prefs.get("dashboard_show_all_nse_movers", False))
        current_cues = bool(prefs.get("dashboard_show_global_cues", True))

        with indices_col:
            show_indices = st.toggle(
                "Show market indices overview",
                value=current_indices,
                key="set_dashboard_indices_overview",
                help="Shows option-enabled indices from NSE and BSE, including NIFTY, "
                     "BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX and BANKEX.",
            )
            st.caption("NSE/BSE option-index levels, daily change and available trend history.")

        with scan_col:
            show_scan = st.toggle(
                "Show scan overview",
                value=current_scan,
                key="set_dashboard_scan_overview",
                help="Shows breadth, strongest long/short names and latest zone-state counts.",
            )
            st.caption("Uses the latest saved analysis results; it does not run a new scan.")

        show_cues = st.toggle(
            "Show global cues (pre-open)",
            value=current_cues,
            key="set_dashboard_global_cues",
            help="US overnight close, Asian opening prints, commodities, DXY "
                 "and yields, interpreted with the historical hit rates from "
                 "the global-influence study. Cues describe the OPENING GAP "
                 "only — they stop predicting after 09:15.",
        )
        st.caption("One small Yahoo batch, cached for 5 minutes.")

        st.markdown("---")
        watchlist_col, market_col = st.columns(2)
        with watchlist_col:
            show_watchlist = st.toggle(
                "Show selected-watchlist movers",
                value=current_watchlist,
                key="set_dashboard_watchlist_movers",
                help="Loads gainers, losers and volume leaders for the watchlist "
                     "currently selected in the sidebar, such as F&O Stocks.",
            )
            st.caption(
                "Follows the active sidebar watchlist. Usually a smaller, faster quote request."
            )

        with market_col:
            show_market = st.toggle(
                "Show All NSE market movers",
                value=current_market,
                key="set_dashboard_all_nse_movers",
                help="Loads gainers, losers and volume leaders across the full NSE stock universe.",
            )
            st.caption(
                "Large quote request. Keep this off for a faster dashboard and enable it only when needed."
            )

        changed: dict[str, bool] = {}
        if show_indices != current_indices:
            changed["dashboard_show_indices_overview"] = show_indices
        if show_scan != current_scan:
            changed["dashboard_show_scan_overview"] = show_scan
        if show_watchlist != current_watchlist:
            changed["dashboard_show_watchlist_movers"] = show_watchlist
        if show_market != current_market:
            changed["dashboard_show_all_nse_movers"] = show_market
        if show_cues != current_cues:
            changed["dashboard_show_global_cues"] = show_cues
        if changed:
            save_preferences(changed)
            st.session_state["settings_flash"] = (
                "success",
                "Dashboard content preferences saved.",
            )
            st.rerun()


# ---------------------------------------------------------------------------
# Chart / alert-channel / appearance panels
# ---------------------------------------------------------------------------

def _render_chart_settings(prefs: dict) -> None:
    with st.container(border=True):
        panel_head("Chart Settings", "Chart behaviour and overlays",
                   icon="trend_up", tone="info")

        current = prefs.get("show_candle_tooltip", True)
        show_tooltip = st.toggle(
            "Show candlestick tooltips", value=current, key="set_tooltip",
            help="Hovering the chart shows a box with OHLC details. Off keeps "
                 "only the crosshair and price label.",
        )
        if show_tooltip != current:
            save_preferences({"show_candle_tooltip": show_tooltip})
            st.rerun()

        # Always drawn by the chart today; there is no preference behind them,
        # so the switches are inert rather than absent.
        st.toggle("Show volume bars", value=True, disabled=True,
                  key="set_volume", help="Always on — not yet configurable.")
        st.toggle("Show EMA / SMA overlays", value=True, disabled=True,
                  key="set_ema",
                  help="Overlays follow the selected strategy and enhancers.")
        st.toggle("Show demand/supply zone labels", value=True, disabled=True,
                  key="set_zone_labels", help="Always on — not yet configurable.")

        st.selectbox(
            "Default timeframe",
            [f"{get_timeframe(prefs.get('trading_type', 'Options Trading')).get('interval', '1d')}"],
            disabled=True, key="set_default_tf",
            help="Derived from the Trading Type in the sidebar, not set here.",
        )
        st.selectbox("Default chart type", ["Candlestick"], disabled=True,
                     key="set_chart_type",
                     help="Only candlestick charts are implemented.")


def _render_alert_channels(cfg: dict) -> None:
    with st.container(border=True):
        panel_head("Alert Settings", "How you get notified",
                   icon="bell", tone="warning")

        enabled = st.toggle(
            "Enable Telegram alerts", value=bool(cfg.get("enabled")),
            key="set_alerts_enabled",
            help="When on, the background monitor sends Telegram messages as "
                 "stocks approach their zones.",
        )
        if enabled != bool(cfg.get("enabled")):
            cfg["enabled"] = enabled
            save_alert_config(cfg)
            st.rerun()

        # Telegram is the only delivery channel that exists. The rest are in
        # the design but have no implementation behind them.
        st.toggle("Enable desktop notifications", value=False, disabled=True,
                  key="set_desktop", help="No desktop channel is implemented.")
        st.toggle("Enable sound alerts", value=False, disabled=True,
                  key="set_sound", help="No sound channel is implemented.")
        st.toggle("Enable results alerts", value=False, disabled=True,
                  key="set_results_alerts",
                  help="Earnings-date alerts are Reports Phase 2.")

        st.selectbox("Alert sound", ["—"], disabled=True, key="set_alert_sound",
                     help="Available once sound alerts are implemented.")

        channels = 1 if enabled else 0
        st.markdown(
            f"<div style='margin-top:6px;'>{bias_pill(f'{channels} of 4 channels enabled', 'bullish' if channels else 'muted')}</div>",
            unsafe_allow_html=True,
        )


def _render_appearance(prefs: dict) -> None:
    with st.container(border=True):
        panel_head("Appearance & Experience", "Look and feel",
                   icon="palette", tone="purple")
        style_keys = list(SCAN_PROGRESS_STYLE_OPTIONS)
        current_style = str(prefs.get("scan_progress_style", "speedometer"))
        current_index = (
            style_keys.index(current_style)
            if current_style in style_keys else 0
        )
        selected_label = st.selectbox(
            "Scan progress style",
            [SCAN_PROGRESS_STYLE_OPTIONS[key] for key in style_keys],
            index=current_index,
            key="set_scan_progress_style",
            help="Controls the progress card shown while scans are running.",
        )
        selected_style = next(
            key for key, label in SCAN_PROGRESS_STYLE_OPTIONS.items()
            if label == selected_label
        )
        if selected_style != current_style:
            save_preferences({"scan_progress_style": selected_style})
            st.rerun()

        # These controls are pending: theming is fixed in .streamlit/config.toml
        # and there is no accent/density system yet.
        st.radio("Theme", ["Light", "Dark", "System"], index=0, horizontal=True,
                 disabled=True, key="set_theme",
                 help="Theme is fixed in .streamlit/config.toml for now.")
        st.radio("Density", ["Comfortable", "Compact"], index=0, horizontal=True,
                 disabled=True, key="set_density",
                 help="No density system is implemented.")
        st.selectbox("Accent colour", ["Indigo (default)"], disabled=True,
                     key="set_accent",
                     help="Accent colours come from the Streamlit theme file.")
        st.toggle("Animations", value=True, disabled=True, key="set_anim",
                  help="Not yet configurable.")
        st.toggle("Blinking indicators for active alerts", value=False,
                  disabled=True, key="set_blink",
                  help="Not yet configurable.")
        st.caption("Theme, density and animation controls are not built yet.")


# ---------------------------------------------------------------------------
# Telegram setup and alert conditions
# ---------------------------------------------------------------------------

def _render_telegram_setup(cfg: dict) -> None:
    tg = cfg.get("telegram", {})
    recipients: list[dict] = list(tg.get("recipients", []))

    with st.container(border=True):
        panel_head("Telegram Setup & Recipients",
                   "Connect your bot and manage who receives alerts",
                   icon="send", tone="info")

        with st.expander("How to set up Telegram alerts", expanded=False):
            st.markdown(
                "1. Open Telegram and search for **@BotFather**\n"
                "2. Send `/newbot` and follow the prompts\n"
                "3. Copy the bot token (looks like `123456:ABC-DEF1234...`)\n"
                "4. Create a group or channel and add your bot to it\n"
                "5. Send a message there, then open "
                "`https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` to find "
                "your `chat_id`\n"
                "6. Add each `chat_id` below as a separate recipient."
            )

        tok, act = st.columns([3, 1])
        with tok:
            bot_token = st.text_input(
                "Bot token", value=tg.get("bot_token", ""), type="password",
                key="alert_bot_token",
                help="Paste the token from @BotFather. Stored locally, never uploaded.",
            )
        with act:
            st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
            save_token = st.button("Save token", use_container_width=True,
                                   key="set_save_token")

        if save_token:
            cfg.setdefault("telegram", {})["bot_token"] = bot_token
            save_alert_config(cfg)
            st.success("Bot token saved.")

        st.markdown("**Recipients**")
        if recipients:
            for i, recip in enumerate(recipients):
                rc1, rc2, rc3 = st.columns([3, 4, 1])
                with rc1:
                    st.text(recip.get("chat_id", ""))
                with rc2:
                    st.text(recip.get("label", ""))
                with rc3:
                    if st.button("✕", key=f"del_recip_{i}",
                                 help="Remove this recipient"):
                        recipients.pop(i)
                        cfg["telegram"]["recipients"] = recipients
                        save_alert_config(cfg)
                        st.rerun()
        else:
            st.caption("No recipients added yet.")

        ac1, ac2, ac3 = st.columns([2, 2, 1])
        with ac1:
            new_chat_id = st.text_input("Chat ID", key="new_chat_id",
                                        placeholder="e.g. -1001234567890")
        with ac2:
            new_label = st.text_input("Label", key="new_label",
                                      placeholder="e.g. My Trading Group")
        with ac3:
            st.markdown("<div style='height:28px;'></div>", unsafe_allow_html=True)
            add_clicked = st.button("Add", use_container_width=True,
                                    key="add_recipient")

        if add_clicked:
            if new_chat_id.strip():
                recipients.append({
                    "chat_id": new_chat_id.strip(),
                    "label": new_label.strip() or new_chat_id.strip(),
                })
                cfg.setdefault("telegram", {})["recipients"] = recipients
                cfg["telegram"]["bot_token"] = bot_token
                save_alert_config(cfg)
                st.rerun()
            else:
                st.warning("Enter a chat ID first.")

        if st.button("Send test message", icon=":material/send:",
                     key="set_test_msg"):
            _send_test(bot_token, recipients)


def _send_test(bot_token: str, recipients: list[dict]) -> None:
    if not bot_token.strip():
        st.warning("Enter a bot token first.")
    elif not recipients:
        st.warning("Add at least one recipient first.")
    else:
        result = send_to_all_recipients(bot_token, recipients, format_test_message())
        for label in result["sent"]:
            st.success(f"Sent to {label}")
        for label in result["failed"]:
            st.error(f"Failed to send to {label}")


_PROXIMITY_OPTIONS = {"0.5%": 0.5, "1%": 1.0, "2%": 2.0, "3%": 3.0}
_SCORE_OPTIONS = [5.0, 5.5, 6.0, 6.5, 7.0]
_ZONE_TYPE_OPTIONS = {"Both": "both", "Demand only": "demand", "Supply only": "supply"}
_COOLDOWN_OPTIONS = {
    "Once per zone per day": "once_per_zone_per_day",
    "Every approach": "every_approach",
    "Once per zone ever": "once_per_zone_ever",
}
_STOCKS_SOURCE_OPTIONS = ["Watchlist", "F&O", "All NSE", "Custom"]
_STOCKS_SOURCE_MAP = {
    "Watchlist": "watchlist",
    "F&O": "fno",
    "All NSE": "all_nse",
    "Custom": "custom",
}
_STOCKS_SOURCE_REVERSE = {v: k for k, v in _STOCKS_SOURCE_MAP.items()}


def _render_alert_conditions(cfg: dict) -> None:
    cond = cfg.get("conditions", {})

    with st.container(border=True):
        panel_head("Alert Conditions", "When an alert should fire",
                   icon="target", tone="warning")

        source_label = _STOCKS_SOURCE_REVERSE.get(
            cond.get("stocks_source", "watchlist"), "Watchlist")
        stocks_source = st.selectbox(
            "Stocks to monitor", _STOCKS_SOURCE_OPTIONS,
            index=_STOCKS_SOURCE_OPTIONS.index(source_label),
            key="alert_stocks_source",
        )

        custom_stocks_text = ""
        if stocks_source == "Custom":
            custom_stocks_text = st.text_area(
                "Custom symbols (comma-separated)",
                value=", ".join(cond.get("custom_stocks", [])),
                key="alert_custom_stocks", placeholder="RELIANCE, INFY, TCS",
            )

        # Match the saved number to its label rather than formatting it, so a
        # JSON int (6) and a float option (6.0) cannot fall out of step.
        prox_labels = list(_PROXIMITY_OPTIONS)
        prox_label = next(
            (k for k, v in _PROXIMITY_OPTIONS.items()
             if v == cond.get("proximity_pct", 1.0)), "1%")
        proximity = st.selectbox(
            "Zone proximity threshold", prox_labels,
            index=prox_labels.index(prox_label), key="alert_proximity",
            help="Alert when the price is within this distance of a zone's proximal.",
        )

        score_current = float(cond.get("min_score", 6.0))
        min_score = st.selectbox(
            "Minimum ODD score", _SCORE_OPTIONS,
            index=_SCORE_OPTIONS.index(score_current)
            if score_current in _SCORE_OPTIONS else 2,
            key="alert_min_score", format_func=str,
        )

        zt_labels = list(_ZONE_TYPE_OPTIONS)
        zt_label = next((k for k, v in _ZONE_TYPE_OPTIONS.items()
                         if v == cond.get("zone_type", "both")), "Both")
        zone_type = st.selectbox("Zone type", zt_labels,
                                 index=zt_labels.index(zt_label),
                                 key="alert_zone_type")

        cd_labels = list(_COOLDOWN_OPTIONS)
        cd_label = next((k for k, v in _COOLDOWN_OPTIONS.items()
                         if v == cond.get("cooldown", "once_per_zone_per_day")),
                        "Once per zone per day")
        cooldown = st.selectbox(
            "Alert cooldown", cd_labels, index=cd_labels.index(cd_label),
            key="alert_cooldown",
            help="How often you are re-alerted for the same zone.",
        )

        st.selectbox("Priority level", ["Normal"], disabled=True,
                     key="set_priority",
                     help="Alert priorities are not implemented.")

        if st.button("Save alert settings", type="primary",
                     use_container_width=True, key="set_save_conditions"):
            conditions = cfg.setdefault("conditions", {})
            conditions["stocks_source"] = _STOCKS_SOURCE_MAP.get(
                stocks_source, "watchlist")
            conditions["custom_stocks"] = [
                s.strip().upper() for s in custom_stocks_text.split(",") if s.strip()
            ] if stocks_source == "Custom" else []
            conditions["proximity_pct"] = _PROXIMITY_OPTIONS[proximity]
            conditions["min_score"] = min_score
            conditions["zone_type"] = _ZONE_TYPE_OPTIONS[zone_type]
            conditions["cooldown"] = _COOLDOWN_OPTIONS[cooldown]
            save_alert_config(cfg)
            st.success("Alert settings saved.")


# ---------------------------------------------------------------------------
# Monitor, data management, roadmap
# ---------------------------------------------------------------------------

def _render_background_monitor() -> None:
    with st.container(border=True):
        running = is_running()
        pid = monitor_pid()
        panel_head(
            "Background Monitor",
            f"Running · pid {pid}" if running else "Not running",
            icon="monitor", tone="bullish" if running else "muted",
        )
        st.caption(
            "Checks every 5 minutes during market hours (9:15 AM – 3:30 PM IST, "
            "Mon–Fri) and sends alerts even when this app is closed."
        )

        b1, b2 = st.columns(2)
        with b1:
            if st.button("Start", use_container_width=True, disabled=running,
                         key="set_mon_start"):
                _monitor_action(start_monitor())
        with b2:
            if st.button("Stop", use_container_width=True, disabled=not running,
                         key="set_mon_stop"):
                _monitor_action(stop_monitor())

        st.caption("Or run it yourself:")
        st.code(
            "cd /home/gongati/projects/market-lens\n"
            "source venv/bin/activate\n"
            "python alert_monitor.py",
            language="bash",
        )


def _monitor_action(result: tuple[bool, str]) -> None:
    """Show the outcome of a start/stop and redraw so the status updates."""
    ok, message = result
    st.session_state["settings_flash"] = ("success" if ok else "warning", message)
    st.rerun()


def _render_data_management() -> None:
    with st.container(border=True):
        panel_head("Data Management & Exports", "Stored data and export files",
                   icon="download", tone="bearish")
        st.warning("These actions are irreversible. Deleted data cannot be recovered.")

        d1, d2, d3 = st.columns(3)
        with d1:
            if st.button("Clear analysis history", use_container_width=True,
                         key="set_clear_hist"):
                _guarded(clear_all_analysis_history, "Analysis history cleared.")
        with d2:
            if st.button("Clear stock notes", use_container_width=True,
                         key="set_clear_notes"):
                _guarded(clear_all_notes, "Stock notes cleared.")
        with d3:
            if st.button("Clear cached data", use_container_width=True,
                         key="set_clear_cache",
                         help="Bhavcopy and earnings caches. Both are re-fetchable."):
                _guarded(lambda: clear_caches(), "Cached data cleared.")

        st.markdown("**Credentials**")
        st.caption("Clearing credentials means re-entering API keys on the next run.")
        c1, c2 = st.columns([2, 1])
        with c1:
            source_to_clear = st.selectbox(
                "Clear credentials for", ["All sources"] + SUPPORTED_DATA_SOURCES,
                key="set_cred_source", label_visibility="collapsed",
            )
        with c2:
            if st.button("Clear credentials", use_container_width=True,
                         key="set_clear_creds"):
                _clear_credentials(source_to_clear)

        st.markdown("**Exports**")
        st.caption(f"Excel and PDF exports are written to `{exports_dir()}`.")


def _guarded(action, success_message: str) -> None:
    """Run a destructive action, reporting either outcome to the user."""
    try:
        action()
        st.success(success_message)
    except Exception as exc:
        st.error(f"Action failed: {exc}")


def _clear_credentials(source_to_clear: str) -> None:
    try:
        if source_to_clear == "All sources":
            clear_credentials(source=None)
            st.session_state.credentials = {}
            st.success("All credentials cleared.")
        else:
            clear_credentials(source=source_to_clear)
            creds = st.session_state.get("credentials", {})
            creds.pop(source_to_clear, None)
            st.session_state.credentials = creds
            st.success(f"Credentials for {source_to_clear} cleared.")
    except Exception as exc:
        st.error(f"Failed to clear credentials: {exc}")


# Planned work, newest intent first. Deliberately carries NO target quarters:
# nothing here is scheduled, and a date on an unscheduled item reads as a
# commitment the project has not made.
_ROADMAP: list[str] = [
    "Entry, stop-loss and target levels (Phase 2 · M1)",
    "Multi-timeframe trend confirmation (Phase 3)",
    "Dark theme and appearance controls",
    "Email alert notifications",
    "Live market news feed",
    "Derivatives OI and volume spike alerts",
    "Backtesting engine with historical signal replay",
    "Docker containerisation for one-command setup",
    "TradingView full data integration",
    "Increase the watchlist limit beyond 10",
    "Run multiple primary strategies side by side",
    "Real-time auto-refresh during market hours",
    "Zerodha Kite Connect order placement",
    "Upstox API instrument key mapping",
    "Portfolio P&L tracking",
    "Custom alert conditions (price triggers, RSI thresholds)",
    "RSI enhancer implementation (selectable today, not yet wired)",
    "Chart drawing tools (manual trend lines)",
    "Sector-wise heatmap view",
]


def _render_roadmap() -> None:
    with st.container(border=True):
        panel_head("Upcoming Features", "Planned enhancements",
                   icon="rocket", tone="purple")
        for item in _ROADMAP[:_ROADMAP_VISIBLE]:
            st.markdown(
                f"<div style='display:flex;gap:8px;align-items:flex-start;"
                f"padding:3px 0;'>"
                f"<span style='flex:0 0 6px;height:6px;border-radius:50%;"
                f"background:#7B61E3;margin-top:7px;'></span>"
                f"<span style='font-size:0.82rem;color:#4A5361;line-height:1.35;'>"
                f"{html.escape(item)}</span></div>",
                unsafe_allow_html=True,
            )
        with st.expander(f"View all {len(_ROADMAP)} planned features"):
            for item in _ROADMAP[_ROADMAP_VISIBLE:]:
                st.markdown(f"- {item}")


def _render_footer() -> None:
    items = ("Real-time data", "Advanced analytics", "Risk management",
             "Actionable insights")
    pills = "".join(
        f"<span style='font-size:0.76rem;color:#71757C;'>{html.escape(i)}</span>"
        for i in items
    )
    st.markdown(
        f"<div style='border-top:1px solid #E7E9ED;padding:12px 2px;"
        f"display:flex;flex-wrap:wrap;gap:18px;align-items:center;"
        f"justify-content:space-between;'>"
        f"<span style='font-size:0.8rem;font-weight:600;color:#4A5361;'>"
        f"Market Lens &mdash; v{APP_VERSION}</span>"
        f"<span style='display:flex;gap:18px;flex-wrap:wrap;'>{pills}</span>"
        f"<span style='font-size:0.72rem;color:#9AA0A8;'>"
        f"Data may be delayed. For educational and research use only &mdash; "
        f"not investment advice.</span></div>",
        unsafe_allow_html=True,
    )
