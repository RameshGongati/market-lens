#!/bin/bash
# Out-of-sample run: the year BEFORE the in-sample year, daily + weekly only
# (intraday history that old is not available from the provider).
# Signals restricted to [2024-08-11, 2025-08-10]; scope label "oos" keeps the
# outputs (trades_daily_oos.parquet, trades_weekly_oos.parquet) separate from
# the in-sample trades_*_all.parquet files.
set -e
cd /home/gongati/projects/market-lens
source venv/bin/activate

export RE_TEST_START="2024-08-11"
export RE_TEST_END="2025-08-10"

SHARDS="0:26 26:52 52:78 78:104 104:130 130:156 156:182 182:210"

for TF in daily weekly; do
  echo "=== OOS $TF ==="
  rm -f research_engine/output/trades_${TF}_oos_shard*.parquet research_engine/output/trades_${TF}_oos.parquet
  for r in $SHARDS; do
    python research_engine/harness/run_backtest.py "$TF" oos "$r" > "/tmp/oos_${TF}_${r/:/_}.log" 2>&1 &
  done
  wait
  python - "$TF" <<'EOF'
import glob, os, sys
import pandas as pd
tf = sys.argv[1]
files = sorted(glob.glob(f"research_engine/output/trades_{tf}_oos_shard*.parquet"))
assert len(files) == 8, f"{tf}: only {len(files)} shards"
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df.to_parquet(f"research_engine/output/trades_{tf}_oos.parquet")
print(f"OOS {tf}: merged {len(df)} trades")
for f in files:
    os.remove(f)
EOF
done
echo OOS_RUN_DONE
