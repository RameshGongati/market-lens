# Market Lens — Refinement Plan

Prioritized implementation roadmap derived from the cross-check of the master requirements document against the codebase (2026-07-18). Items are ordered by dependency and phase.

---

## Immediate — Close Phase 1 Gaps

### Resolved

- ~~M3 Prolonged Habitation~~ — Deemed unnecessary (2026-07-20). Enter+exit cycle counting correctly handles all real-world cases (e.g., NHPC consecutive daily tests). No code change needed.
- ~~M3 Same-Bar Enter+Exit~~ — Fixed (`7c96f2c`, 2026-07-20). A candle that enters AND exits the zone on the same bar now counts as one complete test. Entry and exit checks are separate `if` blocks instead of `if/elif`.
- ~~M3 Close-Based Exit~~ — Fixed (`ba9e212`, 2026-07-20). Exit now requires candle to CLOSE outside the zone, not just wick. A wick that enters the zone but closes inside does not count as tested. Discovered via BAJFINANCE RBD zone (Jul 20 candle).
- ~~M3 Persistent Habitation~~ — Added (`144a0bc`, 2026-07-20). If 4 consecutive candles close inside a zone after entry, the zone is invalidated (imbalance exhausted). Counter resets on any close outside. Discovered via ADANIPORTS DBR zone where price sat inside for 14+ candles.
- ~~M46 Distal Wick Breaches~~ — Resolved by changing M46 to wick-based invalidation (`458ba6c`, 2026-07-20). Any wick past distal now destroys the zone, so a "wick breaches" counter is unnecessary.
- ~~M10 Garbage-Area Rejection~~ — Implemented (`44ef8e9`, 2026-07-26). Achievement ratio = legout_move / base_range. Ratio < 0.5 rejects garbage zones; 0.5-1.0 flags "Weak Departure"; >= 1.0 is "Clean". Skipped for missing-base (M17) and near-zero base ranges. Does not modify ODD score. 10 tests added.
- ~~M12 Narrow Base Width~~ — Implemented (`7e0d79d`, 2026-07-27). `base_width_pct = (base_high - base_low) / proximal * 100`, using the full base range regardless of M13's marking. Information only — no filtering, no score change. Flagged "Wide Base" on the chart above 3% (`WIDE_BASE_THRESHOLD_PCT`, public so the UI imports it), with the raw number always shown in a detail-panel expander. Flag suppressed for missing-base zones: their turning-point candle is exciting by definition, so its range sits near the threshold structurally and the warning was misleading. 6 tests added.

- ~~M17 Docstring Fix~~ — Fixed (`559cb9e`, 2026-08-02). The docstring named
  the body bottom of the legout for demand proximal; the code computes
  `min(body_top_tp, body_top_legout)` — the more conservative of the two body
  tops, the edge nearer the distal. For demand the old text pointed at the
  opposite end of the body from what is computed, so anyone reasoning about
  M17 from the docstring had the zone boundary wrong. Docstring only.

### Remaining

**Nothing. Phase 1 is COMPLETE** (2026-08-02, tagged `v0.6.0`). The last
outstanding rule, M65/M66, was dropped by directive — see below.

---

## Phase 1 — Dropped

### M65/M66 — LOTL Merge + Achievement (DROPPED, 2026-08-02)

**Dropped by directive.** Implemented once and reverted: it "completely messed
up the zone markings" on real charts. The branch was reset, so nothing of that
attempt survives in history.

Merging widens a zone to the union of its members, which moves the
**proximal** — and the proximal is the entry level being traded. Overlapping
zones are therefore kept separate, each reflecting its own base candles only.

`_merge_overlapping_zones()` stays in `filters.py:78-108` but is deliberately
**not called** from `filter_zones()` — see Gotcha 7 in `CLAUDE.md`. It is not
dead code awaiting wiring; leave it unwired unless this decision is revisited.

If it ever is revisited: validate merged boundaries against real charts across
many symbols *before* committing, not just against unit tests. Unit tests
cannot tell you a zone edge moved to the wrong price.

---

## Phase 2-8 — Future Phases

Listed in recommended implementation order within each phase. See `requirements.md` for full specs.

