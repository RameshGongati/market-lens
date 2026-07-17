# Market Lens — GTF Methodology Requirements

This document describes the GTF (Trading in the Zone) demand/supply zone methodology **as implemented in code and validated by tests**. Each rule is identified by its M-number from the GTF course. Items marked `[ASSUMPTION — needs confirmation]` are inferred from roadmap descriptions, not from code or tests.

---

## Overview

The GTF methodology detects institutional demand/supply zones from OHLCV candlestick data. A "zone" forms when institutions accumulate (demand) or distribute (supply) at a price level, visible as a legin-base-legout candlestick pattern. Zones are scored on a 7-point ODD (Odds Enhancers) trade score combining freshness, departure strength, and time-at-base. Higher scores indicate higher-probability trade setups.

The methodology is organized into phases, each building on the previous:

| Phase | Focus | Status |
|-------|-------|--------|
| Phase 1 | Zone marking, scoring, and boundary refinements | 9 rules done, 3 pending |
| Phase 2 | Trade computation (entry/stop/target) | All pending |
| Phase 3 | Multi-timeframe analysis | All pending |
| Phase 4 | Structural trend analysis | All pending |
| Phase 5 | Confluence enhancers | Partially implemented (EMA20, Fibonacci) |
| Phase 6 | Risk & trade management | All pending |
| Phase 7 | Sector / market context | All pending |
| Phase 8 | Options (non-GTF) | All pending |

---

## Phase 1 — Zone Marking & Scoring (Implemented)

### M5 — Candle Classification

**Rule:** A candle is classified as EXCITING (institutional conviction) when:
1. Its body is >= 50% of its total range (high - low), AND
2. Its body is >= 1.3% of the candle's price (filters noise on low-price stocks)

Both conditions must be met. A candle failing either is BORING (consolidation/indecision).

**Sub-classifications:**
- **Strong exciting:** body >= 80% of range (minimal wicks — very high conviction)
- **Doji:** close == open (always boring, body_pct = 0)
- **Direction:** close > open = bullish; close < open = bearish; close == open = doji

**Constants:** `_EXCITING_THRESHOLD=0.50`, `_STRONG_THRESHOLD=0.80`, `_MIN_BODY_PCT_OF_PRICE=0.013`

**Tests:** 7 tests covering boring, exciting, strong, boundary (exactly at 0.50), just-below, doji, zero-range guard, plus 2 tests for the 1.3% price filter.

---

### Zone Pattern Detection (Core Scanner)

**Rule:** A zone forms from a three-part structure:
1. **Legin** — at least one exciting candle moving toward the base
2. **Base** — 1 to 10 consecutive boring candles (consolidation)
3. **Legout** — at least one exciting candle departing the base, whose CLOSE clears the base range

The legin and legout directions determine the zone type:

| Legin | Legout | Pattern | Zone Type |
|-------|--------|---------|-----------|
| Bearish | Bullish | DBR (Drop-Base-Rally) | Demand |
| Bullish | Bullish | RBR (Rally-Base-Rally) | Demand |
| Bullish | Bearish | RBD (Rally-Base-Drop) | Supply |
| Bearish | Bearish | DBD (Drop-Base-Drop) | Supply |

**Leg extension:** Both legin and legout extend up to 6 consecutive same-direction exciting candles (`_MAX_LEG_RUN=6`).

**Legout trimming:** After extending the legout, any candle that opens outside the zone and touches back in is a zone test, not a legout continuation.

**Gap-as-legout:** A price gap >= 1.3% of price between consecutive base candles terminates the base and acts as a legout departure. The candle after the gap can be boring — the gap itself carries institutional conviction.

**NORMAL boundary marking:**
- **Proximal** (edge nearest to current price): body tops for demand, body bottoms for supply
- **Distal** (far edge): wick lows for demand, wick highs for supply

**Tests:** 2 core detection tests (DBR demand, RBD supply) plus 7 gap-in-base tests.

---

### M2 — Auto-Exceptional Distal Marking

**Rule:** When a legin or legout candle's wick extends beyond the base range, the zone's distal line automatically widens to that wick extreme. This is the "Exceptional" distal marking — it captures the full institutional footprint.

**Specifics by pattern:**
- DBR: distal extends to the lowest wick of legin OR legout (whichever is lower)
- RBD: distal extends to the highest wick of legin OR legout (whichever is higher)
- RBR: distal extends to the lowest wick of legout only (legin is same direction)
- DBD: distal extends to the highest wick of legout only

