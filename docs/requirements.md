# Market Lens — GTF Methodology Requirements (Cross-Checked)

This document describes the GTF (Trading in the Zone) demand/supply zone methodology as specified in the master requirements roadmap, **cross-checked against the actual codebase** (2026-07-18). Each item is verified: constants, logic, thresholds, and test coverage.

Items marked **DONE** are implemented and tested. Items marked **DONE\*** have known gaps noted inline. Items marked **TODO** are not yet implemented. Items marked **DNB** (Do Not Build) are explicitly excluded.

---

## Phase 1 — Zone Marking & Scoring

### #1 M5 — Candle Classification (DONE)

**Spec:** A candle is EXCITING when body >= 50% of range AND body >= 1.3% of price. Otherwise BORING. Strong exciting = body >= 80% of range. Doji (close == open) is always boring.

**Code:** `analysis/zone_engine/candles.py:28-30`
- `_EXCITING_THRESHOLD = 0.50`
- `_STRONG_THRESHOLD = 0.80`
- `_MIN_BODY_PCT_OF_PRICE = 0.013`

Both conditions checked at line 77-80. Direction: bullish/bearish/doji. All constants and logic match the spec.

**Tests:** 9 tests (7 classification + 2 price filter).

**Status:** INLINE with spec.

---

### #2 M28 — Time-at-Base Scoring (DONE)

**Spec (Episode 8 thresholds):**
- 0-3 base candles = 2 points
- 4-5 base candles = 1 point
- 6+ base candles = 0 points

Missing-base zones (0 candles) receive maximum 2 points.

**Code:** `analysis/zone_engine/scoring.py:30-32`
- `_SHORT_BASE_POINTS = 2.0` (num_base_candles <= 3)
- `_MEDIUM_BASE_POINTS = 1.0` (4-5)
- `_LONG_BASE_POINTS = 0.0` (>5)

Boundary at `<= 3` covers 0 (missing-base). All thresholds match.

**Tests:** 8 parametrized cases (1,2,3 -> 2pts; 4,5 -> 1pt; 6,7,10 -> 0pts).

**Status:** INLINE with spec.

---

### #3 M3 — Zone Test Counting / Freshness (DONE)

**Spec:** A "test" = complete enter+exit cycle. Entry: wick touches/crosses proximal (wick-based). Exit: candle closes outside the zone (close-based). Only full round-trips count — including same-bar enter+exit (a candle whose wick enters the zone and closes outside on the same bar counts as one test). A wick that enters the zone but closes inside does NOT count as a test. First entry sets `activation_touch` = True without counting as test. Freshness scoring: 0 tests = 3pts, 1 test = 1.5pts, 2+ tests = 0pts.

**Code:** `analysis/zone_engine/scoring.py:135-203` — `count_zone_tests()`
- Scans from `test_scan_start_idx = legout_end + 1`
- Entry: `low <= proximal` (demand) or `high >= proximal` (supply) — wick-based
- Exit: `close > proximal` (demand) or `close < proximal` (supply) — close-based (`ba9e212`)
- Invalidation: wick or close beyond distal (M46 integration)
- **Persistent habitation:** once inside, if 4 consecutive candles all close inside the zone, the zone is invalidated — the institutional imbalance is exhausted (`144a0bc`). Counter resets on any close outside (exit). Constant: `_HABITATION_LIMIT = 4`.
- `activation_touch` set on first entry without incrementing count
- Same-bar enter+exit: entry and exit are two separate `if` blocks (not `if/elif`), so a candle that enters AND closes outside in one bar increments `tests` by 1

All scanning, entry/exit, and scoring logic matches. Discovered via ADANIPORTS DBR zone (Jun 30 – Jul 1 2026) where price re-entered and sat inside for 14+ candles without departing.

**Tests:** 13 tests covering fresh, one-cycle, two-cycles, activation-touch, no-trade, same-bar demand, same-bar supply, wick-inside-no-test, close-outside-counts, habitation demand, habitation supply, habitation reset, habitation boundary.

