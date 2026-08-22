# Market Lens — Claude Code Context

Market Lens is a Streamlit application for Indian equity market analysis (2,374 NSE-listed stocks). It implements the institutional GTF (Trading in the Zone) demand/supply zone methodology: detecting legin-base-legout candlestick patterns (DBR, RBR, RBD, DBD), scoring them with a 7-point ODD trade score, and presenting the top tradeable zones on interactive Plotly charts. A secondary Trend Following strategy (SMA 50/200 crossover) is also available via a two-axis configuration model (Trading Type × Primary Strategy).

A separate **Chart Pattern Scanner** sits alongside the GTF engine — triangles, VCP, range breakouts, flags/pennants and double tops/bottoms, scanned across a watchlist. It is deliberately its OWN pipeline (`analysis/pattern_*`), not an extension of the zone engine: it borrows zone output only as context, and nothing it computes can reach `odd_score`. See *Chart Pattern Scanner* below.

## Tech Stack

- **Runtime:** Python 3.12, Streamlit
- **Data:** yfinance (Yahoo Finance) and jugaad-data (NSE direct) both fully working; 4 other sources scaffolded but not functional
- **Charts:** Plotly (candlestick + volume subplots with zone/SMA/Fibonacci overlays)
- **Storage:** SQLite (`~/.market-lens/market_lens.db`, 7 tables) for watchlists, analysis results, alerts, notes, the last-scan snapshot and the pattern-scan cache; JSON (`~/.market-lens/user_preferences.json`) for preferences
- **Alerts:** Telegram Bot API, config in `config/alert_config.json` (gitignored — holds the bot token)
- **Export:** openpyxl (Excel), reportlab (PDF)
- **Tests:** pytest (496 tests across 23 files)
- **Dependency note:** `jugaad-data` is installed in the venv but MISSING from `requirements.txt` — a fresh `pip install -r requirements.txt` breaks the Jugaad source. Research-only deps (matplotlib, mplfinance, python-docx) live in `requirements-research.txt` and are NOT needed to run the app

## Repo Structure

