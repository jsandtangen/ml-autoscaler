import csv
import gzip
import json

import pytest

from scaler.forecasting import PlayerObservation
from scaler.simulation import (SimulationClock, FixedCapacityStrategy, longest_clean_segment,
                               simulate_five_minute_scaling, simulate_scaling)
from scaler.strategies.capacity import CapacityPolicy, ReactiveCapacityStrategy
from scaler.strategies.demand import AggressiveCapacityStrategy, CostOptimizedStrategy
from scripts.simulate_scaling import parse_args, run_study, load_study_cache
from scaler.engine.scaling_limits import ScalingLimits


def rows(counts):
    return [PlayerObservation(i*300, c) for i, c in enumerate(counts)]


def replay(strategy, counts, *, initial=1, maximum=3, startup=300, cooldown=120, clock=None):
    policy = CapacityPolicy(1, 100, 0, 0)
    return simulate_five_minute_scaling(rows(counts), {'rule': strategy}, policy,
        ScalingLimits(0, maximum, 1), clock or SimulationClock(), 1, len(counts),
        startup_seconds=startup, cooldown_seconds=cooldown, initial_instances=initial)


def test_legacy_replay_does_not_replace_aggressive_reserve_with_shared_capacity():
    policy = CapacityPolicy(1, 100, 0, 0)
    hourly = [PlayerObservation(i*3600, 50) for i in range(3)]
    result, trace = simulate_scaling(hourly, {'aggressive': AggressiveCapacityStrategy(policy)}, policy,
                                    1, limits=ScalingLimits(0, 5, 2))
    assert trace[0]['running_instances'] == 2
    assert trace[0]['required_instances'] == 2


def test_longest_segment_selection_uses_quality_only_and_earliest_tie():
    data = rows([1, 2, 3, 0, 4, 5, 6, 0, 8])
    assert longest_clean_segment(data, 3) == data[:3]
    with pytest.raises(ValueError, match='long enough'):
        longest_clean_segment(data, 4)
    data = [PlayerObservation(0, 1), PlayerObservation(300, 1), PlayerObservation(900, 1)]
    assert len(longest_clean_segment(data, 2)) == 2


def test_startup_is_billed_but_has_no_capacity_and_no_duplicate_starts():
    policy = CapacityPolicy(1, 100, 0, 0)
    result, trace = replay(ReactiveCapacityStrategy(policy), [200]*3)
    score = result['rule']
    assert score['hours_below_capacity'] == pytest.approx(5/60)
    assert score['billable_instance_hours'] == pytest.approx(2*10/60)
    assert score['startup_instance_hours'] == pytest.approx(5/60)
    assert score['unserved_player_hours'] == pytest.approx(100*5/60)
    assert score['instances_started'] == score['scale_up_events'] == 1
    assert trace[9]['ready_instances'] == 1
    assert trace[10]['ready_instances'] == 2
    assert all(row['billable_instances'] == 2 for row in trace)


def test_cost_strategy_uses_virtual_180_second_timer_and_real_contextual_logic():
    clock = SimulationClock()
    policy = CapacityPolicy(1, 100, 0, 0)
    result, trace = replay(CostOptimizedStrategy(policy, clock=clock), [50]*3,
                           initial=3, clock=clock)
    stops = [row['timestamp']-300 for row in trace if row['change'] < 0]
    assert stops == [180, 390]
    assert result['rule']['instances_stopped'] == 2
    assert all(row['ready_instances'] == 3 for row in trace[:6])


def test_cooldown_and_limits_count_starting_instances():
    result, trace = replay(FixedCapacityStrategy(3), [100]*4)
    starts = [row['timestamp']-300 for row in trace if row['change'] > 0]
    assert starts == [0, 120]
    assert all(row['billable_instances'] <= 3 and abs(row['change']) <= 1 for row in trace)
    assert result['rule']['instances_started'] == 2


@pytest.mark.parametrize('startup', [0, 30, 300, 900])
def test_vm_hours_and_cost_have_consistent_units(startup):
    result, trace = replay(FixedCapacityStrategy(2), [100]*5, startup=startup)
    score = result['rule']
    assert score['total_cost_eur'] == pytest.approx(score['billable_instance_hours']*0.10)
    assert score['billable_instance_hours'] == pytest.approx(score['ready_instance_hours']+score['startup_instance_hours'])


class Model:
    def __init__(self):
        self.calls = []
    def predict_steps(self, counts):
        self.calls.append(list(counts))
        return [counts[-1]+i for i in range(1, 7)]


def study_args(tmp_path):
    history = tmp_path/'history.json'
    history.write_text(json.dumps({'appid': 730, 'interval_seconds': 300, 'data_epoch': 0,
        'data_points': 14, 'data': [{'interval': i, 'timestamp': i*300, 'count': 100+i} for i in range(14)]}))
    return parse_args(['--five-minute-study', '--appid', '730', '--history-path', str(history),
        '--output-dir', str(tmp_path/'study'), '--window-size', '2', '--player-share', '1',
        '--minimum-instances', '0', '--max-scaling-change', '1',
        '--compare-max-instances', '1', '3', '--startup-seconds', '60', '300'])


