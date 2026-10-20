"""Start/stop an explicitly owned fixed VM pool through an existing SSH route."""
from dataclasses import dataclass
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
from uuid import UUID

from .vm_controller import ControllerError


@dataclass(frozen=True)
class OpenStackPool:
    ssh_host: str
    identity_file: str
    openrc_path: str
    name: str
    appid: str
    server_ids: tuple[str, ...]

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+@[a-zA-Z0-9_.-]+", self.ssh_host):
            raise ValueError("Expected user@host for OpenStack SSH")
        if not self.openrc_path.startswith('/') or any(c in self.openrc_path for c in '\r\n\0'):
            raise ValueError("OpenRC must be an absolute remote path")
        if not self.identity_file or self.identity_file.startswith('-'):
            raise ValueError("Expected SSH identity path")
        for value in (self.name, self.appid):
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,48}", value):
                raise ValueError("Invalid pool name/appid")
        if not self.server_ids or len(set(self.server_ids)) != len(self.server_ids):
            raise ValueError("Pool requires distinct VM UUIDs")
        for value in self.server_ids:
            if str(UUID(value)) != value:
                raise ValueError("Pool requires canonical VM UUIDs")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        data['server_ids'] = tuple(data['server_ids'])
        return cls(**data)


class OpenStackController:
    """Only ACTIVE/SHUTOFF owned VMs are eligible; never create or delete VMs.

    ACTIVE means the hypervisor reports running, not game-server readiness.
    One manager must own the pool. Operations wait for a confirmed final state.
    """

    def __init__(self, pool, runner=None, clock=time.monotonic, sleep=time.sleep,
                 operation_timeout=180):
        self.pool = pool
        self._runner = runner or subprocess.run
        self._clock, self._sleep = clock, sleep
        self.operation_timeout = operation_timeout

    def _openstack(self, *args):
        command = ('source ' + shlex.quote(self.pool.openrc_path)
                   + ' >/dev/null 2>&1 </dev/null; exec timeout 35 openstack '
                   + shlex.join(args))
        # Python stdin avoids Windows/POSIX nested shell quoting and carries no credentials.
        script = ('import subprocess,sys\n'
                  f'r=subprocess.run(["bash","-c",{command!r}],capture_output=True,text=True,timeout=40)\n'
                  'sys.stdout.write(r.stdout if r.returncode == 0 else "")\n'
                  'sys.exit(r.returncode)\n')
        try:
            result = self._runner(
                ['ssh', '-i', str(Path(self.pool.identity_file).expanduser()),
                 '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                 '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=10',
                 '-T', self.pool.ssh_host, 'python3', '-'],
                input=script, capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=55, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ControllerError('OpenStack SSH command unavailable or timed out') from error
        if result.returncode:
            raise ControllerError(f'OpenStack {args[0]} {args[1]} failed (exit {result.returncode})')
        return result.stdout

    def _state(self, server_id):
        try:
            data = json.loads(self._openstack('server', 'show', server_id, '-f', 'json'))
            properties = data['properties']
            if (data['id'] != server_id or not isinstance(properties, dict)
                    or properties.get('ruby_acorn_pool') != self.pool.name
                    or properties.get('ruby_acorn_game') != self.pool.appid):
                raise ValueError('ownership')
            status = data['status']
            task = data['OS-EXT-STS:task_state']
            if not isinstance(status, str):
                raise ValueError('status')
            return status, task
        except (ValueError, KeyError, TypeError) as error:
            raise ControllerError(f'Invalid VM identity, ownership or state: {server_id}') from error

    def snapshot(self):
        return {sid: self._state(sid) for sid in self.pool.server_ids}

    def running_instances(self):
        states = self.snapshot()
        if any(state not in ('ACTIVE', 'SHUTOFF') or task is not None
               for state, task in states.values()):
            raise ControllerError('Pool has a transitioning or unhealthy VM; skip scaling')
        return sum(state == 'ACTIVE' for state, _ in states.values())

    def _scale(self, count, start):
        if type(count) is not int or count < 0:
            raise ValueError('Scaling count must be a nonnegative integer')
        if count == 0:
            return
        states = self.snapshot()
        if any(state not in ('ACTIVE', 'SHUTOFF') or task is not None
               for state, task in states.values()):
            raise ControllerError('Pool has a transitioning or unhealthy VM; skip scaling')
        source, target, action = ('SHUTOFF', 'ACTIVE', 'start') if start else ('ACTIVE', 'SHUTOFF', 'stop')
        candidates = [sid for sid, (state, _) in states.items() if state == source]
        if count > len(candidates):
            raise ControllerError('Requested change exceeds available fixed-pool VMs')
        for sid in candidates[:count]:
            # Recheck immediately before mutation, including ownership and task state.
            if self._state(sid) != (source, None):
                raise ControllerError('VM state changed before operation')
            self._openstack('server', action, sid)
            deadline = self._clock() + self.operation_timeout
            while True:
                state, task = self._state(sid)
                if state == target and task is None:
                    break
                if state == 'ERROR' or self._clock() >= deadline:
                    raise ControllerError(f'VM did not reach {target}: {sid}')
                self._sleep(2)

    def scale_up(self, count):
        self._scale(count, True)

    def scale_down(self, count):
        self._scale(count, False)