### Phase 2: Trade Computation
1. **M7** — Volatility buffer (ATR-based, needed by M1)
2. **M1** — Entry/SL/target at 2R
3. **M29** — Entry Types 1/2/3 mechanical detection

### Phase 3: Multi-Timeframe
4. **M18/M23** — 3-TF architecture (major infrastructure)
5. **M19-M21** — Curve/location/trend gating
6. **M22/M27** — HTF priority and execution gate
7. **M25/M26** — Score-then-context tie-breaking
8. **M50** — Refine wide HTF zone via LTF (depends on 3-TF)
9. **M34-M39** — Variable aggression (depends on 3-TF)
10. **M48/M49/M51** — Counter-trend rules (depends on 3-TF)

### Phase 4: Structural Trend
11. **M45** — Zone-violation trend
12. **M46ext** — Close-beyond-distal for structural trend

### Phase 5: Confluence Enhancers
13. **M43** — EMA20 trending-only filter (existing code needs: check `detect_trend()`, skip when SIDEWAYS)
14. **M44** — EMA20 multi-TF (depends on Phase 3)
15. **M40/M41** — MA crossover as +1 enhancer (wire existing `TrendFollowingAnalysis`)
16. **M59-M64** — Gap theory
17. **M70/M71** — RSI + divergence (existing UI checkbox, needs analysis logic)
18. **M67** — Bull/bear trap detection

### Phase 6: Risk & Trade Management
19. **M31** — Position sizing
20. **M32/M33** — R:R enforcement + stop discipline
21. **M72/M73** — Structural trailing stop
22. **M74** — Hit-to-hit stops

### Phase 7: Sector / Market Context
23. **M52-M54** — Index/sector/stock confluence (needs new data infrastructure)
24. **M55** — Sector +2 enhancer
25. **M56** — Investing vs trading mode

### Phase 8: Options
26. **Options module** — Awaiting spec documents

### Do Not Build
- **M57/M58** — Candlestick pattern detectors (excluded by directive; still stands)
- ~~**M69** — Conventional chart pattern detectors~~ — **directive REVERSED
  2026-08-06.** Shipped as the Chart Pattern Scanner, built as a separate
  pipeline so the zone engine is untouched. See below.

---

## Shipped Since the Last Doc Update (`1979dc3` → `57cdf35`)

| Commit | What |
|--------|------|
| `45ddedb` | Rebuild trading sessions the data sources drop. Yahoo omits whole sessions and Jugaad loses parallel chunks; `fill_missing_sessions` rebuilds them from the NSE bhavcopy. 70 of 208 F&O stocks were missing 31 July — all 70 repaired. |
| `eb954a9` | Merge the alerts page into one sortable, clickable feed. |
| `95797d6` | One row per stock on the Alerts page, plus a Refresh button. |
| `fa7dcd7` | Persist alerts as they are sent, and stop the monitor stacking copies. History was flushed only at end of cycle (APLAPOLLO sent 07:23:20, written 07:29:35); cron restarted the never-exiting script daily, so instances accumulated — now guarded by an flock. |
| `8bb7cee` | Numbered pagination bar (`page_slice` / `pagination_bar`) and a red Alerts badge. Streamlit silently drops markdown colour directives in button labels, so the badge is CSS `::after`. |
| `50ad77b` | Fix the Reports universe falling back to 50 stocks — two faults, session-state memoisation and widget-state loss on rerun. See Gotcha 29. |
| `3e1b161` | Rebuild the Settings page to the new design. Live status strip (disk footprint, provider, monitor), Telegram setup, data management. Adds `alerts/monitor_control.py` and `utils/system_info.py`; unbuilt controls render disabled rather than hidden. 22 tests. |
| `bdda60c` | Sort the alert feed by time, not by origin. See Gotcha 31. 14 tests. |
| `eb93a7d` | Refine sidebar watchlist controls. |
| `7185ecb` | Run the Reports refresh at page level, not inside a button column. See Gotcha 30. |
| `c37385b`, `6f76404` | Chart Pattern Scanner — see below. |
| `57cdf35` | Improve stock detail chart styling. |

---

## Shipped Since (`cdb9eda` → `542e658`, 2026-08-07..11)