```
app.py                          # Streamlit entry point, session state, page routing
analysis/
  base.py                       # BaseAnalysis ABC, Status/Strength types
  demand_supply.py              # Orchestrator: detection → scoring → filtering → enrichment
  trend_following.py            # SMA 50/200 golden/death cross strategy
  short_term.py                 # Legacy single-axis module; only _compute_rsi still used (chart RSI subplot)
  long_term.py, intraday.py     # Legacy single-axis modules — no importers, kept pending review
  zone_engine/                  # Core GTF engine (all zone detection lives here)
    candles.py                  #   Candle classification (boring/exciting/strong)
    patterns.py                 #   Legin-base-legout pattern detection + boundary marking
    scoring.py                  #   ODD trade score + M3 test counting + M8 closing quality
    models.py                   #   Zone dataclass (34 fields, 3-stage layering)
    filters.py                  #   Display filtering (freshness/score/nearest-N)
    trend.py                    #   50 SMA clock method (Stage 2)
    enhancers.py                #   EMA 20 confluence (Stage 2)
    fibonacci.py                #   Fibonacci retracement confluence (Stage 3, opt-in)
  pattern_models.py             # PatternMatch / PatternPoint + to_dict/from_dict
  pattern_scanner.py            # Watchlist orchestration, result filters, export rows
  pattern_detectors/            # Chart-pattern engine — SEPARATE from zone_engine
    pattern_types.py            #   Family and type label constants (single source)
    shared.py                   #   PatternCandidate + candidate_to_match (stage/bias/confidence/R:R).
                                #   NOTE: imports helpers FROM triangles.py — triangles is the de-facto
                                #   base module (frame prep, swing detection live there)
    triangles.py                #   Symmetrical / ascending / descending; also _prepare_frame and
                                #   _find_swings used by every other detector
    vcp.py                      #   Volatility contraction / tight base
    range_breakouts.py          #   Rectangle range + bull/bear breakout
    flag_pennant.py             #   Bull/bear flags and pennants
    double_patterns.py          #   Double top / double bottom
alerts/
  telegram.py                   # Telegram delivery + HTML message formatting
  zone_alert_checker.py         # Scans cached results for zone-proximity matches
  manager.py                    # check_and_trigger_alerts: writes signal rows to the alerts table
                                #   (needs a real stock_id, so index/F&O scans never persist)
  inapp.py                      # DEAD CODE — no importers; UI calls storage.database directly
  monitor_control.py            # flock-based status + start/stop for alert_monitor
alert_monitor.py                # Standalone background monitor (runs outside Streamlit).
                                #   Hardcodes Yahoo + Short-term (1y/1d); duplicates the
                                #   zone-proximity math of zone_alert_checker inline
config/
  trading_config.py             # Two-axis model: trading types, strategies, enhancers, timeframes
  preferences.py                # JSON persistence with legacy migration; restored at launch (Gotcha 14)
  alert_settings.py             # Load/save for the Telegram alert config
  credentials.py                # Fernet-encrypted broker credentials (~/.market-lens/credentials.json;
                                #   key sits beside the ciphertext — obfuscation, not protection)
  settings.py                   # App constants, data sources, limits
data/
  manager.py                    # DataSourceManager, timeframe-aware fetching, intraday fallback
  nse_bhavcopy.py               # NSE end-of-day file; backup close for an unfinished bar
  market_indices.py             # NIFTY 50 / BANK NIFTY snapshots + 20-EMA market bias
  market_heatmap.py             # Heatmap universe: 20 index/sector groups, batched Yahoo quotes,
                                #   unweighted-basket fallback for patchy sector indices
  nse_indices.py                # Refreshes predefined_watchlists.json from live NSE (writes the
                                #   TRACKED repo file; does not invalidate helpers' lru_cache)
  earnings_calendar.py          # Results calendar, disk-cached daily (~/.market-lens/earnings)
  sources/base.py               # DataSource ABC + drop_incomplete_bars() + fill_missing_sessions()
  sources/yahoo_finance.py      # Working source; unadjusted prices (auto_adjust=False)
  sources/jugaad.py             # Working source; NSE direct, UTC->IST dates, 30s timeout
  stock_list.json               # 2,374 NSE stocks
  predefined_watchlists.json    # NIFTY50, BANKNIFTY, F&O index watchlists
ui/
  components/panels.py          # Shared surfaces: stat_card, filter_chip, kv_row,
                                #   page_title, panel_head, bias_pill,
                                #   scan_progress (5 styles), page_slice/pagination_bar, SVG icons
  components/stock_detail.py    # Chart page: 7 tabs + Setup Summary / Trade Plan rail
  components/stock_card.py      # build_detail_url() deep-link builder (live).
                                #   render_stock_card grid is UNREACHABLE — see Gotcha 22
  components/sidebar.py         # Two-axis control panel, market status, watchlist picker,
                                #   primary/secondary nav, NSE index refresh
  components/watchlist_panel.py # Watchlist Manager page body: CRUD, autocomplete add,
                                #   own price path (session cache + yfinance direct fallback)
  components/tradingview_chart.py # NOT an embed — placeholder box + tradingview.com deep link
                                #   (free tv.js widget won't load NSE data reliably)
  components/alerts_toggle.py   # DEAD CODE — no caller; alerts_on flag is live but has no
                                #   sidebar UI to set it
  pages/dashboard.py            # Scan engine (run_scan, scan_context) + shared helpers.
                                #   NOT a page — see Gotcha 22
  pages/market_overview.py      # Dashboard landing page (market strip, heatmap widget,
                                #   top opportunities, quick tools)
  pages/market_heatmap.py       # Full heatmap page: group grid + per-group stock tiles,
                                #   st.cache_data quotes (15min groups / 5min stocks),
                                #   scan-setup overlay applied OUTSIDE the cache
  pages/analysis_results.py     # Scan results: cards, filters, ranked table, View links;
                                #   executes the scan and saves the latest_analysis_snapshot
  pages/alerts_page.py          # Zone-proximity matches + Telegram alert history
  pages/reports_page.py         # F&O results monitor (earnings calendar, timing filters)
  pages/settings.py             # Status strip, chart/alert/appearance panels,
                                #   Telegram setup, monitor control, data management
  pages/pattern_scanner.py      # Pattern scan setup (family, type, scope, filters)
  pages/pattern_results.py      # Pattern results table + scan execution
  pages/pattern_detail.py       # Single-pattern chart with trendline overlays
  pages/pattern_common.py       # Universe resolution + pattern deep-link builder
  pages/placeholders.py         # Trade Journal — routed, awaiting requirements
  pages/research_page.py        # Market Lens Research Engine (Findings, Trade Candidates,
                                #   Run Research placeholder, Engine Config, Validation History).
                                #   Reads ONLY research_engine.store/importer — never harness
research_engine/
  harness/                      # Offline backtesting (fetch → detect → simulate → aggregate →
                                #   reports). Carries a BACKTEST-ONLY zone-engine patch behind
                                #   enable_backtest_mode() — see Gotcha 34. CLI-run, not app-run
  store.py                      # SEPARATE SQLite DB: ~/.market-lens/research_engine.db
  importer.py                   # Loads harness outputs into the store as runs (file reads only)
  cache/, output/               # Generated data — gitignored, never committed
storage/database.py             # SQLite CRUD (7 tables, incl. pattern_scans and the
                                #   single-row latest_analysis_snapshot)
utils/
  helpers.py                    # Currency formatting, stock list loading, company names
  export.py                     # Excel + PDF export
  market_hours.py               # NSE market hours, holidays, countdown
  system_info.py                # App-dir disk footprint + cache clearing
  logger.py                     # File + console logging
watchlist/manager.py            # Business-rule layer over DB (limits, uniqueness)
watchlist/models.py             # Watchlist & Stock dataclasses
tests/                          # 22 test files, 481 tests
```

