from .base import ScalingStrategy
from .aggressive import AggressiveStrategy
from .threshold import ThresholdStrategy
from .capacity import CapacityPolicy, ReactiveCapacityStrategy


DEFAULT_STRATEGY = "reactive_capacity"
STRATEGIES: dict[str, type[ScalingStrategy]] = {
    "reactive_capacity": ReactiveCapacityStrategy,
    "aggressive": AggressiveStrategy,
    "threshold": ThresholdStrategy,
}


def create_strategy(name: str, policy: CapacityPolicy | None = None) -> ScalingStrategy:
    try:
        strategy_type = STRATEGIES[name]
    except KeyError as error:
        raise ValueError(f"Unknown scaling strategy: {name}") from error
    return strategy_type(policy) if strategy_type is ReactiveCapacityStrategy else strategy_type()


def strategy_name(strategy: ScalingStrategy) -> str:
    for name, strategy_type in STRATEGIES.items():
        if type(strategy) is strategy_type:
            return name
    return type(strategy).__name__