| Commit | What |
|--------|------|
| `61c2db3` | Polish the analysis results table. |
| `ea6bc97` | Configurable scan progress styles — five (capsule / donut / speedometer / pulse / ribbon), selectable in Settings, persisted as `scan_progress_style`. |
| `736fca8` | **Market heatmap dashboard** — `data/market_heatmap.py` (20 index/sector groups, batched Yahoo quotes, unweighted-basket fallback), `ui/pages/market_heatmap.py` (group grid → per-group stock tiles, `st.cache_data` quotes with scan overlays applied outside the cache), heatmap widget on the Market Overview. 6 tests. |
| `1eaf15c` | **Preserve scans across heatmap navigation.** Heatmap tiles are real URL navigations that can start a fresh Streamlit session, so: preferences are now RESTORED at launch (11 session keys — reverses the old "recorded, not restored" design; Gotcha 14 rewritten), a single-row `latest_analysis_snapshot` table restores the last scan, and the heatmap query-param handler uses an idempotency tuple. 5 tests. |
| `542e658` | Reports timing filters (same-day not "still due"; upcoming/recent windows) and chart navigation. 3 tests. |

Full heatmap spec: *Market Heatmap* in `requirements.md`.

---

## Chart Pattern Scanner (DONE — `c37385b`, `6f76404`, 2026-08-06)

M69, previously Do Not Build. Reversed and built as an independent pipeline:
`analysis/pattern_models.py`, `analysis/pattern_scanner.py`, seven modules
under `analysis/pattern_detectors/`, four pages under `ui/pages/pattern_*.py`,
and a `pattern_scans` table for cross-tab deep links.

Five families — Triangles, VCP / Tight Base, Range Breakouts, Flag / Pennant,
Double Top / Bottom. Zone output is consumed only as `zone_context`; nothing
here can write to a `Zone` or to `odd_score`.

**Verified behaviour** (measured, not assumed): on synthetic negatives the
triangle detector flags a clean uptrend 0 times, a flat channel 0 times, and
5.0% of 200 random walks — a fair rate for a shape that does occur in noise.
The gating (contraction >= 0.18, sequence score >= 0.62, apex window) is
doing real work.

### Fixed during review

- **Long-only risk/reward** — `_risk_reward` computed `stop = max(price -
  lower, 0.01)` regardless of direction, so a downside break clamped the stop
  to 0.01 and reported R:R of 6926.25 on live DLF. Now branches on direction;
  the same case reads 1.44.
- **Forming candle** — the detectors now drop the still-open bar
  (`_drop_incomplete_latest_bar` in `triangles.py`, `df.iloc[:-1]` in `vcp.py`
  and `range_breakouts.py`), matching `demand_supply.py` (Gotcha 1). Without
  it "Breakout Confirmed" appeared intraday and reverted by the close, and
  disagreed with `zone_context`, which comes from a pipeline that does drop it.

### Open (PENDING)

| # | Item | Detail |
|---|------|--------|
| 1 | **Test coverage** | 9 tests, all happy-path detection. Missing: negative cases (verified good by hand, pinned by nothing), boundary values on every threshold that decides output (`0.18` contraction, `0.62` sequence, `0.35` flat, `55.0` confidence floor, apex window), `_risk_reward` — which would have caught the bug above — plus `_passes_scan_settings` / `apply_result_filters` and the `save_pattern_scan` → `get_pattern_scan` round-trip. |
| 2 | Detection runs twice per symbol | `pattern_scanner.py` calls `_detect_family` with `zone_result=None` purely as a gate, then again for real. The gate is sound (it avoids paying for `DemandSupplyAnalysis` when there is no pattern) but the second full detection is waste — detect once, enrich after. Costs ~7ms per symbol, so this is a clarity problem, not a speed one. |
| 3 | Dead branches in `_breakout_bias` | The final three branches all return `"Neutral Until Break"`, so both `zone_context` checks do nothing. |
| 4 | Unused imports | The `pattern_types` block in `pattern_scanner.py` (12 names) and `bias_pill` in `pattern_detail.py`. |
| 5 | Layering | `storage/database.py` imports `analysis.pattern_models`. No cycle today — `analysis` imports nothing from `storage` — but it puts domain deserialization in the bottom layer, where every other table speaks plain dicts. |
| 6 | `_LOOKBACK` is a bar count | 120 bars is ~6 months daily but ~5 sessions on 15m, so the pattern window silently shrinks in wall-clock time as the timeframe gets finer. Probably too short to mean much intraday. |