def test_study_generates_once_then_reuses_cache_for_all_strategies_and_scenarios(tmp_path):
    args, model = study_args(tmp_path), Model()
    args.study_phase = 'forecasts'
    cache_report = run_study(args, model)
    assert cache_report['origins'] == 6
    assert model.calls == [[101+i, 102+i] for i in range(6)]
    args.study_phase = 'replay'
    report = run_study(args)
    assert len(report['scenarios']) == 4
    assert report['evaluation']['origins'] == 6
    for case in report['scenarios']:
        assert set(case['results']) == {'aggressive_capacity', 'cost_optimized', 'timesfm_15m', 'timesfm_30m', 'fixed_capacity'}
        assert len({round(r['evaluated_hours'], 8) for r in case['results'].values()}) == 1
        assert case['results']['timesfm_15m']['successful_forecasts'] == 6
        assert case['results']['timesfm_30m']['successful_forecasts'] == 6
        assert all(r['forecast_fallbacks'] == 0 for r in case['results'].values())
    first_dir = args.output_dir/'production_assumptions-max1-startup60'
    with gzip.open(first_dir/'decisions.csv.gz', 'rt') as file:
        trace = list(csv.DictReader(file))
    first = [r for r in trace if r['timestamp'] == trace[0]['timestamp']]
    assert all(r['allocated_before'] == '1' for r in first)
    assert all(float(r['source_timestamp']) <= float(r['timestamp']) for r in trace)
    # Repeated 30s decisions do not append repeated source counts to the ML window.
    assert len(model.calls) == 6
    with pytest.raises(ValueError, match='already exists'):
        run_study(args)


def test_missing_or_tampered_cache_never_silently_falls_back(tmp_path):
    args = study_args(tmp_path)
    args.study_phase = 'replay'
    with pytest.raises(ValueError, match='Incomplete'):
        run_study(args)
    args.study_phase = 'forecasts'
    run_study(args, Model())
    path = args.output_dir/'forecasts.jsonl'
    items = [json.loads(line) for line in path.read_text().splitlines()]
    items[0]['context_sha256'] = 'wrong'
    path.write_text(''.join(json.dumps(item)+'\n' for item in items))
    args.study_phase = 'replay'
    with pytest.raises(ValueError, match='mismatched'):
        run_study(args)


def test_study_refuses_openstack_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('OS_AUTH_URL', 'synthetic')
    with pytest.raises(ValueError, match='without OpenStack'):
        run_study(study_args(tmp_path), Model())


def test_independent_audit_reconstructs_rule_logic_and_rejects_changed_summary(tmp_path):
    from scripts.verify_scaling_study import verify
    args = study_args(tmp_path)
    run_study(args, Model())
    audit = verify(args.output_dir, args.history_path)
    assert audit['passed'] and len(audit['scenarios']) == 4
    path = args.output_dir/'study.json'
    result = json.loads(path.read_text())
    result['scenarios'][0]['results']['cost_optimized']['total_cost_eur'] += 1
    path.write_text(json.dumps(result))
    with pytest.raises(AssertionError, match='total_cost_eur'):
        verify(args.output_dir, args.history_path)


def test_last_value_comparison_is_identical_when_cached_forecasts_equal_last_value(tmp_path):
    from scripts.verify_scaling_study import verify
    class LastValueModel:
        def predict_steps(self, counts):
            return [counts[-1]]*6
    args = study_args(tmp_path)
    args.study_phase = 'forecasts'
    run_study(args, LastValueModel())
    args.study_phase = 'replay'
    args.compare_last_value = True
    report = run_study(args)
    for case in report['scenarios']:
        assert set(case['results']) == {'timesfm_15m', 'last_value_15m'}
        assert case['results']['timesfm_15m'] == case['results']['last_value_15m']
    assert verify(args.output_dir, args.history_path)['passed']
    # The independent auditor also checks baseline requests against current data.
    case = report['scenarios'][-1]
    path = args.output_dir/f"{case['label']}-max3-startup300"/'decisions.csv.gz'
    with gzip.open(path, 'rt', newline='') as file:
        trace = list(csv.DictReader(file))
    next(row for row in trace if row['strategy'] == 'last_value_15m')['strategy_request'] = '99'
    with gzip.open(path, 'wt', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=list(trace[0]))
        writer.writeheader()
        writer.writerows(trace)
    with pytest.raises(AssertionError, match='actual strategy request'):
        verify(args.output_dir, args.history_path)


def test_last_value_comparison_requires_cached_replay(tmp_path):
    args = study_args(tmp_path)
    args.compare_last_value = True
    with pytest.raises(ValueError, match='existing forecast cache'):
        run_study(args, Model())
