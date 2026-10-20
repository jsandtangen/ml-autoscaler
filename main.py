from __future__ import annotations

import argparse
import logging
import math
import os
import time
import signal
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
import json
from pathlib import Path

from scaler.data.client import GameDataClient, GameDataClientError
from scaler.data.history import PlayerHistoryStore
from scaler.engine.decision_engine import DecisionEngine
from scaler.engine.scaling_limits import ScalingLimits
from scaler.strategies.capacity import CapacityPolicy
from scaler.strategies.demand import DemandTuning
from scaler.exporter.autoscaler_metrics import (
    AutoscalerMetrics,
    AutoscalerMetricsConfig,
    start_metrics_server,
)
from scaler.infrastructure.vm_controller import ControllerError, FakeVMController
from scaler.infrastructure.docker_controller import DockerController
from scaler.infrastructure.openstack_controller import OpenStackController, OpenStackPool
from scaler.infrastructure.pegasus_controller import PegasusController, PegasusConfig
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
    capacity_policy: CapacityPolicy = field(default_factory=CapacityPolicy)
    max_instances: int = 7
    max_scaling_change: int = 2
    max_sample_age_seconds: int = 90
    shadow_decisions_dir: str | None = "data/shadow-capacity"
    simulation_report_path: str | None = None
    openstack_pool: OpenStackPool | None = None
    pegasus_config: PegasusConfig | None = None
    pegasus_apply: bool = False
    demand_tuning: DemandTuning = field(default_factory=DemandTuning)


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

    if not math.isfinite(parsed) or parsed <= 0:
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
    parser.add_argument("--aggressive-buffer", type=nonnegative_float, default=0.30)
    parser.add_argument("--aggressive-spare", type=nonnegative_int, default=1)
    parser.add_argument("--cost-buffer", type=nonnegative_float, default=0.0)
    parser.add_argument("--scale-up-threshold", type=positive_float, default=1.10)
    parser.add_argument("--scale-down-threshold", type=positive_float, default=0.80)
    parser.add_argument("--downscale-seconds", type=nonnegative_float, default=180)
    parser.add_argument("--player-share", type=nonnegative_float, default=os.getenv("AUTOSCALER_PLAYER_SHARE", "0.001"))
    parser.add_argument("--players-per-instance", type=positive_int, default=os.getenv("AUTOSCALER_PLAYERS_PER_INSTANCE", "100"))
    parser.add_argument("--headroom-fraction", type=nonnegative_float, default=os.getenv("AUTOSCALER_HEADROOM_FRACTION", "0.10"))
    parser.add_argument("--minimum-instances", type=nonnegative_int, default=os.getenv("AUTOSCALER_MINIMUM_INSTANCES", "1"))
    parser.add_argument("--max-instances", type=positive_int, default=os.getenv("AUTOSCALER_MAX_INSTANCES"))
    parser.add_argument("--max-scaling-change", type=positive_int, default=os.getenv("AUTOSCALER_MAX_SCALING_CHANGE", "2"))
    parser.add_argument("--max-sample-age-seconds", type=positive_int, default=os.getenv("AUTOSCALER_MAX_SAMPLE_AGE_SECONDS", "90"))
    parser.add_argument("--shadow-decisions-dir", default=os.getenv("AUTOSCALER_SHADOW_DECISIONS_DIR", "data/shadow-capacity"))
    parser.add_argument("--simulation-report-path", default=os.getenv("AUTOSCALER_SIMULATION_REPORT_PATH"),
                        help="Read-only offline comparison JSON to expose as separate simulation metrics.")
    parser.add_argument(
        "--controller", choices=["fake", "docker", "openstack", "pegasus"],
        default=os.getenv("AUTOSCALER_CONTROLLER", "fake"),
        help="Resource controller. Docker mode requires the host Docker CLI.",
    )
    parser.add_argument("--openstack-pool", default=os.getenv("AUTOSCALER_OPENSTACK_POOL"),
                        help="JSON file for one explicitly owned fixed VM pool over SSH.")
    parser.add_argument("--pegasus-config", help="Non-secret Pegasus SDK configuration JSON.")
    parser.add_argument("--pegasus-apply", action="store_true",
                        help="Enable real Pegasus changes ONLY after explicit approval. Default is dry-run.")
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
    try:
        tuning = DemandTuning(args.aggressive_buffer, args.aggressive_spare, args.cost_buffer,
                              args.scale_up_threshold, args.scale_down_threshold, args.downscale_seconds)
    except ValueError as error:
        parser.error(str(error))
    pool = None
    pegasus = None
    if args.pegasus_apply and args.controller != "pegasus":
        parser.error("--pegasus-apply requires --controller pegasus")
    if args.controller == "pegasus":
        try:
            if not args.pegasus_config:
                raise ValueError("Pegasus requires --pegasus-config")
            pegasus = PegasusConfig.load(args.pegasus_config)
        except (OSError, ValueError, KeyError, TypeError):
            parser.error("Invalid Pegasus configuration; see docs/pegasus.md")
    if args.controller == "openstack":
        try:
            if not args.openstack_pool:
                raise ValueError("OpenStack requires --openstack-pool")
            pool = OpenStackPool.load(args.openstack_pool)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(f"Invalid OpenStack pool: {error}")
    maximum = args.max_instances if args.max_instances is not None else (
        pegasus.maximum_instances if pegasus else len(pool.server_ids) if pool else args.docker_max_instances if args.controller == "docker" else 7)
    if pegasus and maximum > pegasus.maximum_instances:
        parser.error("Maximum instances exceeds the Pegasus configured limit")
    if pool and maximum > len(pool.server_ids):
        parser.error("Maximum instances exceeds the fixed VM pool")
    if args.controller == "docker":
        maximum = min(maximum, args.docker_max_instances)
    try:
        policy = CapacityPolicy(args.player_share, args.players_per_instance, args.headroom_fraction, args.minimum_instances)
        ScalingLimits(policy.minimum_instances, maximum, args.max_scaling_change)
    except ValueError as error:
        parser.error(str(error))
    if args.strategy not in STRATEGIES:
        parser.error(f"Unknown scaling strategy: {args.strategy}")
    if args.controller not in {"fake", "docker", "openstack", "pegasus"}:
        parser.error(f"Unknown controller: {args.controller}")
    if not 1 <= args.docker_max_instances <= 5:
        parser.error("Docker demo limit must be between 1 and 5")
    label_filters = parse_env_label_filters(os.getenv("AUTOSCALER_LABELS"))
    label_filters.update(dict(args.label))
    try:
        games = load_games(args.games_config) if args.games_config else ()
    except (OSError, ValueError) as error:
        parser.error(f"Invalid games configuration: {error}")
    if pool and (games or label_filters.get("appid") != pool.appid):
        parser.error("OpenStack supports one game; --label appid must match the pool")
    if pegasus and not games and label_filters.get("appid") != pegasus.appid:
        parser.error("Pegasus --label appid must match configuration")
    if pegasus and games and policy.minimum_instances * len(games) > maximum:
        parser.error("Per-game minimums exceed the shared Pegasus fleet limit")

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
        capacity_policy=policy, max_instances=maximum, max_scaling_change=args.max_scaling_change,
        max_sample_age_seconds=args.max_sample_age_seconds, shadow_decisions_dir=args.shadow_decisions_dir,
        simulation_report_path=args.simulation_report_path,
        openstack_pool=pool,
        pegasus_config=pegasus, pegasus_apply=args.pegasus_apply,
        demand_tuning=tuning,
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
    elif config.controller_backend == "openstack":
        pool = config.openstack_pool
        if (pool is None or config.games or game != pool.appid
                or config.max_instances > len(pool.server_ids)):
            raise ValueError("OpenStack requires a matching single-game bounded pool")
        controller = OpenStackController(pool)
    elif config.controller_backend == "pegasus":
        pc = config.pegasus_config
        if (pc is None or (not config.games and game != pc.appid)
                or (config.games and game not in {g.appid for g in config.games})
                or config.max_instances > pc.maximum_instances):
            raise ValueError("Pegasus requires a matching game and bounded fleet configuration")
        controller = PegasusController(replace(pc, appid=game), dry_run=not config.pegasus_apply)
    else:
        raise ValueError(f"Unknown controller: {config.controller_backend}")
    policy = config.capacity_policy if config else CapacityPolicy()
    maximum = config.max_instances if config else 7
    if config and config.controller_backend == "docker":
        maximum = min(maximum, config.docker_max_instances)
    limits = ScalingLimits(policy.minimum_instances, maximum, config.max_scaling_change if config else 2)
    return DecisionEngine(create_strategy(strategy_name, policy, config.demand_tuning if config else None), controller, limits, policy)


