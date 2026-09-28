from scaler.strategies.threshold import ThresholdStrategy


def test_threshold_strategy():
    strategy = ThresholdStrategy()

    assert strategy.desired_instances(50) == 1
    assert strategy.desired_instances(150) == 2
    assert strategy.desired_instances(450) == 3
    assert strategy.desired_instances(700) == 4