## Running Locally

```bash
cd /home/gongati/projects/market-lens
source venv/bin/activate
streamlit run app.py
```

## Running Tests

```bash
cd /home/gongati/projects/market-lens
source venv/bin/activate
python -m pytest tests/ -v
```

## Coding Conventions

- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes/TypedDicts. Private helpers prefixed with `_`. Constants are `_UPPER_SNAKE_CASE` (module-private).
- **GTF rule comments:** Each rule implementation is tagged with its M-number in a `# Rule:` or `# M<N>:` comment at the definition site. When in doubt, search for `M<N>` to find where a rule is implemented.
- **Dataclass immutability:** Zone enrichment uses `dataclasses.replace()` to produce new instances rather than mutating fields.
- **Stage separation:** Stage 1 (detection/scoring) fields are never modified by Stage 2 (trend/EMA20) or Stage 3 (Fibonacci). `odd_score` is sacrosanct.
- **Test patterns:** Hand-crafted OHLC DataFrames with inline arithmetic comments explaining body_pct, gap sizes, and threshold hits. Each test isolates one rule or one aspect. Both demand and supply sides are tested. Boundary values are explicitly covered (exactly-at-threshold, one-above, one-below).
- **Legacy compatibility:** Zone dicts carry both new fields (`proximal`, `distal`, `odd_score`) and legacy aliases (`top`, `bottom`, `mid`, `touches`) — see `demand_supply.py:_zone_dict()`.
- **HTML in Streamlit:** `st.markdown(unsafe_allow_html=True)` for custom card layouts. Symbols are HTML-escaped (`html.escape()`) and URL-encoded (`urllib.parse.urlencode()`) for special characters like `&` in M&M.

## Implemented GTF Rules (all passing tests)

| Rule | What It Does |
|------|-------------|
| M2   | Auto-exceptional distal when leg wick exceeds base wick |
| M3   | Zone test counting: wick-entry + close-exit cycles + persistent habitation (4 closes inside = dead) |
| M5   | Exciting candle: body >= 50% of range AND body >= 1.3% of price |
| M8   | Closing concept: legout closes beyond opposing zone? strong/weak/unchecked |
| M10  | Garbage-area rejection: achievement ratio < 0.5 rejects, 0.5-1.0 flags "Weak Departure" |
| M12  | Base width as % of proximal; flags "Wide Base" above 3% (suppressed for missing-base zones) |
| M13  | Proximal marking: WTW vs BTW via priority chain (P1 explosive, P2 doji, P3 ratio) |
| M17  | Missing-base zones: instant reversal, 0 base candles |
| M28  | Time-at-base scoring: 0-3 candles = 2pts, 4-5 = 1pt, 6+ = 0pts |
| M46  | Wick-based invalidation: any penetration past distal (wick or close) invalidates |

**Phase 1 is COMPLETE** (`v0.6.0`, 2026-08-02). Every rule above is implemented and tested.

## Chart Pattern Scanner

A second, independent scanner covering conventional chart patterns. Five families, each a module under `analysis/pattern_detectors/`:

