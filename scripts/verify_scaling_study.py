"""Independently audit saved decisions against the actual rule implementations."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path

from scaler.data.shadow_source import parse_replay_history
from scaler.simulation import SimulationClock, longest_clean_segment
from scaler.strategies.capacity import CapacityPolicy
from scaler.strategies.demand import AggressiveCapacityStrategy, CostOptimizedStrategy, DemandTuning
from scaler.engine.scaling_limits import ScalingLimits
from scripts.simulate_scaling import load_study_cache


def verify(directory, history_path):
    report = json.loads((directory/'study.json').read_text())
    signature = report['dataset']
    assert hashlib.sha256(history_path.read_bytes()).hexdigest() == signature['source_sha256'], 'source hash'
    all_rows = parse_replay_history(json.loads(history_path.read_text()))
    rows = longest_clean_segment(all_rows, signature['window_size']+7)
    assert hashlib.sha256(json.dumps([(r.timestamp, r.player_count) for r in rows]).encode()).hexdigest() == signature['segment_sha256']
    start, stop = signature['evaluation_start_index'], signature['evaluation_stop_index']
    assert start == signature['window_size'] and stop == len(rows)-6
    cached = load_study_cache(directory/'forecasts.jsonl', rows, start, stop, signature['window_size'])
    assert len(cached) == stop-start
    assert hashlib.sha256((directory/'forecasts.jsonl').read_bytes()).hexdigest() == report['forecast_cache_sha256']
    assumptions = report['assumptions']
    step, cooldown = assumptions['decision_seconds'], assumptions['cooldown_seconds']
    price, initial = assumptions['vm_cost_per_hour'], assumptions['initial_ready_instances']
    checks = []
    for case in report['scenarios']:
        policy, limits = CapacityPolicy(**case['policy']), ScalingLimits(**case['limits'])
        clock = SimulationClock()
        rules = {'aggressive_capacity': AggressiveCapacityStrategy(policy, DemandTuning(**assumptions['tuning'])),
                 'cost_optimized': CostOptimizedStrategy(policy, DemandTuning(**assumptions['tuning']), clock=clock)}
        states = {name: {'ready': initial, 'pending': [], 'last_action': -math.inf,
                        'ticks': 0, 'cost': 0., 'vm_hours': 0., 'under_hours': 0., 'unserved': 0.,
                        'up': 0, 'down': 0, 'started': 0, 'stopped': 0} for name in case['results']}
        path = directory/f"{case['label']}-max{limits.maximum_instances}-startup{case['startup_seconds']}"/'decisions.csv.gz'
        decisions = 0
        with gzip.open(path, 'rt', newline='') as file:
            for record in csv.DictReader(file):
                name = record['strategy']
                state = states[name]
                now = float(record['timestamp'])
                assert now == rows[start].timestamp+state['ticks']*step, 'control-clock cadence'
                clock.now = now
                index = int((now-rows[0].timestamp)//300)
                assert start <= index < stop and float(record['source_timestamp']) == rows[index].timestamp
                assert int(record['global_players']) == rows[index].player_count
                completed = sum(due <= now for due in state['pending'])
                state['ready'] += completed
                state['pending'] = [due for due in state['pending'] if due > now]
                allocated = state['ready']+len(state['pending'])
                assert allocated == int(record['allocated_before'])
                if name == 'cost_optimized':
                    expected = rules[name].desired_for_current(rows[index].player_count, allocated)
                elif name == 'aggressive_capacity':
                    expected = rules[name].desired_instances(rows[index].player_count)
                elif name.startswith('timesfm_'):
                    horizon = name.split('_')[1].rstrip('m')
                    expected = policy.desired_instances(cached[index][horizon])
                elif name == 'last_value_15m':
                    expected = policy.desired_instances(rows[index].player_count)
                else:
                    assert name == 'fixed_capacity'
                    expected = limits.maximum_instances
                assert expected == int(record['strategy_request']), f'{name} actual strategy request'
                plan = limits.plan(expected, allocated)
                change = plan.cycle_target_instances-allocated if now-state['last_action'] >= cooldown else 0
                assert change == int(record['change']) and abs(change) <= limits.max_change
                if change > 0:
                    state['pending'].extend([now+case['startup_seconds']]*change)
                    state['up'] += 1
                    state['started'] += change
                elif change < 0:
                    cancel = min(-change, len(state['pending']))
                    state['pending'].sort()
                    if cancel:
                        del state['pending'][-cancel:]
                    state['ready'] -= -change-cancel
                    state['down'] += 1
                    state['stopped'] -= change
                if change:
                    state['last_action'] = now
                state['ready'] += sum(due <= now for due in state['pending'])
                state['pending'] = [due for due in state['pending'] if due > now]
                active = state['ready']+len(state['pending'])
                assert state['ready'] == int(record['ready_instances'])
                assert len(state['pending']) == int(record['starting_instances'])
                assert active == int(record['billable_instances']) and 0 <= active <= limits.maximum_instances
                shortfall = max(0., policy.customer_players(rows[index].player_count)-state['ready']*policy.players_per_instance)
                assert math.isclose(shortfall, float(record['shortfall_players']), abs_tol=1e-9)
                state['ticks'] += 1
                state['cost'] += active*step/3600*price
                state['vm_hours'] += active*step/3600
                state['under_hours'] += step/3600 if shortfall > 0 else 0
                state['unserved'] += shortfall*step/3600
                decisions += 1
        for name, state in states.items():
            result = case['results'][name]
            assert state['ticks']*step == (stop-start)*300, 'identical scored duration'
            for actual, field in [('cost','total_cost_eur'), ('vm_hours','billable_instance_hours'),
                ('under_hours','hours_below_capacity'), ('unserved','unserved_player_hours'),
                ('up','scale_up_events'), ('down','scale_down_events'),
                ('started','instances_started'), ('stopped','instances_stopped')]:
                assert math.isclose(state[actual], result[field], rel_tol=1e-9, abs_tol=1e-7), field
            assert math.isclose(result['ready_instance_hours']+result['startup_instance_hours'], state['vm_hours'], abs_tol=1e-7)
            if name.startswith('timesfm_') or name == 'last_value_15m':
                assert result['successful_forecasts'] == stop-start and result['forecast_fallbacks'] == 0
        checks.append({'label':case['label'], 'max_instances':limits.maximum_instances,
                       'startup_seconds':case['startup_seconds'], 'audited_decisions':decisions})
    return {'passed': True, 'cached_origins': len(cached), 'scenarios': checks,
            'checks': ['actual strategy requests at every tick (rule implementations or forecast through shared policy)',
                       'past-only cached contexts and identical origin range',
                       'common initial fleet, cadence, cooldown, limits and pending readiness',
                       'cost, VM hours, shortfall and scaling counts reconstructed from traces']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study-dir', type=Path, required=True)
    parser.add_argument('--history-path', type=Path, required=True)
    parser.add_argument('--output-path', type=Path)
    args = parser.parse_args()
    result = verify(args.study_dir, args.history_path)
    if args.output_path:
        from scripts.simulate_scaling import atomic_json
        atomic_json(args.output_path, result)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
