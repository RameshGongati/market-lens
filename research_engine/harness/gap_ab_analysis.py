"""A/B analysis: gap vs prior HIGH (production rule) against gap vs prior
CLOSE (superset variant), plus the MARGINAL cohort (clears the close but not
the high). Read-only over the experiment parquets; changes nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

OUT = REPO_ROOT / "research_engine" / "output"

WINDOWS = {"exp1insample": "2025-26 (in-sample yr)", "exp1oos": "2024-25 (OOS yr)"}


def stats(g: pd.DataFrame) -> dict:
    r = g["r_multiple"].clip(-3, 10)
    pos = r[r > 0].sum()
    neg = -r[r <= 0].sum()
    per_sym = g.groupby("symbol")["r_multiple"].agg(["count", "mean"])
    per_sym = per_sym[per_sym["count"] >= 3]
    return {
        "n": len(g),
        "expectancy_r": round(r.mean(), 3),
        "win_2r_pct": round(g["hit_2r"].mean() * 100, 1),
        "stop_hit_pct": round((g["result"] == "loss").mean() * 100, 1),
        "profit_factor": round(pos / neg, 2) if neg else np.inf,
        "pct_symbols_positive": round((per_sym["mean"] > 0).mean() * 100, 1) if len(per_sym) >= 5 else None,
        "n_symbols_3plus": int(len(per_sym)),
    }


def main() -> None:
    rows = []
    for scope, wlabel in WINDOWS.items():
        for tf in ("daily", "weekly"):
            path = OUT / f"trades_{tf}_{scope}.parquet"
            if not path.exists():
                continue
            d = pd.read_parquet(path)
            high = d[d.setup_name == "gap_up_go"]
            close_all = d[d.setup_name == "gap_up_close_go"]
            marginal = close_all[~close_all["tag_also_high_gap"].fillna(False).astype(bool)]
            overlap = close_all[close_all["tag_also_high_gap"].fillna(False).astype(bool)]
            for name, g in [("A: gap vs prior HIGH (production)", high),
                            ("B: gap vs prior CLOSE (all)", close_all),
                            ("B-only MARGINAL (close yes, high no)", marginal),
                            ("overlap sanity (close & high)", overlap)]:
                if len(g) < 10:
                    continue
                rows.append({"window": wlabel, "timeframe": tf, "cohort": name, **stats(g)})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "gap_definition_ab.csv", index=False)
    pd.set_option("display.width", 220)
    for tf in ("daily", "weekly"):
        sub = df[df.timeframe == tf]
        if len(sub):
            print(f"===== {tf.upper()} =====")
            print(sub.drop(columns=["timeframe"]).to_string(index=False))
            print()


if __name__ == "__main__":
    main()