| Family | Types |
|--------|-------|
| Triangle Patterns | Symmetrical, Ascending, Descending |
| VCP / Tight Base | VCP / Tight Base |
| Range Breakouts | Rectangle Range, Bullish Breakout, Bearish Breakdown |
| Flag / Pennant | Bull/Bear Flag, Bull/Bear Pennant |
| Double Top / Bottom | Double Bottom, Double Top |

Every label lives in `pattern_detectors/pattern_types.py` — the single source. Do not re-declare family or type strings in the UI or the orchestrator; they are compared by equality all over the filter chain, and a second copy drifts silently.

Design rules that must hold:

- **It is not part of the zone engine.** `PatternMatch` is its own dataclass, not a `Zone`. Zone output enters only as `zone_context` (nearest demand/supply proximity) and nothing in this pipeline may write back to a zone or to `odd_score`.
- **The forming candle is dropped**, the same as `demand_supply.py` does — `triangles.py` via `_drop_incomplete_latest_bar`, `vcp.py` and `range_breakouts.py` via `df.iloc[:-1]`. Breakout stage is decided against a closed bar, or it flips intraday and reverts by the close.
- **Stage is a three-way**: `Forming` → `Near Apex` → `Breakout Confirmed`. `Breakout Confirmed` covers breaks in BOTH directions; read `breakout_bias` for which way.
- **`_LOOKBACK` is a bar count, not a duration.** 120 bars is ~6 months daily but only ~5 sessions on 15m, so the pattern window shrinks in wall-clock time as the timeframe gets finer.

Results are cached in the `pattern_scans` table so a Pattern Detail deep link can restore them in a new tab (see Gotcha 13 — a new tab is a separate session, and pattern results live in session state).

Two facts a reader of the module list would get wrong:

- **`triangles.py` is the de-facto base module, not `shared.py`.** Frame prep (`_prepare_frame`, forming-bar drop) and swing detection (`_find_swings`) live in `triangles.py` and every other detector imports them from there; `shared.py` itself imports five private helpers *from* `triangles.py`. `shared.py` owns `PatternCandidate` and `candidate_to_match` (stage/bias/confidence/R:R assembly).
- **`apex_proximity` means two different things by family.** For triangles it is percent of TIME remaining to the apex (`Near Apex` at <= 15); for every other family it is percent PRICE distance to the trigger (`Near Apex` at <= 3.0). Both land in the same field and the same sort key, and the export labels the column "Trigger Distance %". Comparing values across families is meaningless.
- **Only the single highest-confidence match per symbol survives a scan** (`run_pattern_scan` keeps `max(candidates, key=confidence_score)`). A stock with both a triangle and a flag reports one pattern.

## Market Heatmap (2026-08-09..08-10, `736fca8` + `1eaf15c`)

A market/sector heatmap dashboard: 20 index/sector groups (`data/market_heatmap.py:GROUPS`) drawn as a clickable tile grid on its own page, with a compact widget on the Market Overview. Not in the sidebar nav — reached from the dashboard's "View Full Heatmap" button, the Quick Tools grid, or a `?heatmap_group=` URL.

- **Tiles are real URL navigations** (`<a target='_self'>` to `?heatmap_group=<id>&heatmap_view=Group Stocks`), which can start a FRESH Streamlit session. Three restore mechanisms make that survivable: `init_session_state()` seeds 11 keys from saved preferences; an empty `analysis_results` is restored from the single-row `latest_analysis_snapshot` SQLite table (written by the results page after each scan); and the heatmap query-param handler uses an idempotency tuple `_hm_qp_last = (group, view)` — not a one-shot flag — so tile-to-tile navigation re-fires but a plain rerun does not clobber in-page selections.
- **Quotes and scan overlays are cached separately.** Group tiles use `@st.cache_data(ttl=900)`, stock tiles `ttl=300`; the scan-setup counts are layered on AFTER the cache (`_overlay_setup_counts`), so a fresh scan updates setups without invalidating quotes.
- **Sector tiles can be a basket average, not the index.** When Yahoo's index ticker is missing/not-ok, the tile falls back to an unweighted mean of up to 60 constituents' `change_pct` (`source="basket"` is the only signal). NIFTY200/NIFTY500 tiles are backed by the F&O watchlist by design (`note="F&O coverage"`). Note `market_indices.py` (older) deliberately REFUSES to fetch sector indices for patchiness; `market_heatmap.py` (newer) fetches them and papers over gaps with the basket — two opposite decisions about the same data.

