"""Render worked/failed example charts for the report (mplfinance)."""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import mplfinance as mpf
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.harness import datafetch  # noqa: E402
from research_engine.harness.indicators import compute  # noqa: E402

OUT = REPO_ROOT / "research_engine" / "output"
CHARTS = OUT / "charts"

# headline setups to illustrate (winner + loser each where available)
SHOWCASE = [
    "demand_bounce", "supply_rejection", "zone_touch_fresh",
    "ema20_bounce", "bb_squeeze_breakout", "range_breakout",
    "double_top_break", "bullish_engulfing", "gap_up_go", "vcp_breakout",
    "rsi_bull_divergence", "hh_hl_continuation",
]


def render_example(row: pd.Series, tag: str) -> str | None:
    sym = row["symbol"].replace("NIFTY50", "^NSEI").replace("BANKNIFTY", "^NSEBANK")
    tf = row["timeframe"]
    df = datafetch.load(sym, tf)
    if df is None:
        return None
    df = compute(df)
    sig_ts = pd.Timestamp(row["signal_date"])
    # parquet round-trips can promote mixed tz columns to UTC; align to frame
    frame_tz = getattr(df.index, "tz", None)
    if frame_tz is None and sig_ts.tzinfo is not None:
        sig_ts = sig_ts.tz_convert("Asia/Kolkata").tz_localize(None).normalize()
    elif frame_tz is not None and sig_ts.tzinfo is None:
        sig_ts = sig_ts.tz_localize("Asia/Kolkata")
    elif frame_tz is not None:
        sig_ts = sig_ts.tz_convert(frame_tz)
    try:
        i = df.index.get_loc(sig_ts)
    except KeyError:
        pos = df.index.searchsorted(sig_ts)
        if pos >= len(df):
            return None
        i = int(pos)
    lo, hi = max(0, i - 55), min(len(df), i + 26)
    win = df.iloc[lo:hi]

    entry, stop, t2 = row["entry_price"], row["stop_loss"], row["target_2"]
    add = [
        mpf.make_addplot(win["ema20"], color="#1f77b4", width=0.9),
        mpf.make_addplot(win["sma50"], color="#ff7f0e", width=0.9),
    ]
    outcome = row["result"].upper()
    title = (f"{row['symbol']} {tf} — {row['setup_name']} ({row['bullish_or_bearish']}) "
             f"{str(sig_ts)[:10]} -> {outcome} ({row['r_multiple']:+.2f}R)")
    fname = CHARTS / f"{tag}_{row['symbol']}_{tf}_{row['setup_name']}_{str(sig_ts)[:10]}.png"
    fname.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = mpf.plot(
        win, type="candle", style="yahoo", volume=True, addplot=add,
        hlines=dict(hlines=[entry, stop, t2], colors=["#2ca02c", "#d62728", "#9467bd"],
                    linestyle=["-", "--", ":"], linewidths=[1.2, 1.2, 1.2]),
        vlines=dict(vlines=[df.index[i]], colors=["#7f7f7f"], linewidths=[0.8], alpha=0.5),
        title=title, figsize=(12, 7), returnfig=True,
    )
    axes[0].legend(["EMA20", "SMA50"], loc="upper left", fontsize=8)
    fig.savefig(fname, dpi=110, bbox_inches="tight")
    import matplotlib.pyplot as plt
    plt.close(fig)
    return str(fname)


def main() -> None:
    frames = [pd.read_parquet(p) for p in sorted(OUT.glob("trades_*_all.parquet"))]
    df = pd.concat(frames, ignore_index=True)
    made = []
    for setup in SHOWCASE:
        g = df[(df["setup_name"] == setup)]
        if g.empty:
            continue
        g = g[g["risk_pct"].between(1, 6)]
        pref = g[g["timeframe"] == "daily"]
        g = pref if len(pref) >= 4 else g
        wins = g[(g["result"] == "win")].sort_values("r_multiple", ascending=False)
        losses = g[(g["result"] == "loss")].sort_values("r_multiple")
        for tag, pool in (("worked", wins), ("failed", losses)):
            for _, row in pool.head(3).iterrows():
                try:
                    f = render_example(row, tag)
                    if f:
                        made.append(f)
                        break
                except Exception as e:
                    print("chart fail", setup, tag, e)
    print(f"made {len(made)} charts")
    for m in made:
        print(" ", Path(m).name)


if __name__ == "__main__":
    main()
