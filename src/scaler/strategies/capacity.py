"""Shared prototype capacity assumptions for reactive and predictive decisions."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
import math

from .base import ScalingStrategy


@dataclass(frozen=True)
class CapacityPolicy:
    player_share: float = 0.001
    players_per_instance: int = 100
    headroom_fraction: float = 0.10
    minimum_instances: int = 1

    def __post_init__(self):
        if not math.isfinite(self.player_share) or not 0 < self.player_share <= 1:
            raise ValueError("Player share must be in (0, 1]")
        if type(self.players_per_instance) is not int or self.players_per_instance < 1:
            raise ValueError("Players per instance must be a positive integer")
        if not math.isfinite(self.headroom_fraction) or self.headroom_fraction < 0:
            raise ValueError("Headroom must be finite and nonnegative")
        if type(self.minimum_instances) is not int or self.minimum_instances < 0:
            raise ValueError("Minimum instances must be a nonnegative integer")

    def customer_players(self, global_players: float) -> float:
        if not math.isfinite(global_players) or global_players < 0:
            raise ValueError("Player count must be finite and nonnegative")
        return global_players * self.player_share

    def required_instances(self, global_players: float) -> int:
        self.customer_players(global_players)
        # Avoid an extra instance from binary rounding at exact boundaries.
        demand = Decimal(str(global_players)) * Decimal(str(self.player_share))
        buffered = demand * (1 + Decimal(str(self.headroom_fraction)))
        instances = int((buffered / self.players_per_instance).to_integral_value(rounding=ROUND_CEILING))
        return instances

    def desired_instances(self, global_players: float) -> int:
        return max(self.minimum_instances, self.required_instances(global_players))


class ReactiveCapacityStrategy(ScalingStrategy):
    """Apply the capacity rule to the latest observed global player count."""

    def __init__(self, policy: CapacityPolicy | None = None):
        self.policy = policy or CapacityPolicy()

    def desired_instances(self, player_count: int) -> int:
        return self.policy.desired_instances(player_count)
