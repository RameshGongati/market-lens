"""Gap-Up Continuation signals — the first rule graduated from the Research
Engine (twice-validated: +0.41R in-sample, +0.34R out-of-sample, daily bars).

This module is the SINGLE SOURCE for the rule: the production Signals page
scans with it, and the research harness imports it for backtests, so scanner
and backtest can never drift apart.

Rule (must stay byte-identical to the validated definition — change only with
a fresh validation run):
  signal bar i : Open[i] > High[i-1] * (1 + GAP_MIN_PCT)   -- strict >
                 AND Close[i] >= Open[i]                    -- close holds
  stop         : Low[i-1] - ATR_BUFFER * ATR14[i]           -- Wilder ATR
  entry        : next bar's open (the backtest entered there)
  target       : 2R primary
  dedupe       : a signal within DEDUPE_BARS-1 bars of the previous one on the
                 same frame is suppressed (matches the harness's 3-bar rule)
  guards       : risk below MIN_RISK_FRAC or above MAX_RISK_FRAC of the entry
                 reference is not tradeable (the backtest simulator's guard)

Isolation: pure functions over OHLC frames. No zone-engine imports, no Zone
objects, nothing here can touch GTF/ODD scoring. Zone context arrives only as
an already-computed analysis result dict (read-only), like the Pattern
Scanner's zone_context.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Rule constants — validated values; see module docstring before changing.
GAP_MIN_PCT = 0.013
ATR_PERIOD = 14
ATR_BUFFER = 0.1
DEDUPE_BARS = 3
MIN_RISK_FRAC = 0.001
MAX_RISK_FRAC = 0.12

# Evidence rank (a SORTING AID, not predictive confidence): base for the
# twice-validated rule, plus the two boosters the research measured as
# additive. T9/T13 are informational tags only and deliberately score nothing.
EVIDENCE_BASE = 70
EVIDENCE_ZONE_BONUS = 10
EVIDENCE_VOLUME_BONUS = 5

# Index underlyings are excluded as a PRODUCT decision (the backtest included
# them; its per-instrument table showed indices were the study's worst
# instruments). Not part of the validated rule itself.
EXCLUDED_SYMBOLS = {"NIFTY50", "BANKNIFTY", "^NSEI", "^NSEBANK"}


@dataclass
class GapSignal:
    i: int                      # positional index of the signal bar
    date: pd.Timestamp
    gap_pct: float              # percent over prior high
    open: float
    close: float
    prior_low: float
    atr: float
    stop: float
    tags: dict = field(default_factory=dict)

    @property
    def risk_pct_vs_close(self) -> float:
        """Risk as % of the close (entry reference until next open exists)."""
        return (self.close - self.stop) / self.close * 100

    def target_2r(self, entry_ref: float | None = None) -> float:
        e = self.close if entry_ref is None else entry_ref
        return round(e + 2 * (e - self.stop), 2)


def wilder_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """ATR exactly as the harness computes it (ewm alpha=1/period, adjust=False)."""
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def detect_gap_up_continuation(df: pd.DataFrame, atr: pd.Series | None = None,
                               dedupe_bars: int = DEDUPE_BARS) -> list[GapSignal]:
    """All confirmed gap-up continuation signals in an OHLC(V) frame.

    The frame must contain COMPLETED bars only (callers drop the forming bar,
    exactly as every other scanner in the app does).
    """
    if df is None or len(df) < ATR_PERIOD + 2:
        return []
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    atr_v = (atr if atr is not None else wilder_atr(df)).to_numpy(float)

    out: list[GapSignal] = []
    last_i = -10**9
    for i in range(1, len(df)):
        if not np.isfinite(atr_v[i]) or atr_v[i] <= 0:
            continue
        if not (o[i] > h[i - 1] * (1 + GAP_MIN_PCT) and c[i] >= o[i]):
            continue
        if i - last_i < dedupe_bars:
            continue
        last_i = i
        out.append(GapSignal(
            i=i, date=df.index[i],
            gap_pct=round((o[i] / h[i - 1] - 1) * 100, 2),
            open=float(o[i]), close=float(c[i]),
            prior_low=float(l[i - 1]), atr=float(atr_v[i]),
            stop=float(l[i - 1] - ATR_BUFFER * atr_v[i]),
        ))
    return out


def risk_ok(sig: GapSignal, entry_ref: float | None = None) -> bool:
    """The backtest simulator's tradeability guard (0.1%..12% of entry)."""
    e = sig.close if entry_ref is None else entry_ref
    risk = e - sig.stop
    return bool(np.isfinite(risk) and MIN_RISK_FRAC * e < risk < MAX_RISK_FRAC * e)


