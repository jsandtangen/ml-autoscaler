"""Read-only monitoring checks using Grafana's API, including unified storage.

Run as root on the monitoring host. Existing service credentials stay in memory.
"""
import base64
import json
from pathlib import Path
import subprocess
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


def grafana_headers():
    spec=json.loads(subprocess.check_output(['docker','service','inspect','prom_grafana']))[0]
    values=spec['Spec']['TaskTemplate']['ContainerSpec'].get('Env', [])
    env=dict(value.split('=',1) for value in values if '=' in value)
    password=env.get('GF_SECURITY_ADMIN_PASSWORD')
    if password is None:
        return {}
    user=env.get('GF_SECURITY_ADMIN_USER','admin')
    credential=base64.b64encode((user+':'+password).encode()).decode()
    return {'Authorization': 'Basic '+credential}


def grafana(path, headers):
    request=urllib.request.Request('http://127.0.0.1:3000'+path,headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def main():
    headers=grafana_headers()
    sources=grafana('/api/datasources',headers)
    sources=[source for source in sources if source['type']=='prometheus' and source['name']=='Prometheus']
    if len(sources)!=1:
        raise ValueError('Expected exactly one existing Prometheus datasource')
    datasource=sources[0]['uid']
    report={'verified_at': time.time(), 'dashboards': [], 'targets': query('up'),
            'actual_workers': query('pegasus_running_instances{job="ruby_acorn_pegasus"}'),
            'last_decisions': query('last_decision_timestamp_seconds{job="ruby_acorn_pegasus"}'),
            'inputs': query('input_available{job="ruby_acorn_pegasus"}'),
            'grafana_health': grafana('/api/health',headers)}
    for path in sorted(Path('/home/ubuntu/prometheus/grafana/provisioning/dashboards').glob('ruby-acorn-pegasus-*.json')):
        doc=json.loads(path.read_text())
        stored=grafana('/api/dashboards/uid/'+urllib.parse.quote(doc['uid']),headers)
        loaded=stored['dashboard']['uid']==doc['uid']
        replay_label=any('historical replay' in panel.get('options',{}).get('content','')
                         for panel in stored['dashboard']['panels'])
        checks=[]
        # Inspect the dashboard Grafana actually serves, not only the source file.
        for panel in stored['dashboard']['panels']:
            for target in panel.get('targets',[]):
                for appid in ('730','570'):
                    expression=target['expr'].replace('$appid',appid)
                    results=query(expression)
                    path='/api/datasources/proxy/uid/'+urllib.parse.quote(datasource)+'/api/v1/query?'
                    proxied=grafana(path+urllib.parse.urlencode({'query':expression}),headers)
                    if proxied.get('status')!='success':
                        raise ValueError('Grafana datasource proxy query failed')
                    proxy_results=proxied['data']['result']
                    checks.append({'panel': panel['title'], 'appid':appid,'query': expression,
                                   'series': len(results), 'grafana_series':len(proxy_results)})
        report['dashboards'].append({'uid': doc['uid'], 'loaded_in_grafana': loaded,
                                     'replay_label_present':replay_label,
                                     'queries': checks})
    report['decisions_fresh']=(len(report['last_decisions'])==2 and
        all(-5 <= report['verified_at']-float(item['value'][1]) <= 120 for item in report['last_decisions']))
    report['passed']=(len(report['dashboards'])==3 and report['decisions_fresh'] and
                      report['grafana_health']['database']=='ok' and len(report['inputs'])==2 and
                      all(item['value'][1]=='1' for item in report['inputs']) and
                      all(d['loaded_in_grafana'] and d['replay_label_present'] and d['queries'] and
                          all(q['series'] and q['grafana_series'] for q in d['queries'])
                          for d in report['dashboards']) and
                      any(t['metric'].get('job')=='ruby_acorn_pegasus' and t['value'][1]=='1'
                          for t in report['targets']))
    print(json.dumps(report,indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
