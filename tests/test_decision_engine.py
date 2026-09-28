from scaler.engine.decision_engine import DecisionEngine
from scaler.infrastructure.vm_controller import FakeVMController
from scaler.strategies.threshold import ThresholdStrategy


def test_decision_engine_scales_up_and_down():
    strategy = ThresholdStrategy()
    vm_controller = FakeVMController()
    engine = DecisionEngine(strategy, vm_controller)

    assert engine.evaluate(50) == 1
    assert engine.evaluate(150) == 2
    assert engine.evaluate(450) == 3
    assert engine.evaluate(700) == 4
    assert engine.evaluate(150) == 2
    assert engine.evaluate(50) == 1