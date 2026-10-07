import pytest

from scaler.strategies.registry import create_strategy
from scaler.strategies.threshold import ThresholdStrategy


def test_create_threshold_strategy():
    assert isinstance(create_strategy("threshold"), ThresholdStrategy)


def test_unknown_strategy_fails():
    with pytest.raises(ValueError, match="Unknown scaling strategy"):
        create_strategy("unknown")
