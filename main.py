from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from scaler.data.client import GameDataClient, GameDataClientError
from scaler.engine.decision_engine import DecisionEngine
from scaler.exporter.autoscaler_metrics import (
    AutoscalerMetrics,
    AutoscalerMetricsConfig,
    start_metrics_server,
)
from scaler.infrastructure.vm_controller import FakeVMController
from scaler.game_config import GameConfig, load_games
from scaler.strategies.registry import DEFAULT_STRATEGY, STRATEGIES, create_strategy


DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"
DEFAULT_INTERVAL_SECONDS = 30.0
DEFAULT_METRIC_NAME = "steam_player_count"
DEFAULT_METRICS_HOST = "0.0.0.0"
DEFAULT_METRICS_PORT = 8001

logger = logging.getLogger("ruby_acorn.autoscaler")


@dataclass(frozen=True)
class AutoscalerConfig:
    prometheus_url: str
    interval_seconds: float
    metric_name: str
    label_filters: dict[str, str]
    metrics_host: str = DEFAULT_METRICS_HOST
    metrics_port: int = DEFAULT_METRICS_PORT
    run_once: bool = False
    strategy_name: str = DEFAULT_STRATEGY
    games: tuple[GameConfig, ...] = ()


def parse_label_filter(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"Label filter must use key=value format: {value}"
        )

    key, label_value = value.split("=", 1)
    key = key.strip()

    if not key:
        raise argparse.ArgumentTypeError("Label filter key cannot be empty")

    return key, label_value.strip()


