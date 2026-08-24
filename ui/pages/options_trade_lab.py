"""Options Trade Lab — Research Engine sub-page.

Researches ONE bought CE/PE idea at a time, in two layers: can the stock move
(point-in-time, no lookahead) and is the selected contract suitable. All
interpretation lives in research_engine.trade_lab (pure, tested); this page
only gathers inputs, fetches data, and renders the returned LabReport.

Educational research only — research classifications, never buy/sell
recommendations. Missing data reduces DATA COVERAGE, it never adds risk.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from data.manager import build_source_manager, fetch_by_interval
from research_engine import store
from research_engine import trade_lab as tl
from ui.components.panels import page_title, section_title
from ui.pages.gap_signals import drop_forming_bar

_MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"]
_REASONS = ["Demand zone", "Supply zone", "Breakout", "Gap-up continuation",
            "Pattern", "Other"]


# ------------------------------------------------------------- data fetching
def _fetch_daily(symbol: str) -> pd.DataFrame | None:
    source = st.session_state.get("selected_data_source", "Yahoo Finance")
    creds = st.session_state.get("credentials", {}).get(source, {})
    try:
        manager = build_source_manager(source, creds)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not connect to {source}: {exc}")
        return None
    full = f"{symbol}.NS" if source == "Yahoo Finance" else symbol
    df, _meta = fetch_by_interval(full, "Daily", fetch_fn=manager.get_history)
    if df is None or len(df) < 60:
        return None
    return drop_forming_bar(df)


def _market_context(as_of: dt.date) -> dict:
    out: dict = {}
    try:
        df = _fetch_daily("^NSEI")
        if df is None:
            return out
        cut = df[[ts.date() <= as_of for ts in df.index]]
        if len(cut) < 30:
            return out
        c = cut["Close"]
        ema20 = c.ewm(span=20, adjust=False).mean()
        tr = pd.concat([cut.High - cut.Low, (cut.High - c.shift()).abs(),
                        (cut.Low - c.shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
        out["market_regime_up"] = bool(c.iloc[-1] > ema20.iloc[-1])
        out["market_ext_atr"] = round(float((c.iloc[-1] - ema20.iloc[-1]) / atr.iloc[-1]), 2)
    except Exception:  # noqa: BLE001
        pass
    return out


def _sector_line(symbol: str) -> str | None:
    try:
        from utils.helpers import load_predefined_watchlists
        for wl in load_predefined_watchlists():
            name = wl.get("name", "")
            if name.startswith("Nifty ") and name not in ("Nifty 50", "Nifty Next 50") \
                    and symbol in wl.get("symbols", []):
                return f"listed in the {name} sector watchlist"
    except Exception:  # noqa: BLE001
        pass
    return None


def _result_days(symbol: str, as_of: dt.date) -> tuple[int | None, bool]:
    try:
        from data.earnings_calendar import get_earnings
        row = get_earnings([symbol], cache_only=True).get(symbol) or {}
        raw = row.get("result_date")
        if not raw:
            return None, False
        delta = (dt.date.fromisoformat(str(raw)[:10]) - as_of).days
        return (delta if 0 <= delta <= 30 else None), True
    except Exception:  # noqa: BLE001
        return None, False


def _history(symbol: str) -> dict | None:
    try:
        run = store.latest_run()
        if not run:
            return None
        df = store.get_findings(run["id"], "stock_findings")
        if df is None or df.empty:
            return None
        row = df[df["symbol"] == symbol]
        return row.iloc[0].to_dict() if len(row) else None
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------ rendering
def _verdict_card(col, title: str, text: str, tone: str) -> None:
    colors = {"good": "#1B5E20", "warn": "#8a6d00", "bad": "#B3261E", "info": "#31333F"}
    col.markdown(
        f"<div style='border:1px solid {colors.get(tone, '#31333F')}44;"
        f"border-left:5px solid {colors.get(tone, '#31333F')};border-radius:8px;"
        f"padding:0.7rem 0.9rem;background:#FFFFFF;height:100%;'>"
        f"<div style='font-size:0.72rem;letter-spacing:0.06em;color:#666;"
        f"text-transform:uppercase;'>{title}</div>"
        f"<div style='font-size:0.98rem;font-weight:600;color:{colors.get(tone, '#31333F')};"
        f"margin-top:0.25rem;line-height:1.35;'>{text}</div></div>",
        unsafe_allow_html=True,
    )


def _tone_for(verdict: str) -> str:
    v = verdict.lower()
    if "take" in v or "valid" in v and "not" not in v and "avoid" not in v:
        return "good"
    if "avoid" in v or "failed" in v or "bad option" in v or "too expensive" in v:
        return "bad"
    return "warn"


def _chart(frame: pd.DataFrame, as_of: dt.date, levels: dict,
           buy_date: dt.date | None, sell_date: dt.date | None) -> go.Figure:
    cut = frame[[ts.date() <= (sell_date or as_of) for ts in frame.index]]
    view = cut.iloc[-140:]
    fig = go.Figure(go.Candlestick(
        x=view.index, open=view.Open, high=view.High, low=view.Low,
        close=view.Close, name="price",
        increasing_line_color="#1B5E20", decreasing_line_color="#B3261E"))
    if levels.get("zone_lo") and levels.get("zone_hi"):
        fig.add_hrect(y0=levels["zone_lo"], y1=levels["zone_hi"],
                      fillcolor="#2E7D32", opacity=0.12, line_width=0,
                      annotation_text="demand zone", annotation_font_size=10)
    if levels.get("supply_lo") and levels.get("supply_hi"):
        fig.add_hrect(y0=levels["supply_lo"], y1=levels["supply_hi"],
                      fillcolor="#B3261E", opacity=0.10, line_width=0,
                      annotation_text="supply zone", annotation_font_size=10)
    line_specs = [("breakeven", "#6750A4", "dot", "breakeven"),
                  ("invalidation", "#B3261E", "dash", "invalidation (stop loss ref)"),
                  ("target", "#1B5E20", "dot", "target / opposing zone")]
    for key, color, dash, label in line_specs:
        val = levels.get(key)
        if val:
            fig.add_hline(y=val, line_color=color, line_dash=dash, line_width=1.1,
                          annotation_text=f"{label} {val:,.1f}",
                          annotation_font_size=10)
    for d, label in [(as_of, "research date"), (buy_date, "buy"), (sell_date, "sell")]:
        if d:
            fig.add_vline(x=pd.Timestamp(d), line_color="#7f7f7f", line_dash="dot",
                          line_width=1, annotation_text=label,
                          annotation_font_size=9)
    fig.update_layout(height=430, margin=dict(l=10, r=10, t=25, b=10),
                      xaxis_rangeslider_visible=False, showlegend=False)
    return fig


# ------------------------------------------------------------------ the page
def render_options_trade_lab() -> None:
    page_title(
        "Options Trade Lab",
        "Research one CE/PE idea at a time — the stock setup and the option "
        "contract judged separately",
    )
    st.info(
        "**Research and learning only — not a buy/sell recommendation tool.** "
        "Layer 1 asks whether the stock can move in your expected direction; "
        "Layer 2 asks whether the selected contract is suitable even if it "
        "does. Missing data reduces *data coverage* — it is never guessed. "
        "V1 covers bought CE/PE positions only.",
        icon=":material/science:",
    )
    if st.button("← Back to Research Engine", key="otl_back"):
        st.session_state.active_page = "research"
        st.rerun()

    # ---------------- smart text ----------------
    with st.expander("Smart text input (optional)", expanded=False):
        raw = st.text_area(
            "Describe the trade", key="otl_smart_text", height=70,
            placeholder="CDSL Aug 1380 CE premium 45   ·   or   ·   "
                        "CDSL August 1380 CE bought 23 July premium 45 sold 20 August premium 8")
        if st.button("Parse into the form", key="otl_parse"):
            fields, warns = tl.parse_smart_text(raw)
            mapping = {"symbol": "otl_symbol", "strike": "otl_strike",
                       "premium": "otl_premium", "sell_premium": "otl_sell_premium",
                       "option_type": "otl_type", "expiry_month": "otl_month",
                       "num_lots": "otl_lots", "lot_size": "otl_lot_size"}
            for k, widget in mapping.items():
                if k in fields:
                    if k == "expiry_month":
                        st.session_state[widget] = _MONTH_NAMES[fields[k] - 1]
                    elif k == "option_type":
                        st.session_state[widget] = fields[k]
                    elif k == "symbol":
                        st.session_state[widget] = str(fields[k])
                    else:
                        st.session_state[widget] = float(fields[k])
            if "buy_date" in fields:
                st.session_state["otl_buy_date"] = fields["buy_date"]
            if "sell_date" in fields:
                st.session_state["otl_sell_date"] = fields["sell_date"]
                st.session_state["otl_mode"] = "Post-trade review"
            for w in warns:
                st.warning(w)

    # ---------------- form ----------------
    c1, c2, c3, c4 = st.columns(4)
    symbol = c1.text_input("Stock symbol", key="otl_symbol").strip().upper()
    mode_label = c2.radio("Trade mode", ["Pre-trade research", "Post-trade review"],
                          key="otl_mode", horizontal=False)
    direction = c3.radio("Direction", ["Bullish", "Bearish"], key="otl_direction")
    option_type = c4.radio("Option type", ["CE", "PE"], key="otl_type")
    mode = "post" if mode_label.startswith("Post") else "pre"

    c1, c2, c3, c4 = st.columns(4)
    strike = c1.number_input("Strike price", min_value=0.0, step=10.0, key="otl_strike")
    premium = c2.number_input("Premium paid (per share)", min_value=0.0, step=0.5,
                              key="otl_premium")
    num_lots = c3.number_input("Number of lots", min_value=0.0, step=1.0, key="otl_lots")
    lot_size = c4.number_input("Lot size (shares per lot)", min_value=0.0, step=25.0,
                               key="otl_lot_size",
                               help="From your broker's contract details. Total "
                                    "quantity = lots × lot size.")
    if num_lots and lot_size:
        st.caption(f"Total quantity: {int(num_lots * lot_size):,} shares · total "
                   f"premium: ₹{premium * num_lots * lot_size:,.0f}")

    c1, c2, c3 = st.columns(3)
    research_date = c1.date_input("Research date", value=dt.date.today(),
                                  key="otl_research_date")
    buy_date = None
    sell_date = None
    sell_premium = None
    spot_entry = spot_exit = None
    if mode == "post":
        buy_date = c2.date_input("Buy date", value=st.session_state.get(
            "otl_buy_date", dt.date.today()), key="otl_buy_date_w")
        sell_date = c3.date_input("Sell date", value=st.session_state.get(
            "otl_sell_date", dt.date.today()), key="otl_sell_date_w")
        c1, c2, c3 = st.columns(3)
        sell_premium = c1.number_input("Sell premium (per share)", min_value=0.0,
                                       step=0.5, key="otl_sell_premium")
        spot_entry = c2.number_input(
            "Spot at entry (optional)", min_value=0.0, step=1.0, key="otl_spot_entry",
            help="Underlying price at your actual fill time. Leave 0 to use the "
                 "daily close (labelled as a proxy).") or None
        spot_exit = c3.number_input(
            "Spot at exit (optional)", min_value=0.0, step=1.0,
            key="otl_spot_exit") or None

    c1, c2, c3 = st.columns(3)
    reason = c1.selectbox("Trade reason", _REASONS, key="otl_reason")
    c2.selectbox("Timeframe", ["Daily"], key="otl_tf", disabled=True,
                 help="V1 analyses daily bars; intraday timeframes come later.")
    notes = c3.text_input("Notes", key="otl_notes")

    # ---------------- expiry (3-badge model) ----------------
    section_title("Expiry")
    chain = st.session_state.get(f"otl_chain_{symbol}")
    e1, e2, e3 = st.columns([1.2, 1.4, 1.4])
    month_name = e1.selectbox("Expiry month", _MONTH_NAMES,
                              index=dt.date.today().month - 1, key="otl_month")
    month = _MONTH_NAMES.index(month_name) + 1
    year = research_date.year if month >= research_date.month else research_date.year + 1

    nse_expiries = chain.get("expiries") if chain and chain.get("ok") else None
    manual = st.session_state.get("otl_expiry_manual")
    expiry_date, badge, expiry_warnings = tl.resolve_expiry(
        month, year, nse_expiries, manual)
    with e2:
        if badge == tl.EXPIRY_SUGGESTION and expiry_date:
            st.markdown(f"Suggested: **{expiry_date}** · `{badge}`")
            if st.button("Use this date (manual confirmation)", key="otl_accept_sugg"):
                st.session_state["otl_expiry_manual"] = expiry_date
                st.rerun()
        else:
            st.markdown(f"Expiry: **{expiry_date}** · `{badge}`")
    with e3:
        override = st.date_input("Manual expiry override", value=manual or
                                 (expiry_date or dt.date.today()), key="otl_expiry_w")
        if st.button("Apply manual date", key="otl_apply_manual"):
            st.session_state["otl_expiry_manual"] = override
            st.rerun()
    if expiry_date and badge != tl.EXPIRY_SUGGESTION:
        anchor = buy_date if mode == "post" else research_date
        st.caption(f"Days to expiry from {'buy' if mode == 'post' else 'research'} "
                   f"date: **{(expiry_date - anchor).days}**")

    # ---------------- live chain (explicit fetch only) ----------------
    f1, f2 = st.columns([1.2, 2.8])
    if f1.button("Fetch live contract data (NSE)", key="otl_fetch_chain",
                 disabled=not symbol):
        from data.nse_options import fetch_option_chain
        with st.spinner("Fetching option chain from NSE…"):
            st.session_state[f"otl_chain_{symbol}"] = fetch_option_chain(symbol)
        st.rerun()
    if chain is not None:
        if chain.get("ok"):
            listed = len(chain.get("expiries", []))
            # covered_expiries is absent on chains cached before the
            # multi-expiry fetch existed — fall back to the listed count.
            covered = len(chain.get("covered_expiries") or []) or listed
            f2.caption(f"Live chain: spot {chain.get('spot')} · "
                       f"strikes for {covered} of {listed} expiries · as of "
                       f"{chain.get('chain_timestamp', '')}")
            if covered < listed:
                f2.caption("Some expiries could not be fetched — contracts on "
                           "them will fall back to proxy option metrics.")
        else:
            f2.warning(f"Live chain unavailable ({chain.get('error', 'unknown')}) — "
                       "analysis will use proxy option metrics.")

    # ---------------- analyse ----------------
    st.divider()
    if st.button("Run research", type="primary", key="otl_run",
                 disabled=not symbol or not strike or not premium):
        if badge == tl.EXPIRY_SUGGESTION:
            st.error("The expiry is still an unverified suggestion. Confirm it "
                     "(button above) or set a manual date, then run again.")
            return
        frame = _fetch_daily(symbol)
        if frame is None:
            st.error(f"Could not fetch daily history for {symbol}.")
            return
        as_of = buy_date if mode == "post" else research_date
        context = _market_context(as_of)
        sector = _sector_line(symbol)
        if sector:
            context["sector_line"] = sector
        rd, known = _result_days(symbol, as_of)
        context["result_days"] = rd
        context["result_date_known"] = known
        context["history"] = _history(symbol)
        if chain and chain.get("ok") and expiry_date:
            from data.nse_options import strike_record
            rec = strike_record(chain, expiry_date, float(strike), option_type)
            context["chain"] = {"strike_record": rec} if rec else None
            if rec is None:
                st.warning("The selected strike/expiry was not found in the live "
                           "chain — proxy option metrics used.")
        inputs = {
            "mode": mode, "symbol": symbol, "direction": direction.lower(),
            "option_type": option_type, "strike": float(strike),
            "premium": float(premium), "sell_premium": sell_premium or None,
            "expiry_date": expiry_date, "expiry_badge": badge,
            "research_date": research_date, "buy_date": buy_date,
            "sell_date": sell_date, "num_lots": num_lots or None,
            "lot_size": lot_size or None, "spot_at_entry": spot_entry,
            "spot_at_exit": spot_exit, "trade_reason": reason, "notes": notes,
        }
        report = tl.analyse(inputs, frame, context)
        st.session_state["otl_report"] = report
        st.session_state["otl_inputs"] = inputs
        st.session_state["otl_frame"] = frame

    report: tl.LabReport | None = st.session_state.get("otl_report")
    if report is None:
        return
    inputs = st.session_state["otl_inputs"]
    frame = st.session_state["otl_frame"]

    # ---------------- verdict cards ----------------
    st.divider()
    v1, v2, v3 = st.columns(3)
    _verdict_card(v1, "Stock Setup Verdict", report.stock.verdict,
                  _tone_for(report.stock.verdict))
    _verdict_card(v2, "Option Suitability Verdict", report.option.verdict,
                  _tone_for(report.option.verdict))
    _verdict_card(v3, "Final Research Verdict", report.final_verdict,
                  _tone_for(report.final_verdict))
    st.caption(report.final_reason)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Sideways risk", f"{report.sideways_score}/100",
              report.sideways_band, delta_color="off")
    m2.metric("Trap risk", f"{report.trap_score}/100", report.trap_band,
              delta_color="off")
    m3.metric("Premium burden", report.option.burden_rating or "n/a")
    m4.metric("Data coverage",
              f"{report.coverage_available}/{report.coverage_total} checks")
    st.caption(report.option.burden_caption)

    for w in report.warnings:
        st.warning(w)

    # ---------------- chart ----------------
    as_of = inputs.get("buy_date") or inputs.get("research_date")
    st.plotly_chart(_chart(frame, as_of, report.chart_levels,
                           inputs.get("buy_date"), inputs.get("sell_date")),
                    use_container_width=True, key="otl_chart")

    # ---------------- detail tables ----------------
    d1, d2 = st.columns(2)
    with d1:
        section_title("Stock setup details")
        s = report.stock
        rows = {
            "Verdict": s.verdict,
            "Reasons": " · ".join(s.reasons),
            "Price vs zone": s.price_vs_zone,
            "Confirmation": s.confirmation_status,
            "Closes inside zone": s.closes_inside,
            "Days since zone touch": s.days_since_touch,
            "Zone (demand)": (f"{s.demand.proximal:.1f} / {s.demand.distal:.1f} · "
                              f"ODD {s.demand.odd_score} · {s.demand.strength} · "
                              f"tested {s.demand.times_tested}x") if s.demand else "—",
            "Nearest supply": (f"{s.supply.proximal:.1f} (tested "
                               f"{s.supply.times_tested}x)") if s.supply else "—",
            "RR to opposing zone": s.rr_to_obstacle,
            "Above EMA20 / SMA50 / SMA200":
                f"{s.above_ema20} / {s.above_sma50} / {s.above_sma200}",
            "20-day momentum": f"{s.momentum_20d_pct}%",
            "Max recent volume vs 20-avg": s.bounce_volume_ratio,
            "Market": s.market_line,
            "Sector": s.sector_line,
            "History": s.history_line,
        }
        st.table(pd.DataFrame(rows.items(), columns=["Check", "Value"]).astype(str))
    with d2:
        section_title("Option details")
        o = report.option
        rows = {
            "Verdict": o.verdict,
            "Reasons": " · ".join(o.reasons),
            "Moneyness": f"{o.moneyness} ({o.strike_distance_pct:+.1f}% from spot)",
            "Breakeven": o.breakeven,
            "Required move to breakeven": f"{o.required_move_pct}%",
            "Expected move to expiry (approx)": f"{o.expected_move_pct}%",
            "Days to expiry": o.days_to_expiry,
            "Premium % of spot": f"{o.premium_pct_of_spot}%",
            "Premium burden": o.burden_rating,
            "Total quantity": o.total_quantity or "— (enter lots + lot size)",
            "Total premium at risk": f"₹{o.total_premium:,.0f}" if o.total_premium else "—",
            "Live IV": o.iv if o.iv else "missing",
            "Live OI / volume": f"{o.oi} / {o.chain_volume}" if o.oi is not None else "missing",
            "Bid/ask spread": f"{o.spread_pct}%" if o.spread_pct is not None else "missing",
        }
        st.table(pd.DataFrame(rows.items(), columns=["Check", "Value"]).astype(str))

    with st.expander("Score components (observed flags only)"):
        sc1, sc2 = st.columns(2)
        sc1.markdown("**Sideways risk components**")
        sc1.dataframe(pd.DataFrame(report.sideways_components), hide_index=True)
        sc2.markdown("**Trap risk components**")
        sc2.dataframe(pd.DataFrame(report.trap_components), hide_index=True)
        if report.coverage_missing:
            st.caption("Not assessable (reduces coverage, adds no risk): "
                       + "; ".join(report.coverage_missing))

    # ---------------- post-trade learning ----------------
    if report.mode == "post" and report.decomposition:
        section_title("Premium decomposition")
        d = report.decomposition
        st.caption(d.caption)
        if d.rows:
            st.table(pd.DataFrame(d.rows).astype(str))
        else:
            st.markdown(d.text)
        section_title("Learning notes")
        for note in report.learning:
            st.markdown(f"- {note}")

    # ---------------- save / copy ----------------
    st.divider()
    a1, a2 = st.columns([1, 2.5])
    if a1.button("Save analysis to research DB", key="otl_save"):
        report_dict = dataclasses.asdict(report)
        aid = store.save_trade_lab_analysis(inputs["symbol"], report.mode,
                                            inputs, report_dict)
        st.success(f"Saved (id {aid[:8]}…). Recent analyses appear below.")
    with a2.expander("Copy summary"):
        st.code(tl.build_summary_text(report, inputs), language="text")

    recent = store.list_trade_lab_analyses(limit=8)
    if recent:
        st.caption("Recent saved analyses: " + " · ".join(
            f"{r['symbol']} ({r['mode']}, {str(r['created_at'])[:10]})"
            for r in recent))