**Status:** DONE (`144a0bc`). No open gaps.

---

### #4 M2 — Auto-Exceptional Distal (DONE)

**Spec:** When a leg candle's wick extends beyond the base range, the distal line widens to that wick extreme ("Exceptional" marking). The check is pattern-specific:
- **DBR/RBD (reversal patterns):** Check BOTH legin and legout wicks
- **RBR/DBD (continuation patterns):** Check legout wicks ONLY (legin wicks point the wrong direction in continuation)

**Code:** `analysis/zone_engine/patterns.py:256-285` — `_exceptional_distal()`
- Pattern-specific logic at lines 518-525 (auto-apply block)
- DBR: `distal_exceptional = min(legin_low, legout_low)` vs `distal`
- RBD: `distal_exceptional = max(legin_high, legout_high)` vs `distal`
- RBR: `distal_exceptional = legout_low` (no legin)
- DBD: `distal_exceptional = legout_high` (no legin)

All pattern-specific logic correct. The `marking` field is set to `"Exceptional"` when triggered.

**Tests:** 7 tests covering all 4 pattern types, proximal independence, M2+M13 combined.

**Status:** INLINE with spec. The pattern-specific behavior (reversal = both legs, continuation = legout only) is correctly implemented but was oversimplified in the original REQUIREMENTS.md as "leg-in or leg-out" generically.

---

### #5 M13 — Proximal Marking: WTW vs BTW (DONE)

**Spec:** 3-priority chain determines proximal marking:
- **P1 Explosive legout:** total_legout_units >= 2 (exciting candles + inter-legout gaps) -> WTW
- **P2 Doji in base:** any base candle with body < 10% of range -> BTW
- **P3 Width ratio:** WTW_width / BTW_width > 1.5 -> BTW, else WTW
- P1 overrides P2

Gap-as-legout threshold: `_MIN_GAP_LEGOUT_PCT = _MIN_BODY_PCT_OF_PRICE` (1.3%)

**Code:** `analysis/zone_engine/patterns.py:190-228` — `_m13_proximal_marking()`
- `_DOJI_BODY_THRESHOLD = 0.10`
- `_WICK_TO_BODY_ZONE_RATIO_THRESHOLD = 1.5`
- Gap scanner at lines 437-450

All constants and priority chain logic match.

**Tests:** 11 tests covering each priority, supply WTW, gap legout, P1 overrides P2, M2+M13 combined.

**Status:** INLINE with spec.

---

### #6 M17 — Missing-Base Zones (DONE)

**Spec:** Two consecutive exciting candles in opposite directions form an instant-reversal zone (0 base candles). Bearish->Bullish = DBR demand; Bullish->Bearish = RBD supply.

**Boundary marking:** The proximal uses the more conservative value from BOTH the turning-point and legout candle:
- Demand: `proximal = min(body_top_turning_point, body_top_legout)` — the lower of the two body tops
- Supply: `proximal = max(body_bottom_turning_point, body_bottom_legout)` — the higher of the two body bottoms
- Distal uses the most extreme wick of both candles

**Code:** `analysis/zone_engine/patterns.py:231-253` — `_missing_base_marking()`
- Demand: `proximal = min(max(tp_o, tp_c), max(lo_o, lo_c))`
- Supply: `proximal = max(min(tp_o, tp_c), min(lo_o, lo_c))`
- Distal: `min(tp_l, lo_l)` (demand) or `max(tp_h, lo_h)` (supply)

Logic correct. Validation requires at least one extended legout candle to clear the turning point's range.

**Tests:** 13 tests covering DBR, RBD, scoring, no double-counting, legout-must-clear, extended legout, legin/legout extension, same-direction rejection, M2 exceptional, continuation rejection, gap in legout, any-candle fix.

