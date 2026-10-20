"""Shared fleet bounds and per-cycle rate limit, independent of controllers."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ScalingPlan:
    required_instances: int
    desired_instances: int
    cycle_target_instances: int


@dataclass(frozen=True)
class ScalingLimits:
    minimum_instances: int = 1
    maximum_instances: int = 7
    max_change: int = 2

    def __post_init__(self):
        if any(type(v) is not int for v in (self.minimum_instances, self.maximum_instances, self.max_change)):
            raise ValueError("Scaling limits must be integers")
        if self.minimum_instances < 0 or self.maximum_instances < self.minimum_instances or self.max_change < 1:
            raise ValueError("Require 0 <= minimum <= maximum and positive max change")

    def plan(self, required: int, current: int) -> ScalingPlan:
        if any(type(v) is not int or v < 0 for v in (required, current)):
            raise ValueError("Required/current counts must be nonnegative integers")
        desired = min(self.maximum_instances, max(self.minimum_instances, required))
        delta = max(-self.max_change, min(self.max_change, desired-current))
        return ScalingPlan(required, desired, current+delta)
