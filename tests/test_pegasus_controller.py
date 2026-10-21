from dataclasses import asdict, replace
import json
from types import SimpleNamespace as NS

import pytest

from main import build_engine, evaluate_once, load_config
from scaler.engine.decision_engine import DecisionEngine
from scaler.engine.scaling_limits import ScalingLimits
from scaler.exporter.autoscaler_metrics import AutoscalerMetrics
from scaler.infrastructure.pegasus_controller import PegasusConfig, PegasusController
from scaler.infrastructure.vm_controller import ControllerError
from scaler.strategies.threshold import ThresholdStrategy


PROJECT = "00000000000000000000000000000001"


def config(tmp_path, **kwargs):
    return replace(PegasusConfig("pegasus", PROJECT, "demo", "730",
        "00000000-0000-0000-0000-000000000002", "1",
        "00000000-0000-0000-0000-000000000003", "test", ("default",),
        state_dir=str(tmp_path)), **kwargs)


class Cloud:
    def __init__(self):
        self.current_project_id = PROJECT
        self.compute = self
        self.instances = []
        self.calls = []
        self.fail = False
        self.complete = True
        self.instance_limit = 10
        self.core_limit = 10
        self.ram_limit = 10240
        self.get_fail = False

    def servers(self, details=True):
        if self.get_fail:
            raise RuntimeError("SECRET API exception")
        return iter(self.instances)

    def get_server(self, sid):
        return next(s for s in self.instances if s.id == sid)

    def get_limits(self):
        return NS(absolute=NS(max_total_instances=self.instance_limit,
            total_instances_used=len(self.instances), max_total_cores=self.core_limit,
            total_cores_used=len(self.instances), max_total_ram_size=self.ram_limit,
            total_ram_used=len(self.instances)*1024))

    def get_flavor(self, flavor_id):
        return NS(vcpus=1, ram=1024)

    def create_server(self, **kwargs):
        self.calls.append(("create", kwargs))
        if self.fail:
            raise RuntimeError("SECRET API exception")
        server = NS(id=str(len(self.instances)+1), project_id=PROJECT,
                    name=kwargs["name"], metadata=kwargs["metadata"], task_state=None,
                    status="ACTIVE" if self.complete else "BUILD")
        self.instances.append(server)
        return server

    def start_server(self, server):
        self.calls.append(("start", server.id))
        if self.fail:
            raise RuntimeError("SECRET API exception")
        if self.complete:
            server.status = "ACTIVE"
        else:
            server.task_state = "powering-on"

    def stop_server(self, server):
        self.calls.append(("stop", server.id))
        server.status = "SHUTOFF"


@pytest.fixture
def setup(tmp_path):
    api = Cloud()
    clock = [1000.0]
    controller = PegasusController(config(tmp_path), api, dry_run=False,
        clock=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0]+seconds))
    yield controller, api, clock
    controller.close()


def advance(clock):
    clock[0] += 121


def test_reconcile_one_three_one_reuses_stopped_and_keeps_unrelated(setup):
    controller, api, clock = setup
    unrelated = NS(id="other", name="school", project_id=PROJECT, metadata={}, status="ACTIVE", task_state=None)
    api.instances.append(unrelated)
    engine = DecisionEngine(ThresholdStrategy(), controller, ScalingLimits(1, 6, 2))
    assert engine.apply_desired_instances(1) == 1
    advance(clock)
    assert engine.apply_desired_instances(3) == 3
    advance(clock)
    assert engine.apply_desired_instances(1) == 1
    advance(clock)
    assert engine.apply_desired_instances(3) == 3
    assert len(api.instances) == 4
    assert unrelated.status == "ACTIVE"
    assert [a for a, _ in api.calls].count("create") == 3
    assert [a for a, _ in api.calls].count("start") == 2
    before = len(api.calls)
    advance(clock)
    engine.apply_desired_instances(3)
    assert len(api.calls) == before