## Dropped Rules

- **M65/M66** — LOTL merge + achievement weighting. Dropped by directive after an implementation was reverted for corrupting zone markings. Merging widens a zone to the union of its members, which moves the proximal — the entry level being traded. Overlapping zones stay separate. See Gotcha 7.

## Reversed Directives

- **M69 — conventional chart patterns.** Previously marked Do Not Build. Reversed 2026-08-06 and shipped as the Chart Pattern Scanner above. It was excluded on the grounds that it would dilute the GTF zone methodology; it is built as a wholly separate pipeline for exactly that reason, so the zone engine is unchanged. **M57/M58 (candlestick pattern detectors) remain DNB** — that directive was not reversed.

## Next Pending Rules

Phase 2 (M1 — entry/stop-loss/target) is the next phase. Nothing in Phase 1 remains.

See `docs/requirements.md` for the cross-checked GTF roadmap (Phases 1-8) and `docs/REFINEMENT_PLAN.md` for the prioritized implementation plan.

After completing any task from `docs/requirements.md`, update both `docs/requirements.md` (mark item DONE with commit hash) and `docs/REFINEMENT_PLAN.md` (update current status). Commit doc changes separately from code changes.

## Gotchas & Non-Obvious Design Decisions

1. **Today's candle drop:** During market hours (before 4 PM IST), `demand_supply.py` drops the last candle from zone detection (`zone_data = data.iloc[:-1]`) because its OHLC values are still changing. The live price is still used for display. This can cause different zone counts/scores depending on when analysis runs.

2. **Extended legout run:** `_extend_run()` walks forward from legout_start while candles are exciting+same direction, up to 6 candles. The `test_scan_start_idx` is `legout_end + 1` (the LAST candle of the extended run + 1), NOT `legout_idx + 1`. Getting this wrong breaks test counting.

3. **M3 "perpetual zone":** If price enters a zone and never leaves (e.g., 16+ candles with High >= proximal), the test count is 0 — because M3 requires a complete enter+exit cycle. The zone stays "fresh" despite price living inside it. Note: entry is wick-based but exit is close-based — a candle must CLOSE outside the zone to complete a test. A candle that enters AND closes outside on the same bar counts as one complete test (same-bar enter+exit).

4. **M46 strict inequality:** Wick or close exactly AT the distal means the zone survives. Any penetration strictly BEYOND the distal (wick or close) invalidates. This is intentional — the zone boundary is the decision point, not the invalidation point.

5. **Legout trimming:** After extending the legout run, any candle that opens outside the zone and touches back in is treated as a test (not a legout continuation). This uses the WTW proximal for the check (widest zone boundary).

6. **Gap-as-legout:** A gap >= 1.3% between consecutive base candles terminates the base and counts as a legout departure. The gap candle can be boring — the gap itself is the institutional conviction signal.

7. **`_merge_overlapping_zones()` exists but is DELIBERATELY not called** from `filter_zones()`. The merge-intervals code is present in `filters.py`, and wiring it up is M65 — which was implemented once, corrupted the zone markings on real charts, and was reverted and then dropped by directive. This is not dead code awaiting completion: merging widens a zone to the union of its members, which moves the proximal, and the proximal is the entry level being traded. Overlapping zones stay separate, each reflecting its own base candles only. Do not "finish" it.

8. **Data source limitation:** Yahoo Finance and Jugaad Data (NSE) both work. The other 4 sources (NSE India, Zerodha, Upstox, TradingView) are scaffolded but require credentials or unavailable libraries.

9. **Yahoo silently drops trading days.** For some symbols Yahoo omits an entire session — e.g. Mon 2026-07-27 is missing for TORNTPHARM, RELIANCE and LUPIN but present for TCS, INFY, SBIN, ITC and ONGC. A gap is indistinguishable from a shorter dataframe, so nothing can detect it. Jugaad reads NSE directly and has the day. A missing candle shifts base counts and legout runs, so zone output can differ between sources for reasons that are not a bug in the engine.

10. **Prices are UNADJUSTED (`auto_adjust=False`).** Yahoo defaults to dividend-adjusted history, which shifts every pre-dividend candle down by the dividend and so misplaces zones against the current price. Both `yahoo_finance.py` and `manager._default_fetch_fn` pass `auto_adjust=False` so levels match TradingView, TraderTiger and Zerodha Kite. Keep the two in step — they are separate code paths.

