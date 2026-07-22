"""App settings page."""

import streamlit as st

from alerts.telegram import format_test_message, send_to_all_recipients
from config.alert_settings import load_alert_config, save_alert_config
from config.credentials import clear_credentials
from config.preferences import load_preferences, reset_preferences, save_preferences
from config.settings import APP_VERSION, SUPPORTED_DATA_SOURCES
from storage.database import clear_all_analysis_history, clear_all_notes, db_path
from utils.export import exports_dir


def render_settings() -> None:
    """Render the application settings page."""
    st.title("⚙️ Settings")

    # ---------- About ----------
    st.markdown("### About")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("App Version", f"v{APP_VERSION}")
    with col2:
        active_source = st.session_state.get("selected_data_source", "Yahoo Finance")
        st.metric("Active Data Source", active_source)

    st.markdown("---")

    # ---------- Storage ----------
    st.markdown("### Storage")
    st.info(f"Database: `{db_path()}`")

    st.markdown("---")

    # ---------- User Preferences ----------
    st.markdown("### User Preferences")
    st.caption("Preferences are saved automatically when you change selections in the sidebar.")
    try:
        prefs = load_preferences()
    except Exception:
        prefs = {}

    if prefs:
        # Structured display of the two-axis selections — every read is
        # defensive (.get with a default) so a missing key never crashes
        # the page, even on a preferences file written by an older version.
        p1, p2 = st.columns(2)
        with p1:
            st.markdown(f"**Data Source:** {prefs.get('selected_data_source', 'Yahoo Finance')}")
            st.markdown(f"**Trading Type:** {prefs.get('trading_type', 'Options Trading')}")
            st.markdown(f"**Primary Strategy:** {prefs.get('primary_strategy', 'Demand/Supply Zones')}")
        with p2:
            _enh = prefs.get("enhancers") or []
            st.markdown(f"**Enhancers:** {', '.join(_enh) if _enh else 'None'}")
            st.markdown(f"**Alerts:** {'On' if prefs.get('alerts_on') else 'Off'}")
            st.markdown(f"**Theme:** {prefs.get('theme', 'Light (default)')}")
        with st.expander("Raw preferences (JSON)"):
            st.json(prefs, expanded=True)
    else:
        st.caption("No saved preferences found.")

    if st.button("Reset Preferences to Defaults", type="secondary", use_container_width=False):
        try:
            reset_preferences()
            st.success("Preferences reset to defaults.")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to reset preferences: {exc}")

    st.markdown("---")

    # ---------- Chart Settings ----------
    st.markdown("### Chart Settings")
    show_tooltip = st.toggle(
        "Show candle details tooltip",
        value=prefs.get("show_candle_tooltip", True),
        help="When ON, hovering over the chart shows a box with OHLC candle details. "
        "Turn OFF to hide the box and keep only the crosshair lines and price label.",
    )
    if show_tooltip != prefs.get("show_candle_tooltip", True):
        save_preferences({"show_candle_tooltip": show_tooltip})
        st.rerun()

    st.markdown("---")

    # ---------- Telegram Alert Settings ----------
    st.markdown("### 🔔 Alert Settings")
    _render_telegram_alert_settings()

    st.markdown("---")

    # ---------- Data Management ----------
    st.markdown("### Data Management")
    st.warning(
        "These actions are irreversible. Analysis history and notes will be permanently deleted."
    )

    dm1, dm2 = st.columns(2)
    with dm1:
        if st.button("Clear All Analysis History", type="secondary", use_container_width=True):
            try:
                clear_all_analysis_history()
                st.success("All analysis history cleared.")
            except Exception as exc:
                st.error(f"Failed to clear history: {exc}")
    with dm2:
        if st.button("Clear All Stock Notes", type="secondary", use_container_width=True):
            try:
                clear_all_notes()
                st.success("All stock notes cleared.")
            except Exception as exc:
                st.error(f"Failed to clear notes: {exc}")

    st.markdown("---")

    # ---------- Credentials ----------
    st.markdown("### Credentials")
    st.caption("Clearing credentials will require re-entering API keys on next run.")

    col_a, col_b = st.columns(2)
    with col_a:
        source_to_clear = st.selectbox(
            "Clear credentials for", ["All sources"] + SUPPORTED_DATA_SOURCES
        )
    with col_b:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("Clear Credentials", type="secondary"):
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

    st.markdown("---")

    # ---------- Exports ----------
    st.markdown("### Exports")
    export_path = exports_dir()
    st.info(f"Export files are saved to: `{export_path}`")
    st.caption("Navigate to that folder manually to access exported Excel and PDF files.")

    st.markdown("---")

    # ---------- Roadmap ----------
    st.markdown("### Pending Features Roadmap")
    roadmap = [
        "Dark theme toggle",
        "Email alert notifications",
        "Live market news feed",
        "Multi-exchange global support (NYSE, NASDAQ, LSE)",
        "Backtesting engine with historical signal replay",
        "Docker containerisation for one-command setup",
        "TradingView full data integration (pending stable library)",
        "Increase watchlist limit beyond 10",
        "Run multiple primary strategies side-by-side",
        "Real-time auto-refresh every 5 minutes during market hours",
        "Zerodha Kite Connect order placement integration",
        "Upstox API instrument key mapping",
        "Portfolio P&L tracking",
        "Custom alert conditions (price triggers, RSI thresholds)",
        "RSI enhancer implementation (selectable today, not yet wired)",
        "Chart drawing tools (manual trend lines)",
        "Sector-wise heatmap view",
    ]
    for item in roadmap:
        st.markdown(f"- {item}")