def test_default_dry_run_never_fakes_state_or_actions(tmp_path):
    api = Cloud()
    c = PegasusController(config(tmp_path), api)
    try:
        engine = DecisionEngine(ThresholdStrategy(), c, ScalingLimits(1, 6, 2))
        assert engine.evaluate(600) == 0
        assert len(c.plan) == 2
        assert not api.calls
        assert c.telemetry()["actions_succeeded_total"] == 0
        assert c.telemetry()["running_instances"] == 0
    finally:
        c.close()


def test_dry_run_noop_clears_preceding_create_plan(tmp_path):
    api = Cloud()
    c = PegasusController(config(tmp_path), api)
    try:
        engine = DecisionEngine(ThresholdStrategy(), c, ScalingLimits(0, 1, 1))
        evaluate_once(NS(get_player_count=lambda labels: 100), engine, {})
        assert c.plan[0]['action'] == 'create'
        engine.strategy = NS(desired_instances=lambda count: 0)
        evaluate_once(NS(get_player_count=lambda labels: 0), engine, {})
        assert c.plan == []
        assert not api.calls
    finally:
        c.close()


@pytest.mark.parametrize("field,value", [("instance_limit", 0), ("core_limit", 0), ("ram_limit", 512)])
def test_quota_blocks_create_including_unrelated_usage(setup, field, value):
    c, api, _ = setup
    setattr(api, field, value)
    with pytest.raises(ControllerError, match="quota"):
        c.scale_up(1)
    assert not api.calls


def test_limit_and_dry_run_full_plan_quota(setup):
    c, api, _ = setup
    with pytest.raises(ControllerError, match="limit"):
        c.scale_up(7)
    c.dry_run = True
    api.instance_limit = 1
    with pytest.raises(ControllerError, match="quota"):
        c.scale_up(2)
    assert not api.calls


def test_cooldown_avoids_immediate_stop_start_cycles(setup):
    c, api, clock = setup
    c.scale_up(1)
    c.scale_down(1)
    assert c.running_instances() == 1
    assert len(api.calls) == 1
    advance(clock)
    c.scale_down(1)
    assert c.running_instances() == 0


@pytest.mark.parametrize("change", ["metadata", "project", "duplicate", "task", "error"])
def test_ownership_or_unhealthy_pool_blocks_changes(setup, change):
    c, api, clock = setup
    c.scale_up(1)
    advance(clock)
    server = api.instances[0]
    if change == "metadata": server.metadata = {}
    if change == "project": server.project_id = "f"*32
    if change == "duplicate": api.instances.append(NS(**vars(server)))
    if change == "task": server.task_state = "powering-off"
    if change == "error": server.status = "ERROR"
    with pytest.raises(ControllerError):
        c.scale_up(1)
    assert len(api.calls) == 1


def test_ambiguous_create_latches_across_restart(setup):
    c, api, clock = setup
    api.fail = True
    with pytest.raises(ControllerError) as error:
        c.scale_up(1)
    assert "SECRET" not in str(error.value)
    assert c.telemetry()["actions_failed_total"] == 1
    cfg = c.config
    c.close()
    restarted = PegasusController(cfg, api, dry_run=False)
    try:
        api.fail = False
        with pytest.raises(ControllerError, match="latched"):
            restarted.scale_up(1)
        assert len(api.calls) == 1
    finally:
        restarted.close()


def test_timeout_pending_metrics_and_no_duplicate_creation(setup):
    c, api, clock = setup
    api.complete = False
    with pytest.raises(ControllerError):
        c.scale_up(1)
    assert c.telemetry()["pending_operations"] == 1
    assert c.telemetry()["running_instances"] == 0
    assert c.telemetry()["actions_succeeded_total"] == 0
    assert c.telemetry()["actions_failed_total"] == 1
    advance(clock)
    with pytest.raises(ControllerError):
        c.scale_up(1)
    assert len(api.calls) == 1