**The marking field:** `"Normal"` (base wicks define distal) or `"Exceptional"` (leg wick extends distal).

**Independence:** M2 affects the distal line; M13 affects the proximal line. They apply independently and can both be active on the same zone.

**Tests:** 7 tests — DBR legin wick below base, DBR legin wick not below, RBD legin wick above, RBR legout wick below, DBD legout wick above, proximal independence, M2+M13 combined.

---

### M3 — Zone Test Counting

**Rule:** A "test" is a complete round-trip where price enters and then exits a zone:
1. **Entry:** Any candle's wick touches or crosses the proximal line
2. **Exit:** A subsequent candle's wick moves back outside the zone

Only complete enter+exit cycles count. If price enters and the data ends before exit, the count stays at 0 but `activation_touch` is set to True.

**Scanning starts** at `test_scan_start_idx = legout_end + 1` (the candle after the last legout candle, including any extended run).

**Freshness:** 0 tests = fresh (3 points), 1 test = tested once (1.5 points), 2+ tests = used up (0 points).

**Edge case — perpetual zone:** If price enters a zone and never leaves (e.g., 16+ consecutive candles with High >= proximal), the test count is 0. The zone stays "fresh" per this rule despite price living inside it.

**Tests:** 5 tests — zero returns (fresh), one cycle, two cycles, activation touch without exit, no-trade when score drops below 5.

---

### M5 (Scoring) — ODD Trade Score

**Rule:** The 7-point ODD score combines three independent components:

| Component | Scoring | Rule Detail |
|-----------|---------|-------------|
| **Freshness** | 3 / 1.5 / 0 | Never tested = 3, tested once = 1.5, tested 2+ times = 0 |
| **Strength** | 2 / 1 | Gap departure OR 2+ exciting legout candles = 2, else 1 |
| **Time-at-base** | 2 / 1 / 0 | 0-3 base candles = 2, 4-5 = 1, 6+ = 0 |

**Entry recommendations:**
- Score >= 7.0: "Entry Type 1 (aggressive)" — all odds in favor
- Score >= 5.0: "Entry Type 2/3 (confirmation)" — wait for confirming price action
- Score < 5.0: "No Trade" — odds insufficient

**Zone strength labels** (based on strong candles in legout, body >= 80%):
- 0 strong = "Normal"
- 1 strong = "Strong"
- 2+ strong = "Very Strong"

**Tests:** 1 full scoring test (fresh zone, 2 base candles, gap legout = 7 points), 7 parametrized entry threshold tests.

---

### M28 — Time-at-Base Scoring

**Rule:** Institutional zones where price departs quickly (few base candles) are higher quality — institutions couldn't wait to move:
- 0-3 base candles = 2 points (maximum — fastest departure)
- 4-5 base candles = 1 point
- 6+ base candles = 0 points

Missing-base zones (M17, 0 candles) receive the maximum 2 points.

**Tests:** 8 parametrized cases across 3 test functions (1, 2, 3 candles → 2pts; 4, 5 → 1pt; 6, 7, 10 → 0pts).

---

### M13 — Proximal Marking (Wick-to-Wick vs Body-to-Wick)

**Rule:** The proximal line (edge nearest to price) can be drawn two ways:
- **Wick-to-Wick (WTW):** proximal at the wick extreme of the base candles (wider zone, earlier entry)
- **Body-to-Wick (BTW):** proximal at the body extreme of the base candles (narrower zone, confirmed entry)

A 3-priority chain decides which to use:

**P1 — Explosive legout (highest priority):** If the legout has 2+ "units" of institutional conviction (exciting candles + gaps between consecutive legout candles), use WTW. Rationale: the explosive departure signals strong institutional presence at the base's full range.

**P2 — Doji in base:** If any base candle is a doji (body < 10% of range), use BTW. Rationale: doji candles have wide wicks that would make the WTW zone unreasonably wide.

**P3 — Width ratio (lowest priority):** Compare the WTW zone width to the BTW zone width. If the ratio > 1.5, the wick-based zone is disproportionately wide → use BTW. If ratio <= 1.5, the wicks are reasonably close to bodies → use WTW.

**P1 overrides P2:** An explosive legout takes priority over a doji in the base.

**Tests:** 11 tests covering each priority, supply-side WTW, gap between legout candles, P1 overriding P2, M2+M13 combined.

