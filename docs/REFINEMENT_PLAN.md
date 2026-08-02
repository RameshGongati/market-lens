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

### 2. M65/M66 — LOTL Merge + Achievement

**Priority:** Medium (depends on M8 being fully stable)

> **Attempted once and reverted.** A previous implementation (capping the
> merged span) was removed at the user's request — it "completely messed up
> the zone markings" on real charts. The branch was reset, so nothing of it
> survives in history. Before retrying: merging changes the drawn boundaries
> of zones the user reads daily, so validate against real charts across many
> symbols *before* committing, not just against unit tests. Widening a zone's
> proximal moves the entry level, which is the number being traded.

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

## Pending — Discussed, Not Started

### Main-Area UI Pass (PENDING)

The sidebar was redesigned in `6eec011`; the main pages were deliberately
left alone and now look unstyled beside it.

- **Page width cap** (~1400px, centred). `layout="wide"` currently lets
  dropdowns and containers stretch to the full monitor width.
- **Card treatment** for the Settings page sections, matching the sidebar.
- **`STRENGTH_BG` / `STRENGTH_COLORS`** in `analysis/base.py` are Bootstrap 4
  alert colours (`#d4edda`, `#fff3cd`, `#f8d7da`) and look dated against the
  current palette. They stay green/amber/red — they are semantic — but the
  shades want refreshing.

**Constraint:** the main canvas must stay white. `backgroundColor` in
`.streamlit/config.toml` is app-wide, so tinting it also tints the area the
Plotly charts render on, whose colours were chosen against white. Put cards
on a white canvas rather than tinting the canvas.

### Discarded Quote Fetch (PENDING — one line)

`yahoo_finance.fetch_quote` assigns `hist = ticker.history(...)` and never
reads it; every returned value comes from `fast_info`. It is a full chart API
request per quote: **260ms with it, 42ms without**, and `fetch_quote` runs
once per stock, so a 50-stock scan wastes roughly 11 seconds. Deleting the
line changes no output.

(By contrast, the unused `stock_df` import in `jugaad.connect` is
deliberate — an import probe paired with the `except ImportError` below it.
pyflakes flags it because pyflakes does not honour `# noqa`.)

---

## Completed Features (Non-GTF)

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
