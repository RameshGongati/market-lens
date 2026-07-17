# Market Lens — Architecture

## Module Breakdown

### Core Analysis Engine (`analysis/zone_engine/`)

The zone engine is the heart of the application — 8 files implementing the GTF demand/supply zone methodology in a three-stage pipeline.

#### `candles.py` — Candle Classification

Classifies each OHLC candle as BORING or EXCITING (the fundamental building block for pattern detection).

- `classify_candle(open, high, low, close) → CandleInfo`
- **Boring:** body < 50% of range, OR body < 1.3% of price (institutional noise filter)
- **Exciting:** body >= 50% of range AND body >= 1.3% of price
- **Strong:** body >= 80% of range (sub-category of exciting)
- **Direction:** bullish (close > open), bearish (close < open), doji (close == open, always boring)

Constants: `_EXCITING_THRESHOLD=0.50`, `_STRONG_THRESHOLD=0.80`, `_MIN_BODY_PCT_OF_PRICE=0.013`

#### `patterns.py` — Pattern Detection & Boundary Marking

The core scanner. Walks the DataFrame looking for legin → base → legout structures.

**Pattern identification:**
```
legin \ legout |  bullish     |  bearish
---------------+--------------+-----------
bearish        | DBR (demand) | DBD (supply)
bullish        | RBR (demand) | RBD (supply)
```

**Scanner algorithm:**
1. Find an exciting candle (the legin anchor)
2. Look for 1-10 consecutive boring candles (the base)
3. Look for an exciting candle that clears the base range (the legout)
4. Extend both legs forward/backward (up to 6 same-direction exciting candles)
5. Apply boundary marking (Normal, Exceptional, WTW, BTW, Missing-Base)
6. Score the zone via `scoring.score_zone()`
7. Skip invalidated zones; append valid ones

**Boundary marking functions:**
- `_normal_marking()` — proximal = body edges, distal = wick extremes (M2 baseline)
- `_exceptional_distal()` — extends distal to leg wick when it exceeds base wick (M2)
- `_m13_proximal_marking()` — 3-priority chain for WTW vs BTW proximal (M13)
- `_missing_base_marking()` — instant-reversal zones with 0 base candles (M17)

**Special cases:**
- Gap-as-legout: gaps >= 1.3% of price terminate the base and act as legout
- Legout trimming: candles opening outside the zone and touching back are tests, not legout
- Missing-base (M17): two consecutive exciting candles in opposite directions

Constants: `_MAX_SCAN_BASE_CANDLES=10`, `_MAX_LEG_RUN=6`, `_MIN_GAP_LEGOUT_PCT=0.013`

#### `scoring.py` — ODD Trade Score, Test Counting & Closing Quality

**ODD Score (7-point maximum):**
| Component | Values | Rule |
|-----------|--------|------|
| Freshness | 3 / 1.5 / 0 | 0 tests = 3, 1 test = 1.5, 2+ tests = 0 |
| Strength  | 2 / 1 | Gap or 2+ exciting legout candles = 2, else 1 |
| Time-at-base | 2 / 1 / 0 | 0-3 candles = 2, 4-5 = 1, 6+ = 0 |

**Entry recommendations:** Score >= 7 = "Entry Type 1 (aggressive)", >= 5 = "Entry Type 2/3 (confirmation)", < 5 = "No Trade"

**Zone strength labels:** Based on strong candles (body >= 80%) in legout:
- 0 strong = "Normal", 1 strong = "Strong", 2+ strong = "Very Strong"

**Test counting (`count_zone_tests` — M3 + M46):**
- Walks forward from `test_scan_start_idx` (= legout_end + 1)
- Entry = wick touches/crosses proximal; Exit = wick leaves the zone
- Only complete enter+exit cycles count as a test
- `activation_touch` = True if price has entered the zone at least once (even without exiting)
- Invalidation: only when CLOSE is strictly beyond the distal (M46). Wick through distal = zone survives.

**Closing quality (`assess_closing_quality` — M8):**
- For each zone, finds the nearest opposing zone that the legout passed through
- "strong" = legout CLOSED beyond opposing zone's proximal
- "weak" = legout wicked past but closed before opposing proximal
- "unchecked" = no opposing zone found in the legout's path
- Flag only — does NOT affect ODD score

