from .base import ScalingStrategy
from .threshold import ThresholdStrategy


DEFAULT_STRATEGY = "threshold"
STRATEGIES: dict[str, type[ScalingStrategy]] = {
    "threshold": ThresholdStrategy,
}


def create_strategy(name: str) -> ScalingStrategy:
    try:
        strategy_type = STRATEGIES[name]
    except KeyError as error:
        raise ValueError(f"Unknown scaling strategy: {name}") from error
    return strategy_type()
