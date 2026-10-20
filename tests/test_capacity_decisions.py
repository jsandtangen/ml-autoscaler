from dataclasses import asdict
import json

import pytest

from main import build_engine, evaluate_once, load_config, run_loop
from scaler.data.client import GameDataClient, PlayerCountNotFoundError
from scaler.engine.scaling_limits import ScalingLimits
from scaler.exporter.autoscaler_metrics import AutoscalerMetrics
from scaler.infrastructure.vm_controller import ControllerError
from scaler.strategies.capacity import CapacityPolicy
from scaler.strategies.registry import STRATEGIES
from scaler.shadow import ShadowObserver
from scaler.forecasting import PlayerObservation


class Counts:
    def __init__(self, value): self.value=value
    def get_player_count(self, labels):
        if isinstance(self.value,Exception): raise self.value
        return self.value


@pytest.mark.parametrize("players,required", [(0,0),(535000,6),(700000,8),(1000000,11)])
def test_uncapped_capacity_requirement(players,required):
    assert CapacityPolicy().required_instances(players)==required


def test_reactive_controller_converges_with_two_per_cycle_and_observable_unmet_demand():
    cfg=load_config(["--once"])
    engine=build_engine(config=cfg)
    metrics=AutoscalerMetrics()
    counts=Counts(535000)
    actual=[evaluate_once(counts,engine,{"appid":"730"},metrics) for _ in range(3)]
    assert actual==[2,4,6]
    counts.value=700000
    assert evaluate_once(counts,engine,{},metrics)==7
    body=metrics.render().decode()
    assert "required_instances 8\n" in body
    assert "desired_instances 7\n" in body
    assert "running_instances 7\n" in body
    assert "unmet_instances 1\n" in body
    assert "capacity_limit_shortfall_instances 1\n" in body
    assert "unmet_player_capacity 0\n" in body  # The missing VM is buffered demand.
    counts.value=0
    assert [evaluate_once(counts,engine,{},metrics) for _ in range(3)]==[5,3,1]
    assert "required_instances 0\n" in metrics.render().decode()


def test_first_cycle_exposes_rate_limit_shortfall():
    engine=build_engine(config=load_config([]));metrics=AutoscalerMetrics()
    evaluate_once(Counts(700000),engine,{},metrics)
    body=metrics.render().decode()
    for sample in ["global_player_count 700000", "customer_player_count 700", "required_instances 8",
                   "desired_instances 7", "cycle_target_instances 2", "running_instances 2",
                   "unmet_instances 6", "unmet_player_capacity 500"]:
        assert sample+"\n" in body


def test_existing_over_limit_fleet_scales_down_gradually():
    limits=ScalingLimits(maximum_instances=7)
    assert limits.plan(20,10).cycle_target_instances==8
    assert limits.plan(20,8).cycle_target_instances==7


@pytest.mark.parametrize("options", [["--minimum-instances","8"],["--max-instances","0"],
    ["--max-scaling-change","0"],["--player-share","0"],["--player-share","1.1"],
    ["--headroom-fraction","nan"],["--max-sample-age-seconds","0"]])
def test_invalid_scaling_configuration(options):
    with pytest.raises(SystemExit): load_config(options)


def test_configured_docker_limit_takes_precedence_without_running_docker():
    cfg=load_config(["--controller","docker","--docker-max-instances","3","--max-instances","7"])
    assert cfg.max_instances==3
    engine=build_engine(config=cfg)
    assert engine.limits.maximum_instances==3
    assert "timesfm" not in STRATEGIES  # Predictive control is not selectable.


def test_environment_capacity_settings_and_cli_override(monkeypatch):
    monkeypatch.setenv("AUTOSCALER_PLAYER_SHARE","0.002")
    monkeypatch.setenv("AUTOSCALER_MAX_INSTANCES","9")
    cfg=load_config(["--max-instances","6"])
    assert cfg.capacity_policy.player_share==0.002
    assert cfg.max_instances==6


def test_missing_input_preserves_resources_and_decision_timestamp():
    cfg=load_config(["--once"]);engine=build_engine(config=cfg);metrics=AutoscalerMetrics()
    run_loop(cfg,Counts(535000),engine,metrics)
    before=[s for s in metrics.render().decode().splitlines() if s.startswith("last_decision_timestamp_seconds ")]
    run_loop(cfg,Counts(PlayerCountNotFoundError("stale or unavailable")),engine,metrics)
    assert engine.vm_controller.running_instances()==2
    body=metrics.render().decode()
    assert "input_available 0\n" in body
    assert before==[s for s in body.splitlines() if s.startswith("last_decision_timestamp_seconds ")]


def test_prometheus_query_filters_old_source_samples():
    class Session:
        def get(self,url,params,timeout):
            assert params["query"]=='last_over_time(steam_player_count{appid="730"}[90s])'
            class Response:
                def raise_for_status(self): pass
                def json(self): return {"status":"success","data":{"result":[]}}
            return Response()
    with pytest.raises(PlayerCountNotFoundError):
        GameDataClient("http://local",session=Session(),max_sample_age_seconds=90).get_player_count({"appid":"730"})