def test_delayed_start_waits_for_final_state(setup):
    c, api, clock = setup
    c.scale_up(1)
    advance(clock)
    c.scale_down(1)
    advance(clock)
    api.complete = False
    def complete(seconds):
        clock[0] += seconds
        assert c.telemetry()["pending_operations"] == 1
        api.instances[0].task_state = None
        api.instances[0].status = "ACTIVE"
    c._sleep = complete
    c.scale_up(1)
    assert c.running_instances() == 1
    assert c.telemetry()["actions_succeeded_total"] == 3


def test_project_guard_and_single_manager_lock(setup, tmp_path):
    c, api, _ = setup
    with pytest.raises(ControllerError, match="manager"):
        PegasusController(c.config, api)
    api.current_project_id = "f"*32
    with pytest.raises(ControllerError):
        PegasusController(config(tmp_path/"other"), api)


def test_metrics_actual_state_and_failures(setup):
    c, api, _ = setup
    metrics = AutoscalerMetrics()
    metrics.set_controller("pegasus")
    metrics.set_pegasus_provider(c.telemetry)
    engine = DecisionEngine(ThresholdStrategy(), c, ScalingLimits(1, 6, 2))
    assert evaluate_once(NS(get_player_count=lambda labels: 150), engine, {}, metrics) == 2
    body = metrics.render().decode()
    assert "pegasus_desired_instances 2" in body
    assert "pegasus_running_instances 2" in body
    assert "pegasus_actions_succeeded_total 2" in body
    assert "pegasus_action_latency_seconds_count 2" in body
    api.get_fail = True
    with pytest.raises(ControllerError): c.running_instances()
    assert "pegasus_state_available 0" in metrics.render().decode()


def test_cli_default_dry_run_and_guardrails(tmp_path, monkeypatch):
    path = tmp_path/"pegasus.json"
    path.write_text(json.dumps(asdict(config(tmp_path))))
    argv = ["--controller", "pegasus", "--pegasus-config", str(path), "--label", "appid=730"]
    cfg = load_config(argv)
    assert cfg.max_instances == 6
    assert not cfg.pegasus_apply
    for extra in (["--max-instances", "7"], ["--label", "appid=570"]):
        with pytest.raises(SystemExit): load_config(argv+extra)
    with pytest.raises(SystemExit): load_config(["--pegasus-apply"])
    seen = []
    monkeypatch.setattr("main.PegasusController", lambda pc, dry_run: seen.append(dry_run) or NS())
    build_engine(config=cfg, game="730")
    build_engine(config=load_config(argv+["--pegasus-apply"]), game="730")
    assert seen == [True, False]


def test_sdk_resource_attribute_contract():
    pytest.importorskip("openstack")
    from openstack.compute.v2.limits import Limits
    from openstack.compute.v2.server import Server
    resource = Limits(absolute={"maxTotalInstances": 10, "totalInstancesUsed": 0,
        "maxTotalCores": 10, "totalCoresUsed": 0, "maxTotalRAMSize": 10240, "totalRAMUsed": 0})
    assert resource.absolute.max_total_ram_size == 10240
    server = Server(tenant_id=PROJECT, status="SHUTOFF", **{"OS-EXT-STS:task_state": None})
    assert server.project_id == PROJECT
    assert server.task_state is None


def test_sdk_factory_sets_timeout_on_region_and_rejects_password_auth(tmp_path, monkeypatch):
    openstack = pytest.importorskip("openstack")
    from scaler.infrastructure.pegasus_controller import connect
    region = NS(config={"auth_type": "v3applicationcredential", "verify": True,
                        "auth": {"auth_url": "https://example.invalid/v3"}})
    monkeypatch.setattr(openstack.config, "get_cloud_region", lambda **kw: region)
    received = []
    monkeypatch.setattr(openstack.connection, "Connection", lambda **kw: received.append(kw) or NS())
    connect(config(tmp_path))
    assert received == [{"config": region}]
    assert region.config["api_timeout"] == 15
    assert region.config["compute_connect_retries"] == 0
    assert region.config["compute_status_code_retries"] == 0
    for update in ({"auth_type": "password"}, {"verify": False}, {"auth": {"auth_url": "http://example.invalid"}}):
        saved = dict(region.config)
        region.config.update(update)
        with pytest.raises(ControllerError): connect(config(tmp_path))
        region.config = saved


