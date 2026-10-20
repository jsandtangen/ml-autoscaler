#!/usr/bin/env bash
set -euo pipefail
# Run after placing the current source in /opt/ruby-acorn. No credentials copied.
cd /opt/ruby-acorn
mode=${1:---prepare}
if [[ "$mode" != --prepare && "$mode" != --start-live ]]; then
  echo 'Usage: install-management.sh [--prepare|--start-live]' >&2
  exit 2
fi
sudo install -d -o ubuntu -g ubuntu -m 700 /var/lib/ruby-acorn
if ! test -x .venv/bin/pip; then
  sudo apt-get update -qq
  sudo apt-get install -y python3.12-venv
  python3 -m venv .venv
fi
.venv/bin/python -m pip install '.[openstack,dev]'
.venv/bin/python -m pytest -q
source /home/ubuntu/pegasus-openrc.sh
.venv/bin/python scripts/inspect_pegasus.py --config deploy/pegasus/workers.json
if [[ "$mode" == --start-live ]]; then
  .venv/bin/python - <<'PY'
import json
from pathlib import Path
report=json.loads(Path('/var/lib/ruby-acorn/demo-live.json').read_text())
if not (report.get('status')=='verified' and report.get('apply') is True
        and [s['running'] for s in report['steps']]==[1,0]
        and report['protected_before']==report['protected_after']
        and report['config']['pool']=='course'
        and report['config']['project_id']=='234f15222c5e4396851d2713697893b1'):
    raise SystemExit('A verified course-pool demo receipt is required before autonomous startup')
PY
  sudo install -m 644 deploy/pegasus/ruby-acorn.service /etc/systemd/system/
  sudo systemd-analyze verify /etc/systemd/system/ruby-acorn.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now ruby-acorn
  systemctl is-enabled ruby-acorn
  systemctl is-active ruby-acorn
fi