def parse_env_label_filters(value: str | None) -> dict[str, str]:
    if not value:
        return {}

    filters: dict[str, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        key, label_value = parse_label_filter(item)
        filters[key] = label_value

    return filters


def positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Expected a number, got {value}") from error

    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than zero")

    return parsed


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Expected an integer, got {value}") from error

    if parsed <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than zero")

    return parsed


def load_config(argv: Sequence[str] | None = None) -> AutoscalerConfig:
    parser = argparse.ArgumentParser(description="Run the Ruby Acorn autoscaler loop.")
    parser.add_argument(
        "--games-config",
        default=os.getenv("AUTOSCALER_GAMES_CONFIG"),
        help="TOML file with per-game strategies. Also AUTOSCALER_GAMES_CONFIG.",
    )
    parser.add_argument(
        "--strategy",
        choices=sorted(STRATEGIES),
        default=os.getenv("AUTOSCALER_STRATEGY", DEFAULT_STRATEGY),
        help="Scaling strategy. Can also be set with AUTOSCALER_STRATEGY.",
    )
    parser.add_argument(
        "--prometheus-url",
        default=os.getenv("PROMETHEUS_URL", DEFAULT_PROMETHEUS_URL),
        help="Base URL for Prometheus. Can also be set with PROMETHEUS_URL.",
    )
    parser.add_argument(
        "--interval",
        type=positive_float,
        default=positive_float(
            os.getenv("AUTOSCALER_INTERVAL_SECONDS", str(DEFAULT_INTERVAL_SECONDS))
        ),
        help="Seconds between autoscaler evaluations.",
    )
    parser.add_argument(
        "--metric-name",
        default=os.getenv("AUTOSCALER_METRIC_NAME", DEFAULT_METRIC_NAME),
        help="Prometheus metric containing player counts.",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        type=parse_label_filter,
        metavar="KEY=VALUE",
        help=(
            "Prometheus label filter. Can be repeated. "
            "Defaults can also be set with AUTOSCALER_LABELS=key=value,key2=value2."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one autoscaler evaluation and exit.",
    )
    parser.add_argument(
        "--metrics-host",
        default=os.getenv("AUTOSCALER_METRICS_HOST", DEFAULT_METRICS_HOST),
        help="Autoscaler metrics bind host. Can also be set with AUTOSCALER_METRICS_HOST.",
    )
    parser.add_argument(
        "--metrics-port",
        type=positive_int,
        default=positive_int(
            os.getenv("AUTOSCALER_METRICS_PORT", str(DEFAULT_METRICS_PORT))
        ),
        help="Autoscaler metrics port. Can also be set with AUTOSCALER_METRICS_PORT.",
    )

    args = parser.parse_args(argv)
    if args.strategy not in STRATEGIES:
        parser.error(f"Unknown scaling strategy: {args.strategy}")
    label_filters = parse_env_label_filters(os.getenv("AUTOSCALER_LABELS"))
    label_filters.update(dict(args.label))
    try:
        games = load_games(args.games_config) if args.games_config else ()
    except (OSError, ValueError) as error:
        parser.error(f"Invalid games configuration: {error}")

    return AutoscalerConfig(
        prometheus_url=args.prometheus_url,
        interval_seconds=args.interval,
        metric_name=args.metric_name,
        label_filters=label_filters,
        metrics_host=args.metrics_host,
        metrics_port=args.metrics_port,
        run_once=args.once,
        strategy_name=args.strategy,
        games=games,
    )


def build_engine(strategy_name: str = DEFAULT_STRATEGY) -> DecisionEngine:
    return DecisionEngine(create_strategy(strategy_name), FakeVMController())


def evaluate_once(
    client: GameDataClient,
    engine: DecisionEngine,
    label_filters: dict[str, str],
    metrics: AutoscalerMetrics | None = None,
    metrics_appid: str | None = None,
) -> int:
    player_count = client.get_player_count(label_filters)
    current_instances = engine.vm_controller.running_instances()
    desired_instances = engine.strategy.desired_instances(player_count)
    running_instances = engine.apply_desired_instances(desired_instances)
    if metrics:
        metrics.set_running_instances(running_instances, appid=metrics_appid)

    if desired_instances > current_instances:
        action = f"scaled up by {desired_instances - current_instances}"
    elif desired_instances < current_instances:
        action = f"scaled down by {current_instances - desired_instances}"
    else:
        action = "no scaling action"

    logger.info(
        "players=%s current_instances=%s desired_instances=%s "
        "running_instances=%s action=%s appid=%s",
        player_count,
        current_instances,
        desired_instances,
        running_instances,
        action,
        label_filters.get("appid", "unspecified"),
    )

    return running_instances


def run_loop(
    config: AutoscalerConfig,
    client: GameDataClient | None = None,
    engine: DecisionEngine | None = None,
    metrics: AutoscalerMetrics | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    client = client or GameDataClient(
        config.prometheus_url,
        metric_name=config.metric_name,
    )
    metrics = metrics or AutoscalerMetrics()
    if config.games:
        if engine is not None:
            raise ValueError("A shared engine cannot be used with per-game configuration")
        evaluations = []
        for game in config.games:
            labels = {**config.label_filters, "appid": game.appid}
            evaluations.append((game.appid, labels, build_engine(game.strategy_name)))
            metrics.set_running_instances(0, appid=game.appid)
            logger.info(
                "Configured game appid=%s strategy=%s", game.appid, game.strategy_name
            )
    else:
        evaluations = [
            (None, config.label_filters, engine or build_engine(config.strategy_name))
        ]

    logger.info(
        "Starting autoscaler loop prometheus_url=%s metric_name=%s labels=%s "
        "interval_seconds=%s strategy=%s",
        config.prometheus_url,
        config.metric_name,
        config.label_filters or "{}",
        config.interval_seconds,
        "per-game" if config.games else config.strategy_name,
    )
    if not config.run_once:
        start_metrics_server(
            AutoscalerMetricsConfig(config.metrics_host, config.metrics_port),
            metrics,
        )

    while True:
        for appid, labels, game_engine in evaluations:
            try:
                evaluate_once(client, game_engine, labels, metrics, metrics_appid=appid)
            except GameDataClientError as error:
                logger.warning(
                    "Skipping autoscaler evaluation: %s appid=%s",
                    error,
                    labels.get("appid", "unspecified"),
                )

        if config.run_once:
            return

        sleep(config.interval_seconds)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    config = load_config(argv)

    try:
        run_loop(config)
    except KeyboardInterrupt:
        logger.info("Autoscaler loop stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
