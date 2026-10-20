from collections import deque
import logging
import math

from scaler.forecasting import Forecaster
from .base import ScalingStrategy
from .capacity import CapacityPolicy


logger = logging.getLogger("ruby_acorn.predictive")


class PredictiveCapacityStrategy(ScalingStrategy):
    """Forecast next-period demand, with reactive fallback on warmup/failure."""

    def __init__(self, policy: CapacityPolicy, forecaster: Forecaster, window_size: int):
        if type(window_size) is not int or window_size < 1:
            raise ValueError("Window size must be a positive integer")
        self.policy = policy
        self.forecaster = forecaster
        self.history = deque(maxlen=window_size)
        self.last_prediction: float | None = None
        self.status = "warmup"

    def desired_instances(self, player_count: int) -> int:
        # Validate before updating the input window.
        reactive = self.policy.desired_instances(player_count)
        self.history.append(player_count)
        self.last_prediction = None
        if len(self.history) < self.history.maxlen:
            self.status = "warmup"
            return reactive
        try:
            prediction = float(self.forecaster.predict(list(self.history)))
            if not math.isfinite(prediction) or prediction < 0:
                raise ValueError("Prediction must be finite and nonnegative")
            desired = self.policy.desired_instances(prediction)
        except (ValueError, TypeError, RuntimeError, OSError) as error:
            logger.warning("Using reactive capacity after forecast failure: %s", error)
            self.status = "fallback"
            return reactive
        self.last_prediction = prediction
        self.status = "forecast"
        return desired
