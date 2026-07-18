# Market Lens — Claude Code Context

Market Lens is a Streamlit application for Indian equity market analysis (2,374 NSE-listed stocks). It implements the institutional GTF (Trading in the Zone) demand/supply zone methodology: detecting legin-base-legout candlestick patterns (DBR, RBR, RBD, DBD), scoring them with a 7-point ODD trade score, and presenting the top tradeable zones on interactive Plotly charts. A secondary Trend Following strategy (SMA 50/200 crossover) is also available via a two-axis configuration model (Trading Type × Primary Strategy).

## Tech Stack

- **Runtime:** Python 3.12, Streamlit
- **Data:** yfinance (Yahoo Finance); 4 other data sources scaffolded but not functional
- **Charts:** Plotly (candlestick + volume subplots with zone/SMA/Fibonacci overlays)
- **Storage:** SQLite (`~/.market-lens/market_lens.db`) for watchlists, analysis results, alerts, notes; JSON (`~/.market-lens/user_preferences.json`) for preferences
- **Export:** openpyxl (Excel), reportlab (PDF)
- **Tests:** pytest (340 tests across 11 files)

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
config/
  trading_config.py             # Two-axis model: trading types, strategies, enhancers, timeframes
  preferences.py                # JSON persistence with legacy migration
  settings.py                   # App constants, data sources, limits
data/
  manager.py                    # DataSourceManager, timeframe-aware fetching, intraday fallback
  sources/yahoo_finance.py      # Only fully functional data source
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
tests/                          # 11 test files, 340 tests
```

## Running Locally

```bash
cd /home/gongati/market-lens
source venv/bin/activate
streamlit run app.py
```

## Running Tests

```bash
cd /home/gongati/market-lens
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
| M3   | Zone test counting: complete enter+exit wick cycles |
| M5   | Exciting candle: body >= 50% of range AND body >= 1.3% of price |
| M8   | Closing concept: legout closes beyond opposing zone? strong/weak/unchecked |
| M13  | Proximal marking: WTW vs BTW via priority chain (P1 explosive, P2 doji, P3 ratio) |
| M17  | Missing-base zones: instant reversal, 0 base candles |
| M28  | Time-at-base scoring: 0-3 candles = 2pts, 4-5 = 1pt, 6+ = 0pts |
| M46  | Close-based invalidation: only CLOSE beyond distal invalidates (wick = survives) |

## Next Pending Rules

- **M10** — Garbage-area rejection (legout barely clears base)
- **M12** — Narrow base width as quality metric
- **M65/M66** — LOTL merge + achievement weighting

See `docs/requirements.md` for the cross-checked GTF roadmap (Phases 1-8) and `docs/REFINEMENT_PLAN.md` for the prioritized implementation plan.

## Gotchas & Non-Obvious Design Decisions

1. **Today's candle drop:** During market hours (before 4 PM IST), `demand_supply.py` drops the last candle from zone detection (`zone_data = data.iloc[:-1]`) because its OHLC values are still changing. The live price is still used for display. This can cause different zone counts/scores depending on when analysis runs.

2. **Extended legout run:** `_extend_run()` walks forward from legout_start while candles are exciting+same direction, up to 6 candles. The `test_scan_start_idx` is `legout_end + 1` (the LAST candle of the extended run + 1), NOT `legout_idx + 1`. Getting this wrong breaks test counting.

3. **M3 "perpetual zone":** If price enters a zone and never leaves (e.g., 16+ candles with High >= proximal), the test count is 0 — because M3 requires a complete enter+exit cycle. The zone stays "fresh" despite price living inside it.

4. **M46 strict inequality:** Close exactly AT the distal means the zone survives. Only close strictly BEYOND the distal invalidates. This is intentional — the zone boundary is the decision point, not the invalidation point.

5. **Legout trimming:** After extending the legout run, any candle that opens outside the zone and touches back in is treated as a test (not a legout continuation). This uses the WTW proximal for the check (widest zone boundary).

6. **Gap-as-legout:** A gap >= 1.3% between consecutive base candles terminates the base and counts as a legout departure. The gap candle can be boring — the gap itself is the institutional conviction signal.

7. **`_merge_overlapping_zones()` exists but is not called** from `filter_zones()`. The merge-intervals code is present in `filters.py` but the current pipeline keeps overlapping zones separate.

8. **Data source limitation:** Only Yahoo Finance works. The other 4 sources (NSE India, Zerodha, Upstox, TradingView) are scaffolded but require credentials or unavailable libraries.

9. **Worktree branch warning:** This repo uses worktrees. Always commit to named feature branches (e.g., `feature/demand-supply-refinement`), never to `claude/wizardly-*` worktree branches. Never stage the `.claude/` directory. Git commands must use WSL bash: `wsl -d Ubuntu -- bash -lc "cd /home/gongati/market-lens && ..."`.

## Critical Instruction

**Always check GTF methodology rule definitions (M-numbers) before modifying demand/supply marking logic.** The rules interact in subtle ways (e.g., M2 affects distal, M13 affects proximal independently; M3 counts tests via wicks but M46 invalidates via closes; M8 is a flag that does NOT change ODD score). Read the relevant test cases in `tests/test_zone_engine.py` before changing any detection or scoring code — each rule has dedicated tests that document the exact expected behavior with hand-crafted OHLC data and inline arithmetic.
