"""Trade simulator: fixed rules, conservative fills, full metric capture.

Rules (stated in the report):
  entry      : next bar's OPEN after the signal bar closes — except zone
               limit-touch entries, which fill AT the proximal on the touch bar
  stop       : setup-specific (see detectors), always ATR-buffered
  target     : 2R primary; 1R/3R hits tracked from excursion for hit-rate stats
  time stop  : bars per timeframe below — exit at close if neither side hit
  same-bar   : if a bar touches BOTH stop and target, it counts as a LOSS
               (conservative; intrabar order is unknowable from OHLC)
  costs      : 0.1% of entry per round trip (brokerage + slippage), subtracted
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_engine.harness.detectors import Signal

TIME_STOP = {"daily": 20, "weekly": 8, "60m": 35, "75m": 30, "15m": 75}
COST_FRAC = 0.001          # round-trip cost as fraction of entry
MIN_RISK_FRAC = 0.001      # skip degenerate stops (<0.1% of entry)
MAX_RISK_FRAC = 0.12       # skip absurd stops (>12% of entry)


def simulate(df: pd.DataFrame, sigs: list[Signal], tf: str) -> list[dict]:
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    idx = df.index
    n = len(df)
    max_hold = TIME_STOP[tf]
    rows: list[dict] = []

    for s in sigs:
        d = s.direction
        if s.entry_at_signal_bar:
            e_i, entry = s.i, float(s.entry_price)
        else:
            e_i = s.i + 1
            if e_i >= n:
                continue
            entry = o[e_i]
        risk = (entry - s.stop) * d
        if not np.isfinite(risk) or risk <= 0:
            continue
        if risk < MIN_RISK_FRAC * entry or risk > MAX_RISK_FRAC * entry:
            continue
        t1 = entry + 1 * risk * d
        t2 = entry + 2 * risk * d
        t3 = entry + 3 * risk * d

        hit1 = hit2 = hit3 = False
        stopped = False
        exit_price = None
        exit_i = None
        mfe = 0.0
        mae = 0.0
        breakout_level = s.tags.get("breakout_level")
        false_break = False

        last_j = min(e_i + max_hold, n - 1)
        for j in range(e_i, last_j + 1):
            # Limit-touch entries fill mid-bar: the entry bar's high may have
            # printed BEFORE the fill, so credit nothing favorable on that bar
            # (stop-outs still count — conservative).
            entry_bar_blind = s.entry_at_signal_bar and j == e_i
            if not entry_bar_blind:
                fav = (h[j] - entry) * d if d == 1 else (entry - l[j])
                adv = (entry - l[j]) * d if d == 1 else (h[j] - entry)
                mfe = max(mfe, fav / risk)
                mae = max(mae, adv / risk)
            stop_touch = (l[j] <= s.stop) if d == 1 else (h[j] >= s.stop)
            if stop_touch:
                stopped = True
                exit_price, exit_i = s.stop, j
                break
            if entry_bar_blind:
                continue
            if not hit1 and ((h[j] >= t1) if d == 1 else (l[j] <= t1)):
                hit1 = True
            if not hit2 and ((h[j] >= t2) if d == 1 else (l[j] <= t2)):
                hit2 = True
            if not hit3 and ((h[j] >= t3) if d == 1 else (l[j] <= t3)):
                hit3 = True
            if s.tags.get("is_breakout") and breakout_level and not hit1 and j <= e_i + 3:
                if (c[j] < breakout_level) if d == 1 else (c[j] > breakout_level):
                    false_break = True
            if hit2:
                exit_price, exit_i = t2, j
                break
        if exit_price is None:
            exit_i = last_j
            exit_price = c[exit_i]

        cost = COST_FRAC * entry
        pnl = (exit_price - entry) * d - cost
        realized_r = pnl / risk
        result = "win" if (hit2 and not stopped) else ("loss" if stopped else "timeout")

        rows.append({
            "signal_date": idx[s.i],
            "entry_date": idx[e_i],
            "exit_date": idx[exit_i],
            "setup_name": s.setup,
            "bullish_or_bearish": "bullish" if d == 1 else "bearish",
            "entry_price": round(entry, 2),
            "stop_loss": round(float(s.stop), 2),
            "target_1": round(t1, 2),
            "target_2": round(t2, 2),
            "target_3": round(t3, 2),
            "exit_price": round(float(exit_price), 2),
            "result": result,
            "win_loss": "win" if realized_r > 0 else "loss",
            "r_multiple": round(realized_r, 3),
            "return_pct": round(pnl / entry * 100, 3),
            "risk_pct": round(risk / entry * 100, 3),
            "max_profit_percent": round(mfe * risk / entry * 100, 3),
            "max_loss_percent": round(mae * risk / entry * 100, 3),
            "mfe_r": round(mfe, 3),
            "mae_r": round(mae, 3),
            "hit_1r": hit1 or (hit2 or hit3),
            "hit_2r": hit2 or hit3,
            "hit_3r": hit3,
            "false_breakout": false_break,
            "holding_period": exit_i - e_i,
            "signal_i": s.i,
            **{f"tag_{k}": v for k, v in s.tags.items()},
        })
    return rows
