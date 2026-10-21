#!/usr/bin/env bash
set -euo pipefail
# Add just the ML endpoint to the EXISTING job. Never call install-monitoring.sh.
cd /home/ubuntu/ruby-acorn-ml-monitoring
config=/home/ubuntu/prometheus/prometheus/prometheus.yml
prom=$(sudo docker ps -q --filter name=prom_prometheus.1)
test -n "$prom"
python3 - <<'PY'
from pathlib import Path
import re
path=Path('/home/ubuntu/prometheus/prometheus/prometheus.yml')
text=path.read_text()
match=re.search(r"(?m)^  - job_name: ['\"]ruby_acorn_pegasus['\"]\s*$",text)
if not match:
    raise ValueError('Expected existing ruby_acorn_pegasus job')
end=re.search(r'(?m)^  - job_name:',text[match.end():])
stop=match.end()+end.start() if end else len(text)
block=text[match.start():stop]
if '192.168.45.220:8003' not in block:
    old="['192.168.45.220:8001']"
    if block.count(old)!=1:
        raise ValueError('Unexpected existing target format; inspect before changing')
    block=block.replace(old,"['192.168.45.220:8001', '192.168.45.220:8003']")
candidate=text[:match.start()]+block+text[stop:]
Path(str(path)+'.ml-candidate').write_text(candidate)
PY
sudo docker exec "$prom" promtool check config /etc/prometheus/prometheus.yml.ml-candidate
if ! cmp -s "$config" "$config.ml-candidate"; then
  cp -p "$config" "$config.before-ml-shadow-$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$config.ml-candidate" "$config"
  sudo docker kill --signal HUP "$prom" >/dev/null
fi
cp ruby-acorn-ml-shadow.json /home/ubuntu/prometheus/grafana/provisioning/dashboards/
