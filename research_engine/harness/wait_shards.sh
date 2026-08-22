#!/bin/bash
cd /home/gongati/projects/market-lens
for i in $(seq 1 40); do
  n=$(ls research_engine/output/trades_15m_all_shard*.parquet 2>/dev/null | wc -l)
  alive=$(pgrep -f 'run_backtest.py 15m' | wc -l)
  echo "check $i: shards_done=$n alive=$alive"
  if [ "$n" -eq 8 ]; then echo ALL_DONE; break; fi
  if [ "$alive" -eq 0 ] && [ "$n" -lt 8 ]; then
    echo PROCESSES_DIED_EARLY
    tail -5 /tmp/shard_0_26.log
    break
  fi
  sleep 15
done
