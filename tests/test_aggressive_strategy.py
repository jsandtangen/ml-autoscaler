import pytest

from scaler.strategies.aggressive import AggressiveStrategy


@pytest.mark.parametrize(
    "players, desired",
    [(0, 2), (99, 2), (100, 3), (299, 3), (300, 4), (599, 4), (600, 5), (700_000, 5)],
)
def test_aggressive_keeps_one_buffer_instance(players, desired):
    assert AggressiveStrategy().desired_instances(players) == desired
