"""Capacity strategies for the prototype; player capacity remains an assumption."""
from dataclasses import dataclass
import math
import time

from .capacity import CapacityPolicy
from .base import ScalingStrategy


@dataclass(frozen=True)
class DemandTuning:
    aggressive_buffer: float = 0.30
    aggressive_spare: int = 1
    cost_buffer: float = 0.0
    scale_up_threshold: float = 1.10
    scale_down_threshold: float = 0.80
    downscale_seconds: float = 180.0

    def __post_init__(self):
        for value in (self.aggressive_buffer, self.cost_buffer, self.downscale_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError("Buffers and downscale delay must be finite and nonnegative")
        if type(self.aggressive_spare) is not int or self.aggressive_spare < 0:
            raise ValueError("Spare instances must be a nonnegative integer")
        if not (math.isfinite(self.scale_up_threshold) and math.isfinite(self.scale_down_threshold)
                and 0 < self.scale_down_threshold < self.scale_up_threshold <= 2):
            raise ValueError("Require 0 < down threshold < up threshold <= 2")


class AggressiveCapacityStrategy(ScalingStrategy):
    def __init__(self, policy=None, tuning=None):
        self.policy = policy or CapacityPolicy()
        self.tuning = tuning or DemandTuning()

    def desired_instances(self, player_count):
        demand = self.policy.customer_players(player_count)
        if not demand:
            return self.policy.minimum_instances
        return max(self.policy.minimum_instances,
                   math.ceil(demand * (1 + self.tuning.aggressive_buffer)
                             / self.policy.players_per_instance) + self.tuning.aggressive_spare)


class CostOptimizedStrategy(ScalingStrategy):
    """Immediate upscale above threshold; downscale only after sustained slack.

    Default 110% utilization permits 10% modeled overload before requesting more
    capacity. Fleet limits/startup delays can add shortfall. Restart resets the
    downscale timer conservatively. The controller independently enforces cooldown.
    """
    def __init__(self, policy=None, tuning=None, clock=time.monotonic):
        self.policy = policy or CapacityPolicy()
        self.tuning = tuning or DemandTuning()
        self.clock = clock
        self._pending = None
        self._since = None

    def desired_instances(self, player_count):
        demand = self.policy.customer_players(player_count)
        return max(self.policy.minimum_instances,
                   math.ceil(demand * (1 + self.tuning.cost_buffer)
                             / (self.policy.players_per_instance * self.tuning.scale_up_threshold)))

    def reset_observations(self):
        self._pending = self._since = None

    def desired_for_current(self, player_count, current):
        target = self.desired_instances(player_count)
        demand = self.policy.customer_players(player_count) * (1 + self.tuning.cost_buffer)
        if target >= current:
            self._pending = self._since = None
            return target
        if demand > target * self.policy.players_per_instance * self.tuning.scale_down_threshold:
            self._pending = self._since = None
            return current
        if self._pending != (target, current):
            self._pending, self._since = (target, current), self.clock()
        return target if self.clock() - self._since >= self.tuning.downscale_seconds else current
