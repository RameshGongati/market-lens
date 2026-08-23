#!/bin/bash
# A/B experiment: gap vs prior HIGH (production rule) against gap vs prior
# CLOSE (superset variant), daily + weekly, run over BOTH test years so the
# variant faces the same two-year bar the original passed.
set -e
cd /home/gongati/projects/market-lens
source venv/bin/activate

SHARDS="0:26 26:52 52:78 78:104 104:130 130:156 156:182 182:210"

run_window () {
  local SCOPE=$1 START=$2 END=$3
  export RE_TEST_START="$START"
  export RE_TEST_END="$END"
  for TF in daily weekly; do
    echo "=== $SCOPE $TF ($START..$END) ==="
    rm -f research_engine/output/trades_${TF}_${SCOPE}_shard*.parquet research_engine/output/trades_${TF}_${SCOPE}.parquet
    for r in $SHARDS; do
      python research_engine/harness/run_backtest.py "$TF" "$SCOPE" "$r" > "/tmp/ab_${SCOPE}_${TF}_${r/:/_}.log" 2>&1 &
    done
    wait
    python - "$TF" "$SCOPE" <<'EOF'
import glob, os, sys
import pandas as pd
tf, scope = sys.argv[1], sys.argv[2]
files = sorted(glob.glob(f"research_engine/output/trades_{tf}_{scope}_shard*.parquet"))
assert len(files) == 8, f"{tf} {scope}: only {len(files)} shards"
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df.to_parquet(f"research_engine/output/trades_{tf}_{scope}.parquet")
print(f"{scope} {tf}: {len(df)} trades")
for f in files:
    os.remove(f)
EOF
  done
}

run_window exp1insample "2025-08-11" "2026-08-11"
run_window exp1oos "2024-08-11" "2025-08-10"
echo AB_RUN_DONE