**Status:** INLINE with spec. (The docstring formerly said "body bottom of legout" for demand proximal while the code computed `min(body_top_tp, body_top_legout)`; corrected in `559cb9e`.)

---

### #7 M46 — Zone Invalidation (DONE — `458ba6c`)

**Spec:** A zone is invalidated when price breaches the distal via wick OR close:
- Demand: `low < distal` invalidates
- Supply: `high > distal` invalidates
- Wick or close exactly AT distal: survives (strict inequality)

**Code:** `analysis/zone_engine/scoring.py:168-178` — within `count_zone_tests()`
- Demand: `low < distal` -> invalidated
- Supply: `high > distal` -> invalidated
- Strict inequality confirmed

**Tests:** 5 tests — demand wick invalidates, demand close invalidates, supply wick invalidates, supply close invalidates, wick exactly at distal survives.

**Status:** DONE. Previously used close-only invalidation; updated to wick-based in `458ba6c`.

---

### #8 M8 — Closing Concept (DONE)

**Spec:** Evaluate legout quality by checking if legout CLOSED beyond the nearest opposing zone's proximal:
- "strong": closed beyond opposing proximal
- "weak": wicked past but closed before opposing proximal
- "unchecked": no opposing zone in legout's path

Pipeline order: `detect_zones()` -> `assess_closing_quality()` -> `filter_zones()`. Only checks prior zones (`zones[:i]`). Does NOT change ODD score — flag only.

**Code:** `analysis/zone_engine/scoring.py:312-353` — `assess_closing_quality()`
Called at `analysis/demand_supply.py:222`, between detect and filter. Checks `zones[:i]` for prior opposing zones. Sets `closing_quality` field. Score unchanged confirmed by test.

**Tests:** 6 tests — demand strong, demand weak, unchecked, supply strong, supply weak, score unchanged.

**Status:** INLINE with spec.

---

### #9 Gap-as-Legout Noise Threshold (DONE)

**Spec:** Gap >= 1.3% between consecutive base candles terminates the base and acts as legout. The 1.3% threshold matches `_MIN_BODY_PCT_OF_PRICE`. Separately, `_has_gap` for ODD strength scoring has NO minimum threshold (any gap counts).

**Code:** `analysis/zone_engine/patterns.py:40`
- `_MIN_GAP_LEGOUT_PCT = _MIN_BODY_PCT_OF_PRICE` (= 0.013)
- Gap scanner at lines 437-450
- `_has_gap()` at lines 100-122: no threshold, any gap counts for strength

**Tests:** 7 tests including noise gap ignored, real gap triggers, gap-with-exciting, gap-has_gap=True.

**Status:** INLINE with spec.

---

## Phase 1 — Formerly Pending Rules (COMPLETE)

### #10 M10 — Garbage-Area Rejection (DONE — `44ef8e9`)

**Spec:** Reject zones where the legout barely clears the base. Achievement ratio formula:
```
achievement_ratio = (legout_extreme - proximal) / (proximal - distal)
```

Three tiers:
- `< 0.5` = hard reject (zone discarded)
- `0.5 - 1.0` = "Weak Departure" flag (kept but flagged)
- `>= 1.0` = clean (no flag)

Guards:
- Missing-base zones (M17, 0 base candles): skip check, always "Clean"
- Near-zero base range (< 0.01): skip check, always "Clean" (division guard)

M10 does NOT modify ODD score — it is a quality gate (reject) and quality flag only.

**Code:**
- `analysis/zone_engine/patterns.py:43-50` — constants `_MIN_ACHIEVEMENT_RATIO`, `_CLEAN_ACHIEVEMENT_RATIO`, `_MIN_BASE_RANGE_FOR_M10`
- `analysis/zone_engine/patterns.py:289-332` — `_m10_achievement_check()` computes ratio and returns (keep, zone_quality)
- `analysis/zone_engine/models.py:77-82` — `zone_quality: str = "Clean"` field on Zone dataclass
- `ui/components/stock_detail.py:886-887` — " | Weak Departure" flag in chart zone labels