11. **The detail chart fetches its own bars.** `render_stock_detail` calls `fetch_by_interval` for the selected candle interval; no OHLCV frame is passed in. A prefetch-and-prime path used to exist but its cache key could never match the interval cache's, and the two paths fetch different windows anyway (dashboard uses the trading type's timeframe, 1y for Options Trading; the chart uses the interval's, 5y for Daily). Do not "repair" that by aligning keys — it would show a different history on first render than on later ones.

12. **Chart caches are keyed by data source.** `detail_cache_{symbol}_{interval}_{use_fib}_{source}`. Without the source component, switching Yahoo -> Jugaad hits the cache and replays the old source's bars, and the source-aware fetch never runs.

13. **A new browser tab is a separate Streamlit session.** The "View" deep link (`?stock=...`) carries the analysis context — `src`, `tt`, `ps`, `enh`, `cf` — because the new tab cannot see the opening tab's session state. `app.main()` validates each against its allowed list before applying, and must do so BEFORE `render_sidebar()`. Anything that changes what the chart draws belongs here: `cf` (zone confirmation) was missed at first, and every chart opened from the dashboard silently rendered with the mode off, which looked like the drawing code was broken. `cf` also seeds `sidebar_screener_confirmation`, or the new tab's checkbox renders unticked while the mode is active.

14. **Preferences ARE restored at launch (reversed in `1eaf15c`, 2026-08-10).** This gotcha used to say the opposite. `init_session_state()` now seeds eleven session keys from `user_preferences.json` — watchlist selection (`selected_watchlist_id`, `watchlist_source`, `selected_predefined_watchlist`, `selected_nse_batch`), the two-axis triple (`trading_type`, `primary_strategy`, `enhancers`), `selected_data_source`, `alerts_on`, plus `show_candle_tooltip` and `last_analysis_timestamp`. The change was forced by the heatmap: tile clicks are real URL navigations that can start a fresh session, and the watchlist selection has to survive them. The Settings page still labels the block "Last Used Selections" — that label is now stale.

15. **A bar with a missing OHLC value is silently corrupting, not merely undrawable.** Every comparison against NaN is False, so `classify_candle` reports a NaN-close bar as a BORING DOJI — a plausible base candle that can extend a base, shift `base_end_idx` or break a leg-out run, with nothing raising anywhere. `drop_incomplete_bars` in `sources/base.py` removes such rows in both working sources. Volume is deliberately NOT checked: a zero-volume session is a real if illiquid bar.

16. **Yahoo leaves the daily close unset for the WHOLE market, not one symbol.** On 2026-07-31 every stock checked came back with open, high, low and volume but `Close=NaN`. Treating this as a per-symbol quirk led to a guard that stripped the latest bar from every chart at once. When the last bar looks broken, check a second symbol before concluding it is specific to one.

17. **The missing close is repaired from Yahoo INTRADAY first, NSE bhavcopy second.** Authority is not the only axis: the bhavcopy costs ~5s and NSE rate-limits, while intraday costs ~200ms, comes from the same provider as the bar being repaired, and was verified to return NSE's exact close. Bhavcopy-first made every chart open stall for seconds and sometimes paid that cost only to fail. Bhavcopy remains the backup because Yahoo keeps only ~60 days of hourly data, so an older session genuinely needs NSE. Repair runs BEFORE the drop, and only on the last bar of daily data — a gap mid-history is a different problem.

18. **`nse_bhavcopy` caches to `~/.market-lens/bhavcopy` and retries failures.** A success is cached permanently (the file cannot change once published); a failure backs off for 120s rather than being cached forever, because caching one transient NSE response left no symbol repairable for the whole process. NSE also changed the schema — `TckrSymb`/`OpnPric`/`ClsPric` now, `SYMBOL`/`OPEN`/`CLOSE` in older archives — and both are parsed, since wrong column names fail SILENTLY. Only the `EQ` series is kept; BE and BZ rows share the same ticker.

19. **Tests that touch the bhavcopy must redirect `_DISK_CACHE`.** `tests/test_incomplete_bars.py` has an autouse fixture pointing every cache at `tmp_path`. Without it a test fixture is written into the REAL cache under `~/.market-lens`, where the app reads it back as a genuine session — observed serving a fabricated RELIANCE close of 1510.00 against an actual 1307.80.

