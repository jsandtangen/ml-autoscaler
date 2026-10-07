from __future__ import annotations

import csv
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Protocol


class Forecaster(Protocol):
    def predict(self, player_counts: Sequence[int]) -> float: ...


class PersistenceForecaster:
    """Predict the future count using the latest observed count."""

    def predict(self, player_counts: Sequence[int]) -> float:
        if not player_counts:
            raise ValueError("Prediction requires at least one observation")
        return float(player_counts[-1])


class MovingAverageForecaster:
    """Predict the future count using the mean of the supplied input window."""

    def predict(self, player_counts: Sequence[int]) -> float:
        if not player_counts:
            raise ValueError("Prediction requires at least one observation")
        return fmean(player_counts)


@dataclass(frozen=True)
class PlayerObservation:
    timestamp: float
    player_count: int


@dataclass(frozen=True)
class ForecastScore:
    sample_count: int
    mae: float
    rmse: float


def load_player_history(path: str | Path) -> dict[str, list[PlayerObservation]]:
    """Read history as separate, chronologically ordered series per appid."""
    games: dict[str, list[PlayerObservation]] = {}
    with Path(path).open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != ["timestamp", "appid", "player_count"]:
            raise ValueError("Expected CSV columns: timestamp,appid,player_count")
        for row in reader:
            try:
                timestamp = float(row["timestamp"])
                player_count = int(row["player_count"])
                appid = row["appid"]
                if (
                    not math.isfinite(timestamp) or timestamp < 0
                    or player_count < 0 or appid is None or None in row
                ):
                    raise ValueError("Invalid observation")
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid history row at line {reader.line_num}") from error
            games.setdefault(appid, []).append(PlayerObservation(timestamp, player_count))
    for appid, observations in games.items():
        observations.sort(key=lambda observation: observation.timestamp)
        if any(a.timestamp == b.timestamp for a, b in zip(observations, observations[1:])):
            raise ValueError(f"Duplicate timestamps for appid {appid!r}")
    return games


def evaluate_forecaster(
    observations: Sequence[PlayerObservation],
    forecaster: Forecaster,
    window_size: int = 10,
    horizon: int = 1,
) -> ForecastScore:
    """Score rolling forecasts with targets strictly after each input window."""
    if window_size < 1 or horizon < 1:
        raise ValueError("Window size and horizon must be positive")
    if len(observations) < window_size + horizon:
        raise ValueError(f"Need at least {window_size + horizon} observations")
    if any(a.timestamp >= b.timestamp for a, b in zip(observations, observations[1:])):
        raise ValueError("Observations must have strictly increasing timestamps")

    errors = []
    for end in range(window_size, len(observations) - horizon + 1):
        window = [observation.player_count for observation in observations[end - window_size:end]]
        prediction = forecaster.predict(window)
        if not math.isfinite(prediction) or prediction < 0:
            raise ValueError("Prediction must be finite and nonnegative")
        target = observations[end + horizon - 1].player_count
        errors.append(prediction - target)
    return ForecastScore(
        sample_count=len(errors),
        mae=fmean(abs(error) for error in errors),
        rmse=math.sqrt(fmean(error * error for error in errors)),
    )
