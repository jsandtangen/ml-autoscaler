from __future__ import annotations

import argparse
import logging
import math
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from scaler.data.client import GameDataClient, GameDataClientError
from scaler.data.history import PlayerHistoryStore
from scaler.engine.decision_engine import DecisionEngine
from scaler.exporter.autoscaler_metrics import (
    AutoscalerMetrics,
    AutoscalerMetricsConfig,
    start_metrics_server,
)
from scaler.infrastructure.vm_controller import ControllerError, FakeVMController
from scaler.infrastructure.docker_controller import DockerController
from scaler.cost import CostModel
from scaler.game_config import GameConfig, load_games
from scaler.strategies.registry import (
    DEFAULT_STRATEGY, STRATEGIES, create_strategy, strategy_name,
)


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
    vm_cost_per_hour: float = 0.10
    fixed_baseline_instances: int = 4
    controller_backend: str = "fake"
    docker_namespace: str = "ruby-acorn-demo"
    docker_max_instances: int = 5
    player_history_path: str | None = None


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


def nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected a nonnegative number") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError("Value must be finite and nonnegative")
    return parsed


def nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected a nonnegative integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("Value must be nonnegative")
    return parsed


def load_config(argv: Sequence[str] | None = None) -> AutoscalerConfig:
    parser = argparse.ArgumentParser(description="Run the Ruby Acorn autoscaler loop.")
    parser.add_argument(
        "--controller", choices=["fake", "docker"],
        default=os.getenv("AUTOSCALER_CONTROLLER", "fake"),
        help="Resource controller. Docker mode requires the host Docker CLI.",
    )
    parser.add_argument(
        "--docker-namespace", default=os.getenv("AUTOSCALER_DOCKER_NAMESPACE", "ruby-acorn-demo"),
        help="Ownership namespace for Docker demo containers.",
    )
    parser.add_argument(
        "--docker-max-instances", type=positive_int, choices=range(1, 6),
        default=os.getenv("AUTOSCALER_DOCKER_MAX_INSTANCES", "5"),
        help="Maximum containers across all games in the namespace (1-5).",
    )
    parser.add_argument(
        "--vm-cost-per-hour", type=nonnegative_float,
        default=os.getenv("AUTOSCALER_VM_COST_PER_HOUR", "0.10"),
        help="Estimated EUR per VM-hour. Also AUTOSCALER_VM_COST_PER_HOUR.",
    )
    parser.add_argument(
        "--fixed-baseline-instances", type=nonnegative_int,
        default=os.getenv("AUTOSCALER_FIXED_BASELINE_INSTANCES", "4"),
        help="Fixed VM baseline per game. Also AUTOSCALER_FIXED_BASELINE_INSTANCES.",
    )
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
    parser.add_argument(
        "--player-history-path",
        default=os.getenv("AUTOSCALER_PLAYER_HISTORY_PATH"),
        help=(
            "CSV file for historical player-count samples. "
            "Can also be set with AUTOSCALER_PLAYER_HISTORY_PATH."
        ),
    )

    args = parser.parse_args(argv)
    if args.strategy not in STRATEGIES:
        parser.error(f"Unknown scaling strategy: {args.strategy}")
    if args.controller not in {"fake", "docker"}:
        parser.error(f"Unknown controller: {args.controller}")
    if not 1 <= args.docker_max_instances <= 5:
        parser.error("Docker demo limit must be between 1 and 5")
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
        vm_cost_per_hour=args.vm_cost_per_hour,
        fixed_baseline_instances=args.fixed_baseline_instances,
        controller_backend=args.controller,
        docker_namespace=args.docker_namespace,
        docker_max_instances=args.docker_max_instances,
        player_history_path=args.player_history_path,
    )


def build_engine(
    strategy_name: str = DEFAULT_STRATEGY,
    config: AutoscalerConfig | None = None,
    game: str = "default",
) -> DecisionEngine:
    if config is None or config.controller_backend == "fake":
        controller = FakeVMController()
    elif config.controller_backend == "docker":
        controller = DockerController(config.docker_namespace, game, config.docker_max_instances)
    else:
        raise ValueError(f"Unknown controller: {config.controller_backend}")
    return DecisionEngine(create_strategy(strategy_name), controller)


def evaluate_once(
    client: GameDataClient,
    engine: DecisionEngine,
    label_filters: dict[str, str],
    metrics: AutoscalerMetrics | None = None,
    metrics_appid: str | None = None,
    history: PlayerHistoryStore | None = None,
) -> int:
    player_count = client.get_player_count(label_filters)
    if history:
        history.record(player_count, appid=metrics_appid or label_filters.get("appid"))
    current_instances = engine.vm_controller.running_instances()
    desired_instances = engine.strategy.desired_instances(player_count)
    running_instances = engine.apply_desired_instances(desired_instances)
    if metrics:
        metrics.record_decision(
            player_count, current_instances, desired_instances, running_instances,
            strategy_name(engine.strategy), appid=metrics_appid,
        )

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
    metrics.set_cost_model(CostModel(config.vm_cost_per_hour, config.fixed_baseline_instances))
    history = PlayerHistoryStore(config.player_history_path) if config.player_history_path else None
    if config.games:
        if engine is not None:
            raise ValueError("A shared engine cannot be used with per-game configuration")
        evaluations = []
        for game in config.games:
            labels = {**config.label_filters, "appid": game.appid}
            evaluations.append((game.appid, labels, build_engine(game.strategy_name, config, game.appid)))
            metrics.set_running_instances(0, appid=game.appid)
            logger.info(
                "Configured game appid=%s strategy=%s", game.appid, game.strategy_name
            )
    else:
        evaluations = [
            (None, config.label_filters, engine or build_engine(
                config.strategy_name, config, config.label_filters.get("appid", "default")
            ))
        ]

    for appid, labels, game_engine in evaluations:
        metrics.set_running_instances(game_engine.vm_controller.running_instances(), appid=appid)
        metrics.set_strategy(strategy_name(game_engine.strategy), appid=appid)

    logger.info(
        "Starting autoscaler loop prometheus_url=%s metric_name=%s labels=%s "
        "interval_seconds=%s strategy=%s controller=%s history_path=%s",
        config.prometheus_url,
        config.metric_name,
        config.label_filters or "{}",
        config.interval_seconds,
        "per-game" if config.games else config.strategy_name,
        config.controller_backend,
        config.player_history_path or "disabled",
    )
    if not config.run_once:
        start_metrics_server(
            AutoscalerMetricsConfig(config.metrics_host, config.metrics_port),
            metrics,
        )

    while True:
        for appid, labels, game_engine in evaluations:
            try:
                evaluate_once(
                    client, game_engine, labels, metrics, metrics_appid=appid, history=history
                )
            except GameDataClientError as error:
                logger.warning(
                    "Skipping autoscaler evaluation: %s appid=%s",
                    error,
                    labels.get("appid", "unspecified"),
                )
            except ControllerError as error:
                logger.error("Scaling failed appid=%s: %s", labels.get("appid", "unspecified"), error)
                try:
                    metrics.set_running_instances(game_engine.vm_controller.running_instances(), appid=appid)
                except ControllerError:
                    logger.warning("Unable to refresh running instance count")
                if config.run_once:
                    raise

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
    except (ControllerError, ValueError) as error:
        logger.error("Autoscaler failed: %s", error)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