def evaluate_once(
    client: GameDataClient,
    engine: DecisionEngine,
    label_filters: dict[str, str],
    metrics: AutoscalerMetrics | None = None,
    metrics_appid: str | None = None,
    history: PlayerHistoryStore | None = None,
    shadow_decisions_dir: str | None = None,
) -> int:
    player_count = client.get_player_count(label_filters)
    if history:
        history.record(player_count, appid=metrics_appid or label_filters.get("appid"))
    current_instances = engine.vm_controller.running_instances()
    desired_instances = engine.desired_instances(player_count, current_instances)
    if isinstance(engine.vm_controller, PegasusController):
        engine.vm_controller.record_target(engine.limits.plan(desired_instances, current_instances).desired_instances)
    if metrics and engine.capacity_policy is not None:
        metrics.record_strategy_comparison(player_count, engine.capacity_policy, engine.limits,
                                           metrics_appid, strategy_name(engine.strategy), desired_instances)
        metrics.record_capacity_demand(player_count, engine.capacity_policy,
                                       engine.limits, desired_instances, current_instances, metrics_appid)
    running_instances = engine.apply_desired_instances(desired_instances)
    if isinstance(engine.vm_controller, PegasusController) and engine.vm_controller.dry_run:
        logger.info("Pegasus dry-run plan=%s", engine.vm_controller.plan)
    desired_instances = engine.last_plan.desired_instances
    if metrics:
        metrics.record_decision(
            player_count, current_instances, desired_instances, running_instances,
            strategy_name(engine.strategy), appid=metrics_appid,
        )
        if shadow_decisions_dir and engine.capacity_policy is not None:
            appid = metrics_appid or label_filters.get("appid")
            metrics.clear_prediction(metrics_appid)
            if appid:
                try:
                    decision = json.loads((Path(shadow_decisions_dir)/appid/"latest_decision.json").read_text(encoding="utf-8"))
                    metrics.record_shadow_prediction(decision, engine.capacity_policy, engine.limits, metrics_appid, appid)
                except (OSError, ValueError, KeyError, TypeError, OverflowError):
                    pass  # Optional shadow telemetry never affects resource control.

    if running_instances > current_instances:
        action = f"scaled up by {running_instances - current_instances}"
    elif running_instances < current_instances:
        action = f"scaled down by {current_instances - running_instances}"
    else:
        action = "no scaling action"

    logger.info(
        "players=%s current_instances=%s desired_instances=%s "
        "running_instances=%s action=%s appid=%s required_instances=%s "
        "cycle_target_instances=%s unmet_instances=%s",
        player_count,
        current_instances,
        desired_instances,
        running_instances,
        action,
        label_filters.get("appid", "unspecified"),
        engine.capacity_policy.required_instances(player_count) if engine.capacity_policy else engine.last_plan.required_instances,
        engine.last_plan.cycle_target_instances,
        max(0, (engine.capacity_policy.required_instances(player_count) if engine.capacity_policy else engine.last_plan.required_instances)-running_instances),
    )

    return running_instances


