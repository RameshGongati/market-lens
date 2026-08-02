# Market Lens — Claude Code Context

Market Lens is a Streamlit application for Indian equity market analysis (2,374 NSE-listed stocks). It implements the institutional GTF (Trading in the Zone) demand/supply zone methodology: detecting legin-base-legout candlestick patterns (DBR, RBR, RBD, DBD), scoring them with a 7-point ODD trade score, and presenting the top tradeable zones on interactive Plotly charts. A secondary Trend Following strategy (SMA 50/200 crossover) is also available via a two-axis configuration model (Trading Type × Primary Strategy).

## Tech Stack

- **Runtime:** Python 3.12, Streamlit
- **Data:** yfinance (Yahoo Finance) and jugaad-data (NSE direct) both fully working; 4 other sources scaffolded but not functional
- **Charts:** Plotly (candlestick + volume subplots with zone/SMA/Fibonacci overlays)
- **Storage:** SQLite (`~/.market-lens/market_lens.db`) for watchlists, analysis results, alerts, notes; JSON (`~/.market-lens/user_preferences.json`) for preferences
- **Alerts:** Telegram Bot API, config in `config/alert_config.json` (gitignored — holds the bot token)
- **Export:** openpyxl (Excel), reportlab (PDF)
- **Tests:** pytest (408 tests across 13 files)

## Repo Structure

