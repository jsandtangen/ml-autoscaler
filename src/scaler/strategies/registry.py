from .base import ScalingStrategy
from .aggressive import AggressiveStrategy
from .threshold import ThresholdStrategy


DEFAULT_STRATEGY = "threshold"
STRATEGIES: dict[str, type[ScalingStrategy]] = {
    "aggressive": AggressiveStrategy,
    "threshold": ThresholdStrategy,
}


def create_strategy(name: str) -> ScalingStrategy:
    try:
        strategy_type = STRATEGIES[name]
    except KeyError as error:
        raise ValueError(f"Unknown scaling strategy: {name}") from error
    return strategy_type()
