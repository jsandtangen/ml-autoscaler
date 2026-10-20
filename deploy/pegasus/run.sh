#!/usr/bin/env bash
set -euo pipefail
# Existing guest credentials stay in their original protected location.
source /home/ubuntu/pegasus-openrc.sh
cd /opt/ruby-acorn
exec /opt/ruby-acorn/.venv/bin/python main.py \
  --controller pegasus --pegasus-config /opt/ruby-acorn/deploy/pegasus/workers.json \
  --games-config /opt/ruby-acorn/deploy/pegasus/games.toml \
  --pegasus-apply --prometheus-url http://192.168.47.129:9090 \
  --label job=games --interval 30 --max-sample-age-seconds 120 \
  --minimum-instances 0 --max-instances 1 --max-scaling-change 1 \
  --player-share 0.0001 --players-per-instance 100 \
  --downscale-seconds 180 --fixed-baseline-instances 1 \
  --metrics-host 192.168.45.220 --metrics-port 8001 \
  --player-history-path /var/lib/ruby-acorn/players.csv