20. **Streamlit renders `react-aria-ComboBox`, not BaseWeb.** Sidebar CSS in `app.py` hooked on `[data-baseweb="select"]` matched NOTHING — there are zero BaseWeb elements in this build's DOM — so the rule sat there looking correct while doing nothing. Selectbox styling must target `.react-aria-ComboBox`. Related: the select control already HAS a 1px border, coloured white; on the white section cards that reads as no border at all, so only the colour needs changing, never the box. Verify UI selectors against the running app (`javascript_tool` + `getComputedStyle`) rather than by inspection — a wrong selector fails silently and looks identical to a wrong value.

21. **Popover content renders in a portal OUTSIDE the sidebar.** `st.popover` bodies land under `[data-testid="stPopoverBody"]` at body level, so `section[data-testid="stSidebar"] ...` rules do not reach the screener's dropdowns even though they appear inside the sidebar visually.

22. **`dashboard.py` is not a page.** It holds the scan (`run_scan`, `scan_context`) and the helpers the pages share — the screener predicate, exports, the per-stock detail view, single-stock analysis for deep links. `app.main()` routes thirteen states on `st.session_state.active_page`: `dashboard` → `market_overview.render_market_overview` (the `else` fallthrough — any unknown value lands here), plus `analysis_results`, `stock_detail`, `market_heatmap`, `alerts`, `reports`, `trade_journal`, `watchlist_manager`, `settings`, `research`, `pattern_scanner`, `pattern_results`, `pattern_detail`. `_render_filter_sort_bar` and `_render_results_grid` are still in `dashboard.py` but UNREACHABLE — they lost their caller in the page split and are kept pending review, not because anything calls them. That orphaning also strands `render_stock_card` in `stock_card.py` (only `build_detail_url` is still live from that module).

23. **Every helper in `panels.py` must emit newline-free HTML.** A multi-line f-string produced a whitespace-only line whenever an optional slot (the icon) was empty, and a blank line TERMINATES a markdown HTML block — everything indented after it was then parsed as an indented code block, so cards without an icon rendered their own source as visible text. Cards with icons rendered fine, which is why it survived review.

24. **Never make Streamlit's toolbar `position: fixed` without pinning its size.** Streamlit styles `[data-testid="stToolbar"]` at `width/height: 100%`; going fixed resolves those against the VIEWPORT, turning it into a full-screen invisible sheet at `z-index: 1000` that swallows every click and scroll. Geometry is pinned to `auto` and `pointer-events` is `none` on the container with `auto` only on its children. After any change adding `position: fixed` or `z-index`, probe `document.elementFromPoint` at several page coordinates — a visibility check is not a usability check.

25. **`st.tabs` cannot preselect a tab** — whatever is listed first is what opens. The chart page lists Chart first for that reason, even though the design puts Overview first.

26. **`get_all_alerts()` / `get_unread_alerts()` return DICTS, not objects.** `getattr(row, "message", None) or str(row)` therefore always misses and falls through to printing the entire row — id, stock_id, is_read and all — as the alert text. Invisible until an alert exists.

27. **Result-calendar fetches must pass `cache_only=True` on render.** `earnings_calendar.get_earnings` fetches anything uncached, which on page open is 39s for Nifty 50 and ~164s for the 208-stock F&O universe. Fetching is an explicit user action (the Refresh button), never a side effect of navigation. As with the bhavcopy cache, `_DISK_CACHE` is module-level so tests can redirect it — a fixture written into the real cache is served back as a genuine result date.

28. **Worktree branch warning:** This repo uses worktrees. Always commit to named feature branches (e.g., `feature/demand-supply-refinement`), never to `claude/wizardly-*` worktree branches. Never stage the `.claude/` directory. Git commands must use WSL bash: `wsl -d Ubuntu -- bash -lc "cd /home/gongati/projects/market-lens && ..."`. Commit messages containing apostrophes break BOTH layers: the heredoc alone is not enough, because `wsl -- bash -c '...'` is itself single-quoted and an apostrophe inside terminates it, silently truncating the message at that word. Write the message to a file and use `git commit -F <file>`.

