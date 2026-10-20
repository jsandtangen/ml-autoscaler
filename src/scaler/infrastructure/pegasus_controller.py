"""Project-scoped OpenStack SDK backend. No deletion or credential handling."""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import threading
import time
from uuid import UUID

from .vm_controller import ControllerError


# One OS lock per fleet, shared only by distinct games in this process.
_fleet_leases = {}
_lease_mutex = threading.Lock()


def _acquire_fleet(config):
    path = (Path(config.state_dir)/config.project_id/f"{config.pool}.manager.lock").resolve()
    key = str(path)
    with _lease_mutex:
        if key in _fleet_leases:
            file, games = _fleet_leases[key]
            if config.appid in games:
                raise ControllerError("Pegasus fleet already has a manager for this game")
            games.add(config.appid)
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        file = path.open('a+b')
        try:
            if file.tell() == 0:
                file.write(b'0')
                file.flush()
            file.seek(0)
            import os
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            file.close()
            raise ControllerError("Pegasus fleet already has another manager") from None
        _fleet_leases[key] = (file, {config.appid})
        return key


@dataclass(frozen=True)
class PegasusConfig:
    cloud: str
    project_id: str
    pool: str
    appid: str
    image_id: str
    flavor_id: str
    network_id: str
    key_name: str
    security_groups: tuple[str, ...]
    maximum_instances: int = 6
    operation_timeout: float = 300
    cooldown_seconds: float = 120
    state_dir: str = "data/pegasus"

    def __post_init__(self):
        for value in (self.cloud, self.pool, self.appid, self.key_name):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,48}", value):
                raise ValueError("Invalid Pegasus cloud/pool/appid/key name")
        UUID(self.project_id)
        if not isinstance(self.flavor_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", self.flavor_id):
            raise ValueError("Invalid flavor ID")
        for value in (self.image_id, self.network_id):
            if str(UUID(value)) != value:
                raise ValueError("Pegasus resource IDs must be canonical UUIDs")
        if type(self.maximum_instances) is not int or not 1 <= self.maximum_instances <= 6:
            raise ValueError("Pegasus demo maximum must be 1-6")
        for value in (self.operation_timeout, self.cooldown_seconds):
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError("Timeout/cooldown must be finite and positive")
        if not self.security_groups or any(not isinstance(s, str) or not s for s in self.security_groups):
            raise ValueError("Specify existing security group names")
        if not self.state_dir:
            raise ValueError("Specify a persistent local state directory")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data["security_groups"], list):
            raise ValueError("security_groups must be a list")
        data["security_groups"] = tuple(data["security_groups"])
        return cls(**data)


def connect(config):
    """Use an external clouds.yaml; never accept a user password in this backend."""
    try:
        import openstack
        cloud = (openstack.config.get_cloud_region() if config.cloud == "env"
                 else openstack.config.get_cloud_region(cloud=config.cloud))
        if (cloud.config.get("auth_type") != "v3applicationcredential"
                or cloud.config.get("verify", True) is False
                or not cloud.config.get("auth", {}).get("auth_url", "").startswith("https://")):
            raise ValueError("Application credentials and TLS verification required")
        # Connection kwargs do not override a supplied CloudRegion. Set these on
        # the region before it creates sessions/proxies; never retry a POST.
        cloud.config["api_timeout"] = 15
        for service in ("", "compute_", "identity_", "image_", "network_"):
            cloud.config[service+"connect_retries"] = 0
            cloud.config[service+"status_code_retries"] = 0
        return openstack.connection.Connection(config=cloud)
    except Exception:
        raise ControllerError("Pegasus SDK configuration failed; check external clouds.yaml and install .[openstack]") from None


