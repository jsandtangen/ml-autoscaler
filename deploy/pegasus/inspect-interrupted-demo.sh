#!/usr/bin/env bash
set -euo pipefail
# Read only. Run FIRST on management after the VPN/SSH interruption.
cd /opt/ruby-acorn
source /home/ubuntu/pegasus-openrc.sh
.venv/bin/python - <<'PY'
import json
from pathlib import Path
from scaler.infrastructure.pegasus_controller import PegasusConfig, connect
config=PegasusConfig.load('deploy/pegasus/workers.json')
connection=connect(config)
print(json.dumps({'authenticated_project': connection.current_project_id,
    'servers': [{'id':s.id,'name':s.name,'status':s.status,'task_state':s.task_state}
                for s in connection.compute.servers(details=True)]},indent=2))
for name in ('demo-dry-run.json','demo-live.json'):
    path=Path('/var/lib/ruby-acorn')/name
    print(name+': '+(path.read_text() if path.exists() else 'ABSENT'))
for path in Path('/var/lib/ruby-acorn/pegasus').rglob('uncertain-create.json'):
    print(str(path)+': '+path.read_text())
PY
systemctl show ruby-acorn.service -p LoadState -p ActiveState -p MainPID || true
echo 'Read-only inspection complete. Do not repeat a create until receipts and cloud state are reconciled.'
