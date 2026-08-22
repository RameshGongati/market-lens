"""Market Lens Research Engine page.

Reads ONLY from research_engine.store / research_engine.importer (file/DB
readers). It must never import research_engine.harness modules — the harness
carries a backtest-only zone-engine patch that must not leak into the app.
Everything shown here is research output for scanner design, not trading
advice; candidate labels are research classifications (TAKE candidate, WAIT,
WATCH, REDUCE SIZE, AVOID, NO TRADE), not buy/sell recommendations.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from research_engine import importer, store
from ui.components.panels import page_title, section_title, spacer

_DECISION_ORDER = ["TAKE", "REDUCE SIZE", "WAIT", "WATCH"]
_DECISION_LABELS = {
    "TAKE": "TAKE candidate",
    "REDUCE SIZE": "REDUCE SIZE",
    "WAIT": "WAIT",
    "WATCH": "WATCH",
}

_FINDINGS_MENU = [
    ("engine_ladder", "Ablation ladder (filter stack vs base)"),
    ("decision_validation", "Decision validation"),
    ("setup_rankings", "Setup rankings"),
    ("trap_analysis", "Trap analysis"),
    ("trap_score_validation", "Trap score validation"),
    ("event_impact", "News / result event impact"),
    ("institutional_proxy", "Institutional proxies (labelled proxies)"),
    ("combo_rankings", "Combination rankings"),
    ("flag_effects", "Single-flag effects"),
    ("stock_findings", "Per-stock findings"),
    ("sector_findings", "Per-sector findings"),
]


# ---------------------------------------------------------------- pure helpers
def filter_candidates(df: pd.DataFrame, decisions: list[str], timeframes: list[str],
                      direction: str, setups: list[str], symbol_query: str) -> pd.DataFrame:
    """Pure filter logic (unit-tested; no Streamlit)."""
    out = df
    if decisions:
        out = out[out["final_decision"].isin(decisions)]
    if timeframes:
        out = out[out["timeframe"].isin(timeframes)]
    if direction in ("bullish", "bearish"):
        out = out[out["bullish_or_bearish"] == direction]
    if setups:
        out = out[out["setup_name"].isin(setups)]
    if symbol_query:
        out = out[out["symbol"].str.contains(symbol_query.strip().upper(), na=False)]
    return out


def headline_bits(run: dict) -> list[tuple[str, str]]:
    """(label, value) pairs for the run headline strip. Pure; unit-tested."""
    h = run.get("headline", {}) or {}
    bits: list[tuple[str, str]] = []
    if "engine_expectancy_r" in h:
        bits.append(("Engine expectancy", f"{h['engine_expectancy_r']:+.3f} R"))
    if "base_expectancy_r" in h:
        bits.append(("Base expectancy", f"{h['base_expectancy_r']:+.3f} R"))
    if "engine_n" in h:
        bits.append(("Engine trades", f"{h['engine_n']:,}"))
    if "pct_filtered_out" in h:
        bits.append(("Signals filtered", f"{h['pct_filtered_out']:.1f}%"))
    if "engine_profit_factor" in h:
        bits.append(("Profit factor", f"{h['engine_profit_factor']:.2f}"))
    return bits


# ------------------------------------------------------------------ rendering
def _disclaimer() -> None:
    st.info(
        "**Research & scanner design only — not trading advice.** Everything on "
        "this page comes from historical backtests with in-sample weights. "
        "Labels such as *TAKE candidate*, *WAIT*, *WATCH*, *REDUCE SIZE*, "
        "*AVOID* and *NO TRADE* are research classifications, not buy/sell "
        "recommendations. Historical performance does not guarantee future results.",
        icon=":material/science:",
    )


def _ensure_data() -> dict | None:
    """Init store; auto-import Run 1 on first open. Returns the active run."""
    try:
        store.init_db()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Research Engine database could not be initialised: {exc}")
        return None
    try:
        result = importer.ensure_run1()
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Auto-import of the existing research outputs failed: {exc}")
        result = None
    if result:
        st.toast(f"Imported research run: {result['label']} "
                 f"({result['n_candidates']:,} candidates)")
    return store.latest_run()


def _render_findings(run: dict) -> None:
    bits = headline_bits(run)
    if bits:
        cols = st.columns(len(bits))
        for col, (label, value) in zip(cols, bits):
            col.metric(label, value)
        st.caption(
            "Headline: the engine's gate stack vs the unfiltered base on the same "
            "signals. Full context is in the ablation ladder below."
        )
    available = store.list_findings(run["id"])
    menu = [(name, label) for name, label in _FINDINGS_MENU if name in available]
    if not menu:
        st.warning("No findings imported yet. Use **Re-import** on the "
                   "Validation History tab once research outputs exist.")
        return
    label_by_name = dict(menu)
    choice = st.selectbox(
        "Findings table", [name for name, _ in menu],
        format_func=lambda n: label_by_name[n], key="research_findings_table",
    )
    df = store.get_findings(run["id"], choice)
    if df is None or df.empty:
        st.warning("This table imported empty.")
        return
    st.dataframe(df, use_container_width=True, hide_index=True, height=430)
    st.caption(f"{len(df)} rows · run: {run['label']}")

    charts = [p for p in (run.get("artifacts", {}) or {}).get("charts", []) if Path(p).exists()]
    if charts:
        with st.expander(f"Example charts ({len(charts)}) — winning, losing and trap trades"):
            for row_start in range(0, len(charts), 2):
                cols = st.columns(2)
                for col, path in zip(cols, charts[row_start:row_start + 2]):
                    col.image(path, caption=Path(path).stem.replace("_", " "))
    missing = len((run.get("artifacts", {}) or {}).get("charts", [])) - len(charts)
    if missing > 0:
        st.warning(f"{missing} chart file(s) referenced by this run are missing on disk.")


def _render_candidates(run: dict) -> None:
    st.markdown(
        "**Historical candidate browser.** These are the engine's decisions on "
        "the backtest window, shown *with their eventual outcomes* so the gate "
        "stack can be studied. This is not a live scan: generating candidates "
        "from current market data arrives with the V2 background runner."
    )
    df = store.get_candidates(run["id"])
    if df.empty:
        st.warning("No candidates imported for this run.")
        return
    f1, f2, f3, f4 = st.columns([1.3, 1.1, 0.9, 1.2])
    decisions = f1.multiselect(
        "Decision", _DECISION_ORDER, default=["TAKE", "REDUCE SIZE"],
        format_func=lambda d: _DECISION_LABELS.get(d, d), key="research_cand_decisions",
    )
    timeframes = f2.multiselect(
        "Timeframe", sorted(df["timeframe"].dropna().unique().tolist()),
        key="research_cand_tfs",
    )
    direction = f3.selectbox("Direction", ["both", "bullish", "bearish"],
                             key="research_cand_dir")
    symbol_q = f4.text_input("Symbol contains", key="research_cand_symbol")
    setups = st.multiselect(
        "Setup", sorted(df["setup_name"].dropna().unique().tolist()),
        key="research_cand_setups",
    )
    view = filter_candidates(df, decisions, timeframes, direction, setups, symbol_q)
    view = view.sort_values(["final_confidence_score"], ascending=False)

    n_take = int((view["final_decision"] == "TAKE").sum())
    st.caption(
        f"{len(view):,} candidates shown ({n_take:,} TAKE candidates) · "
        f"outcomes: {round((view['r_multiple'] > 0).mean() * 100, 1) if len(view) else 0}% "
        f"positive R · avg {view['r_multiple'].mean():+.3f} R"
        if len(view) else "0 candidates match the filters"
    )
    show_cols = ["symbol", "sector", "timeframe", "signal_date", "setup_name",
                 "bullish_or_bearish", "final_decision", "entry_price", "stop_loss",
                 "target_2", "rr_to_opposing", "trap_probability_score", "trap_reasons",
                 "final_confidence_score", "days_to_result", "result", "r_multiple",
                 "holding_period"]
    st.dataframe(view[show_cols].head(2000), use_container_width=True,
                 hide_index=True, height=480)
    if len(view) > 2000:
        st.caption(f"Showing the first 2,000 of {len(view):,} rows — narrow the "
                   "filters or download the full filtered set.")
    st.download_button(
        "Download filtered candidates (CSV)",
        view[show_cols].to_csv(index=False).encode(),
        file_name="research_candidates_filtered.csv",
        mime="text/csv",
        key="research_cand_download",
    )


def _render_run_research() -> None:
    st.markdown("**Planned for V2 — not available in this version.**")
    st.markdown(
        "Full research runs take 15–20 minutes across the universe, so they will "
        "run as a **detached background process** (the same flock-guarded pattern "
        "as the alert monitor), never inside this page. This tab will then offer:"
    )
    st.markdown(
        "- Universe (watchlist / Nifty 50 / F&O / All NSE) and timeframe selection\n"
        "- Trade-model parameters (target R, time stop, costs)\n"
        "- A progress card fed by the runner's status file\n"
        "- Automatic import of the finished run into Validation History"
    )
    c1, c2, c3 = st.columns(3)
    c1.selectbox("Universe", ["F&O Stocks (208)"], disabled=True, key="research_run_universe")
    c2.multiselect("Timeframes", ["daily", "weekly", "60m", "75m", "15m"],
                   default=["daily"], disabled=True, key="research_run_tfs")
    c3.selectbox("Trade model", ["2R target · time stop · 0.1% costs"], disabled=True,
                 key="research_run_model")
    st.button("Run research (available in V2)", disabled=True, key="research_run_btn")
    with st.expander("Run it from the command line today"):
        st.code(
            "cd /home/gongati/projects/market-lens\n"
            "source venv/bin/activate\n"
            "pip install -r requirements-research.txt   # first time only\n"
            "bash research_engine/harness/run_all.sh    # full study (~20 min)\n"
            "# then open this page and press Re-import on the Validation History tab",
            language="bash",
        )


def _render_engine_config(run: dict) -> None:
    st.markdown(
        "**Read-only.** The decision is made by evidence-ordered GATES; the "
        "blended confidence score tested non-monotonic and is used only to rank "
        "candidates within a decision bucket."
    )
    section_title("Decision gate stack")
    st.markdown(
        "1. **Setup quality** — the (setup × timeframe) cohort must show positive "
        "expectancy (> +0.03 R) at n ≥ 100. *In-sample tier; re-validated per run.*\n"
        "2. **Location** — a direction-aligned live zone, or the setup is itself a "
        "zone/gap setup.\n"
        "3. **Traps** — only the three validated traps score points (see below); "
        "score ≥ 60 → AVOID, 40–60 → REDUCE SIZE.\n"
        "4. **Risk/reward** — path to the opposing zone ≥ 1.5R (or no obstacle); "
        "1.0–1.5R → WAIT.\n\n"
        "Anything failing gate 1 is **NO TRADE**; qualified setups without "
        "location are **WATCH**."
    )
    section_title("Validated trap weights")
    tw = store.get_findings(run["id"], "trap_weights")
    if tw is not None and not tw.empty:
        st.dataframe(tw, use_container_width=True, hide_index=True)
    else:
        st.warning("Trap weights not imported (trap_weights.json was missing).")
    st.caption(
        "A trap earns points only if it raised stop-outs by ≥0.5pp WITHIN the same "
        "setup and timeframe AND did not improve expectancy. Refuted conditions "
        "(overextension, sharp pre-signal moves, shorting a bullish market, "
        "OBV/AD misalignment …) score zero and are listed in Trap analysis."
    )
    section_title("Explicitly excluded by evidence")
    st.markdown(
        "- OBV / Accumulation-Distribution gating (hurt the ablation ladder)\n"
        "- Market-direction filters on bullish signals (buying fear beat buying strength)\n"
        "- Generic overextension penalties (momentum conditions reduced stop-outs this year)\n"
        "- Institutional claims of any kind — only labelled *proxies* until delivery %, "
        "OI build-up and FII/DII data are wired in"
    )


def _render_validation_history(run: dict | None) -> None:
    runs = store.get_runs()
    if runs:
        rows = []
        for r in runs:
            h = r.get("headline", {}) or {}
            rows.append({
                "run": r["id"], "label": r["label"], "imported_at": r["created_at"],
                "status": r["status"],
                "engine_exp_r": h.get("engine_expectancy_r"),
                "base_exp_r": h.get("base_expectancy_r"),
                "engine_trades": h.get("engine_n"),
                "warnings": len(r.get("warnings", [])),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "Out-of-sample validation is the top open item: every current weight is "
            "calibrated on one in-sample year. Each future run lands here so drift "
            "between runs stays visible."
        )
    else:
        st.warning("No research runs imported yet.")
    if run and run.get("warnings"):
        with st.expander(f"Import warnings for latest run ({len(run['warnings'])})"):
            for w in run["warnings"]:
                st.markdown(f"- {w}")
    spacer(6)
    if st.button("Re-import research outputs", key="research_reimport",
                 help="Re-reads research_engine/output and replaces the run with "
                      "the same label. Use after re-running the harness."):
        with st.spinner("Importing research outputs…"):
            try:
                result = importer.import_run()
                st.success(
                    f"Imported '{result['label']}': {len(result['imported'])} tables, "
                    f"{result['n_candidates']:,} candidates, {result['n_charts']} charts, "
                    f"{len(result['warnings'])} warning(s)."
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Import failed: {exc}")
        st.rerun()


def render_research_page() -> None:
    page_title(
        "Market Lens Research Engine",
        "Evidence base for scanner design — separate from the production zone "
        "engine and scanners",
        icon="logo",
    )
    _disclaimer()
    run = _ensure_data()
    tabs = st.tabs(["Findings", "Trade Candidates", "Run Research",
                    "Engine Config", "Validation History"])
    if run is None:
        with tabs[0]:
            st.warning(
                "No research data available. Generate outputs with the harness "
                "(see Run Research tab) and use Re-import on Validation History."
            )
        with tabs[2]:
            _render_run_research()
        with tabs[4]:
            _render_validation_history(None)
        return
    with tabs[0]:
        _render_findings(run)
    with tabs[1]:
        _render_candidates(run)
    with tabs[2]:
        _render_run_research()
    with tabs[3]:
        _render_engine_config(run)
    with tabs[4]:
        _render_validation_history(run)
