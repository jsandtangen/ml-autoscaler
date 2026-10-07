from .base import ScalingStrategy
from .threshold import ThresholdStrategy


class AggressiveStrategy(ScalingStrategy):
    """Keep one extra instance as a buffer above the threshold baseline."""

    def desired_instances(self, player_count: int) -> int:
        return ThresholdStrategy().desired_instances(player_count) + 1
