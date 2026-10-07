from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Sequence
from dataclasses import asdict

from scaler.forecasting import (
    MovingAverageForecaster,
    PersistenceForecaster,
    evaluate_forecaster,
    load_player_history,
)


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected a positive integer") from error
    if number < 1:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare forecasting baselines on player history.")
    parser.add_argument("--history-path", default="data/player_history.csv")
    parser.add_argument("--appid", help="Select one game; omit to evaluate all games separately.")
    parser.add_argument("--window-size", type=positive_int, default=10)
    parser.add_argument("--horizon", type=positive_int, default=1,
                        help="Number of future observations, not seconds.")
    args = parser.parse_args(argv)
    try:
        games = load_player_history(args.history_path)
        if args.appid is not None:
            if args.appid not in games:
                raise ValueError(f"No history for appid {args.appid!r}")
            games = {args.appid: games[args.appid]}
        if not games:
            raise ValueError("History contains no observations")
        report = {
            "window_size": args.window_size,
            "horizon_observations": args.horizon,
            "games": {},
        }
        for appid, observations in games.items():
            models = {}
            for name, model in (
                ("persistence", PersistenceForecaster()),
                ("moving_average", MovingAverageForecaster()),
            ):
                try:
                    score = evaluate_forecaster(observations, model, args.window_size, args.horizon)
                except ValueError as error:
                    raise ValueError(f"appid {appid!r}: {error}") from error
                models[name] = {
                    **asdict(score),
                    "latest_prediction": model.predict([
                        observation.player_count for observation in observations[-args.window_size:]
                    ]),
                }
            report["games"][appid] = models
    except (OSError, ValueError, csv.Error) as error:
        parser.exit(1, f"Forecast evaluation failed: {error}\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