**Tests:** 10 tests covering clean demand, weak departure, garbage rejection, supply-side, missing-base skip, division guard, regression on existing fixtures, and score independence.

**Status:** INLINE with spec.

---

### #11 M12 — Narrow Base Width (DONE — `7e0d79d`)

**Spec:** Measure base tightness:
```
base_width_pct = (base_high - base_low) / proximal * 100
```

Information-only — never filters a zone and never changes `odd_score`.

**Decisions taken during implementation:**
- **Denominator is `proximal`, not live price.** Using the current market price would make a stored zone property drift every day as price moves. The proximal fixes the value at formation time.
- **Numerator is the FULL base range**, regardless of whether M13 marked the proximal wick-to-wick or body-to-wick. The full range is the territory contested; the marking only decides where price is entered. The tradeable width is already derivable from `proximal` and `distal`, so no second field was added.
- **Demand/supply asymmetry accepted.** `proximal` is the high edge for demand zones and the low edge for supply, so the same base measured either way differs by roughly the width percentage itself — about 0.09pp near the 3% threshold. Judged negligible.
- **Threshold constant is PUBLIC** (`WIDE_BASE_THRESHOLD_PCT`, no leading underscore) so `stock_detail.py` imports it instead of keeping a second copy that could drift from the detection value.
- **"Wide Base" flag suppressed for missing-base zones (M17).** Their base is the single turning-point candle, which is exciting by definition (M5 requires body >= 1.3% of price and >= 50% of range), so its full range sits near the 3% threshold structurally — 2 of 5 LUPIN missing-base zones tripped it. An instant reversal has no base to be sloppy about. The width is still stored and shown in the detail panel; only the chart warning is withheld.

**Code:**
- `analysis/zone_engine/patterns.py` — `WIDE_BASE_THRESHOLD_PCT = 3.0`; `_base_width_pct()` helper; computed at both zone creation paths
- `analysis/zone_engine/models.py` — `base_width_pct: float = 0.0` field on Zone
- `ui/components/stock_detail.py` — `| Wide Base N.N%` chart-label flag above threshold (non-missing-base only); `_render_zone_widths()` expander showing every zone's width always

**Tests:** 6 tests covering narrow base, wide base above threshold, supply-side, missing-base turning-point range, score independence, and regression on existing fixtures. Expected values are derived from each zone's actual `proximal` rather than hardcoded, so the tests do not silently depend on M13's marking choice.

**Status:** INLINE with spec.

---

### #12 M65/M66 — LOTL Merge + Achievement Weighting (DROPPED)

**Dropped by directive, 2026-08-02.** Implemented once and reverted: merging
same-type overlapping zones "completely messed up the zone markings" on real
charts. Merging widens a zone to the union of its members, which moves the
**proximal** — and the proximal is the entry level being traded. Overlapping
zones are therefore kept separate, each reflecting its own base candles only.

`_merge_overlapping_zones()` remains in `analysis/zone_engine/filters.py:78-108`
but is deliberately **not called** from `filter_zones()` — see Gotcha 7 in
`CLAUDE.md`. Leave it that way unless this decision is revisited.

---

## Phase 2 — Trade Computation (All TODO)

### #13 M1 — Entry / Stop-Loss / Target

Entry near proximal, SL near distal, target at 2R minimum. "Slightly" buffer defined by M7 (below).

### #14 M7 — Volatility Buffer

ATR-based buffer: `0.1 * ATR(14)` default. Named constant `_BUFFER_ATR_MULTIPLIER`. Applied to entry and SL prices.

### #15 M29 — Entry Types 1/2/3 Mechanics

- Type 1: Set-and-forget at proximal (score 7 — all odds aligned)
- Type 2: Close+open inside zone confirms (score 5-6)
- Type 3: Wait for departure and return

Currently `entry_recommendation()` returns text labels but has no mechanical Type 2/3 detection logic.

