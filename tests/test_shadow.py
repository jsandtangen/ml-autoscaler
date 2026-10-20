import json

import pytest

from scaler.data.client import GameDataClientError, PrometheusResponseError
from scaler.data.shadow_source import ShadowPrometheusClient
from scaler.forecasting import PlayerObservation
from scaler.shadow import ShadowObserver
from scaler.strategies.capacity import CapacityPolicy
from scripts.shadow_autoscaler import output_lock, parse_args, run


class Forecaster:
    def __init__(self, prediction=250, error=None):
        self.prediction, self.error = prediction, error
        self.windows = []

    def predict(self, values):
        self.windows.append(list(values))
        if self.error:
            raise self.error
        return self.prediction


def observer(model=None, seed=(), live=(), pending=None, window=3):
    return ShadowObserver("730", model or Forecaster(), CapacityPolicy(1,100,0),
                          window, seed, live, pending)


def rows(counts):
    return [PlayerObservation(i*3600, n) for i,n in enumerate(counts)]


def test_seed_only_past_and_predict_once_per_live_hour():
    model=Forecaster()
    shadow=observer(model,rows([100,120,140,9999]))
    decision=shadow.observe(PlayerObservation(7205,150),7210)[-1]
    assert decision["status"]=="forecast"
    assert model.windows==[[100,120,150]]
    assert decision["target_timestamp"]==10800
    assert decision["shadow_desired_instances"]==3
    assert shadow.observe(PlayerObservation(7235,160),7240)[0]["status"]=="already_sampled"
    assert len(model.windows)==1


def test_gap_resets_context_without_filling_or_using_stale_seed():
    model=Forecaster()
    shadow=observer(model,rows([100,120]))
    decision=shadow.observe(PlayerObservation(10805,300),10810)[-1]
    assert decision["status"]=="warmup"
    assert decision["context_observations"]==1
    assert model.windows==[]
    assert decision["shadow_desired_instances"]==3


@pytest.mark.parametrize("sample,now,status", [
    (PlayerObservation(7201,100),7310,"stale_source"),
    (PlayerObservation(7100,100),7210,"stale_source"),
    (PlayerObservation(7199,100),7201,"waiting_for_boundary"),
    (PlayerObservation(7505,100),7510,"waiting_for_boundary"),
    (PlayerObservation(7220,100),7210,"stale_source"),
])
def test_freshness_and_hour_alignment(sample,now,status):
    shadow=observer()
    assert shadow.observe(sample,now)[0]["status"]==status
    assert shadow.last_live_slot==-1


@pytest.mark.parametrize("prediction", [float("nan"),float("inf"),-1,None])
def test_invalid_forecast_records_reactive_fallback(prediction):
    shadow=observer(Forecaster(prediction),rows([100,120]))
    decision=shadow.observe(PlayerObservation(7205,350),7210)[-1]
    assert decision["status"]=="fallback"
    assert decision["shadow_desired_instances"]==4
    assert decision["predicted_global_players"] is None
    assert shadow.pending is None


def test_model_failure_isolated_and_reference_decision_is_recorded():
    shadow=observer(Forecaster(error=RuntimeError("unavailable")),rows([100,120]))
    reference={"strategy":"threshold","running_instances":2}
    decision=shadow.observe(PlayerObservation(7205,350),7210,reference)[-1]
    assert decision["status"]=="fallback"
    assert decision["live_decision"]==reference
    assert "unavailable" in decision["error"]


def test_next_hour_scores_pending_forecast_without_reusing_target_as_input_early():
    shadow=observer(Forecaster(250),rows([100,120]))
    shadow.observe(PlayerObservation(7205,150),7210)
    outcome=shadow.observe(PlayerObservation(10805,310),10810)[0]
    assert outcome["event"]=="outcome"
    assert outcome["absolute_error_players"]==60
    assert outcome["squared_error_players"]==3600
    assert outcome["modeled_shortfall_players"]==10


def test_missed_target_hour_never_scored_against_later_demand():
    shadow=observer(Forecaster(250),rows([100,120]))
    shadow.observe(PlayerObservation(7205,150),7210)
    events=shadow.observe(PlayerObservation(14405,310),14410)
    assert events[0]["event"]=="outcome_unavailable"
    assert events[-1]["status"]=="warmup"


def test_restart_live_history_avoids_duplicate_forecast():
    shadow=observer(seed=rows([100,120]),live=[PlayerObservation(7200,150)])
    assert shadow.observe(PlayerObservation(7205,160),7210)[0]["status"]=="already_sampled"


def test_seed_alignment_is_checked():
    with pytest.raises(ValueError,match="exact UTC hours"):
        observer(seed=[PlayerObservation(1,100)])