class PegasusController:
    """Synchronous bounded operations; ACTIVE is infrastructure state, not readiness.

    A local process lock protects one state directory. Use only one manager across
    hosts. An uncertain creation is durably latched before sending the request and
    requires manual inspection; retries must never create duplicate capacity.
    """

    def __init__(self, config, connection=None, *, dry_run=True,
                 clock=time.monotonic, sleep=time.sleep):
        self.config, self.dry_run = config, dry_run
        self._clock, self._sleep = clock, sleep
        self._mutex = threading.Lock()
        self._metrics = {"dry_run": int(dry_run), "state_available": 0,
                         "pending_operations": 0, "actions_succeeded_total": 0,
                         "actions_failed_total": 0, "action_latency_seconds_sum": 0,
                         "action_latency_seconds_count": 0}
        self.plan = []
        self._last_change = None
        directory = Path(config.state_dir)/config.project_id/f"{config.pool}-{config.appid}"
        directory.mkdir(parents=True, exist_ok=True)
        self._journal = directory/"uncertain-create.json"
        self._cooldown_marker = directory/"last-action.json"
        if self._cooldown_marker.exists():
            # Conservatively wait a full cooldown after a process restart.
            self._last_change = self._clock()
        self._lease = _acquire_fleet(config)
        self.connection = connection
        try:
            self.connection = connection or connect(config)
            if UUID(self.connection.current_project_id) != UUID(config.project_id):
                raise ControllerError("Pegasus authenticated project does not match configured project")
        except Exception:
            self.close()
            raise ControllerError("Pegasus authentication/project verification failed") from None

    def close(self):
        if getattr(self, "_lease", None):
            with _lease_mutex:
                file, games = _fleet_leases[self._lease]
                games.remove(self.config.appid)
                if not games:
                    file.close()
                    del _fleet_leases[self._lease]
            self._lease = None

    @property
    def prefix(self):
        return f"ruby-acorn-pegasus-{self.config.pool}-{self.config.appid}-"

    @property
    def metadata(self):
        return {"ruby_acorn_controller": "pegasus", "ruby_acorn_pool": self.config.pool,
                "ruby_acorn_game": self.config.appid}

    def _api(self, operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except Exception:
            # SDK exceptions may contain URLs, headers, tokens or response bodies.
            raise ControllerError("Pegasus API request failed or timed out; inspect cloud state before retry") from None

    def _owned(self, server):
        return ((server.metadata or {}).get("ruby_acorn_role") not in ("management", "monitoring")
                and server.project_id is not None
                and server.project_id.replace("-", "").lower() == self.config.project_id.replace("-", "").lower()
                and re.fullmatch(re.escape(self.prefix)+r"\d{2}", server.name or "") is not None
                and all((server.metadata or {}).get(k) == v for k, v in self.metadata.items()))

    def _fleet_owned(self, server):
        metadata = server.metadata or {}
        game = metadata.get('ruby_acorn_game')
        return (metadata.get("ruby_acorn_role") not in ("management", "monitoring")
                and isinstance(game, str)
                and server.project_id is not None
                and server.project_id.replace('-', '').lower() == self.config.project_id.replace('-', '').lower()
                and metadata.get('ruby_acorn_controller') == 'pegasus'
                and metadata.get('ruby_acorn_pool') == self.config.pool
                and re.fullmatch(re.escape(f'ruby-acorn-pegasus-{self.config.pool}-{game}-')+r'\d{2}', server.name or '') is not None)

    def snapshot(self):
        try:
            servers = self._api(lambda: list(self.connection.compute.servers(details=True)))
            fleet = [s for s in servers if self._fleet_owned(s)]
            if any((s.name or '').startswith(f'ruby-acorn-pegasus-{self.config.pool}-')
                   and not self._fleet_owned(s) for s in servers):
                raise ControllerError('Pegasus fleet ownership mismatch')
            managed = []
            for server in servers:
                if self._owned(server):
                    managed.append(server)
                elif (server.name or "").startswith(self.prefix):
                    raise ControllerError("Pegasus reserved name collision or ownership mismatch")
            if len({s.name for s in managed}) != len(managed):
                raise ControllerError("Pegasus duplicate managed names; manual reconciliation required")
            self._set(state_available=1, state_timestamp_seconds=time.time(),
                      fleet_allocated_instances=len(fleet),
                      fleet_running_instances=sum(s.status == 'ACTIVE' and not s.task_state for s in fleet),
                      running_instances=sum(s.status == "ACTIVE" and not s.task_state for s in managed),
                      stopped_instances=sum(s.status == "SHUTOFF" and not s.task_state for s in managed),
                      pending_operations=sum(s.status not in ("ACTIVE", "SHUTOFF", "ERROR")
                                             or bool(s.task_state) for s in managed),
                      error_instances=sum(s.status == "ERROR" for s in managed))
            return sorted(managed, key=lambda s: s.name)
        except Exception:
            self._set(state_available=0)
            raise ControllerError("Pegasus state unavailable, malformed or ownership mismatch") from None

    def validate_resources(self):
        """Read-only preflight. No network, security group or keypair creation."""
        image = self._api(self.connection.image.get_image, self.config.image_id)
        network = self._api(self.connection.network.get_network, self.config.network_id)
        key = self._api(self.connection.compute.find_keypair, self.config.key_name)
        if image is None or image.status.lower() != "active" or network is None or key is None:
            raise ControllerError("Pegasus image/network/keypair missing or image inactive")
        for name in self.config.security_groups:
            groups = self._api(lambda: list(self.connection.network.security_groups(name=name)))
            if len(groups) != 1:
                raise ControllerError("Pegasus security group missing or ambiguous")
        self._quota()

    def _set(self, **values):
        with self._mutex:
            self._metrics.update(values)

    def telemetry(self):
        with self._mutex:
            return dict(self._metrics)

    def record_target(self, desired):
        self._set(desired_instances=desired)

    def running_instances(self):
        servers = self.snapshot()
        return sum(s.status == "ACTIVE" and not s.task_state for s in servers)

    def _quota(self, count=1):
        compute = self.connection.compute
        limits = self._api(compute.get_limits).absolute
        flavor = self._api(compute.get_flavor, self.config.flavor_id)
        try:
            resources = (
                (limits.max_total_instances, limits.total_instances_used, 1),
                (limits.max_total_cores, limits.total_cores_used, flavor.vcpus),
                (limits.max_total_ram_size, limits.total_ram_used, flavor.ram),
            )
        except (AttributeError, TypeError):
            raise ControllerError("Pegasus quota/flavor data unavailable or invalid") from None
        for maximum, used, extra in resources:
            if (type(maximum) is not int or type(used) is not int or type(extra) is not int
                    or used < 0 or extra <= 0 or maximum < -1):
                raise ControllerError("Pegasus quota/flavor data unavailable or invalid")
            if maximum != -1 and used + extra * count > maximum:
                raise ControllerError("Pegasus project compute quota insufficient (includes stopped/unrelated VMs)")

    def _wait(self, server_id, target):
        deadline = self._clock() + self.config.operation_timeout
        while True:
            server = self._api(self.connection.compute.get_server, server_id)
            if not self._owned(server):
                raise ControllerError("Pegasus ownership changed during operation")
            self.snapshot()
            if server.status == target and not server.task_state:
                return
            if server.status == "ERROR" or self._clock() >= deadline:
                raise ControllerError("Pegasus operation did not reach final state within timeout")
            self._sleep(2)

    def _action(self, action, server=None, name=None):
        started = self._clock()
        self._set(pending_operations=1)
        try:
            compute = self.connection.compute
            self._cooldown_marker.write_text(json.dumps({"attempted_at": time.time(), "action": action}), encoding="utf-8")
            if action == "create":
                self._quota()
                # Exclusive, flushed receipt survives both ambiguous API failure and restart.
                with self._journal.open("x", encoding="utf-8") as receipt:
                    json.dump({"name": name, "project_id": self.config.project_id}, receipt)
                    receipt.flush()
                    import os
                    os.fsync(receipt.fileno())
                server = self._api(compute.create_server, name=name,
                    image_id=self.config.image_id, flavor_id=self.config.flavor_id,
                    networks=[{"uuid": self.config.network_id}], key_name=self.config.key_name,
                    security_groups=[{"name": s} for s in self.config.security_groups],
                    metadata=self.metadata)
            else:
                fresh = self._api(compute.get_server, server.id)
                source = "SHUTOFF" if action == "start" else "ACTIVE"
                if not self._owned(fresh) or fresh.status != source or fresh.task_state:
                    raise ControllerError("Pegasus ownership/state changed before operation")
                self._api(compute.start_server if action == "start" else compute.stop_server, fresh)
            self._wait(server.id, "SHUTOFF" if action == "stop" else "ACTIVE")
            if action == "create":
                self._journal.unlink()
            with self._mutex:
                self._metrics["actions_succeeded_total"] += 1
                self._metrics["action_latency_seconds_sum"] += self._clock() - started
                self._metrics["action_latency_seconds_count"] += 1
        except Exception:
            with self._mutex:
                self._metrics["actions_failed_total"] += 1
            raise ControllerError("Pegasus action failed; inspect states and uncertain-create receipt before retry") from None
        finally:
            self._last_change = self._clock()
            try:
                self.snapshot()
            except ControllerError:
                pass

    def _scale(self, count, up):
        if type(count) is not int or count < 0:
            raise ValueError("Scaling count must be a nonnegative integer")
        self.plan = []
        if not count:
            return
        servers = self.snapshot()
        # An unobserved create in another game still consumes shared fleet budget.
        uncertain = (Path(self.config.state_dir)/self.config.project_id).glob(
            f"{self.config.pool}-*/uncertain-create.json")
        if any(uncertain):
            raise ControllerError("Uncertain Pegasus creation latched; manual reconciliation required")
        if any(s.status not in ("ACTIVE", "SHUTOFF") or s.task_state for s in servers):
            raise ControllerError("Pegasus pool is transitioning or unhealthy; no new operations")
        if self._last_change is not None and self._clock()-self._last_change < self.config.cooldown_seconds:
            return
        running = sum(s.status == "ACTIVE" for s in servers)
        if up and (self.telemetry()['fleet_running_instances']+count > self.config.maximum_instances
                   or self.telemetry()['fleet_allocated_instances'] > self.config.maximum_instances):
            raise ControllerError("Pegasus configured fleet limit exceeded")
        if not up and count > running:
            raise ControllerError("Pegasus stop count exceeds running fleet")
        candidates = [s for s in servers if s.status == ("SHUTOFF" if up else "ACTIVE")]
        for server in candidates[:count]:
            self.plan.append({"action": "start" if up else "stop", "id": server.id})
        missing = count - len(self.plan)
        if missing:
            if self.telemetry()['fleet_allocated_instances'] + missing > self.config.maximum_instances:
                raise ControllerError('Pegasus shared fleet allocation limit reached')
            names = {s.name for s in servers}
            for slot in range(1, self.config.maximum_instances+1):
                name = f"{self.prefix}{slot:02d}"
                if name not in names and missing:
                    self.plan.append({"action": "create", "name": name})
                    missing -= 1
            if missing:
                raise ControllerError("Pegasus allocated pool limit reached")
            self._quota(count - len(candidates[:count]))
        if self.dry_run:
            return  # Real cloud state remains unchanged, including running metrics.
        by_id = {s.id: s for s in servers}
        for item in self.plan:
            # Refresh full ownership/state and allocated count before every mutation.
            fresh = self.snapshot()
            if any(s.status not in ("ACTIVE", "SHUTOFF") or s.task_state for s in fresh):
                raise ControllerError("Pegasus pool changed during reconciliation")
            if item["action"] == "start" and self.telemetry()['fleet_running_instances'] >= self.config.maximum_instances:
                raise ControllerError("Pegasus start would exceed running limit")
            if item["action"] == "create" and (self.telemetry()['fleet_allocated_instances'] >= self.config.maximum_instances
                    or any(s.name == item["name"] for s in fresh)):
                raise ControllerError("Pegasus creation would exceed pool or collide")
            self._action(item["action"], by_id.get(item.get("id")), item.get("name"))

    def scale_up(self, count):
        self._scale(count, True)

    def scale_down(self, count):
        self._scale(count, False)
