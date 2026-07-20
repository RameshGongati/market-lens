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

**Code:** `analysis/zone_engine/scoring.py:130-193` — `count_zone_tests()`
- Scans from `test_scan_start_idx = legout_end + 1`
- Entry: `low <= proximal` (demand) or `high >= proximal` (supply) — wick-based
- Exit: `close > proximal` (demand) or `close < proximal` (supply) — close-based (`ba9e212`)
- Invalidation: wick or close beyond distal (M46 integration)
- `activation_touch` set on first entry without incrementing count
- Same-bar enter+exit: entry and exit are two separate `if` blocks (not `if/elif`), so a candle that enters AND closes outside in one bar increments `tests` by 1

All scanning, entry/exit, and scoring logic matches. The prolonged-habitation rule (5/10 candle thresholds) was evaluated and deemed unnecessary — each enter+exit cycle already counts as a separate test, which correctly handles the real-world cases (e.g., NHPC consecutive daily tests, BAJFINANCE Jul 20 2026 wick-entry with close-inside correctly not counted).

**Tests:** 9 tests covering fresh, one-cycle, two-cycles, activation-touch, no-trade, same-bar demand, same-bar supply, wick-inside-no-test, close-outside-counts.

**Status:** DONE (`ba9e212`). No open gaps.

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

**Note:** The code's docstring at line 238 says "body bottom of legout" for demand proximal, but the code actually computes `min(body_top_tp, body_top_legout)`. The docstring is inaccurate; the code and tests are correct. (Flagged for docstring fix — see REFINEMENT_PLAN.md.)

**Tests:** 13 tests covering DBR, RBD, scoring, no double-counting, legout-must-clear, extended legout, legin/legout extension, same-direction rejection, M2 exceptional, continuation rejection, gap in legout, any-candle fix.

**Status:** INLINE with spec (code correct, docstring needs fix).

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

## Phase 1 — Pending Rules

### #10 M10 — Garbage-Area Rejection (TODO)

**Spec:** Reject zones where the legout barely clears the base. Achievement ratio formula:
```
achievement_ratio = (legout_extreme - proximal) / (proximal - distal)
```

Two tiers:
- `< 0.5` = hard reject (zone discarded)
- `0.5 - 1.0` = "Weak Departure" flag (kept but flagged)
- `>= 1.0` = clean (no flag)

Guard: For missing-base zones where `proximal - distal` is near zero, skip the ratio check.

**Existing code:** None.

---

### #11 M12 — Narrow Base Width (TODO)

**Spec:** Measure base tightness:
```
base_width_pct = (base_high - base_low) / price * 100
```

Initially information-only (displayed in zone details), not a score modifier.

**Existing code:** None.

---

### #12 M65/M66 — LOTL Merge + Achievement Weighting (TODO)

**Spec:**
- **M65:** Merge same-type zones with overlapping price ranges. Combined proximal = nearest-to-price edge, combined distal = most extreme edge. Start with overlap-only merge (no proximity-based merge).
- **M66:** Track which sub-zone had the better M8 achievement (closing quality). The merged zone inherits the best achievement.

**Existing code:** `analysis/zone_engine/filters.py:78-108` — `_merge_overlapping_zones()` exists with a merge-intervals algorithm but is NOT called from `filter_zones()`. This is a starting point for M65; needs M66 achievement tracking added.

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

## Open Gaps Summary

| Item | Gap | Priority |
|------|-----|----------|
| #6 M17 | Code docstring at `patterns.py:238` says "body bottom of legout" but code computes `min(body_top_tp, body_top_legout)` | Minor fix |

**Closed gaps:**
- ~~#3 M3 habitation~~ — Deemed unnecessary; enter+exit cycle counting handles all real-world cases
- ~~#7 M46 wick breaches~~ — Resolved by changing invalidation to wick-based (`458ba6c`); wick past distal = zone dead, no need for a counter

## Partial Implementations (Starting Points for TODO Items)

| Item | What Exists | What's Missing |
|------|-------------|----------------|
| #12 M65/M66 | `_merge_overlapping_zones()` in `filters.py:78-108` (merge-intervals algorithm) | Not called from `filter_zones()`, no M66 achievement tracking |
| #25 M42-M44 | `ema20_confluence()` in `enhancers.py` (basic in/near check) | M43 trending-only filter, M44 multi-TF support |
| #26 M40/M41 | `TrendFollowingAnalysis` in `trend_following.py` (standalone strategy) | Not wired as +1 enhancer into D/S pipeline |
| #28 M70/M71 | RSI checkbox in sidebar UI | No analysis logic behind it (inert) |