class FakeSession:
    def __init__(self, results):
        self.results=results
        self.queries=[]

    def get(self,url,params,timeout):
        self.queries.append(params["query"])
        results=self.results
        class Response:
            def raise_for_status(self): pass
            def json(self): return {"status":"success","data":{"result":results}}
        return Response()


def test_source_uses_real_scrape_timestamp_from_range_vector():
    session=FakeSession([{"metric":{"appid":"730"},"values":[[7200,"100"],[7215,"120"]]}])
    client=ShadowPrometheusClient("http://local",session=session)
    assert client.latest_observation("730")==PlayerObservation(7215,120)
    assert session.queries==['steam_player_count{appid="730"}[5m]']


@pytest.mark.parametrize("results", [[], [{"values":[]}], [{"values":[[1,"nan"]]}],
    [{"values":[[1,"1.5"]]}], [{"values":[[1,"10"]]},{"values":[[1,"20"]]}]])
def test_source_rejects_missing_invalid_and_ambiguous_series(results):
    with pytest.raises(GameDataClientError):
        ShadowPrometheusClient("http://local",session=FakeSession(results)).latest_observation("730")


def live_metrics(appid=None):
    return [{"metric":{"__name__":name,**({"appid":appid} if appid else {}),
                       **({"name":"threshold"} if name=="strategy" else {})},
             "value":[7210,str(value)]} for name,value in {
        "running_instances":4,"desired_instances":4,"strategy":1,"last_decision_timestamp_seconds":7200}.items()]


@pytest.mark.parametrize("appid", [None,"730"])
def test_live_reference_supports_legacy_and_per_game_metrics(appid):
    result=ShadowPrometheusClient("http://local",session=FakeSession(live_metrics(appid))).live_decision("730",7210)
    assert result["running_instances"]==4
    assert result["strategy"]=="threshold"
    assert result["fresh"] is True
    assert result["appid_verified"] is (appid == "730")


def test_ambiguous_reference_cannot_be_silently_matched():
    with pytest.raises(PrometheusResponseError):
        ShadowPrometheusClient("http://local",session=FakeSession(live_metrics()*2)).live_decision("730",7210)


def test_legacy_reference_identity_can_be_verified_using_source_info():
    source={"metric":{"__name__":"capacity_source_info","steam_appid":"730"},"value":[7210,"1"]}
    result=ShadowPrometheusClient("http://local",session=FakeSession(live_metrics()+[source])).live_decision("730",7210)
    assert result["appid_verified"] is True
    with pytest.raises(PrometheusResponseError):
        ShadowPrometheusClient("http://local",session=FakeSession(live_metrics()+[source])).live_decision("570",7210)


class Client:
    def __init__(self, timestamp=7205, fail=False):
        self.timestamp,self.fail=timestamp,fail

    def latest_observation(self,appid):
        if self.fail: raise GameDataClientError("offline")
        return PlayerObservation(self.timestamp,150)

    def live_decision(self,appid,now):
        return {"strategy":"threshold","running_instances":2,"desired_instances":2}


def test_runner_persists_hourly_sample_decision_and_restores_pending_on_restart(tmp_path):
    seed=tmp_path/"seed.csv"
    seed.write_text("timestamp,appid,player_count\n0,730,100\n3600,730,120\n")
    output=tmp_path/"shadow"
    args=parse_args(["--once","--output-dir",str(output),"--seed-history-path",str(seed),"--window-size","3"])
    assert run(args,Client(),Forecaster(),clock=lambda:7210)==0
    assert json.loads((output/"latest_decision.json").read_text())["status"]=="forecast"
    assert run(args,Client(),Forecaster(),clock=lambda:7240)==0
    assert len((output/"hourly_observations.csv").read_text().splitlines())==2
    assert run(args,Client(10805),Forecaster(),clock=lambda:10810)==0
    events=[json.loads(x) for x in (output/"events.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["event"]=="decision"])==2
    assert len([e for e in events if e["event"]=="outcome"])==1
    assert json.loads((output/"process.json").read_text())["running"] is False


def test_query_failure_does_not_record_zero_or_hourly_sample(tmp_path):
    args=parse_args(["--once","--output-dir",str(tmp_path/"shadow")])
    assert run(args,Client(fail=True),Forecaster(),clock=lambda:7210)==1
    assert not (args.output_dir/"hourly_observations.csv").exists()
    assert json.loads((args.output_dir/"status.json").read_text())["status"]=="source_error"


def test_single_writer_lock(tmp_path):
    with output_lock(tmp_path):
        with pytest.raises(ValueError,match="Another shadow observer"):
            with output_lock(tmp_path):
                pass


@pytest.mark.parametrize("options", [["--appid","../oops"],["--poll-seconds","nan"],
                                     ["--poll-seconds","120"],["--window-size","0"]])
def test_invalid_runner_configuration(options):
    with pytest.raises(SystemExit):
        parse_args(options)
