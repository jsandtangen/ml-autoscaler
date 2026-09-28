from .base import ScalingStrategy


class ThresholdStrategy(ScalingStrategy):
    def desired_instances(self, player_count: int) -> int:
        if player_count < 100:
            return 1
        elif player_count < 300:
            return 2
        elif player_count < 600:
            return 3
        else:
            return 4