def test_read_only_preflight_never_mutates(setup, monkeypatch, capsys):
    c, api, _ = setup
    from scripts.inspect_pegasus import main
    cfg = c.config
    api.image = NS(get_image=lambda sid: NS(status="active"))
    api.network = NS(get_network=lambda sid: NS(id=sid),
                     security_groups=lambda **kw: iter([NS(name="default")]))
    api.find_keypair = lambda name: NS(name=name)
    c.validate_resources()
    assert not api.calls
    path = c._journal.parent/"config.json"
    path.write_text(json.dumps(asdict(cfg)))
    monkeypatch.setattr("scripts.inspect_pegasus.PegasusController", lambda config: c)
    assert main(["--config", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["read_only"]
    assert report["managed_vms"] == []
    assert not api.calls


def test_malformed_quota_blocks_mutation(setup):
    c, api, _ = setup
    api.get_limits = lambda: NS(absolute=None)
    with pytest.raises(ControllerError, match="invalid"):
        c.scale_up(1)
    assert not api.calls


def test_stop_start_dry_run_preserves_real_states(setup):
    c, api, clock = setup
    c.scale_up(1)
    advance(clock)
    c.dry_run = True
    c.scale_down(1)
    assert c.running_instances() == 1
    assert len(api.calls) == 1
    api.instances[0].status = "SHUTOFF"
    c.scale_up(1)
    assert c.running_instances() == 0
    assert c.plan == [{"action": "start", "id": "1"}]
    assert len(api.calls) == 1


@pytest.mark.parametrize('role', ['management', 'monitoring'])
def test_protected_roles_never_become_workers(setup, role):
    c, api, clock = setup
    c.scale_up(1)
    advance(clock)
    api.instances[0].metadata['ruby_acorn_role'] = role
    with pytest.raises(ControllerError):
        c.scale_down(1)
    assert api.instances[0].status == 'ACTIVE'
    assert len(api.calls) == 1


def test_restart_preserves_conservative_cooldown(setup):
    c, api, clock = setup
    c.scale_up(1)
    cfg = c.config
    c.close()
    restarted = PegasusController(cfg, api, dry_run=False, clock=lambda: clock[0])
    try:
        restarted.scale_down(1)
        assert restarted.running_instances() == 1
        advance(clock)
        restarted.scale_down(1)
        assert restarted.running_instances() == 0
    finally:
        restarted.close()


def test_multiple_games_share_limit_and_one_manager_lease(setup):
    c, api, clock = setup
    second = PegasusController(replace(c.config, appid='570'), api, dry_run=False,
                               clock=lambda: clock[0])
    try:
        c.scale_up(3)
        second.scale_up(3)
        assert c.running_instances() == second.running_instances() == 3
        advance(clock)
        with pytest.raises(ControllerError, match='limit'):
            second.scale_up(1)
        assert len(api.instances) == 6
        assert len(api.calls) == 6
        second.scale_down(1)
        assert c.running_instances() == 3
        assert second.running_instances() == 2
    finally:
        second.close()


def test_uncertain_create_blocks_other_games_in_shared_fleet(setup):
    c, api, clock = setup
    second = PegasusController(replace(c.config, appid='570'), api, dry_run=False)
    try:
        api.fail = True
        with pytest.raises(ControllerError):
            c.scale_up(1)
        api.fail = False
        with pytest.raises(ControllerError, match='latched'):
            second.scale_up(1)
        assert len(api.calls) == 1
    finally:
        second.close()