---

### M17 — Missing-Base Zones (Instant Reversal)

**Rule:** When two consecutive exciting candles fire in opposite directions with no boring base candles between them, it forms an instant-reversal zone:
- Bearish exciting → Bullish exciting = DBR demand (no base)
- Bullish exciting → Bearish exciting = RBD supply (no base)

The "turning point" (the first candle) defines both the proximal and distal boundaries.

**Validation:** At least one candle in the extended legout must clear the turning point's range — a weak reversal that doesn't clear is rejected.

**Same-direction check:** Two consecutive exciting candles in the SAME direction do not form a zone (no reversal).

**Scoring:** 0 base candles → maximum time-at-base score (2 points). M2 exceptional distal also applies.

**Tests:** 13 tests — DBR demand, RBD supply, scoring, no double-counting, legout must clear, multi-candle legout clears, legin extends, legout extends, same direction rejected, M2 exceptional, continuation rejected, gap in legout, first-legout-clears-but-last-does-not (ANY-candle fix).

---

### M46 — Close-Based Zone Invalidation

**Rule:** A zone is invalidated (permanently destroyed) only when price CLOSES strictly beyond the distal line:
- Demand zone: close < distal invalidates
- Supply zone: close > distal invalidates
- Wick through distal: zone SURVIVES (wick is stop-hunt noise)
- Close exactly AT distal: zone SURVIVES (strict inequality required)

**Rationale:** Institutional zones are defended at the close, not the wick. Intraday wick excursions beyond the distal are often stop-hunts that trap retail traders.

**Tests:** 5 tests — demand wick survives, demand close invalidates, supply wick survives, supply close invalidates, close exactly at distal survives.

---

### M8 — Closing Concept (Legout Quality)

**Rule:** Evaluates how convincingly the legout departed from the base by checking whether it CLOSED beyond the nearest opposing zone's proximal line:
- **"strong":** Legout closed beyond the opposing zone's proximal → institutions absorbed the opposing zone
- **"weak":** Legout wicked past but closed before the opposing proximal → departure unconvincing
- **"unchecked":** No opposing zone found in the legout's path → can't evaluate

**Scope:** Only checks against zones that formed BEFORE the current zone (prior zones in the scan order).

**ODD score impact:** NONE. This is a qualitative flag (`closing_quality` field) that provides additional context for trade decisions but does not change the 7-point ODD score.

**Tests:** 6 tests — demand strong, demand weak, no opposing (unchecked), supply strong, supply weak, score unchanged.

---

### Gap-as-Legout Threshold

**Rule:** A gap between consecutive base candles >= 1.3% of price terminates the base and acts as a legout departure. Gaps below 1.3% are considered bid-ask noise and the base extends normally.

The 1.3% threshold matches the exciting candle minimum body filter (`_MIN_BODY_PCT_OF_PRICE`), ensuring consistency: both gaps and candle bodies need institutional-scale conviction.

**Note:** The separate `_has_gap` check used for strength scoring (2 points) has NO minimum threshold — it detects any gap between base-end and legout candles. This is by design: the gap-as-legout scanner decides if a gap IS a legout; `_has_gap` for scoring just checks if a gap EXISTS in the departure.

**Tests:** 7 tests including noise gap ignored, real gap triggers, gap with exciting candle after, gap legout has_gap=True.

---

## Phase 1 — Pending Rules

### M10 — Garbage-Area Rejection

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Zones where the legout barely clears the base range are considered "garbage areas" — the departure was unconvincing, suggesting weak institutional conviction. These zones should be rejected or penalized.

**Possible implementation:** A minimum clearance threshold (e.g., the legout close must exceed the base range by some percentage) beyond the current binary "legout clears base" check.

**Status:** No code or tests. Not yet started.

---

### M12 — Narrow Base Width as Quality Metric

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** The physical price-range width of the base (how tight the consolidation was) serves as a quality signal. A narrower base indicates tighter institutional control and therefore a higher-quality zone.

**Possible implementation:** Measure the base's price range (highest high - lowest low of base candles) relative to the stock's price or ATR, and use it as a scoring factor or filter.

**Status:** No code or tests. Not yet started.

---

### M65/M66 — LOTL Merge & Achievement Weighting

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** "LOTL" = Legs of the Leg — a concept where price moves contain nested sub-structures. M65 merges nearby same-type zones (multiple demand zones at similar price levels consolidated into one representative zone). M66 weights zones based on their position within the larger move structure ("achievement" levels).