```
app.py                          # Streamlit entry point, session state, page routing
analysis/
  base.py                       # BaseAnalysis ABC, Status/Strength types
  demand_supply.py              # Orchestrator: detection → scoring → filtering → enrichment
  trend_following.py            # SMA 50/200 golden/death cross strategy
  zone_engine/                  # Core GTF engine (all zone detection lives here)
    candles.py                  #   Candle classification (boring/exciting/strong)
    patterns.py                 #   Legin-base-legout pattern detection + boundary marking
    scoring.py                  #   ODD trade score + M3 test counting + M8 closing quality
    models.py                   #   Zone dataclass (~40 fields, 3-stage layering)
    filters.py                  #   Display filtering (freshness/score/nearest-N)
    trend.py                    #   50 SMA clock method (Stage 2)
    enhancers.py                #   EMA 20 confluence (Stage 2)
    fibonacci.py                #   Fibonacci retracement confluence (Stage 3, opt-in)
alerts/
  telegram.py                   # Telegram delivery + HTML message formatting
  zone_alert_checker.py         # Scans cached results for zone-proximity matches
alert_monitor.py                # Standalone background monitor (runs outside Streamlit)
config/
  trading_config.py             # Two-axis model: trading types, strategies, enhancers, timeframes
  preferences.py                # JSON persistence with legacy migration
  alert_settings.py             # Load/save for the Telegram alert config
  settings.py                   # App constants, data sources, limits
data/
  manager.py                    # DataSourceManager, timeframe-aware fetching, intraday fallback
  nse_bhavcopy.py               # NSE end-of-day file; backup close for an unfinished bar
  sources/base.py               # DataSource ABC + drop_incomplete_bars() guard
  sources/yahoo_finance.py      # Working source; unadjusted prices (auto_adjust=False)
  sources/jugaad.py             # Working source; NSE direct, UTC->IST dates, 30s timeout
  stock_list.json               # 2,374 NSE stocks
  predefined_watchlists.json    # NIFTY50, BANKNIFTY, F&O index watchlists
ui/
  components/stock_detail.py    # Full detail view with Plotly charts + overlays
  components/stock_card.py      # Dashboard grid cards with deep-link
  components/sidebar.py         # Two-axis control panel, market status, watchlist picker
  pages/dashboard.py            # Analysis loop, results grid, screener
storage/database.py             # SQLite CRUD (5 tables)
utils/
  helpers.py                    # Currency formatting, stock list loading, company names
  export.py                     # Excel + PDF export
  market_hours.py               # NSE market hours, holidays, countdown
watchlist/manager.py            # Business-rule layer over DB (limits, uniqueness)
tests/                          # 13 test files, 408 tests
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

## Dropped Rules

- **M65/M66** — LOTL merge + achievement weighting. Dropped by directive after an implementation was reverted for corrupting zone markings. Merging widens a zone to the union of its members, which moves the proximal — the entry level being traded. Overlapping zones stay separate. See Gotcha 7.

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

14. **Preferences are recorded, not restored.** Sidebar changes are written to `user_preferences.json`, but `init_session_state()` deliberately starts from fixed defaults every launch, so they are not reapplied. Only `show_candle_tooltip` and `last_analysis_timestamp` are read back. The Settings page labels this block "Last Used Selections" for that reason.

15. **A bar with a missing OHLC value is silently corrupting, not merely undrawable.** Every comparison against NaN is False, so `classify_candle` reports a NaN-close bar as a BORING DOJI — a plausible base candle that can extend a base, shift `base_end_idx` or break a leg-out run, with nothing raising anywhere. `drop_incomplete_bars` in `sources/base.py` removes such rows in both working sources. Volume is deliberately NOT checked: a zero-volume session is a real if illiquid bar.

16. **Yahoo leaves the daily close unset for the WHOLE market, not one symbol.** On 2026-07-31 every stock checked came back with open, high, low and volume but `Close=NaN`. Treating this as a per-symbol quirk led to a guard that stripped the latest bar from every chart at once. When the last bar looks broken, check a second symbol before concluding it is specific to one.

17. **The missing close is repaired from Yahoo INTRADAY first, NSE bhavcopy second.** Authority is not the only axis: the bhavcopy costs ~5s and NSE rate-limits, while intraday costs ~200ms, comes from the same provider as the bar being repaired, and was verified to return NSE's exact close. Bhavcopy-first made every chart open stall for seconds and sometimes paid that cost only to fail. Bhavcopy remains the backup because Yahoo keeps only ~60 days of hourly data, so an older session genuinely needs NSE. Repair runs BEFORE the drop, and only on the last bar of daily data — a gap mid-history is a different problem.

18. **`nse_bhavcopy` caches to `~/.market-lens/bhavcopy` and retries failures.** A success is cached permanently (the file cannot change once published); a failure backs off for 120s rather than being cached forever, because caching one transient NSE response left no symbol repairable for the whole process. NSE also changed the schema — `TckrSymb`/`OpnPric`/`ClsPric` now, `SYMBOL`/`OPEN`/`CLOSE` in older archives — and both are parsed, since wrong column names fail SILENTLY. Only the `EQ` series is kept; BE and BZ rows share the same ticker.

19. **Tests that touch the bhavcopy must redirect `_DISK_CACHE`.** `tests/test_incomplete_bars.py` has an autouse fixture pointing every cache at `tmp_path`. Without it a test fixture is written into the REAL cache under `~/.market-lens`, where the app reads it back as a genuine session — observed serving a fabricated RELIANCE close of 1510.00 against an actual 1307.80.

20. **Streamlit renders `react-aria-ComboBox`, not BaseWeb.** Sidebar CSS in `app.py` hooked on `[data-baseweb="select"]` matched NOTHING — there are zero BaseWeb elements in this build's DOM — so the rule sat there looking correct while doing nothing. Selectbox styling must target `.react-aria-ComboBox`. Related: the select control already HAS a 1px border, coloured white; on the white section cards that reads as no border at all, so only the colour needs changing, never the box. Verify UI selectors against the running app (`javascript_tool` + `getComputedStyle`) rather than by inspection — a wrong selector fails silently and looks identical to a wrong value.

21. **Popover content renders in a portal OUTSIDE the sidebar.** `st.popover` bodies land under `[data-testid="stPopoverBody"]` at body level, so `section[data-testid="stSidebar"] ...` rules do not reach the screener's dropdowns even though they appear inside the sidebar visually.

22. **Worktree branch warning:** This repo uses worktrees. Always commit to named feature branches (e.g., `feature/demand-supply-refinement`), never to `claude/wizardly-*` worktree branches. Never stage the `.claude/` directory. Git commands must use WSL bash: `wsl -d Ubuntu -- bash -lc "cd /home/gongati/projects/market-lens && ..."`. Commit messages containing apostrophes need a heredoc (`git commit -F - <<'EOF'`) or the shell mis-parses them as paths.

## Critical Instruction

**Always check GTF methodology rule definitions (M-numbers) before modifying demand/supply marking logic.** The rules interact in subtle ways (e.g., M2 affects distal, M13 affects proximal independently; M3 counts tests via wicks but M46 invalidates via closes; M8 is a flag that does NOT change ODD score). Read the relevant test cases in `tests/test_zone_engine.py` before changing any detection or scoring code — each rule has dedicated tests that document the exact expected behavior with hand-crafted OHLC data and inline arithmetic.