### #16 M50 — Refine Wide HTF Zone

Find narrower LTF zone inside a wide HTF zone for precise entry. Depends on Phase 3 (multi-TF architecture).

---

## Phase 3 — Multi-Timeframe Analysis (All TODO)

### #17 M18/M23 — Three-Timeframe Architecture

MIT/WIT/DIT/HIT timeframe matrix. Requires 3 data fetches + 3 analyses per trade. Major architectural change.

### #18 M19-M21 — Curve / Location / Trend Gating

Retracement thirds (0/33/66/100%) between nearest demand/supply. Location + trend agreement required.

### #19 M22/M27 — HTF Priority

Breached HTF zone invalidates all LTF zones inside it. No LTF execution zone = no trade.

### #20 M25/M26 — Score-Then-Context Tie-Breaking

Score first (ODD), then HTF context as tiebreaker. LTF + same-direction HTF = strongest setup.

### #21 M34-M39 — Variable Aggression

Aggressive/conservative/no-trade matrix. Coinciding zones, opposing HTF blocks, sub-zone refinement.

### #22 M48/M49/M51 — Counter-Trend & Break Rules

Wait for LTF supply break at HTF demand. Break-validates-zone. One vs two breaks depending on HTF backing.

---

## Phase 4 — Structural Trend Analysis (All TODO)

### #23 M45 — Zone-Violation Trend

Track zone violations for structural trend. One breach = sideways, two = confirmed reversal. Currently trend uses SMA-50 clock only.

### #24 M46ext — Close-Beyond-Distal for Trend

Reuse M46 primitive for structural trend state machine. Running tally of violated zones.

---

## Phase 5 — Confluence Enhancers

### EMA 20 Confluence (Implemented)

`analysis/zone_engine/enhancers.py` — checks if 20-period EMA sits inside or within 2% of zone. Flags `ema20_enhancer=True`. Purely additive.

### Fibonacci Retracement Confluence (Implemented, Opt-In)

`analysis/zone_engine/fibonacci.py` — levels at 0.382, 0.5, 0.618, 0.786 within 120-candle lookback, 1% proximity. Strongest: 0.618 > 0.786 > 0.5 > 0.382. Separate scorecard from ODD.

### #25 M42-M44 — EMA20 Dynamic S/R (TODO)

**M42:** EMA20 as dynamic support/resistance level (partially exists — basic confluence check done).
**M43:** Trending-only filter — skip EMA20 confluence when `detect_trend()` returns SIDEWAYS (EMA20 unreliable in sideways). **Not implemented.**
**M44:** Multi-TF EMA20 upgrade. **Not implemented.**

**Existing code:** `analysis/zone_engine/enhancers.py` has `ema20_confluence()` — the basic in/near check exists but lacks the M43 trending-only filter and M44 multi-TF support.

### #26 M40/M41 — MA Crossover as +1 Enhancer (TODO)

Keep both roles: standalone Trend Following strategy AND +1 demand/supply enhancer. No enhancer wiring exists yet.

**Existing code:** `analysis/trend_following.py` — standalone SMA 50/200 crossover strategy exists. Not yet wired as an enhancer into the demand/supply pipeline.

### #27 M59-M64 — Gap Theory (TODO)

Gap classification: up/down, inside/outside, no-vice/pro. Six sub-rules for trading application.

### #28 M70/M71 — RSI + Divergence (TODO)

Oversold at demand = bonus, overbought at supply = bonus. Divergence detection.

**Existing code:** RSI checkbox exists in sidebar UI but is inert (no analysis logic behind it).

### #29 M67 — Bull/Bear Trap Detection (TODO)

Breakout into opposing fresh zone = trap warning. Single-TF initially.

### #30-31 M57/M58 + M69 — Candlestick & Conventional Patterns (DNB)

Explicitly excluded per user directive. No code exists (correct).

---

## Phase 6 — Risk & Trade Management (All TODO)