29. **Streamlit DISCARDS the state of any widget not instantiated during a run.** A button that calls `st.rerun()` ends the script; every widget below it is never created, so its state is garbage-collected and the next run starts it from its default. On the Reports page the refresh buttons render above the `Only F&O` toggle, so pressing Refresh silently reset the universe from 208 stocks back to the default watchlist — it looked like the refresh was rewriting the selection. Two defences, both in use: instantiate the toggles BEFORE the buttons even when they appear below them (create the column rows up front and write into them out of order), and do not call `st.rerun()` at all when the page re-reads its data further down the same run. `_seed_result_filters` in `pattern_results.py` shows the third option — `setdefault` every key on every run, which self-heals.

30. **`st.empty()` inherits the width of whatever container is open when it is CREATED**, not where its content ends up. `_refresh()` was called from inside `with c1:`, one third of the header's right-hand column, so the standalone progress card — built for `min(720px, 100%)` — laid out in a 117px strip and wrapped every word to one character per line. Buttons inside columns must only RECORD a request; the page body performs it. Measured after the move: 117px → 1130px container, card at its intended 720px.

31. **A sort key that groups before it orders makes correct data look missing.** The alerts feed keyed "Latest first" on `(origin == "Sent", time)`, so every live scan match outranked every delivered alert whatever the timestamps said. New Telegram alerts were on disk, parsed and deduped correctly, and still could not reach the top of a list named "Latest first" — the Refresh button got blamed twice. Sort on the quantity the label promises; rows missing that quantity go last, not first and not as zero.

32. **Monitor status is probed with flock, never the PID in the lock file.** The PID is still there, and still parses, long after the process it names has died — a stale lock file would report a crashed monitor as running forever. `alerts/monitor_control.py` tries to TAKE the lock: if that succeeds nobody held it, so nothing is running, and it releases immediately (holding it even briefly would make the Start button beside it believe a duplicate exists). The PID is read only after the lock is shown to be held, and only to signal. Related: the cron entry is `45 3 * * 1-5` in machine local time, and cron does not run jobs missed while the machine was down — if the laptop boots after that slot, the monitor simply never starts that day.

33. **The Research Engine harness patches the zone engine — behind an explicit switch, quarantined to harness runners.** `detect_zones` deliberately discards zones whose FUTURE price action invalidates them (M46 forward scan). Correct live; fatal in a backtest (only zones whose stops were never hit survive — an early research run showed literally zero losing zone trades). `research_engine/harness/detectors.py` therefore patches `score_zone` via `enable_backtest_mode()`, which ONLY harness CLI runners call; the harness zone detectors raise if it hasn't been called, and `disable_backtest_mode()` restores the engine. Nothing in `ui/`, `analysis/` or app paths may call it — `tests/test_research_engine.py` enforces this (importing the Research page must leave `score_zone` untouched).

34. **The Research Engine is architecturally separate, including its storage.** Its SQLite lives at `~/.market-lens/research_engine.db` (never in `market_lens.db`); `ui/pages/research_page.py` reads ONLY `research_engine.store`/`importer` (file/DB readers) and must never import `research_engine.harness`. The sidebar item "Research ↗" is an `<a target="_blank">` to `?research=1` (native buttons can't open tabs) — a new tab is a separate Streamlit session, which is the point: the dashboard keeps working in the original tab. Trade Candidates there are HISTORICAL backtest decisions with outcomes, labelled research classifications (TAKE candidate / WAIT / WATCH / REDUCE SIZE / AVOID / NO TRADE), never buy/sell recommendations, and are not surfaced on the main dashboard until out-of-sample validation passes.

35. **"F&O universe" and "F&O Stocks" are different things on the Reports page.** The first is the `Only F&O` toggle; the second is a predefined watchlist that happens to hold the same 208 symbols. With the toggle off the page falls back to the sidebar watchlist and labels it with that watchlist's own name, so a user whose watchlist is already `F&O Stocks` sees 208 either way and the toggle looks broken. It is not — check which label is showing before debugging. Related: the earnings cache is valid for one CALENDAR DAY, so the first visit each morning legitimately shows an empty table and a prompt to refresh.

## Critical Instruction

**Always check GTF methodology rule definitions (M-numbers) before modifying demand/supply marking logic.** The rules interact in subtle ways (e.g., M2 affects distal, M13 affects proximal independently; M3 test entry is wick-based but exit is close-based, while M46 invalidates on ANY penetration past the distal, wick or close; M8 is a flag that does NOT change ODD score). Read the relevant test cases in `tests/test_zone_engine.py` before changing any detection or scoring code — each rule has dedicated tests that document the exact expected behavior with hand-crafted OHLC data and inline arithmetic.