**Confluence rating (Stage 3):**
- Separate from ODD: EMA20 (+1) + Fib levels (+1 each, capped 2) + golden ratio bonus (+1)
- Labels: None (0), Moderate (1-2), High (3+)

#### `models.py` — Zone Data Model

Single `Zone` dataclass with ~40 fields in three groups:
- **Stage 1:** zone_type, category, proximal, distal, exceptional variants, base indices, ODD score breakdown, marking labels
- **Stage 2:** trend_at_zone, ema20_enhancer, is_tradeable, trade_warning
- **Stage 3:** fib_confluence, fib_levels_in_zone, fib_strongest, confluence_score, confluence_label

#### `filters.py` — Display Filtering

Reduces raw zones (often 20+) to the meaningful subset for chart display:
1. **Freshness filter:** Drop zones tested 2+ times (`_MAX_TIMES_TESTED=1`)
2. **Score filter:** Drop zones scoring below 5.0 (`_MIN_DISPLAY_SCORE=5.0`)
3. **Nearest-N:** Keep at most 3 demand + 3 supply zones nearest to current price (`_MAX_ZONES_PER_SIDE=3`)

Note: `_merge_overlapping_zones()` exists in the code but is not called from `filter_zones()`.

#### `trend.py` — 50 SMA Clock Method (Stage 2)

Determines overall market direction by measuring the 50-period SMA's slope as a clock-hand angle:
- UP: angle in (0°, +60°] and SMA rising
- DOWN: angle in [-60°, 0°) and SMA falling
- SIDEWAYS: slope within ±0.3% flat threshold, or angle outside clock arcs

Constants: `_DEFAULT_SMA_PERIOD=50`, `_DEFAULT_LOOKBACK=7`, `_FLAT_SLOPE_THRESHOLD_PCT=0.3`

#### `enhancers.py` — EMA 20 Confluence (Stage 2)

Checks whether the 20-period EMA sits inside or within 2% of a zone's boundaries. When true, the zone is flagged as "high probability" (EMA20 confluence). Purely additive — never changes ODD score.

#### `fibonacci.py` — Fibonacci Retracement Confluence (Stage 3, Opt-In)

Three-step pipeline:
1. `find_recent_swing()` — highest High + lowest Low within 120 candles; direction from chronological order
2. `calculate_fib_levels()` — computes 0.382/0.5/0.618/0.786 retracement prices
3. `fib_confluence()` — checks if any Fib level falls inside or within 1% of a zone boundary

Strongest level priority: 0.618 > 0.786 > 0.5 > 0.382

### Orchestrator (`analysis/demand_supply.py`)

`DemandSupplyAnalysis(BaseAnalysis)` wires the engine into the app:

```
analyse(symbol, data, use_fibonacci=False)
  │
  ├─ Drop today's candle if market still open (before 4 PM IST)
  ├─ Stage 1: detect_zones(zone_data) → list[Zone]
  ├─ M8: assess_closing_quality(zones, zone_data)
  ├─ filter_zones(zones, current_price) → display zones
  ├─ Stage 2: detect_trend(data) + ema20_confluence per zone
  ├─ Stage 2: _apply_trend_alignment per zone
  ├─ Stage 3 (opt-in): find_recent_swing + fib_confluence per zone
  ├─ Build result dict with new + legacy keys
  └─ Determine status (bullish/bearish/neutral) and summary
```

**Legacy compatibility:** Every zone dict carries both new fields (`proximal`, `distal`, `odd_score`) and aliases (`top`, `bottom`, `mid`, `touches`, `bar_index`) so the UI doesn't need rewriting.

**Trend alignment safety:** Demand zones are tradeable only in UP trend; supply zones only in DOWN; SIDEWAYS = avoid all.

### Alternative Strategy (`analysis/trend_following.py`)

`TrendFollowingAnalysis(BaseAnalysis)` — SMA 50/200 crossover strategy:
- BUY: SMA50 > SMA200 AND trend UP
- SELL: SMA50 < SMA200 AND trend DOWN
- HOLD: everything else
- Surfaces golden/death cross events with candles-ago context