**Possible implementation:** M65 may use the existing `_merge_overlapping_zones()` function in `filters.py` (currently defined but not called). M66 may add a multiplier or bonus to the ODD score based on where a zone sits relative to the overall price structure.

**Status:** No code or tests. `_merge_overlapping_zones()` exists in `filters.py` but is not wired into the pipeline. Not yet started.

---

## Phase 2 — Trade Computation (All Pending)

### M1 — Entry / Stop-Loss / Target at 2R

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Compute concrete trade parameters: entry price at the zone's proximal, stop-loss at the distal, and target at 2× the risk (2R). This turns zone detection into actionable trade setups.

### M7 — Volatility-Based Buffer for Entry/SL

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Add a volatility-based buffer (e.g., ATR-based) to the entry and stop-loss levels to account for normal price fluctuation around zone boundaries.

### M29 — Entry Types 1/2/3 Mechanics

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Define the three entry types referenced in the scoring system:
- Type 1 (aggressive): Enter at the proximal on first touch
- Type 2 (confirmation): Wait for a confirming candle pattern at the proximal
- Type 3 (conservative): Wait for price to enter, exit, and re-enter the zone

Currently the entry_recommendation text references these types but no mechanical implementation exists.

### M50 — Refine Wide HTF Zone

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** When a higher-timeframe zone is too wide for precise entry, use a lower timeframe to find a narrower execution zone within it.

---

## Phase 3 — Multi-Timeframe Analysis (All Pending)

### M18/M23 — Three-Timeframe Architecture

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Analyze demand/supply zones across three timeframes simultaneously (e.g., Weekly → Daily → 75-minute) using a timeframe matrix. Higher timeframes provide context; lower timeframes provide entry precision.

### M19/M20/M21 — Curve, Location & Trend Gating

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** M19: Divide a retracement into thirds (proximal/middle/distal) for location quality. M20: Interpret zone quality based on where in the larger curve it sits. M21: Only trade zones when the higher timeframe trend supports the direction.

### M22/M27 — HTF Priority

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Higher timeframe zones take priority. A zone identified on the HTF but with no executable LTF zone inside it = "location without execution" = no trade.

### M25/M26 — Score-Then-Context Tie-Breaking

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** When multiple zones compete, first rank by ODD score, then by HTF support as tiebreaker.

### M34-M39 — Variable Aggression & Advanced Filters

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** A suite of advanced multi-timeframe rules: variable aggression based on conviction, coinciding zones across timeframes, opposing HTF zone blocking, and sub-zone refinement.

### M48/M49/M51 — Counter-Trend & Break Rules

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** M48: Counter-trend trades require extra confirmation. M49: A break of a zone can validate a new zone in the opposite direction. M51: Distinguishing between one break vs two breaks of a level.

---

## Phase 4 — Structural Trend Analysis (All Pending)

### M45 — Zone-Violation Trend

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Track how many times zones at a given level have been violated to determine structural trend: one breach = sideways, two confirmed breaches = trend reversal.

### M46 (Extended) — Close-Beyond-Distal in Structural Context

The basic M46 close-based invalidation is implemented (see Phase 1). The structural/trend interpretation of zone violations (M45) is pending.

---

## Phase 5 — Confluence Enhancers (Partially Implemented)

### EMA 20 Confluence (Implemented)

**Rule:** When the 20-period EMA sits inside a zone or within 2% of its boundaries, the zone gains a "high probability" confluence flag (`ema20_enhancer=True`). This is purely additive context — it never changes the ODD score.

**Implementation:** `analysis/zone_engine/enhancers.py`

### Fibonacci Retracement Confluence (Implemented, Opt-In)

**Rule:** When enabled via the "Enhance with Fibonacci Confluence" checkbox:
1. Find the most recent significant swing (highest high + lowest low within 120 candles)
2. Compute retracement levels at 0.382, 0.5, 0.618 (golden ratio), 0.786
3. Check each zone for confluence with these levels (inside or within 1%)
4. Rate combined confluence: EMA20 (+1) + Fib levels (+1 each, max 2) + golden ratio bonus (+1)
5. Labels: None (0), Moderate (1-2), High (3+)

This is a SEPARATE scorecard from ODD — it never modifies `odd_score`.

