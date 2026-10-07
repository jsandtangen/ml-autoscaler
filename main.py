from __future__ import annotations

import argparse
import logging
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from scaler.data.client import GameDataClient, GameDataClientError
from scaler.engine.decision_engine import DecisionEngine
from scaler.infrastructure.vm_controller import FakeVMController
from scaler.strategies.threshold import ThresholdStrategy


DEFAULT_PROMETHEUS_URL = "http://127.0.0.1:9090"
DEFAULT_INTERVAL_SECONDS = 30.0
DEFAULT_METRIC_NAME = "steam_player_count"

logger = logging.getLogger("ruby_acorn.autoscaler")


@dataclass(frozen=True)
class AutoscalerConfig:
    prometheus_url: str
    interval_seconds: float
    metric_name: str
    label_filters: dict[str, str]
    run_once: bool = False


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


def load_config(argv: Sequence[str] | None = None) -> AutoscalerConfig:
    parser = argparse.ArgumentParser(description="Run the Ruby Acorn autoscaler loop.")
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

    args = parser.parse_args(argv)
    label_filters = parse_env_label_filters(os.getenv("AUTOSCALER_LABELS"))
    label_filters.update(dict(args.label))

    return AutoscalerConfig(
        prometheus_url=args.prometheus_url,
        interval_seconds=args.interval,
        metric_name=args.metric_name,
        label_filters=label_filters,
        run_once=args.once,
    )


def build_engine() -> DecisionEngine:
    return DecisionEngine(ThresholdStrategy(), FakeVMController())


def evaluate_once(
    client: GameDataClient,
    engine: DecisionEngine,
    label_filters: dict[str, str],
) -> int:
    player_count = client.get_player_count(label_filters)
    current_instances = engine.vm_controller.running_instances()
    desired_instances = engine.strategy.desired_instances(player_count)
    running_instances = engine.evaluate(player_count)

    if desired_instances > current_instances:
        action = f"scaled up by {desired_instances - current_instances}"
    elif desired_instances < current_instances:
        action = f"scaled down by {current_instances - desired_instances}"
    else:
        action = "no scaling action"

    logger.info(
        "players=%s current_instances=%s desired_instances=%s "
        "running_instances=%s action=%s",
        player_count,
        current_instances,
        desired_instances,
        running_instances,
        action,
    )

    return running_instances


def run_loop(
    config: AutoscalerConfig,
    client: GameDataClient | None = None,
    engine: DecisionEngine | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    client = client or GameDataClient(
        config.prometheus_url,
        metric_name=config.metric_name,
    )
    engine = engine or build_engine()

    logger.info(
        "Starting autoscaler loop prometheus_url=%s metric_name=%s labels=%s "
        "interval_seconds=%s",
        config.prometheus_url,
        config.metric_name,
        config.label_filters or "{}",
        config.interval_seconds,
    )

    while True:
        try:
            evaluate_once(client, engine, config.label_filters)
        except GameDataClientError as error:
            logger.warning("Skipping autoscaler evaluation: %s", error)

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
