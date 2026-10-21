from __future__ import annotations

import math
from collections.abc import Sequence
from pathlib import Path


MODEL_ID = "google/timesfm-2.5-200m-pytorch"


class TimesFMError(RuntimeError):
    """TimesFM dependencies, checkpoint loading or inference failed."""


class TimesFMForecaster:
    """Lazy, pretrained TimesFM 2.5 forecasts at a fixed observation horizon.

    No fitting is performed. Only the supplied past window reaches the model.
    Heavy dependencies and weights are loaded on the first valid prediction.
    """

    def __init__(
        self,
        window_size: int = 512,
        horizon: int = 1,
        cache_dir: str | Path = ".model-cache",
        local_files_only: bool = False,
    ) -> None:
        if window_size < 1 or horizon < 1:
            raise ValueError("Window size and horizon must be positive")
        # TimesFM pads context/horizon to input/output patch lengths.
        if math.ceil(window_size / 32) * 32 + math.ceil(horizon / 128) * 128 > 16384:
            raise ValueError("TimesFM padded window + horizon must not exceed 16384")
        self.window_size = window_size
        self.horizon = horizon
        self.cache_dir = str(cache_dir)
        self.local_files_only = local_files_only
        self._model = None

    def _load_model(self):
        try:
            import timesfm
        except ImportError as error:
            raise TimesFMError(
                'TimesFM requires the ML extra: python -m pip install -e ".[ml]"'
            ) from error
        try:
            model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
                MODEL_ID,
                cache_dir=self.cache_dir,
                local_files_only=self.local_files_only,
                force_download=False,
                torch_compile=False,
            )
            model.compile(timesfm.ForecastConfig(
                max_context=self.window_size,
                max_horizon=self.horizon,
                normalize_inputs=True,
                infer_is_positive=True,
                per_core_batch_size=1,
            ))
        except (OSError, RuntimeError, ValueError, AttributeError) as error:
            raise TimesFMError(f"Could not load TimesFM checkpoint {MODEL_ID}: {error}") from error
        return model

    def predict(self, player_counts: Sequence[int]) -> float:
        return self.predict_steps(player_counts)[-1]

    def predict_steps(self, player_counts: Sequence[int]) -> list[float]:
        """Return every step using one loaded model and one inference call."""
        if len(player_counts) != self.window_size:
            raise ValueError(f"TimesFM requires exactly {self.window_size} input observations")
        if any(not math.isfinite(count) or count < 0 for count in player_counts):
            raise ValueError("Player counts must be finite and nonnegative")
        if self._model is None:
            self._model = self._load_model()
        try:
            points, _ = self._model.forecast(
                horizon=self.horizon, inputs=[list(player_counts)]
            )
            # The evaluator scores the target at h, not the mean of steps 1..h.
            if len(points) != 1 or len(points[0]) != self.horizon:
                raise ValueError("Unexpected point forecast shape")
            predictions = [float(value) for value in points[0]]
            if any(not math.isfinite(value) or value < 0 for value in predictions):
                raise ValueError("Prediction must be finite and nonnegative")
        except (OSError, RuntimeError, ValueError, TypeError, IndexError) as error:
            raise TimesFMError(f"TimesFM forecast failed: {error}") from error
        return predictions
