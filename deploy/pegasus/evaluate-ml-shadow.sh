#!/usr/bin/env bash
set -euo pipefail
cd /opt/ruby-acorn-ml-shadow
exec .venv/bin/python -u -m scripts.shadow_autoscaler \
  --five-minute --evaluate-only --appid 730 --window-size 64 \
  --evaluation-origins 288 --prometheus-url http://192.168.47.129:9090 \
  --output-dir /var/lib/ruby-acorn-ml-shadow \
  --model-cache-dir /var/cache/ruby-acorn-ml-shadow \
  --player-share 0.0001 --players-per-instance 100 --headroom-fraction 0.1 \
  --minimum-instances 0 --max-instances 1 --max-scaling-change 1