### #32 M31 — Position Sizing

`qty = fixed_risk / risk_per_share`. User input for risk-per-trade amount.

### #33 M32/M33 — R:R + Stop Discipline

Minimum 2R preferred. Chart-based targets. No averaging down. Exit on stop hit.

### #34 M72/M73 — Structural Trailing Stop

Trail under new quality demand zones (weekly preferred). Hold for ATH when little supply above.

### #35 M74 — Hit-to-Hit Stops

Exit when stop touched (not close-based for stops — distinct from M46). New setup = new trade.

---

## Phase 7 — Sector / Market Context (All TODO)

### #36 M52-M54 — Index -> Sector -> Stock Confluence

Index -> sector -> stock alignment. Needs new data infrastructure (index feeds, sector mapping).

### #37 M55 — Sector +2 Enhancer

Sector alignment = strongest confluence (+2 vs +1 for others).

### #38 M56 — Investing vs Trading

Different rule application for investing (sector-driven baskets) vs trading (stock setup first).

---

## Phase 8 — Options (Non-GTF, All TODO)

### #39 Options Module

Separate documents not yet provided. Not part of GTF course material.

---

## Display & Filtering Rules (Implemented)

| Rule | Value | Code Location |
|------|-------|---------------|
| Max times tested | 1 | `filters.py:_MAX_TIMES_TESTED=1` |
| Min display score | 5.0 | `filters.py:_MIN_DISPLAY_SCORE=5.0` |
| Max zones per side | 3 | `filters.py:_MAX_ZONES_PER_SIDE=3` |
| Trend alignment | Demand=UP only, Supply=DOWN only | `demand_supply.py:_apply_trend_alignment()` |
| Today's candle drop | Before 4 PM IST | `demand_supply.py` drops `data.iloc[:-1]` |

---

## Zone Confirmation Screener (DONE — `f8aae8a`)

Opt-in screener mode surfacing stocks where price **entered a zone and closed
back out through the proximal**, then stayed near it. A different trade from
the one the display filter supports: that finds zones price is *approaching*
(entry is a prediction), this finds zones price has *already reacted to*
(evidence the orders were real).

**Why it cannot reuse `filter_zones`** — arithmetic, not preference.
Confirmation forces freshness off its top value, so the reachable ODD totals
become:

| Zone state | Freshness | Reachable totals |
|------------|-----------|------------------|
| Tested once | 1.5 | 2.5 / 3.5 / 4.5 / 5.5 |
| Tested 2+ | 0.0 | 1.0 / 2.0 / 3.0 / 4.0 |

Neither ladder contains **5.0**, so the `_MIN_DISPLAY_SCORE = 5.0` cutoff
cannot grade a confirmed zone — it silently demands strength 2 AND time 2,
the single perfect pairing. Measured over 8 NIFTY stocks: 17 confirmed zones
sat at 4.5 and were discarded by a margin no zone could ever have earned.
`filter_confirmation_zones` is therefore a **separate selection over the same
raw zones**; `filter_zones` is untouched and `display_zones` is byte-identical.

### Qualifying conditions

| Condition | Value | Code Location |
|-----------|-------|---------------|
| Confirmed at least once | `times_tested >= 1` | `filters.py:filter_confirmation_zones()` |
| Min score | 3.5 | `filters.py:_CONFIRMATION_MIN_SCORE=3.5` |
| Max distance from proximal | 8% | `filters.py:_CONFIRMATION_MAX_DISTANCE_PCT=8.0` |
| Max bars since last exit | 10 | `filters.py:_CONFIRMATION_MAX_BARS_SINCE_TEST=10` |
| Zones per side | 1 (nearest) | `filters.py:_MAX_CONFIRMATION_ZONES_PER_SIDE=1` |

The exit side needs no separate check: `count_zone_tests` only counts a test
when the candle closes back out through the **proximal**, and an exit through
the distal invalidates the zone under M46 instead.

