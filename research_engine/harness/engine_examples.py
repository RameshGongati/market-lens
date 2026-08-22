"""Charts for the engine report: TAKE winners, TAKE losers, AVOID traps."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.harness.examples import render_example  # noqa: E402

OUT = REPO_ROOT / "research_engine" / "output"


def main() -> None:
    d = pd.read_parquet(OUT / "trades_scored.parquet")
    d = d[d.risk_pct.between(1, 6)]
    made = []
    picks = []
    take = d[(d.final_decision == "TAKE") & (d.timeframe.isin(["daily", "weekly", "60m"]))]
    for setup in ["gap_up_go", "zone_touch_fresh", "demand_bounce", "macd_cross_long", "vcp_breakout"]:
        g = take[take.setup_name == setup]
        wins = g[g.result == "win"].sort_values("r_multiple", ascending=False)
        losses = g[g.result == "loss"]
        if len(wins):
            picks.append(("engine_take_win", wins.iloc[0]))
        if len(losses):
            picks.append(("engine_take_loss", losses.iloc[len(losses) // 2]))
    avoid = d[(d.final_decision == "AVOID") & (d.result == "loss") & (d.timeframe == "daily")]
    for _, row in avoid.sort_values("trap_probability_score", ascending=False).head(3).iterrows():
        picks.append(("engine_avoid_trap", row))
    for tag, row in picks:
        try:
            f = render_example(row, tag)
            if f:
                made.append(Path(f).name)
        except Exception as e:  # noqa: BLE001
            print("chart fail", tag, row.get("symbol"), e)
    print(f"made {len(made)}")
    for m in made:
        print(" ", m)


if __name__ == "__main__":
    main()