# ---------------------------------------------------------------------------
# Telegram Alert Settings — full config UI
# ---------------------------------------------------------------------------

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


def _render_telegram_alert_settings() -> None:
    """Render the full Telegram alert configuration UI."""
    cfg = load_alert_config()
    tg = cfg.get("telegram", {})
    cond = cfg.get("conditions", {})

    # -- Master toggle --
    with st.container(border=True):
        enabled = st.toggle(
            "Enable Telegram Alerts",
            value=cfg.get("enabled", False),
            key="alert_enabled_toggle",
            help="When ON, the background monitor sends Telegram messages "
                 "when stocks approach demand/supply zones.",
        )

    # -- Telegram bot setup --
    st.markdown("#### Telegram Setup")
    with st.expander("How to set up Telegram alerts", expanded=False):
        st.markdown(
            "1. Open Telegram, search for **@BotFather**\n"
            "2. Send `/newbot` and follow the prompts\n"
            "3. Copy the bot token (looks like `123456:ABC-DEF1234...`)\n"
            "4. Create a group or channel, add your bot to it\n"
            "5. Send a message in the group, then visit:\n"
            "   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`\n"
            "   to find your `chat_id`\n"
            "6. For multiple recipients, add each `chat_id` separately below."
        )

    with st.container(border=True):
        bot_token = st.text_input(
            "Bot Token",
            value=tg.get("bot_token", ""),
            type="password",
            key="alert_bot_token",
            help="Paste the token from @BotFather. Stored locally, never uploaded.",
        )

        # -- Recipients table --
        st.markdown("**Recipients**")
        recipients: list[dict] = list(tg.get("recipients", []))

        if recipients:
            for i, recip in enumerate(recipients):
                rc1, rc2, rc3 = st.columns([3, 4, 1])
                with rc1:
                    st.text(recip.get("chat_id", ""))
                with rc2:
                    st.text(recip.get("label", ""))
                with rc3:
                    if st.button("✕", key=f"del_recip_{i}"):
                        recipients.pop(i)
                        cfg["telegram"]["recipients"] = recipients
                        save_alert_config(cfg)
                        st.rerun()
        else:
            st.caption("No recipients added yet.")

        # Add recipient form
        ac1, ac2 = st.columns(2)
        with ac1:
            new_chat_id = st.text_input(
                "Chat ID", key="new_chat_id", placeholder="e.g. -1001234567890"
            )
        with ac2:
            new_label = st.text_input(
                "Label", key="new_label", placeholder="e.g. My Trading Group"
            )
        if st.button("Add Recipient", key="add_recipient"):
            if new_chat_id.strip():
                recipients.append({
                    "chat_id": new_chat_id.strip(),
                    "label": new_label.strip() or new_chat_id.strip(),
                })
                cfg["telegram"]["recipients"] = recipients
                cfg["telegram"]["bot_token"] = bot_token
                save_alert_config(cfg)
                st.success(f"Added recipient: {new_label.strip() or new_chat_id.strip()}")
                st.rerun()
            else:
                st.warning("Enter a chat ID first.")

    # -- Alert conditions --
    st.markdown("#### Alert Conditions")
    with st.container(border=True):
        source_label = _STOCKS_SOURCE_REVERSE.get(
            cond.get("stocks_source", "watchlist"), "Watchlist"
        )
        stocks_source = st.selectbox(
            "Stocks to monitor",
            options=_STOCKS_SOURCE_OPTIONS,
            index=_STOCKS_SOURCE_OPTIONS.index(source_label),
            key="alert_stocks_source",
        )

        custom_stocks_text = ""
        if stocks_source == "Custom":
            custom_stocks_text = st.text_area(
                "Custom symbols (comma-separated)",
                value=", ".join(cond.get("custom_stocks", [])),
                key="alert_custom_stocks",
                placeholder="RELIANCE, INFY, TCS",
            )

        prox_labels = list(_PROXIMITY_OPTIONS.keys())
        prox_current = f"{cond.get('proximity_pct', 1.0)}%"
        prox_idx = prox_labels.index(prox_current) if prox_current in prox_labels else 1
        proximity = st.selectbox(
            "Zone proximity threshold",
            options=prox_labels,
            index=prox_idx,
            key="alert_proximity",
            help="Alert when CMP is within this distance of a zone's proximal.",
        )

        score_current = cond.get("min_score", 6.0)
        score_idx = _SCORE_OPTIONS.index(score_current) if score_current in _SCORE_OPTIONS else 2
        min_score = st.selectbox(
            "Minimum ODD score",
            options=_SCORE_OPTIONS,
            index=score_idx,
            key="alert_min_score",
            format_func=lambda x: str(x),
        )

        zt_labels = list(_ZONE_TYPE_OPTIONS.keys())
        zt_current = cond.get("zone_type", "both")
        zt_label = next((k for k, v in _ZONE_TYPE_OPTIONS.items() if v == zt_current), "Both")
        zone_type = st.selectbox(
            "Zone type",
            options=zt_labels,
            index=zt_labels.index(zt_label),
            key="alert_zone_type",
        )

        cd_labels = list(_COOLDOWN_OPTIONS.keys())
        cd_current = cond.get("cooldown", "once_per_zone_per_day")
        cd_label = next(
            (k for k, v in _COOLDOWN_OPTIONS.items() if v == cd_current),
            "Once per zone per day",
        )
        cooldown = st.selectbox(
            "Alert cooldown",
            options=cd_labels,
            index=cd_labels.index(cd_label),
            key="alert_cooldown",
            help="Controls how often you get re-alerted for the same zone.",
        )

    # -- Save button --
    if st.button("💾 Save Alert Settings", type="primary", use_container_width=True):
        cfg["enabled"] = enabled
        cfg["telegram"]["bot_token"] = bot_token
        cfg["telegram"]["recipients"] = recipients
        cfg["conditions"]["stocks_source"] = _STOCKS_SOURCE_MAP.get(
            stocks_source, "watchlist"
        )
        if stocks_source == "Custom" and custom_stocks_text:
            cfg["conditions"]["custom_stocks"] = [
                s.strip().upper() for s in custom_stocks_text.split(",") if s.strip()
            ]
        else:
            cfg["conditions"]["custom_stocks"] = []
        cfg["conditions"]["proximity_pct"] = _PROXIMITY_OPTIONS[proximity]
        cfg["conditions"]["min_score"] = min_score
        cfg["conditions"]["zone_type"] = _ZONE_TYPE_OPTIONS[zone_type]
        cfg["conditions"]["cooldown"] = _COOLDOWN_OPTIONS[cooldown]
        save_alert_config(cfg)
        st.success("Alert settings saved.")

    # -- Test message button --
    st.markdown("#### Test Connection")
    if st.button("📤 Send Test Message", use_container_width=True):
        if not bot_token.strip():
            st.warning("Enter a bot token first.")
        elif not recipients:
            st.warning("Add at least one recipient first.")
        else:
            msg = format_test_message()
            result = send_to_all_recipients(bot_token, recipients, msg)
            for label in result["sent"]:
                st.success(f"✅ Sent to {label}")
            for label in result["failed"]:
                st.error(f"❌ Failed to send to {label}")

    # -- Background monitor instructions --
    st.markdown("#### Background Monitor")
    with st.container(border=True):
        st.markdown(
            "To run alerts in the background (works when app is closed):"
        )
        st.code(
            "cd /home/gongati/projects/market-lens\n"
            "source venv/bin/activate\n"
            "python alert_monitor.py",
            language="bash",
        )
        st.caption(
            "The monitor checks every 5 minutes during market hours "
            "(9:15 AM – 3:30 PM IST, Mon–Fri). "
            "It sleeps automatically outside market hours."
        )
