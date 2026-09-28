from abc import ABC, abstractmethod


class ScalingStrategy(ABC):
    @abstractmethod
    def desired_instances(self, player_count: int) -> int:
        """Return how many VM instances should be running."""
        pass