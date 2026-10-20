#!/usr/bin/env bash
set -euo pipefail
# Run as ubuntu on ruby-monitoring after copying this directory to ~/ruby-acorn-monitoring.
cd /home/ubuntu/ruby-acorn-monitoring
config=/home/ubuntu/prometheus/prometheus/prometheus.yml
directory=/home/ubuntu/prometheus/prometheus
prom=$(sudo docker ps -q --filter name=prom_prometheus.1)
test -n "$prom"
python3 - <<'PY'
from pathlib import Path
path=Path('/home/ubuntu/prometheus/prometheus/prometheus.yml')
text=path.read_text()
job='''
  - job_name: 'ruby_acorn_pegasus'
    scrape_interval: 15s
    static_configs:
      - targets: ['192.168.45.220:8001']
'''
if 'ruby_acorn_pegasus' not in text:
    text += job
Path(str(path)+'.ruby-acorn-candidate').write_text(text)
PY
sudo docker exec "$prom" promtool check config /etc/prometheus/prometheus.yml.ruby-acorn-candidate
if ! cmp -s "$config" "$config.ruby-acorn-candidate"; then
  cp -p "$config" "$config.before-ruby-acorn-$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$config.ruby-acorn-candidate" "$config"
  sudo docker kill --signal HUP "$prom" >/dev/null
fi
# Existing provider watches this directory; existing dashboards/data are preserved.
for dashboard in dashboards/ruby-acorn-pegasus-*.json; do
  cp "$dashboard" /home/ubuntu/prometheus/grafana/provisioning/dashboards/
done
curl -fsS http://127.0.0.1:9090/-/ready
