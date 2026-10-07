import json
import logging
import re
import subprocess
from uuid import uuid4

from .vm_controller import ControllerError


logger = logging.getLogger("ruby_acorn.docker_controller")
OWNER_LABEL = "ruby-acorn.managed"
NAMESPACE_LABEL = "ruby-acorn.namespace"
GAME_LABEL = "ruby-acorn.game"
DEMO_IMAGE = "alpine:3.22"


class DockerController:
    """Manage bounded, label-scoped local containers through the Docker CLI."""

    def __init__(self, namespace="ruby-acorn-demo", game="default", max_instances=5, runner=None):
        for value in (namespace, game):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,48}", value):
                raise ValueError("Docker namespace/game must be a short alphanumeric identifier")
        if not isinstance(max_instances, int) or not 1 <= max_instances <= 5:
            raise ValueError("Docker demo limit must be between 1 and 5")
        self.namespace = namespace
        self.game = game
        self.max_instances = max_instances
        self._runner = runner or subprocess.run

    def _docker(self, *args):
        try:
            result = self._runner(
                ["docker", *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ControllerError(f"Docker command failed: {error}") from error
        if result.returncode:
            raise ControllerError(f"Docker {' '.join(args[:2])} failed: {result.stderr.strip()}")
        return result.stdout.strip()

    def _containers(self, *, all_games=False, include_stopped=False):
        args = ["ps", "--no-trunc", "--format", "{{json .}}",
                "--filter", f"label={OWNER_LABEL}=true",
                "--filter", f"label={NAMESPACE_LABEL}={self.namespace}"]
        if not all_games:
            args.extend(["--filter", f"label={GAME_LABEL}={self.game}"])
        if include_stopped:
            args.append("--all")
        else:
            args.extend(["--filter", "status=running"])
        output = self._docker(*args)
        try:
            containers = [json.loads(line) for line in output.splitlines()]
            ids = [container["ID"] for container in containers]
            if any(not isinstance(cid, str) or not re.fullmatch(r"[0-9a-f]{12,64}", cid) for cid in ids):
                raise ValueError("Invalid container ID")
        except (ValueError, TypeError, KeyError) as error:
            raise ControllerError("Docker returned malformed container data") from error
        return sorted(ids)

    def running_instances(self):
        return len(self._containers())

    def container_ids(self, include_stopped=False):
        return self._containers(include_stopped=include_stopped)

    @staticmethod
    def _validate_count(count):
        if not isinstance(count, int) or count < 0:
            raise ValueError("Scaling count must be a nonnegative integer")

    def scale_up(self, count):
        self._validate_count(count)
        if count == 0:
            return
        allocated = len(self._containers(all_games=True, include_stopped=True))
        if allocated + count > self.max_instances:
            raise ControllerError(f"Docker demo limit {self.max_instances} exceeded in {self.namespace}")
        for _ in range(count):
            name = f"ra-{self.namespace}-{self.game}-{uuid4().hex[:8]}"
            cid = self._docker(
                "run", "--detach", "--rm", "--name", name,
                "--label", f"{OWNER_LABEL}=true",
                "--label", f"{NAMESPACE_LABEL}={self.namespace}",
                "--label", f"{GAME_LABEL}={self.game}",
                "--network", "none", "--memory", "32m", "--memory-swap", "32m",
                "--cpus", "0.1", "--pids-limit", "32", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges=true",
                "--user", "65534:65534", "--init", "--stop-timeout", "2",
                DEMO_IMAGE, "sleep", "infinity",
            )
            logger.info("Started container game=%s id=%s", self.game, cid)

    def scale_down(self, count):
        self._validate_count(count)
        if count == 0:
            return
        for cid in self._containers()[:count]:
            self._docker("stop", "--timeout", "2", cid)
            logger.info("Stopped container game=%s id=%s", self.game, cid)

    def cleanup(self):
        """Remove only this controller's labeled demo resources."""
        for cid in self._containers(include_stopped=True):
            self._docker("rm", "--force", cid)
            logger.info("Removed demo container game=%s id=%s", self.game, cid)
