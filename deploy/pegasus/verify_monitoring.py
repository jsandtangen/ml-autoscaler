"""Run on the monitoring host as root. Read-only DB/API checks; no credentials."""
import json
from pathlib import Path
import sqlite3
import time
import urllib.parse
import urllib.request


def query(expression):
    url='http://127.0.0.1:9090/api/v1/query?'+urllib.parse.urlencode({'query': expression})
    with urllib.request.urlopen(url, timeout=10) as response:
        payload=json.load(response)
    if payload['status'] != 'success':
        raise ValueError('Prometheus query failed')
    return payload['data']['result']


def main():
    connection=sqlite3.connect('file:/var/lib/docker/volumes/prom_grafana_data/_data/grafana.db?mode=ro',uri=True)
    report={'verified_at': time.time(), 'dashboards': [], 'targets': query('up'),
            'actual_workers': query('pegasus_running_instances{job="ruby_acorn_pegasus"}'),
            'last_decisions': query('last_decision_timestamp_seconds{job="ruby_acorn_pegasus"}')}
    for path in sorted(Path('/home/ubuntu/prometheus/grafana/provisioning/dashboards').glob('ruby-acorn-pegasus-*.json')):
        doc=json.loads(path.read_text())
        stored=connection.execute('select uid,title from dashboard where uid=?',(doc['uid'],)).fetchone()
        checks=[]
        for panel in doc['panels']:
            for target in panel['targets']:
                expression=target['expr'].replace('$appid','730')
                results=query(expression)
                checks.append({'panel': panel['title'], 'query': expression, 'series': len(results)})
        report['dashboards'].append({'uid': doc['uid'], 'loaded_in_grafana': stored is not None,
                                     'queries': checks})
    report['passed']=(len(report['dashboards'])==3 and
                      all(d['loaded_in_grafana'] and all(q['series'] for q in d['queries'])
                          for d in report['dashboards']) and
                      any(t['metric'].get('job')=='ruby_acorn_pegasus' and t['value'][1]=='1'
                          for t in report['targets']))
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