### UI Layer

#### `ui/components/stock_detail.py`
Full detail view: interactive Plotly chart (candlestick/line/TradingView types), zone rectangle overlays with proximal/distal lines and right-edge score/strength labels, SMA/EMA reference lines, Fibonacci retracement lines (opt-in), volume subplot, key metrics row, analysis history timeline, personal notes.

#### `ui/components/stock_card.py`
Dashboard grid card: status badge, strength badge, price/change, "In Zone" pulsing indicator, "View SYMBOL →" deep link (`target="_blank"` with `urlencode()`).

#### `ui/components/sidebar.py`
Two-axis control panel: Trading Type selector → Primary Strategy selector → Enhancer checkboxes (cascading resets via `on_change` callbacks). Market status clock, data source picker, watchlist picker (My Watchlists / Index Watchlists / All NSE Stocks), screener filters.

#### `ui/pages/dashboard.py`
Analysis orchestrator: iterates over stocks, fetches data via `DataSourceManager`, runs the configured analyzer, caches results in session state, saves to SQLite, renders 3-column card grid with filter/sort/export controls. Handles deep-link routing for new-tab stock detail.

### Data Layer (`data/`)

#### `data/manager.py`
- `DataSourceManager` — switches between data sources, delegates fetch calls
- `fetch_for_trading_type()` — maps Trading Type to `{period, interval}` via `trading_config`, fetches, falls back from intraday to daily if < 20 rows
- `fetch_by_interval()` — maps UI labels ("Daily", "Weekly", "75m", "15m") to fetch params; handles 75m resampling (5×15m aggregation)

#### `data/sources/yahoo_finance.py`
Only fully functional source. Uses `yfinance` for quotes and OHLCV history. Zero-volume rows filtered for non-weekly/monthly intervals.

### Config (`config/`)

#### `config/trading_config.py`
Central vocabulary for the two-axis model:
- `TRADING_TYPES`: Options Trading, Intraday Trading, Short-term Trading, Long-term Investment
- `TRADING_TYPE_TIMEFRAME`: maps each type to `{period, interval}` (e.g., Intraday → `{60d, 15m}`)
- `PRIMARY_STRATEGIES`, `ENHANCERS`, `TRADING_TYPE_DEFAULTS`

### Storage (`storage/database.py`)

SQLite at `~/.market-lens/market_lens.db` with 5 tables:
- `watchlists` / `stocks` — user watchlist management
- `analysis_results` — append-only with 20-per-stock pruning
- `alerts` — triggered alerts with read/unread state
- `stock_notes` — per-stock personal notes

---

## Data Flow — End to End

### 1. User Clicks "Run Analysis"

`sidebar.py` sets `st.session_state.analysing = True` with the selected watchlist and two-axis config.

### 2. Stock List Resolution

- My Watchlists → `watchlist.manager.get_stocks()` → SQLite
- Index Watchlists → `utils.helpers.load_predefined_watchlists()` → JSON
- All NSE Stocks → `utils.helpers.get_nse_batch_stocks()` → JSON (200-stock batches)

### 3. Per-Stock Data Fetch

`DataSourceManager` (Yahoo Finance) fetches:
- Live quote via `yfinance.Ticker.fast_info` + 2-day history
- OHLCV history via `fetch_for_trading_type()` with auto-fallback from intraday to daily

### 4. Analysis Pipeline