def latest_confirmed_signal(df: pd.DataFrame) -> GapSignal | None:
    """The signal on the LAST completed bar, if that bar is one."""
    sigs = detect_gap_up_continuation(df)
    if sigs and sigs[-1].i == len(df) - 1:
        return sigs[-1]
    return None


# ------------------------------------------------------------------ tracking
TIME_STOP_BARS = 20   # the backtested daily time stop

STATUS_AWAITING = "awaiting_entry"
STATUS_ACTIVE = "active"
STATUS_TARGET = "target_hit"
STATUS_STOP = "stop_loss_hit"
STATUS_TIME = "time_stopped"


@dataclass
class TrackedSignal:
    """A gap signal walked forward under the EXACT backtest trade rules:
    entry next open, stop-loss checked before target on every bar (a bar that
    touches both counts as a stop-loss hit), 2R primary target, time stop at
    TIME_STOP_BARS after entry."""
    signal: GapSignal
    status: str
    entry_i: int | None = None
    entry_date: pd.Timestamp | None = None
    entry_price: float | None = None
    exit_i: int | None = None
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    target: float | None = None
    days_active: int | None = None
    r_multiple: float | None = None       # realized (resolved) or unrealized (active)


def track_signal(df: pd.DataFrame, sig: GapSignal) -> TrackedSignal | None:
    """Walk a signal forward on completed bars. Returns None when the trade
    would have been skipped by the backtest's risk guard (not tradeable)."""
    n = len(df)
    entry_i = sig.i + 1
    if entry_i >= n:
        # entry is the NEXT session's open, which does not exist yet
        return TrackedSignal(signal=sig, status=STATUS_AWAITING)
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    entry = float(o[entry_i])
    risk = entry - sig.stop
    if not (np.isfinite(risk) and MIN_RISK_FRAC * entry < risk < MAX_RISK_FRAC * entry):
        return None   # the backtest skipped this trade; the tracker must too
    target = entry + 2 * risk

    base = dict(signal=sig, entry_i=entry_i, entry_date=df.index[entry_i],
                entry_price=round(entry, 2), target=round(target, 2))
    last_j = min(entry_i + TIME_STOP_BARS, n - 1)
    for j in range(entry_i, last_j + 1):
        if l[j] <= sig.stop:              # stop-loss first: both-touched = loss
            return TrackedSignal(status=STATUS_STOP, exit_i=j, exit_date=df.index[j],
                                 exit_price=round(sig.stop, 2),
                                 days_active=j - entry_i,
                                 r_multiple=-1.0, **base)
        if h[j] >= target:
            return TrackedSignal(status=STATUS_TARGET, exit_i=j, exit_date=df.index[j],
                                 exit_price=round(target, 2),
                                 days_active=j - entry_i,
                                 r_multiple=2.0, **base)
    if last_j == entry_i + TIME_STOP_BARS:
        exit_price = float(c[last_j])
        return TrackedSignal(status=STATUS_TIME, exit_i=last_j, exit_date=df.index[last_j],
                             exit_price=round(exit_price, 2),
                             days_active=last_j - entry_i,
                             r_multiple=round((exit_price - entry) / risk, 2), **base)
    return TrackedSignal(status=STATUS_ACTIVE,
                         days_active=(n - 1) - entry_i,
                         r_multiple=round((float(c[n - 1]) - entry) / risk, 2), **base)


# ------------------------------------------------------------------ context
def zone_context_tag(analysis_result: dict | None, close: float) -> bool:
    """Read-only: did this gap leave from / near a demand zone?

    Consumes an already-computed Demand/Supply analysis result dict (never a
    Zone object). True when the nearest demand zone's proximal sits within 3%
    below the close, or price is inside the zone band.
    """
    if not analysis_result:
        return False
    zone = analysis_result.get("nearest_demand")
    if not zone:
        return False
    proximal = zone.get("proximal") or zone.get("top")
    distal = zone.get("distal") or zone.get("bottom")
    if not proximal or proximal <= 0:
        return False
    if distal and min(proximal, distal) <= close <= max(proximal, distal):
        return True
    return 0 <= (close - proximal) / proximal <= 0.03


def volume_expansion_tag(df: pd.DataFrame, i: int) -> bool:
    if "Volume" not in df.columns or i < 20:
        return False
    v = df["Volume"].to_numpy(float)
    ma20 = np.nanmean(v[i - 20:i])
    return bool(np.isfinite(ma20) and ma20 > 0 and v[i] >= 1.5 * ma20)


def evidence_rank(from_zone: bool, volume_ok: bool) -> int:
    return EVIDENCE_BASE + (EVIDENCE_ZONE_BONUS if from_zone else 0) + \
        (EVIDENCE_VOLUME_BONUS if volume_ok else 0)
