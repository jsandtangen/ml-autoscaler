"""Chronological windows and scoring shared by offline training experiments."""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

from scaler.forecasting import ForecastScore, PlayerObservation


@dataclass(frozen=True)
class ForecastWindow:
    context: tuple[int, ...]
    targets: tuple[int, ...]
    target_indices: tuple[int, ...]


@dataclass(frozen=True)
class TrainingSplit:
    train_end: int
    validation_end: int
    interval_seconds: float
    train: tuple[ForecastWindow, ...]
    validation: tuple[ForecastWindow, ...]
    test: tuple[ForecastWindow, ...]


def chronological_split(
    observations: Sequence[PlayerObservation],
    context_length: int = 192,
    horizon: int = 1,
    validation_size: int = 72,
    test_size: int = 72,
) -> TrainingSplit:
    """Split target periods; rolling context may use earlier observed values.

    A window's entire future target must belong to just one split. No training
    window can contain validation/test data, including at multi-step horizons.
    """
    if min(context_length, horizon, validation_size, test_size) < 1:
        raise ValueError("Context, horizon and split sizes must be positive")
    train_end = len(observations) - validation_size - test_size
    validation_end = len(observations) - test_size
    if train_end < context_length + horizon:
        raise ValueError("Insufficient training observations for context + horizon")
    if min(validation_size, test_size) < horizon:
        raise ValueError("Validation and test periods must each cover the horizon")
    gaps = [b.timestamp - a.timestamp for a, b in zip(observations, observations[1:])]
    if not gaps or not math.isfinite(gaps[0]) or gaps[0] <= 0:
        raise ValueError("Training requires increasing, regularly spaced timestamps")
    if any(not math.isfinite(gap) or not math.isclose(gap, gaps[0], abs_tol=0.001, rel_tol=0)
           for gap in gaps):
        raise ValueError("Training requires increasing, regularly spaced timestamps")
    if any(not math.isfinite(row.player_count) or row.player_count < 0 for row in observations):
        raise ValueError("Player counts must be finite and nonnegative")

    def windows(start: int, stop: int) -> tuple[ForecastWindow, ...]:
        return tuple(ForecastWindow(
            tuple(row.player_count for row in observations[end-context_length:end]),
            tuple(row.player_count for row in observations[end:end+horizon]),
            tuple(range(end, end+horizon)),
        ) for end in range(max(start, context_length), stop-horizon+1))

    return TrainingSplit(
        train_end, validation_end, gaps[0],
        windows(context_length, train_end),
        windows(train_end, validation_end),
        windows(validation_end, len(observations)),
    )


def score_predictions(predictions: Sequence[float], targets: Sequence[int]) -> ForecastScore:
    if not predictions or len(predictions) != len(targets):
        raise ValueError("Predictions and targets must have the same nonzero length")
    if any(not math.isfinite(value) or value < 0 for value in predictions):
        raise ValueError("Predictions must be finite and nonnegative")
    errors = [prediction-target for prediction, target in zip(predictions, targets)]
    return ForecastScore(len(errors), fmean(abs(e) for e in errors), math.sqrt(fmean(e*e for e in errors)))
