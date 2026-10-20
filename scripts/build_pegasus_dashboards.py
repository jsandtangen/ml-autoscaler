"""Generate the three Pegasus dashboards, using Grafana's Prometheus datasource."""
import json
from pathlib import Path


def build():
    selected = '{job="ruby_acorn_pegasus",appid=~"$appid"}'
    games = 'steam_player_count{job="games",appid=~"$appid"}'
    dashboards = [
        ('demand', 'Game demand', [
            ('Observed source player count', games),
            ('Average source player count (1h)', f'avg_over_time({games}[1h])'),
            ('Peak source player count (1h)', f'max_over_time({games}[1h])'),
            ('Source scrape health', 'up{job="games"}'),
            ('Simulated customer demand (configured share)', 'customer_player_count'+selected),
            ('Input available (fresh scrape)', 'input_available'+selected),
        ]),
        ('scaling', 'Autoscaling behavior', [
            ('Desired and actual running workers', ['desired_instances'+selected, 'pegasus_running_instances'+selected]),
            ('Modeled demand and running capacity', ['customer_player_count'+selected, 'pegasus_running_instances'+selected+' * on(appid) capacity_players_per_instance'+selected]),
            ('Successful infrastructure actions', 'pegasus_actions_succeeded_total'+selected),
            ('Modeled unserved players', 'unmet_player_capacity'+selected),
            ('Active strategy (name label)', 'strategy'+selected),
            ('Stopped allocated workers', 'pegasus_stopped_instances'+selected),
            ('Action latency average (seconds)', 'pegasus_action_latency_seconds_sum'+selected+' / clamp_min(pegasus_action_latency_seconds_count'+selected+', 1)'),
            ('Shared fleet allocation (repeated per game; do not sum)', 'pegasus_fleet_allocated_instances'+selected),
        ]),
        ('costs', 'Cost and reliability', [
            ('Estimated running cost (EUR/hour; not a bill)', 'dynamic_cost'+selected),
            ('Estimated fixed baseline (EUR/hour)', 'fixed_baseline_cost'+selected),
            ('Estimated savings (EUR/hour)', 'savings'+selected),
            ('Hypothetical rule targets (not observed fleets)', 'comparison_desired_instances'+selected),
            ('Controller decision errors', 'scaling_errors_total'+selected),
            ('Infrastructure action errors', 'pegasus_actions_failed_total'+selected),
            ('Seconds since successful decision', 'time() - last_decision_timestamp_seconds'+selected),
            ('Exporter and cloud health', ['up{job="ruby_acorn_pegasus"}', 'pegasus_state_available'+selected]),
        ]),
    ]
    destination = Path('deploy/pegasus/dashboards')
    destination.mkdir(parents=True, exist_ok=True)
    for suffix, title, panels in dashboards:
        uid = 'ruby-acorn-pegasus-'+suffix
        doc = {'uid': uid, 'title': 'Ruby Acorn Pegasus — '+title, 'schemaVersion': 39,
               'version': 1, 'tags': ['ruby-acorn', 'pegasus'], 'refresh': '15s',
               'time': {'from': 'now-1h', 'to': 'now'}, 'timezone': 'browser',
               'description': 'Observed Pegasus infrastructure and source exporter counts. Player capacity, customer share and EUR costs are prototype estimates. Source counts may be historical replay. Stopped root disks remain allocated; running-cost estimates exclude storage and management/monitoring.',
               'templating': {'list': [
                   {'name': 'datasource', 'type': 'datasource', 'query': 'prometheus',
                    'current': {'text': 'Prometheus', 'value': 'Prometheus'}},
                   {'name': 'appid', 'label': 'Game', 'type': 'query',
                    'datasource': {'type': 'prometheus', 'uid': '$datasource'},
                    'query': 'label_values(steam_player_count{job="games"}, appid)',
                    'refresh': 1, 'multi': True, 'includeAll': True, 'allValue': '.*',
                    'current': {'text': '730', 'value': '730'}},
               ]},
               'links': [{'title': name, 'url': '/d/ruby-acorn-pegasus-'+key, 'keepTime': True, 'includeVars': True}
                         for key, name, _ in dashboards], 'panels': []}
        for i, (name, queries) in enumerate(panels):
            if isinstance(queries, str):
                queries = [queries]
            doc['panels'].append({'id': i+1, 'title': name, 'type': 'timeseries',
                'datasource': {'type': 'prometheus', 'uid': '$datasource'},
                'gridPos': {'x': (i%2)*12, 'y': (i//2)*8, 'w': 12, 'h': 8},
                'targets': [{'refId': chr(65+j), 'expr': query, 'legendFormat': '{{appid}} {{name}} {{strategy}}'}
                            for j, query in enumerate(queries)],
                'fieldConfig': {'defaults': {'min': 0}, 'overrides': []}})
        (destination/(uid+'.json')).write_text(json.dumps(doc, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    build()
