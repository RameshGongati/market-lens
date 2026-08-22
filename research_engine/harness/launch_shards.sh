#!/bin/bash
# Launch the 15m backtest as 8 parallel universe shards, then merge.
set -e
cd /home/gongati/projects/market-lens
source venv/bin/activate
rm -f research_engine/output/trades_15m_all_shard*.parquet /tmp/shard_*.log

for r in 0:26 26:52 52:78 78:104 104:130 130:156 156:182 182:210; do
  logname="/tmp/shard_${r/:/_}.log"
  python research_engine/harness/run_backtest.py 15m all "$r" > "$logname" 2>&1 &
done
wait
echo "--- all shards exited; merging ---"
python - <<'EOF'
import glob
import pandas as pd
files = sorted(glob.glob("research_engine/output/trades_15m_all_shard*.parquet"))
print("shards found:", len(files))
assert len(files) == 8, "missing shard outputs"
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df.to_parquet("research_engine/output/trades_15m_all.parquet")
print("merged 15m trades:", len(df))
import os
for f in files:
    os.remove(f)
EOF
echo MERGE_DONE
