import argparse
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from uuid import uuid4

from scaler.data.client import GameDataClient, GameDataClientError
from scaler.engine.decision_engine import DecisionEngine
from scaler.infrastructure.docker_controller import DockerController
from scaler.infrastructure.vm_controller import ControllerError
from scaler.strategies.registry import STRATEGIES, create_strategy


def main(argv=None):
    parser = argparse.ArgumentParser(description="Demonstrate real local Docker scaling and cleanup.")
    parser.add_argument("--strategy", choices=sorted(STRATEGIES), default="threshold")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--prometheus-url", help="Optionally evaluate a live appid=730 sample after the scenario.")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    namespace = "demo-" + uuid4().hex[:12]
    controller = DockerController(namespace=namespace, game="730")
    engine = DecisionEngine(create_strategy(args.strategy), controller)
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "resource_type": "local Docker containers, not full virtual machines",
        "namespace": namespace, "strategy": args.strategy,
        "input_source": "synthetic player counts; optional final live Prometheus sample",
        "max_containers": 5, "steps": [], "success": False,
    }
    exit_code = 0
    try:
        for players in [50, 150, 700_000, 50]:
            record_step(report, engine, controller, players, "synthetic")
        if args.prometheus_url:
            players = GameDataClient(args.prometheus_url).get_player_count({"appid": "730"})
            record_step(report, engine, controller, players, "Prometheus appid=730")
        report["success"] = True
    except (ControllerError, GameDataClientError) as error:
        report["error"] = str(error)
        logging.error("Demo failed: %s", error)
        exit_code = 1
    finally:
        try:
            controller.cleanup()
            report["remaining_containers"] = controller.container_ids(include_stopped=True)
            if report["remaining_containers"]:
                raise ControllerError("Demo resources remain after cleanup")
            print(f"cleanup namespace={namespace} remaining=0")
        except ControllerError as error:
            report["cleanup_error"] = str(error)
            report["success"] = False
            logging.error("Cleanup failed: %s", error)
            exit_code = 1
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return exit_code


def record_step(report, engine, controller, players, source):
    previous = controller.running_instances()
    desired = engine.strategy.desired_instances(players)
    running = engine.apply_desired_instances(desired)
    containers = controller.container_ids()
    if running != desired or len(containers) != desired:
        raise ControllerError(f"Expected {desired} live containers, got {len(containers)}")
    step = {
        "source": source, "player_count": players,
        "previous_instances": previous, "desired_instances": desired,
        "running_instances": running, "container_ids": containers,
    }
    report["steps"].append(step)
    print(f"source={source} players={players} instances={previous}->{running} ids={containers}")


if __name__ == "__main__":
    raise SystemExit(main())