**Recency is independent of distance.** A zone can sit 3% from price with its
reaction two months old — price wandered away and came back for unrelated
reasons. Observed on RELIANCE: a supply zone 3.4% from price whose last exit
was 44 bars back, alongside a demand zone 4.3% away that exited 5 bars back.
Only the second is a live signal.

### Display

Confirmed zones are drawn on the chart tagged **CONFIRMED** in blue
(`stock_detail.py:_CONFIRMED_FLAG_COLOR`) — they score below the display floor
and would otherwise be invisible. LUPIN showed why: its confirmed zone 1.7%
from price was undrawn while three displayed demand zones sat 34–45% away, so
the screener listed the stock on evidence the chart never showed.

The card summary leads with the matched zone in this mode, for the same
reason (ONGC advertised a supply zone 17% away while the matched one sat 3%
away, unnamed).

Screener, chart overlay and card summary all read **live session state**
(`screener_confirmation`). An earlier version froze the mode onto the result
at scan time while the screener read it live; the list matched 16 stocks and
not one showed a confirmed zone.

The deep link carries the mode as **`cf=1|0`** — see Gotcha 13 in `CLAUDE.md`.

---

## Open Gaps Summary

| Item | Gap | Priority |
|------|-----|----------|
| — | None open. **Phase 1 is COMPLETE** — M65/M66 was dropped by directive (see #12). | — |

**Closed gaps:**
- ~~#6 M17 docstring~~ — Fixed (`559cb9e`); docstring now matches the computed `min(body_top_tp, body_top_legout)` / `max(body_bottom_tp, body_bottom_legout)`
- ~~#3 M3 habitation~~ — Deemed unnecessary; enter+exit cycle counting handles all real-world cases
- ~~#7 M46 wick breaches~~ — Resolved by changing invalidation to wick-based (`458ba6c`); wick past distal = zone dead, no need for a counter

---

## UI — Multi-Page Application (DONE — `96d5c36`..`4405fd6`, 2026-08-03)

The single-page dashboard became seven routed pages. `dashboard.py` stopped
being a page and now holds the scan plus the helpers the pages share — see
Gotcha 22 in `CLAUDE.md`.

| Page | Module | Content |
|------|--------|---------|
| Dashboard | `market_overview.py` | Market bias (NIFTY vs 20 EMA), NIFTY 50 / BANK NIFTY with sparklines, valid + high-ODD setup counts, four zone-state pills, top opportunities, recent alerts, quick tools |
| Analysis Results | `analysis_results.py` | Scan summary cards, Status/Strength/Sort, removable screener chips, ranked table with search + paging, per-row View deep link |
| Stock Detail | `stock_detail.py` | 7 tabs (Chart first — Gotcha 25) with a Setup Summary / Quick Trade Plan rail |
| Alerts | `alerts_page.py` | Live zone-proximity matches and the Telegram delivery history |
| Reports | `reports_page.py` | F&O results monitor — see below |
| Trade Journal | `placeholders.py` | Routed, awaiting requirements |
| Watchlists / Settings | unchanged | |

**Scan progress** is a standalone page — donut, stock count and five
milestones. The milestones are paced off the percentage and named for the run
as a whole, because all five stages actually execute per stock inside one loop
iteration; a checklist ticking on real phase transitions would reset fifty
times. The measured quantities are the count and the percentage. Streamlit's
native Stop is used because a custom in-page button cannot work — the loop
blocks the script, so the page never processes the click.

**Placeholders** carry their real label and name the phase that will fill
them, never a zero: entry/stop/target and risk-reward (Phase 2), HTF/ITF trend
(Phase 3), everything sector-related (Phase 7).

---

## Reports — F&O Results Monitor (DONE Phase 1 — `4405fd6`, 2026-08-03)

Earnings/results tracking on live yfinance data.

**Live:** next result date, consensus EPS and revenue estimates, countdown and
status, most recent reported EPS with surprise %, close-to-close price
reaction, sector (from the shipped sector watchlists), upcoming calendar,
recent releases, and a per-sector reaction heatmap.

**Caching is structural, not an optimisation.** One symbol costs ~790ms
(`calendar` + `earnings_dates`), so the 208-stock F&O universe is ~164s. The
page renders from a disk cache at `~/.market-lens/earnings` and fetches only
on an explicit Refresh — `get_earnings(..., cache_only=True)` on render, or
opening the page fetches everything uncached inline. See Gotcha 27. The cache
is keyed by calendar day.

**High Impact rule** (transparent by design — every threshold appears verbatim
in the badge tooltip):

| Case | Condition |
|------|-----------|
| Upcoming | due within 2 days **AND** in the F&O universe **AND** (in a major index/sector list **OR** revenue estimate >= 10,000 Cr) |
| Released | EPS surprise >= 5% **OR** price reaction >= 3% |

**Deliberately blank in Phase 1:** Session (BMO/AMO) — yfinance has no
reliable Indian pre/post-market marker; OI / Vol Spike — needs an F&O
derivatives pipeline. Both are stated in the page's Important Notes. Result
alert subscriptions are Phase 2; the page shows the existing zone-alert count.

---

## Alerts — Telegram Zone Proximity Notifications (DONE — `55922c9`..`75b2094`)

### Alert Configuration (DONE)
- **Config file:** `config/alert_config.json` (gitignored — contains bot token)
- **Example:** `config/alert_config.example.json` committed for reference
- **Settings UI:** Full Telegram alert config on Settings page — master toggle, bot token (masked), recipients table with add/delete, alert conditions (stocks source, proximity threshold, min score, zone type, cooldown)
- **Persistence:** `config/alert_settings.py` with `load_alert_config()` / `save_alert_config()`

### Telegram Delivery (DONE)
- **Module:** `alerts/telegram.py`
- **Functions:** `send_telegram_message()`, `send_to_all_recipients()`, `format_zone_alert()`, `format_test_message()`
- **Format:** HTML-formatted messages with 📈/📉 icons, price, zone type, score, closing quality, proximal/distal, trend, IST timestamp
- **Test button:** Settings page has "Send Test Message" to verify bot connectivity

### In-App Alert Badges (DONE)
- **Module:** `alerts/zone_alert_checker.py`
- **AlertMatch dataclass:** symbol, current_price, zone, distance_pct, trend
- **Dashboard banner:** Expandable "🔔 N stocks near zones" at top of results grid — uses cached analysis results, no extra data fetching
- **Conditions:** Respects configured proximity threshold, min score, and zone type filter

### Background Monitor (DONE)
- **Script:** `alert_monitor.py` (standalone, not inside Streamlit)
- **Schedule:** Every 5 minutes during market hours (9:15 AM – 3:30 PM IST, Mon–Fri)
- **Cooldown:** once-per-zone-per-day, every-approach, or once-per-zone-ever — persisted in alert_history
- **Code reuse:** Uses same `DemandSupplyAnalysis`, `detect_zones`, `filter_zones` — no duplication
- **Graceful shutdown:** Handles SIGINT/SIGTERM, saves state before exit

---

## Partial Implementations (Starting Points for TODO Items)

| Item | What Exists | What's Missing |
|------|-------------|----------------|
| ~~#12 M65/M66~~ | `_merge_overlapping_zones()` in `filters.py:78-108` (merge-intervals algorithm) | DROPPED — deliberately not called; see #12 |
| #25 M42-M44 | `ema20_confluence()` in `enhancers.py` (basic in/near check) | M43 trending-only filter, M44 multi-TF support |
| #26 M40/M41 | `TrendFollowingAnalysis` in `trend_following.py` (standalone strategy) | Not wired as +1 enhancer into D/S pipeline |
| #28 M70/M71 | RSI checkbox in sidebar UI | No analysis logic behind it (inert) |