```
OHLCV DataFrame
  │
  ├─ [if market open] drop last candle (incomplete OHLC)
  │
  ├─ classify_candle() for each bar → CandleInfo[]
  │
  ├─ detect_zones() scans for legin-base-legout patterns:
  │    ├─ Normal-base zones (1-10 boring candles between exciting legs)
  │    ├─ Missing-base zones (M17: two opposite exciting candles)
  │    └─ Gap-as-legout zones (1.3% gap terminates base)
  │    For each zone:
  │      ├─ Mark boundaries (Normal/Exceptional/WTW/BTW)
  │      ├─ score_zone() → ODD score (freshness + strength + time)
  │      └─ Skip if invalidated (M46: close beyond distal)
  │
  ├─ assess_closing_quality() — M8 flag per zone
  │
  ├─ filter_zones() → at most 6 display zones
  │    ├─ Drop zones tested 2+ times
  │    ├─ Drop zones scoring < 5.0
  │    └─ Keep nearest 3 per side of current price
  │
  ├─ Stage 2: detect_trend() + ema20_confluence() + trend alignment
  │
  ├─ Stage 3 (opt-in): find_recent_swing() + fib_confluence() + confluence_rating()
  │
  └─ Result dict → session state + SQLite
```

### 5. Display

- Dashboard: 3-column grid of `stock_card` components
- Detail: Plotly chart with zone overlays, metrics, history, notes
- Export: Excel (3 sheets) or PDF

### 6. Deep-Link (New Tab)

Card generates `?stock=SYMBOL&exchange=EXCHANGE` URL. On load:
- `app.py` reads query params → sets session state → routes to detail view
- `dashboard.py:_run_single_stock_analysis()` runs analysis on-the-fly if no cached result

---

## Rule Engine / Rule Numbering System

GTF methodology rules are identified by M-numbers (M1 through M74+). Each rule is a specific trading concept from the GTF course (Episodes 1-20). Rules are implemented incrementally — the current codebase covers 9 rules from Phase 1 (marking refinements).

**How rules are tagged in code:**
- `# Rule:` or `# M<N>:` comments at the definition site
- Test functions named `test_m<N>_<description>` (e.g., `test_m46_demand_close_below_distal_invalidated`)
- Module docstrings reference rule numbers in their descriptions

**Rule interaction model:**
- Rules operate on different zone fields and are mostly independent
- M2 modifies distal; M13 modifies proximal — they apply independently
- M3 uses wicks for test entry/exit; M46 uses closes for invalidation — same function, different thresholds
- M8 is a flag ("closing_quality") that never affects ODD score
- Stage 2/3 context is purely additive — never modifies Stage 1 fields

---

## Testing Strategy

**340 tests** across 11 files:

| File | Tests | What It Validates |
|------|-------|-------------------|
| `test_zone_engine.py` | ~99 functions (expanded by parametrize) | All GTF rules: M2, M3, M5, M8, M13, M17, M28, M46 + candle classification, pattern detection, scoring, filtering, trend, EMA20, Fibonacci, confluence |
| `test_trend_following.py` | SMA crossover strategy | Signal determination, cross detection, strength classification |
| `test_trading_config.py` | Config validation | Timeframe mappings, valid combinations, defaults |
| `test_preferences_migration.py` | Preference migration | Legacy single-axis → two-axis model migration |
| `test_sidebar_selection_logic.py` | Sidebar cascading | Trading type changes reset strategy and enhancers |
| `test_timeframe_fetch.py` | Data fetching | Timeframe resolution, intraday fallback, 75m resampling |
| `test_interval_selector.py` | Interval mapping | UI label → fetch params |
| `test_fibonacci_lines.py` | Chart overlays | Fibonacci lines drawn on Plotly figures |
| `test_export.py` | Export functionality | Excel/PDF generation for both strategies |
| `test_watchlist_autocomplete.py` | Stock search | Symbol/name matching and ranking |

**Test patterns:**
- **Hand-crafted OHLC data:** Every test builds custom DataFrames with specific candle shapes. Inline comments explain the arithmetic (e.g., `body_pct = 9/15 = 0.60`).
- **Helpers:** `_make_df(rows)` builds DataFrames from `(O,H,L,C)` tuples; `_make_zone(**kwargs)` constructs Zone objects with controlled fields; `_closes_df(closes)` builds trivial DataFrames for trend/EMA tests.
- **Boundary coverage:** Tests explicitly hit threshold boundaries (e.g., body_pct exactly 0.50, close exactly at distal).
- **Both directions:** Most rules are tested for both demand (DBR/RBR) and supply (RBD/DBD).
- **Stage isolation:** Tests verify that Stage 2/3 enrichment never modifies Stage 1's `odd_score`.
