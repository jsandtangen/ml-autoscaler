"""Read-only Prometheus/Grafana verification. Run as root on monitoring host.

Existing Grafana service credentials are used only in memory, never output.
"""
import base64
import json
import subprocess
import time
import urllib.parse
import urllib.request


def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=10) as r:
        return json.load(r)


def main():
    spec = json.loads(subprocess.check_output(['docker', 'service', 'inspect', 'prom_grafana']))[0]
    env = dict(item.split('=', 1) for item in spec['Spec']['TaskTemplate']['ContainerSpec'].get('Env', []) if '=' in item)
    headers = {}
    if 'GF_SECURITY_ADMIN_PASSWORD' in env:
        value = env.get('GF_SECURITY_ADMIN_USER', 'admin')+':'+env['GF_SECURITY_ADMIN_PASSWORD']
        headers['Authorization'] = 'Basic '+base64.b64encode(value.encode()).decode()
    grafana = lambda path: get('http://127.0.0.1:3000'+path, headers)
    sources = [s for s in grafana('/api/datasources') if s['name'] == 'Prometheus' and s['type'] == 'prometheus']
    if len(sources) != 1:
        raise ValueError('Expected one existing Prometheus datasource')
    proxy = '/api/datasources/proxy/uid/'+urllib.parse.quote(sources[0]['uid'])+'/api/v1/query?'
    def query(expr):
        data = get('http://127.0.0.1:9090/api/v1/query?'+urllib.parse.urlencode({'query': expr}))
        if data['status'] != 'success':
            raise ValueError('Query failed')
        return data['data']['result']
    dashboard = grafana('/api/dashboards/uid/ruby-acorn-ml-shadow')['dashboard']
    checks = []
    for panel in dashboard['panels']:
        for target in panel.get('targets', []):
            expr = target['expr']
            direct = query(expr)
            proxied = grafana(proxy+urllib.parse.urlencode({'query': expr}))
            if proxied['status'] != 'success':
                raise ValueError('Grafana proxy query failed')
            checks.append({'panel': panel['title'], 'query': expr, 'direct_series': len(direct),
                           'grafana_series': len(proxied['data']['result'])})
    selected = '{job="ruby_acorn_pegasus",appid="730"}'
    report = {'verified_at': time.time(), 'dashboard_uid': dashboard['uid'], 'panel_queries': checks,
              'targets': query('up'), 'predictions': query('ruby_acorn_ml_predicted_players'+selected),
              'recommendations': query('ruby_acorn_ml_recommended_instances'+selected),
              'mae': query('ruby_acorn_ml_mae_players'+selected),
              'rmse': query('ruby_acorn_ml_rmse_players'+selected),
              'live_samples': query('ruby_acorn_ml_scored_forecasts_total'+selected),
              'running': query('pegasus_running_instances{job="ruby_acorn_pegasus"}'),
              'decision_age': query('time()-last_decision_timestamp_seconds{job="ruby_acorn_pegasus"}'),
              'input_available': query('input_available{job="ruby_acorn_pegasus"}'),
              'forecast_available': query('ruby_acorn_ml_forecast_available'+selected),
              'grafana_health': grafana('/api/health')}
    report['both_live_horizons_scored'] = all(any(s['metric']['model'] == 'timesfm'
         and s['metric']['horizon_minutes'] == h and float(s['value'][1]) > 0
         for s in report['live_samples']) for h in ('15', '30'))
    report['passed'] = (len(report['predictions']) == len(report['recommendations']) == 6
        and len(report['running']) == 2 and all(s['value'][1] == '1' for s in report['running'])
        and len(report['decision_age']) == 2 and all(0 <= float(s['value'][1]) < 120 for s in report['decision_age'])
        and len(report['input_available']) == 2 and all(s['value'][1] == '1' for s in report['input_available'])
        and len(report['forecast_available']) == 1 and report['forecast_available'][0]['value'][1] == '1'
        and len({(s['metric']['model'], s['metric']['horizon_minutes']) for s in report['predictions']}) == 6
        and report['grafana_health']['database'] == 'ok'
        and all(c['direct_series'] == c['grafana_series'] for c in checks)
        and all(s['value'][1] == '1' for s in report['targets']))
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
