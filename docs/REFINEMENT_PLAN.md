# Market Lens — Refinement Plan

Prioritized implementation roadmap derived from the cross-check of the master requirements document against the codebase (2026-07-18). Items are ordered by dependency and phase.

---

## Immediate — Close Phase 1 Gaps

### Resolved

- ~~M3 Prolonged Habitation~~ — Deemed unnecessary (2026-07-20). Enter+exit cycle counting correctly handles all real-world cases (e.g., NHPC consecutive daily tests). No code change needed.
- ~~M3 Same-Bar Enter+Exit~~ — Fixed (`7c96f2c`, 2026-07-20). A candle that enters AND exits the zone on the same bar now counts as one complete test. Discovered via SBILIFE DBR zone (Jul 16 candle). Entry and exit checks are separate `if` blocks instead of `if/elif`.
- ~~M46 Distal Wick Breaches~~ — Resolved by changing M46 to wick-based invalidation (`458ba6c`, 2026-07-20). Any wick past distal now destroys the zone, so a "wick breaches" counter is unnecessary.

### Remaining

### 1. M17 Docstring Fix

**Gap:** The docstring at `patterns.py:238` says "body bottom of legout" for demand proximal, but the code computes `min(body_top_tp, body_top_legout)` (the more conservative of the two body tops).

**What to fix:**
```python
# Current (wrong):
#   * DEMAND: proximal = body bottom of legout, distal = lowest low of both
#   * SUPPLY: proximal = body top of legout, distal = highest high of both

# Correct:
#   * DEMAND: proximal = min(body_top_tp, body_top_legout), distal = lowest low of both
#   * SUPPLY: proximal = max(body_bottom_tp, body_bottom_legout), distal = highest high of both
```

**Files:** `analysis/zone_engine/patterns.py` (docstring only, no logic change)

---

## Phase 1 Remaining — New Rules

### 2. M10 — Garbage-Area Rejection

**Priority:** High (filters out low-quality zones before they reach the UI)

**Formula:**
```
achievement_ratio = (legout_extreme - proximal) / (proximal - distal)
```

**Tiers:**
- `< 0.5` = hard reject (discard zone)
- `0.5 - 1.0` = "Weak Departure" flag (keep but flag)
- `>= 1.0` = clean

**Guard:** Skip ratio check for missing-base zones (near-zero denominator).

**Files:** New function in `analysis/zone_engine/scoring.py` or `patterns.py`. Zone model needs `achievement_ratio: float` and possibly `departure_quality: str` fields.

### 3. M12 — Narrow Base Width

**Priority:** Medium (information-only initially)

**Formula:**
```
base_width_pct = (base_high - base_low) / price * 100
```

**Scope:** Add to Zone model as `base_width_pct: float`. Display in zone detail panel. Do NOT use as score modifier initially.

**Files:** `analysis/zone_engine/models.py`, `analysis/zone_engine/patterns.py` (compute during detection), `ui/components/stock_detail.py` (display)

### 4. M65/M66 — LOTL Merge + Achievement

**Priority:** Medium (depends on M8 being fully stable)

**Starting point:** `_merge_overlapping_zones()` exists in `filters.py:78-108` but is not called.

**Steps:**
1. Wire `_merge_overlapping_zones()` into `filter_zones()` pipeline (after score filter, before nearest-N)
2. Verify merge logic: combined proximal = nearest-to-price, combined distal = most extreme
3. Add M66: track which sub-zone had better M8 achievement, inherit best
4. Start with overlap-only merge (no proximity-based merge)

**Files:** `analysis/zone_engine/filters.py`, `analysis/zone_engine/models.py` (if merged zone needs new fields)

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
- **M57/M58** — Candlestick pattern detectors (excluded by directive)
- **M69** — Conventional chart pattern detectors (excluded by directive)

---

## Implementation Notes

- **No Python code changes** were made during this cross-check. This plan is documentation only.
- **Stage separation must be preserved:** Stage 1 fields (especially `odd_score`) are never modified by Stage 2/3 enrichment.
- **Test-first approach:** Each new rule needs hand-crafted OHLC tests with inline arithmetic comments, covering both demand and supply sides, plus boundary values.
- **Dataclass immutability:** Use `dataclasses.replace()` for zone enrichment, not field mutation.
