import json
import subprocess
from types import SimpleNamespace

import pytest

from scaler.engine.decision_engine import DecisionEngine
from scaler.infrastructure.docker_controller import DockerController
from scaler.infrastructure.vm_controller import ControllerError
from scaler.strategies.threshold import ThresholdStrategy


class FakeDocker:
    def __init__(self):
        self.containers = {}
        self.commands = []
        self.next_id = 1
        self.fail_on_run = None

    def __call__(self, command, **kwargs):
        assert kwargs["timeout"] == 60
        self.commands.append(command)
        args = command[1:]
        if args[0] == "ps":
            filters = [args[i + 1][6:] for i, value in enumerate(args) if value == "--filter" and args[i + 1].startswith("label=")]
            selected = []
            for cid, labels in self.containers.items():
                if all(labels.get(key) == value for key, value in (f.split("=", 1) for f in filters)):
                    selected.append(json.dumps({"ID": cid}))
            return SimpleNamespace(returncode=0, stdout="\n".join(selected), stderr="")
        if args[0] == "run":
            if self.next_id == self.fail_on_run:
                return SimpleNamespace(returncode=1, stdout="", stderr="simulated daemon failure")
            cid = f"{self.next_id:064x}"
            self.next_id += 1
            labels = dict(args[i + 1].split("=", 1) for i, value in enumerate(args) if value == "--label")
            self.containers[cid] = labels
            return SimpleNamespace(returncode=0, stdout=cid, stderr="")
        if args[0] in {"stop", "rm"}:
            del self.containers[args[-1]]
            return SimpleNamespace(returncode=0, stdout=args[-1], stderr="")
        raise AssertionError(command)


def test_engine_controls_resources_through_docker_controller():
    docker = FakeDocker()
    controller = DockerController("test", "730", runner=docker)
    engine = DecisionEngine(ThresholdStrategy(), controller)
    for players, expected in [(50, 1), (150, 2), (700_000, 4), (50, 1)]:
        assert engine.evaluate(players) == expected
        assert len(docker.containers) == expected
    assert len([cmd for cmd in docker.commands if cmd[1] == "stop"]) == 3
    controller.cleanup()
    assert docker.containers == {}


def test_ownership_filters_isolate_games_and_namespaces():
    docker = FakeDocker()
    first = DockerController("test", "730", runner=docker)
    second = DockerController("test", "570", runner=docker)
    unrelated = DockerController("other", "730", runner=docker)
    first.scale_up(2)
    second.scale_up(1)
    unrelated.scale_up(1)
    first.scale_down(100)
    first.cleanup()
    assert first.running_instances() == 0
    assert second.running_instances() == unrelated.running_instances() == 1
    restarted = DockerController("test", "570", runner=docker)
    assert restarted.running_instances() == 1


def test_limit_is_shared_across_games_and_does_not_silently_clamp():
    docker = FakeDocker()
    first = DockerController("test", "730", max_instances=3, runner=docker)
    second = DockerController("test", "570", max_instances=3, runner=docker)
    first.scale_up(2)
    with pytest.raises(ControllerError, match="limit"):
        second.scale_up(2)
    assert len(docker.containers) == 2
    second.scale_up(1)
    assert len(docker.containers) == 3


def test_demo_containers_have_resource_limits_and_no_host_mounts():
    docker = FakeDocker()
    controller = DockerController(runner=docker)
    controller.scale_up(1)
    run = next(command for command in docker.commands if command[1] == "run")
    for flag, value in [("--memory", "32m"), ("--cpus", "0.1"), ("--network", "none"), ("--pids-limit", "32")]:
        assert run[run.index(flag) + 1] == value
    assert "--rm" in run and "--read-only" in run
    assert "--privileged" not in run and "--volume" not in run


def test_partial_creation_failure_reports_real_count():
    docker = FakeDocker()
    docker.fail_on_run = 2
    controller = DockerController(runner=docker)
    with pytest.raises(ControllerError, match="daemon failure"):
        controller.scale_up(3)
    assert controller.running_instances() == 1
    controller.cleanup()
    assert controller.running_instances() == 0


@pytest.mark.parametrize("error", [FileNotFoundError("docker missing"), subprocess.TimeoutExpired("docker", 60)])
def test_command_failures_are_controller_errors(error):
    def runner(*args, **kwargs):
        raise error
    with pytest.raises(ControllerError):
        DockerController(runner=runner).running_instances()


def test_malformed_docker_response_is_not_zero_instances():
    def runner(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout='{"ID":"--all"}', stderr="")
    with pytest.raises(ControllerError, match="malformed"):
        DockerController(runner=runner).running_instances()


@pytest.mark.parametrize("limit", [0, 6, -1])
def test_invalid_limits_are_rejected(limit):
    with pytest.raises(ValueError):
        DockerController(max_instances=limit)


def test_invalid_ownership_identifiers_are_rejected():
    with pytest.raises(ValueError):
        DockerController(namespace="--all")