**Implementation:** `analysis/zone_engine/fibonacci.py` + `scoring.py:confluence_rating()`

### M42-M44 — EMA 20 Dynamic S/R (Pending)

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Extend EMA 20 from a simple confluence check to dynamic support/resistance: M42 treats EMA20 as a dynamic S/R level, M43 only applies in trending markets, M44 extends across multiple timeframes.

### M40/M41 — MA Crossover as Enhancer (Pending)

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Use moving average crossovers (golden cross / death cross) as a +1 demand/supply enhancer. The Trend Following strategy exists as a standalone analysis module but is not yet integrated as an enhancer into the demand/supply pipeline.

### M59-M64 — Gap Theory (Pending)

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Classify price gaps (common, breakaway, runaway, exhaustion) and apply three trading applications based on gap type.

### M70/M71 — RSI/Stochastic & Divergence (Pending)

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** RSI and Stochastic oscillator confluence, plus divergence detection. The RSI enhancer checkbox exists in the sidebar but is currently inert (no analysis logic).

### M67 — Bull/Bear Trap Detection (Pending)

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Detect false breakouts (bull traps above supply zones, bear traps below demand zones) as high-probability trade setups.

### M57/M58 — Candlestick Pattern Detectors — DO NOT BUILD

Per user directive: conventional candlestick patterns (hammers, engulfing, etc.) are explicitly excluded from the GTF methodology implementation.

### M69 — Conventional Pattern Detectors — DO NOT BUILD

Per user directive: chart patterns (head & shoulders, cup & handle, channels, etc.) are explicitly excluded.

---

## Phase 6 — Risk & Trade Management (All Pending)

### M31 — Position Sizing

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Calculate position quantity from fixed risk amount divided by risk-per-share (entry price minus stop-loss price).

### M32/M33 — R:R Enforcement & Stop Discipline

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Enforce minimum 2:1 reward-to-risk ratio. No averaging down on losing positions. Strict stop-loss discipline — exit immediately when stop is hit.

### M72/M73 — Structural Trailing Stop

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Trail stop-loss based on zone structure (not fixed percentage). Hold through clean upside / all-time-high moves.

### M74 — Hit-to-Hit Stop Discipline

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Move stop-loss from one structural level to the next as price achieves targets.

---

## Phase 7 — Sector / Market Context (All Pending)

### M52-M54 — Index → Sector → Peer Confluence

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Analyze zones at three levels — market index, sector index, individual stock — and weight trades where all three levels align.

### M55 — Sector Support as +2 Enhancer

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** When a stock's zone is supported by a zone at the sector level, add a +2 enhancer bonus.

### M56 — Investing vs Trading Application

`[ASSUMPTION — needs confirmation]`

**Expected behavior:** Different rule application for investing (long-term holds) vs trading (short-term positions).

---

## Phase 8 — Options (Non-GTF, All Pending)

Options-specific logic is planned as a separate module. The user has not yet provided the source documents for this phase. It is NOT part of the GTF course material.

---

## Display & Filtering Rules (Implemented)

These are not M-numbered rules but are essential to how zones are presented:

### Freshness Filter
Zones tested 2+ times are dropped from display. Only fresh (0 tests) and once-tested (1 test) zones are shown.

### Score Filter
Zones scoring below 5.0 on the ODD score are dropped from display (mirrors the "No Trade" entry recommendation threshold).

### Nearest-N Filter
At most 3 demand zones below current price and 3 supply zones above are displayed, sorted by proximity to the current price.

### Trend Alignment Safety
- Demand zones are tradeable only when the overall trend is UP
- Supply zones are tradeable only when the overall trend is DOWN
- In a SIDEWAYS market, no zone is considered tradeable

### Today's Candle Drop
During market hours (before 4 PM IST), the most recent candle is excluded from zone detection because its OHLC values are still changing. The live price is still used for display and proximity calculations.

---

## Implementation Status Summary

**Done (9 rules + display rules):**
M2, M3, M5, M8, M13, M17, M28, M46, Gap-as-legout threshold, EMA20 confluence, Fibonacci confluence, display filtering, trend alignment

**Next up (Phase 1 remaining):**
M10, M12, M65/M66

**Future (Phases 2-8):**
M1, M7, M18-M27, M29, M31-M45, M48-M56, M59-M64, M67, M70-M74, Options module

**Do not build:**
M57/M58 (candlestick patterns), M69 (conventional chart patterns)