def test_partial_controller_failure_keeps_uncapped_requirement_visible():
    cfg=load_config(["--once"]);engine=build_engine(config=cfg);metrics=AutoscalerMetrics()
    def fail(count):
        engine.vm_controller.instances=1
        raise ControllerError("partial failure")
    engine.vm_controller.scale_up=fail
    with pytest.raises(ControllerError): run_loop(cfg,Counts(700000),engine,metrics)
    body=metrics.render().decode()
    assert "required_instances 8\n" in body
    assert "running_instances 1\n" in body
    assert "unmet_instances 7\n" in body
    assert "scale_up_events_total 0\n" in body
    assert not any(s.startswith("last_decision_timestamp_seconds ") for s in body.splitlines())


def test_shadow_uses_same_uncapped_calculation_bounds_and_cycle_limit():
    class Forecast:
        def predict(self, counts): return 700000
    shadow=ShadowObserver("730",Forecast(),CapacityPolicy(),window_size=1)
    reference={"running_instances":2,"fresh":True,"appid_verified":True}
    decision=shadow.observe(PlayerObservation(7205,535000),7210,reference)[-1]
    assert decision["shadow_required_instances"]==8
    assert decision["shadow_desired_instances"]==7
    assert decision["shadow_cycle_target_instances"]==4
    assert decision["reactive_capacity_required_instances"]==6


def test_shadow_forecast_is_exported_but_does_not_control_resources(tmp_path,monkeypatch):
    now=7205
    monkeypatch.setattr("scaler.exporter.autoscaler_metrics.time.time",lambda:now)
    folder=tmp_path/"730";folder.mkdir()
    decision={"appid":"730","status":"forecast","model_id":"google/timesfm-2.5-200m-pytorch",
              "policy":asdict(CapacityPolicy()),"issued_at":7200,"target_timestamp":10800,
              "predicted_global_players":700000}
    (folder/"latest_decision.json").write_text(json.dumps(decision))
    engine=build_engine(config=load_config([]));metrics=AutoscalerMetrics()
    evaluate_once(Counts(535000),engine,{"appid":"730"},metrics,shadow_decisions_dir=str(tmp_path))
    assert engine.vm_controller.running_instances()==2
    body=metrics.render().decode()
    assert "predicted_required_instances 8\n" in body
    assert "predicted_desired_instances 7\n" in body
    monkeypatch.setattr("scaler.exporter.autoscaler_metrics.time.time",lambda:10801)
    assert not any(s.startswith("predicted_required_instances ") for s in metrics.render().decode().splitlines())


def test_incompatible_shadow_policy_is_not_exported(monkeypatch):
    monkeypatch.setattr("scaler.exporter.autoscaler_metrics.time.time",lambda:7205)
    metrics=AutoscalerMetrics()
    decision={"appid":"730","status":"forecast","model_id":"google/timesfm-2.5-200m-pytorch",
              "policy":asdict(CapacityPolicy(player_share=1)),"issued_at":7200,"target_timestamp":10800,
              "predicted_global_players":700000}
    metrics.record_shadow_prediction(decision,CapacityPolicy(),ScalingLimits(),source_appid="730")
    assert not any(s.startswith("predicted_required_instances ") for s in metrics.render().decode().splitlines())


def test_shadow_refuses_to_propose_under_mismatched_live_assumptions():
    class Forecast:
        def predict(self, counts): raise AssertionError("Must not run mismatched comparison")
    shadow=ShadowObserver("730",Forecast(),CapacityPolicy(),window_size=1)
    reference={"fresh":True,"appid_verified":True,"running_instances":2,
               "capacity_policy":asdict(CapacityPolicy(player_share=0.01))}
    result=shadow.observe(PlayerObservation(7205,535000),7210,reference)[-1]
    assert result["status"]=="configuration_mismatch"
    assert result["shadow_desired_instances"] is None


@pytest.mark.parametrize("value", ["-1","NaN","+Inf"])
def test_invalid_player_values_are_not_treated_as_zero(value):
    class Session:
        def get(self,*args,**kwargs):
            class Response:
                def raise_for_status(self): pass
                def json(self): return {"status":"success","data":{"result":[{"metric":{},"value":[1,value]}]}}
            return Response()
    from scaler.data.client import PrometheusResponseError
    with pytest.raises(PrometheusResponseError):
        GameDataClient("http://local",session=Session()).get_player_count()


def test_replay_preserves_uncapped_demand_while_applying_same_cycle_limits():
    from scaler.simulation import simulate_scaling
    from scaler.strategies.capacity import ReactiveCapacityStrategy
    policy=CapacityPolicy()
    observations=[PlayerObservation(i*3600,700000) for i in range(4)]
    _,trace=simulate_scaling(observations,{"reactive":ReactiveCapacityStrategy(policy)},policy,1)
    assert [row["running_instances"] for row in trace]==[4,6,7]
    assert all(row["required_instances"]==8 for row in trace)
    assert all(row["desired_instances"]==7 for row in trace)
    assert all(row["starting_instances"]<=2 for row in trace)
