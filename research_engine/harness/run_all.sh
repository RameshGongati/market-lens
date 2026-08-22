#!/bin/bash
# Re-run the whole study with the survivorship fix + uniform intraday gate.
# One timeframe at a time, 8 parallel universe shards each, merge, then
# aggregate + example charts.
set -e
cd /home/gongati/projects/market-lens
source venv/bin/activate

SHARDS="0:26 26:52 52:78 78:104 104:130 130:156 156:182 182:210"

for TF in daily weekly 60m 75m 15m; do
  echo "=== $TF ==="
  rm -f research_engine/output/trades_${TF}_all_shard*.parquet research_engine/output/trades_${TF}_all.parquet
  for r in $SHARDS; do
    python research_engine/harness/run_backtest.py "$TF" all "$r" > "/tmp/bt_${TF}_${r/:/_}.log" 2>&1 &
  done
  wait
  python - "$TF" <<'EOF'
import glob, os, sys
import pandas as pd
tf = sys.argv[1]
files = sorted(glob.glob(f"research_engine/output/trades_{tf}_all_shard*.parquet"))
assert len(files) == 8, f"{tf}: only {len(files)} shards"
df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
df.to_parquet(f"research_engine/output/trades_{tf}_all.parquet")
print(f"{tf}: merged {len(df)} trades")
for f in files:
    os.remove(f)
EOF
done

echo "=== aggregate ==="
python research_engine/harness/aggregate.py
echo "=== charts ==="
python research_engine/harness/examples.py
echo ALL_DONE
