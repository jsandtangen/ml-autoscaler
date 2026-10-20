from dataclasses import replace
import pytest

from scaler.strategies.capacity import CapacityPolicy
from scaler.strategies.demand import DemandTuning, AggressiveCapacityStrategy, CostOptimizedStrategy
from scaler.strategies.registry import create_strategy
from scaler.engine.decision_engine import DecisionEngine
from scaler.engine.scaling_limits import ScalingLimits
from scaler.infrastructure.vm_controller import FakeVMController
from main import load_config


def test_different_policies_on_same_demand_and_bounds():
    policy = CapacityPolicy(1, 100, 0, 0)
    aggressive = create_strategy('aggressive_capacity', policy)
    cost = create_strategy('cost_optimized', policy)
    assert aggressive.desired_instances(200) == 4
    assert cost.desired_instances(200) == 2
    assert aggressive.desired_instances(0) == cost.desired_instances(0) == 0
    engine = DecisionEngine(aggressive, FakeVMController(), ScalingLimits(0, 2, 1))
    assert engine.evaluate(100000) == 1
    assert engine.evaluate(100000) == 2
    assert engine.evaluate(100000) == 2
    assert engine.evaluate(0) == 1
    assert engine.evaluate(0) == 0


def test_cost_delay_threshold_and_demand_recovery():
    clock = [0]
    strategy = CostOptimizedStrategy(CapacityPolicy(1, 100, 0, 0), clock=lambda: clock[0])
    assert strategy.desired_for_current(110, 1) == 1
    assert strategy.desired_for_current(111, 1) == 2
    assert strategy.desired_for_current(80, 2) == 2
    clock[0] = 179
    assert strategy.desired_for_current(80, 2) == 2
    assert strategy.desired_for_current(81, 2) == 2  # resets delay
    clock[0] = 180
    assert strategy.desired_for_current(80, 2) == 2
    clock[0] = 360
    assert strategy.desired_for_current(80, 2) == 1
    assert strategy.desired_for_current(0, 1) == 1
    clock[0] = 540
    assert strategy.desired_for_current(0, 1) == 0


def test_engine_uses_actual_state_and_cost_hysteresis():
    now = [0]
    policy = CapacityPolicy(1, 100, 0, 0)
    strategy = CostOptimizedStrategy(policy, clock=lambda: now[0])
    engine = DecisionEngine(strategy, FakeVMController(), ScalingLimits(0, 2, 2))
    assert engine.evaluate(200) == 2
    assert engine.evaluate(0) == 2
    now[0] = 180
    assert engine.evaluate(0) == 0


def test_unavailable_data_breaks_sustained_downscale_observation():
    now = [0]
    strategy = CostOptimizedStrategy(CapacityPolicy(1, 100, 0, 0), clock=lambda: now[0])
    assert strategy.desired_for_current(0, 1) == 1
    strategy.reset_observations()
    now[0] = 500
    assert strategy.desired_for_current(0, 1) == 1
    now[0] = 680
    assert strategy.desired_for_current(0, 1) == 0


@pytest.mark.parametrize('updates', [dict(downscale_seconds=-1), dict(scale_up_threshold=0),
    dict(scale_down_threshold=1.2), dict(cost_buffer=float('nan')), dict(aggressive_spare=True)])
def test_invalid_tuning(updates):
    with pytest.raises(ValueError):
        replace(DemandTuning(), **updates)


def test_cli_and_per_game_selection(tmp_path):
    path = tmp_path/'games.toml'
    path.write_text('[games."730"]\nstrategy="aggressive_capacity"\n[games."570"]\nstrategy="cost_optimized"\n')
    config = load_config(['--games-config', str(path), '--downscale-seconds', '250', '--cost-buffer', '0.05'])
    assert config.demand_tuning.downscale_seconds == 250
    assert config.demand_tuning.cost_buffer == .05
    assert [g.strategy_name for g in config.games] == ['aggressive_capacity', 'cost_optimized']
    with pytest.raises(SystemExit):
        load_config(['--scale-down-threshold', '2'])