def _run_loop(
    config: AutoscalerConfig,
    client: GameDataClient | None = None,
    engine: DecisionEngine | None = None,
    metrics: AutoscalerMetrics | None = None,
    sleep: Callable[[float], None] = time.sleep,
    controllers: list | None = None,
    stop_requested: Callable[[], bool] = lambda: False,
) -> None:
    def tracked(game_engine):
        if isinstance(game_engine.vm_controller, PegasusController):
            controllers.append(game_engine.vm_controller)
        return game_engine
    client = client or GameDataClient(
        config.prometheus_url,
        metric_name=config.metric_name,
        max_sample_age_seconds=config.max_sample_age_seconds,
    )
    metrics = metrics or AutoscalerMetrics()
    metrics.set_simulation_report(config.simulation_report_path)
    metrics.set_cost_model(CostModel(config.vm_cost_per_hour, config.fixed_baseline_instances))
    history = PlayerHistoryStore(config.player_history_path) if config.player_history_path else None
    if config.games:
        if engine is not None:
            raise ValueError("A shared engine cannot be used with per-game configuration")
        evaluations = []
        for game in config.games:
            labels = {**config.label_filters, "appid": game.appid}
            evaluations.append((game.appid, labels, tracked(build_engine(game.strategy_name, config, game.appid))))
            metrics.set_running_instances(0, appid=game.appid)
            logger.info(
                "Configured game appid=%s strategy=%s", game.appid, game.strategy_name
            )
    else:
        evaluations = [
            (None, config.label_filters, tracked(engine or build_engine(
                config.strategy_name, config, config.label_filters.get("appid", "default")
            )))
        ]

    for appid, labels, game_engine in evaluations:
        metrics.set_controller(config.controller_backend, appid)
        if isinstance(game_engine.vm_controller, PegasusController):
            metrics.set_pegasus_provider(game_engine.vm_controller.telemetry, appid)
        metrics.set_source_appid(labels.get("appid"), appid)
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
            if stop_requested():
                return
            try:
                evaluate_once(
                    client, game_engine, labels, metrics, metrics_appid=appid, history=history,
                    shadow_decisions_dir=config.shadow_decisions_dir,
                )
            except GameDataClientError as error:
                reset = getattr(game_engine.strategy, "reset_observations", None)
                if reset:
                    reset()
                metrics.set_input_available(False, appid)
                if isinstance(game_engine.vm_controller, PegasusController):
                    try:
                        metrics.set_running_instances(game_engine.vm_controller.running_instances(), appid)
                    except ControllerError:
                        pass  # Infrastructure availability/age remain explicit in telemetry.
                logger.warning(
                    "Skipping autoscaler evaluation: %s appid=%s",
                    error,
                    labels.get("appid", "unspecified"),
                )
            except ControllerError as error:
                metrics.record_scaling_error(appid)
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


def run_loop(config, client=None, engine=None, metrics=None, sleep=time.sleep,
             stop_requested=lambda: False):
    controllers = []
    try:
        return _run_loop(config, client, engine, metrics, sleep, controllers, stop_requested)
    finally:
        for controller in reversed(controllers):
            controller.close()
            close = getattr(controller.connection, 'close', None)
            if close:
                close()


class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({"timestamp": record.created, "level": record.levelname,
                           "logger": record.name, "message": record.getMessage()})


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if os.getenv("AUTOSCALER_LOG_FORMAT") == "json":
        for handler in logging.getLogger().handlers:
            handler.setFormatter(JsonFormatter())


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    config = load_config(argv)
    stopped = threading.Event()
    def stop(signum, frame):
        stopped.set()
    def interruptible_sleep(seconds):
        if stopped.wait(seconds):
            raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        run_loop(config, sleep=interruptible_sleep, stop_requested=stopped.is_set)
    except KeyboardInterrupt:
        logger.info("Autoscaler loop stopped")
    except (ControllerError, ValueError) as error:
        logger.error("Autoscaler failed: %s", error)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