---

## Known Issues (found in the 2026-08-12 codebase audit, not yet fixed)

Confirmed against the code; none has a fix committed. Ordered by likely impact.

| # | Issue | Detail |
|---|-------|--------|
| 1 | **Swapped export arguments on the results page** | `ui/pages/analysis_results.py:186-190` passes `(results, ctx["analysis_type"], ctx["wl_name"])` but the signatures are `_do_export_excel/_do_export_pdf(results, wl_name, analysis_type)` — every export from the results page writes the strategy name into the watchlist field and vice versa (and into the filename). The dead `_render_filter_sort_bar` path passes them correctly, so this regressed in the page split (`96d5c36`). |
| 2 | **"All Chart Patterns" empties the pattern results table** | `ui/pages/pattern_results.py:219` offers `["All Families", *PATTERN_FAMILIES]`, and `PATTERN_FAMILIES[0]` is `"All Chart Patterns"`. Only the `"All Families"` literal bypasses filtering; selecting `"All Chart Patterns"` compares every match's family against that string and returns zero rows. Two "all" entries, one of which silently filters out everything. `"All Families"` is also a bare literal not declared in `pattern_types.py` — exactly the drift CLAUDE.md's single-source rule warns about. |
| 3 | **NSE holiday table stops at 2025** | `utils/market_hours.py:_NSE_HOLIDAYS` has no 2026 entries (and duplicates `2025-10-02`), so every 2026 holiday is treated as a trading day: `is_market_open()` returns True, the alert monitor runs full cycles on closed markets, and `fill_missing_sessions` hunts for bars that never existed. |
| 4 | **`alert_monitor._run_cycle` double-writes the config** | `_flush_history` re-reads/merges/saves per alert precisely to avoid overwrites, but the end of the cycle calls `save_alert_config(config)` with the stale object loaded at cycle start — reintroducing the overwrite hazard for every non-`alert_history` key. |
| 5 | **Zone-proximity math implemented twice** | `alerts/zone_alert_checker.check_zone_alerts` and `alert_monitor._run_cycle` contain independent copies of the same distance/filter logic; a fix to one silently diverges from the other. The monitor could build its results dict and call the checker. |
| 6 | **`jugaad-data` missing from `requirements.txt`** | Installed in the venv (0.33.1) but not listed, so a fresh `pip install -r requirements.txt` produces a broken Jugaad source. |
| 7 | **NSE index refresh dirties the repo and serves stale data** | `data/nse_indices.refresh_all_watchlists()` rewrites the git-tracked `data/predefined_watchlists.json` in place, and does not invalidate `utils.helpers.load_predefined_watchlists`'s `lru_cache(maxsize=1)`, so the running process keeps serving the pre-refresh list. |
| 8 | **Stale comment contradicts Gotcha 7** | `analysis/demand_supply.py:203-205` still describes `filter_zones` output as "overlaps merged" — merging is deliberately never invoked (dropped M65). A reader following the comment would conclude the merge is live. |
| 9 | **Settings "Last Used Selections" label is stale** | Preferences have been restored at launch since `1eaf15c`; the label (and the block's framing) still describes the old recorded-only behaviour. |
| 10 | **`alerts_on` has no sidebar UI** | `ui/components/alerts_toggle.py` lost its caller; the flag is still read by `run_scan` and persisted by the Run button, but nothing in the sidebar renders a toggle to change it. |
| 11 | **Pattern results export built eagerly** | `pattern_results._render_export_bar` builds both the Excel and the PDF bytes on every rerun (`st.download_button(data=...)` is eager), whether or not anyone exports. |

### Dead code inventory (confirmed no importers/callers)

- `alerts/inapp.py` — every function wraps a `storage.database` function the UI calls directly.
- `ui/components/alerts_toggle.py:render_alerts_toggle` — see issue 10.
- `ui/pages/dashboard.py:_render_filter_sort_bar` / `_render_results_grid` and `ui/components/stock_card.py:render_stock_card` — the pre-split results grid (kept pending review, see *Dead Code — Awaiting Review* below).
- `ui/components/panels.py:pending_panel` — defined, documented, never called.
- `ui/pages/placeholders.py:render_reports_page` — shadowed by the real `reports_page.py` import in `app.py`.
- `analysis/long_term.py`, `analysis/intraday.py` — legacy single-axis modules with no importers; `analysis/short_term.py` is still imported for `_compute_rsi` (chart RSI subplot) only.

---

## Research Engine V1 (BUILT — uncommitted, on feature/research-engine)

The two-layer F&O research study (816,937 simulated trades; see
`research_engine/output/FnO_Pattern_Research_Report.docx` and
`Overall_Strategy_Engine_Report.docx`) shipped as a separate subsystem:

- `research_engine/harness/` — the reproducible backtest (CLI-run, survivorship
  patch quarantined behind `enable_backtest_mode()`).
- `research_engine/store.py` + `importer.py` — separate DB at
  `~/.market-lens/research_engine.db`; Run 1 = "2026-08 in-sample year".
- `ui/pages/research_page.py` — "Market Lens Research Engine": Findings,
  Trade Candidates (historical, with outcomes), Run Research (V2 placeholder),
  Engine Config (read-only), Validation History (+ Re-import).
- Sidebar "Research ↗" opens `?research=1` in a new tab (separate session).
- 15 tests incl. the isolation guard (page import must not patch `score_zone`).

### Research Engine — Out-of-sample validation (DONE, Run 2)

Run 1's parameters were applied FROZEN (no re-fitting) to the prior year
(2024-08-11..2025-08-10, daily+weekly, 63,420 trades; intraday history that
old is unavailable). `research_engine/harness/oos_validate.py`; imported as
Run 2 "2024-25 out-of-sample (daily+weekly, frozen params)".

**The engine survives at roughly half strength** — TAKE +0.045R (n=4,928,
PF 1.07) vs NO TRADE −0.056R; AVOID was the worst bucket (−0.187R). Decision
ordering held. Expect this in-sample→OOS decay pattern in every future run.

Twice-validated (build with confidence): Gap-Up Continuation daily
(+0.41 → +0.34R), Fresh Zone Touch daily (+0.09 → +0.10R) and weekly
(+0.07 → +0.09R), MACD bull cross weekly (+0.28 → +0.31R, n=99), the
zone-location gate itself, and trap T4 breakout-without-volume (+2.9 →
+2.3pp stop uplift — the only trap that replicated on stops).

Exposed as in-sample flukes (do NOT build): weekly hammer (−0.23R OOS) and
morning star (−0.26R) — the old R4 recommendation is cut back to its
MACD-cross part only; RSI/stochastic divergence daily (−0.19/−0.20R); MACD
cross daily (−0.13R); weekly demand bounce (−0.11R). Traps T13/T9 faded to
~zero stop-uplift OOS (keep as informational flags, not hard gates); T10
stale zones was strong OOS (+5.6pp) after being negligible in-sample —
regime-sensitive, but consistent with the app already excluding 2+-tested
zones.

### Research experiments log (research-only; no product changes)

**Gap definition A/B — prior HIGH vs prior CLOSE (2026-08-24, DECIDED: keep
production rule unchanged).** `harness/run_gap_ab.sh` + `gap_ab_analysis.py`,
both test years, daily+weekly; results in `output/gap_definition_ab.csv`.
Close-based gaps are a SUPERSET of the production rule, so the question is the
marginal cohort (clears the prior close but not the prior high — "partial
gaps"). Findings (daily): the marginal cohort is genuinely profitable in both
years (+0.14R / +0.16R, PF ~1.3, ~700 trades/yr) but roughly HALF the
production rule's edge (+0.41R / +0.34R, PF 1.9–2.3) with nearly double the
stop-loss rate (~48% vs ~25–34%). Weekly: unusable under either definition.
Decision: production stays high-based; the partial-gap cohort has passed the
two-year bar and could later ship as a clearly-labelled weaker second tier if
breadth is ever wanted. The `gap_up_close_go` setup stays in the harness as a
research-only setup and gets re-validated for free in future runs.

**Gap-day base rates (2026-08-24, myth check).** Across both years, the
gap-up candle itself is a coin flip: full gaps closed green 49.0% of the time
(baseline any-day: 46.1%), mean open→close −0.03%, and 34% faded >1% from the
open. The scanner's edge never lived on the gap day — the close-holds filter
splits the coin flip after the fact and the +0.34R plays out over the
following sessions. Supporting the full-gap definition: only 20% of full gaps
filled back to the prior close intraday vs 46% of partial gaps.

**Possible future experiment:** a 1.3% threshold ROBUSTNESS sweep (0.8%–3%).
The number was inherited from GTF M5 a priori, never optimized — which is why
the OOS validation is clean. The sweep's goal would be confirming the edge
sits on a plateau, NOT picking a new "best" value (that would be curve-fitting).

### Research Engine V2 (PENDING)

| # | Item | Detail |
|---|------|--------|
| 1 | Detached background runner | flock-guarded process + `monitor_control`-style probe + progress file; wire the Run Research tab. |
| 2 | Production graduation (twice-validated list only) | **Gap-Up Continuation daily scanner: BUILT** — Signals page (`ui/pages/gap_signals.py`), single-source detector + lifecycle tracker (`analysis/gap_signals.py:track_signal`, simulator-parity tested) now imported by the harness, `gap_scans` cache table, chart overlay via `?sig=gapup`, Evidence rank (sorting aid; T9/T13 informational only), confirmed-EOD signals from the last 60 sessions grouped into Active / Target hit / Stop loss hit / Time-stopped tabs with counts; Telegram alert deferred. Still pending: SMA50 gate on fresh zone touches; weekly MACD-cross scan; T4 volume gate + chips in the Pattern Scanner; RR-to-opposing column. |
| 3 | Live candidate generation | Apply the gate stack to a fresh scan (frozen, twice-validated parameters). |
| 4 | Rolling re-validation | Re-run OOS quarterly as new data accrues; watch Run-over-run drift in Validation History. |
| 5 | Real institutional data | NSE delivery %, F&O OI build-up, FII/DII before the Institutional Support Score gets any veto power (OBV/AD tested harmful; VWAP alignment +0.099R was the one good proxy). |

---

## Pending — Discussed, Not Started

### UI Redesign — Open Decisions (PENDING, 2026-08-03)

Awaiting a call from the user; nothing blocked on implementation.

| # | Item | Options |
|---|------|---------|
| 1 | **Sidebar dark navy rail** | Cards restyled as navy panels **(A, recommended)** vs flattened to labelled sections (B) |
| 2 | Sidebar popovers / dropdown lists | Light **(recommended)** vs dark. They render in a portal outside the sidebar, so "dark" also darkens the main-area Export popover and the results-page dropdowns — see Gotcha 21 |
| 3 | Run Analysis indigo `#5B5BD6` | Sidebar-only override **(recommended)** vs changing `primaryColor` app-wide, which recolours main-area primary buttons |
| 4 | Market status block | Mockup form (red CLOSED chip + NIFTY level and change) **(recommended)** vs today's amber card |
| 5 | **Overview tab on the stock detail page** | It duplicates the chart rail — Setup Summary and Quick Trade Plan render in both, and only Key Levels is unique. Drop it (move Key Levels into the rail), keep it as a chart-free small-screen view, or repurpose it |

### Dead Code — Awaiting Review (PENDING)

`_render_filter_sort_bar` and `_render_results_grid` in `ui/pages/dashboard.py`
are defined but unreachable. They lost their caller when the results grid moved
to `ui/pages/analysis_results.py` in `96d5c36`. Exports, filters and sorting
were rebuilt on the new page; the zone-proximity alert banner they also
contained was NOT, and is now the Alerts page (`0fb5106`). The orphaning also
strands `render_stock_card` in `ui/components/stock_card.py` (241 lines; only
`build_detail_url` from that module is still live).

Roughly 130 lines in `dashboard.py` plus the card component. Left in place at
the user's request pending review — the decision is whether anything in them
is still wanted before deletion. The full dead-code inventory is under *Known
Issues* above.

### Reports Page — Phase 2 (PENDING)

Phase 1 shipped the results monitor on live yfinance data. Deferred:

- **OI / Vol Spike column** — needs an F&O derivatives pipeline
  (`jugaad_data.derivatives_df` / `bhavcopy_fo_raw`) with its own history and
  cache. Column is present and blank.
- **BMO / AMO session column** — yfinance carries no reliable Indian
  pre/post-market marker. NSE publishes board-meeting intimations with the
  session, but only as unstructured announcements. Column is present and
  blank; do not guess it.
- **Result alert subscriptions** — "result today", "result tomorrow", "EPS
  surprise", "price reaction after result". Phase 1 shows the existing
  zone-alert count only.
- **Full F&O refresh does not switch the view.** The refresh button fills the
  cache for all 208 symbols but leaves the `Only F&O` toggle off, so the table
  still shows the smaller universe and the refresh appears to have done
  nothing. Should enable the toggle on completion — one line.
- **Sector coverage.** Sectors come from the seven shipped sector watchlists,
  which cover 129 of the 208 F&O stocks; the rest show `—` and do not
  contribute to the reaction heatmap. Either extend the lists or accept the
  gaps. `yfinance info["sector"]` is the alternative but costs a network call
  per symbol and uses US-style taxonomy.
- **EPS surprise outliers.** Loss-making comparison quarters produce values
  like `-143.5%` (observed on INDIGO), which trip the 5% High Impact
  threshold. Consider capping or special-casing.

### Trade Journal Page (PENDING)

Routed and reachable, renders a "not built yet" placeholder. Awaiting the
requirements for what it should show.

### Alerts Never Persist for Index Watchlists (PENDING)

`check_and_trigger_alerts` only writes an alert when `stock.id` is set.
Predefined index watchlists build their stocks with `id=0`, so scanning
Nifty 50 produces zone-proximity matches on the Alerts page but writes no
history and sends no Telegram message. Pre-existing behaviour, made visible
by the Alerts page in `0fb5106`. Decide whether index scans should alert.

### Zone Confirmation Grading (PENDING)

The results table grades confirmation as Strong (confirmed-zone score >= 4.5)
or Moderate. That threshold is a placeholder chosen during implementation and
was never confirmed against the GTF methodology.

### Main-Area UI Pass (PENDING)

The sidebar was redesigned in `6eec011`; the main pages were deliberately
left alone and now look unstyled beside it.

- ~~**Dropdown borders**~~ — DONE, see *Select Control Borders* below.
- ~~**Page width cap**~~ — DONE in `96d5c36`; `stMainBlockContainer` is capped
  at 1600px.
- **Card treatment** for the Settings page sections, matching the sidebar.
- **`STRENGTH_BG` / `STRENGTH_COLORS`** in `analysis/base.py` are Bootstrap 4
  alert colours (`#d4edda`, `#fff3cd`, `#f8d7da`) and look dated against the
  current palette. They stay green/amber/red — they are semantic — but the
  shades want refreshing.

**Constraint:** the main canvas must stay white. `backgroundColor` in
`.streamlit/config.toml` is app-wide, so tinting it also tints the area the
Plotly charts render on, whose colours were chosen against white. Put cards
on a white canvas rather than tinting the canvas.

(By contrast, the unused `stock_df` import in `jugaad.connect` is
deliberate — an import probe paired with the `except ImportError` below it.
pyflakes flags it because pyflakes does not honour `# noqa`.)

---

## Completed Features (Non-GTF)

### Select Control Borders (DONE — `c4d3fa9`, then app-wide, 2026-08-02)

The border was never missing. Streamlit draws a 1px border on every select
control and colours it **white**, so against the white section cards, the
white main canvas and the cream popover surface it measured a contrast ratio
of 1.00–1.08 — invisible. Only the colour changes (`#C6C4BC`, and `#4A5361`
on hover/focus); the box, radius and metrics stay Streamlit's, so nothing
shifts by a pixel.

| Surface | Before | After |
|---------|--------|-------|
| Sidebar (Data Source, Watchlist) | 1.00 | 1.75 |
| Screener popover (Proximity, Min ODD Score) | 1.08 | 1.61 |
| Main area (filter bar, Watchlists page) | 1.00 | 1.75 |

The rule is **deliberately unscoped**. `c4d3fa9` scoped it to the sidebar and
reached neither the popover (portal, outside the sidebar element — Gotcha 21)
nor the main filter bar, leaving the same defect one click away in two
places.

Two selector traps recorded as Gotchas 20/21: this build renders
`react-aria-ComboBox` with **no BaseWeb elements at all**, so the previous
`[data-baseweb="select"]` rule matched nothing while looking correct; and
popover bodies render outside the sidebar. Verify UI selectors against the
running app — a wrong selector fails silently and looks exactly like a wrong
value.

### Discarded Quote Fetch Removed (DONE — 2026-08-02)

`yahoo_finance.fetch_quote` assigned `hist = ticker.history(...)` and never
read it; all eight returned fields come from `fast_info`. Deleted.

Re-measured cold, on two disjoint 8-symbol sets so neither path reuses the
other's cache: **209ms → 124ms per quote, 85ms saved, 4.2s across a 50-stock
scan.** The previously recorded "260ms → 42ms, ~11s per scan" was measured
warm and overstated the gain.

Output verified identical across 8 symbols and all 8 fields against a
byte-for-byte copy of the old implementation. `fast_info` reports live traded
values and is not dividend-adjusted, so the `auto_adjust=False`
unadjusted-price guarantee that `history()` needs does not apply to this path.

### Zone Confirmation Screener (DONE — `f8aae8a`, 2026-08-02)

Was "Trade Confirmation Filter (PENDING)". Full spec in
`requirements.md` → *Zone Confirmation Screener*. How the open questions were
resolved:

- **The score blocker** — resolved by the *contained* option: a separate
  `filter_confirmation_zones` over the same raw zones with its own 3.5 floor.
  `filter_zones`, `_MIN_DISPLAY_SCORE` and `odd_score` are untouched, so
  charts and alerts are unaffected. Confirmed by A/B: HEAD and the feature
  branch run against one cached set of OHLCV frames for 12 stocks produce
  byte-identical zones, scores, status, trend and summaries with the mode off.
- **The 5.0 arithmetic was worse than recorded.** The old note covered
  once-tested zones (2.5/3.5/4.5/5.5). Zones tested 2+ times have freshness
  0.0 and reach only 1.0–4.0, so the 5.0 cutoff excluded them *entirely* —
  not just the imperfect ones. 3.5 admits a once-tested zone that earned at
  least one of strength/time, and a repeatedly-tested zone only at 4.0, its
  own ladder's maximum.
- **Recency** — resolved with `_CONFIRMATION_MAX_BARS_SINCE_TEST = 10`.
  `_bars_since_last_confirmation` replays M3's cycle definition to date the
  last exit; deliberately kept in the confirmation path rather than added to
  `Zone`, so scoring is not disturbed.
- **Nearest-per-side cap of 1** added — the level price would meet next.

Measured on Nifty 50 / Swing Trading / Yahoo: **16 of 50 matched**.

Two traps worth remembering, both cost a debugging round:

1. **The scan and the chart use different windows.** The scan fetches the
   trading type's timeframe (1y for Swing), the detail chart the interval's
   (5y for Daily) — see Gotcha 11. A confirmed zone older than the scan
   window is invisible to the screener but drawn on the chart (RELIANCE:
   zone formed ~319 bars back, absent from a 246-bar scan). Not introduced
   here, but this feature makes it visible.
2. **The View link opens a new tab = a separate Streamlit session.** Until
   `cf` was added to the deep link, every chart opened from the dashboard
   rendered with the mode off and showed nothing (Gotcha 13).

### Telegram Alert System (DONE — `55922c9`..`75b2094`, 2026-07-22)
- Config UI on Settings page with bot token, recipients, conditions
- Telegram delivery module with HTML-formatted zone proximity messages
- In-app dashboard alert banner showing stocks near zones
- Background monitor script (`alert_monitor.py`) for market-hours scanning
- Three cooldown modes: once-per-zone-per-day, every-approach, once-per-zone-ever

---

## Implementation Notes

- **No Python code changes** were made during this cross-check. This plan is documentation only.
- **Stage separation must be preserved:** Stage 1 fields (especially `odd_score`) are never modified by Stage 2/3 enrichment.
- **Test-first approach:** Each new rule needs hand-crafted OHLC tests with inline arithmetic comments, covering both demand and supply sides, plus boundary values.
- **Dataclass immutability:** Use `dataclasses.replace()` for zone enrichment, not field mutation.
