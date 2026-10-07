from scaler.strategies.threshold import ThresholdStrategy
import pytest


@pytest.mark.parametrize(
    "players, desired", [(0, 1), (99, 1), (100, 2), (299, 2), (300, 3), (599, 3), (600, 4)]
)
def test_threshold_boundaries(players, desired):
    assert ThresholdStrategy().desired_instances(players) == desired


def test_threshold_strategy():
    strategy = ThresholdStrategy()

    assert strategy.desired_instances(50) == 1
    assert strategy.desired_instances(150) == 2
    assert strategy.desired_instances(450) == 3
    assert strategy.desired_instances(700) == 4
