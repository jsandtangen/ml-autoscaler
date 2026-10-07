from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CostEstimate:
    dynamic_cost: float
    fixed_baseline_cost: float
    savings: float


@dataclass(frozen=True)
class CostModel:
    """Estimated EUR per hour at the current fake VM allocation."""

    vm_cost_per_hour: float = 0.10
    fixed_baseline_instances: int = 4

    def __post_init__(self) -> None:
        if not math.isfinite(self.vm_cost_per_hour) or self.vm_cost_per_hour < 0:
            raise ValueError("VM cost must be finite and nonnegative")
        if not isinstance(self.fixed_baseline_instances, int) or self.fixed_baseline_instances < 0:
            raise ValueError("Fixed baseline must be a nonnegative integer")

    def estimate_hourly(self, running_instances: int) -> CostEstimate:
        if running_instances < 0:
            raise ValueError("Running instance count cannot be negative")
        dynamic = running_instances * self.vm_cost_per_hour
        fixed = self.fixed_baseline_instances * self.vm_cost_per_hour
        return CostEstimate(dynamic, fixed, fixed - dynamic)
