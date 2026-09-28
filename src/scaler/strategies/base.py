from abc import ABC, abstractmethod


class ScalingStrategy(ABC):
    @abstractmethod
    def desired_instances(self, player_count):
        """Return how many VM instances should be running."""
        pass