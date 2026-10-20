import ast
import json
import shlex
import subprocess
from types import SimpleNamespace

import pytest

from main import load_config, build_engine
from scaler.engine.decision_engine import DecisionEngine
from scaler.engine.scaling_limits import ScalingLimits
from scaler.exporter.autoscaler_metrics import AutoscalerMetrics
from scaler.infrastructure.openstack_controller import OpenStackController, OpenStackPool
from scaler.infrastructure.vm_controller import ControllerError
from scaler.strategies.capacity import ReactiveCapacityStrategy


IDS = tuple(f'00000000-0000-0000-0000-{i:012d}' for i in range(1, 5))


def pool(ids=IDS):
    return OpenStackPool('ubuntu@moriavm', '~/.ssh/acit4410_key',
                         '/home/ubuntu/moria-openrc.sh', 'ruby-acorn', '730', ids)


class FakeOpenStack:
    def __init__(self):
        self.states = {sid: 'SHUTOFF' for sid in IDS}
        self.task = None
        self.owned = True
        self.changes = []
        self.fail_on = None
        self.complete = True

    def __call__(self, command, **kwargs):
        assert command[-2:] == ['python3', '-']
        assert 'StrictHostKeyChecking=yes' in command
        assert kwargs['timeout'] == 55
        tree = ast.parse(kwargs['input'])
        remote = ast.literal_eval(tree.body[1].value.args[0].elts[2])
        args = shlex.split(remote.split('exec timeout 35 openstack ', 1)[1])
        _, action, sid, *_ = args
        if action == 'show':
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'id': sid, 'status': self.states[sid], 'OS-EXT-STS:task_state': self.task,
                'properties': {'ruby_acorn_pool': 'ruby-acorn' if self.owned else 'other',
                               'ruby_acorn_game': '730'}}))
        assert action in ('start', 'stop')
        if sid == self.fail_on:
            return SimpleNamespace(returncode=1, stdout='', stderr='sensitive error not exposed')
        self.changes.append((action, sid))
        if self.complete:
            self.states[sid] = 'ACTIVE' if action == 'start' else 'SHUTOFF'
        return SimpleNamespace(returncode=0, stdout='')


def test_real_vm_interface_rate_limits_and_restart_discovery():
    api = FakeOpenStack()
    controller = OpenStackController(pool(), runner=api)
    engine = DecisionEngine(ReactiveCapacityStrategy(), controller, ScalingLimits(maximum_instances=4))
    assert engine.evaluate(700000) == 2
    assert engine.evaluate(700000) == 4
    assert engine.evaluate(0) == 2
    assert engine.evaluate(0) == 1
    assert OpenStackController(pool(), runner=api).running_instances() == 1
    assert len(api.changes) == 7
    assert all(sid in IDS for _, sid in api.changes)


@pytest.mark.parametrize('task,state', [('powering-on', 'SHUTOFF'), (None, 'ERROR'), (None, 'BUILD')])
def test_transition_or_error_blocks_changes(task, state):
    api = FakeOpenStack()
    api.task = task
    api.states[IDS[0]] = state
    controller = OpenStackController(pool(), runner=api)
    with pytest.raises(ControllerError):
        controller.scale_up(1)
    with pytest.raises(ControllerError):
        controller.running_instances()
    assert not api.changes


def test_ownership_failure_blocks_entire_pool_before_mutation():
    api = FakeOpenStack()
    api.owned = False
    with pytest.raises(ControllerError, match='ownership'):
        OpenStackController(pool(), runner=api).scale_up(1)
    assert not api.changes


def test_pool_exhaustion_and_partial_failure():
    api = FakeOpenStack()
    controller = OpenStackController(pool(), runner=api)
    with pytest.raises(ControllerError, match='exceeds'):
        controller.scale_up(5)
    assert not api.changes
    api.fail_on = IDS[1]
    with pytest.raises(ControllerError, match='failed') as error:
        controller.scale_up(2)
    assert 'sensitive' not in str(error.value)
    assert controller.running_instances() == 1


def test_wait_timeout_is_failure_and_not_success():
    api = FakeOpenStack()
    api.complete = False
    clock = iter([0, 10])
    controller = OpenStackController(pool(), runner=api, clock=lambda: next(clock),
                                     sleep=lambda _: None, operation_timeout=5)
    with pytest.raises(ControllerError, match='did not reach'):
        controller.scale_up(1)


@pytest.mark.parametrize('failure', [FileNotFoundError(), subprocess.TimeoutExpired('ssh', 55)])
def test_transport_failure(failure):
    def runner(*a, **kw):
        raise failure
    with pytest.raises(ControllerError):
        OpenStackController(pool(), runner=runner).running_instances()


def test_malformed_response():
    with pytest.raises(ControllerError):
        OpenStackController(pool(), runner=lambda *a, **kw: SimpleNamespace(
            returncode=0, stdout='{}')).running_instances()


def test_demo_records_real_ids_and_leaves_pool_stopped():
    from scripts.demo_openstack_scaling import demonstrate
    api = FakeOpenStack()
    report = demonstrate(OpenStackController(pool(), runner=api))
    assert report['success']
    assert [row['running_instances'] for row in report['steps']] == [2, 4, 2, 0]
    assert all(len(row['changed_vm_ids']) == 2 for row in report['steps'])
    assert set(api.states.values()) == {'SHUTOFF'}


def test_demo_refuses_preexisting_active_pool_and_reports_partial_failure():
    from scripts.demo_openstack_scaling import demonstrate
    api = FakeOpenStack()
    api.states[IDS[0]] = 'ACTIVE'
    with pytest.raises(ControllerError, match='initially SHUTOFF'):
        demonstrate(OpenStackController(pool(), runner=api))
    assert not api.changes
    api.states[IDS[0]] = 'SHUTOFF'
    api.fail_on = IDS[1]
    report = demonstrate(OpenStackController(pool(), runner=api))
    assert not report['success']
    assert api.states[IDS[0]] == 'ACTIVE'


@pytest.mark.parametrize('ids', [(), (IDS[0], IDS[0]), ('not-a-uuid',)])
def test_invalid_pool(ids):
    with pytest.raises(ValueError):
        pool(ids)


def test_openstack_configuration_and_telemetry(tmp_path, monkeypatch):
    from dataclasses import asdict
    path = tmp_path/'pool.json'
    path.write_text(json.dumps(asdict(pool())))
    argv = ['--controller', 'openstack', '--openstack-pool', str(path), '--label', 'appid=730']
    config = load_config(argv)
    assert config.max_instances == 4
    assert isinstance(build_engine(config=config, game='730').vm_controller, OpenStackController)
    for extra in (['--max-instances', '5'], ['--label', 'appid=570']):
        with pytest.raises(SystemExit):
            load_config(argv+extra)
    with pytest.raises(SystemExit):
        load_config(['--controller', 'openstack'])
    metrics = AutoscalerMetrics()
    metrics.set_controller('openstack')
    assert b'resource_type="virtual_machines"' in metrics.render()
