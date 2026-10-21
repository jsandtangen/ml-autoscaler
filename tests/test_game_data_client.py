import pytest

from scaler.data.client import (
    GameDataClient,
    PlayerCountNotFoundError,
    PrometheusQueryError,
    PrometheusResponseError,
)


class FakeResponse:
    def __init__(self, payload, status_error=None):
        self.payload = payload
        self.status_error = status_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def get(self, url, params, timeout):
        self.requests.append({"url": url, "params": params, "timeout": timeout})
        return self.response


def test_get_player_counts_without_label_assumptions():
    session = FakeSession(
        FakeResponse(
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"appid": "730", "name": "Counter-Strike 2"},
                            "value": [123.0, "456789.0"],
                        },
                        {
                            "metric": {"appid": "570", "name": "Dota 2"},
                            "value": [123.0, "123456"],
                        },
                    ]
                },
            }
        )
    )
    client = GameDataClient("http://prometheus.local/", session=session)

    samples = client.get_player_counts()

    assert session.requests[0]["url"] == "http://prometheus.local/api/v1/query"
    assert session.requests[0]["params"] == {"query": "steam_player_count"}
    assert samples[0].metric["appid"] == "730"
    assert samples[0].player_count == 456789
    assert samples[1].metric["name"] == "Dota 2"
    assert samples[1].player_count == 123456


def test_get_player_count_with_label_filters():
    session = FakeSession(
        FakeResponse(
            {
                "status": "success",
                "data": {
                    "result": [
                        {
                            "metric": {"appid": "730"},
                            "value": [123.0, "42"],
                        }
                    ]
                },
            }
        )
    )
    client = GameDataClient("http://prometheus.local", session=session)

    player_count = client.get_player_count({"appid": "730"})

    assert player_count == 42
    assert session.requests[0]["params"] == {
        "query": 'steam_player_count{appid="730"}'
    }


def test_get_player_count_rejects_ambiguous_series():
    session = FakeSession(
        FakeResponse(
            {
                "status": "success",
                "data": {
                    "result": [
                        {"metric": {"appid": "1"}, "value": [123.0, "10"]},
                        {"metric": {"appid": "2"}, "value": [123.0, "20"]},
                    ]
                },
            }
        )
    )
    client = GameDataClient("http://prometheus.local", session=session)

    with pytest.raises(PrometheusResponseError, match="Expected exactly one"):
        client.get_player_count()


def test_get_player_counts_raises_when_no_series_match():
    session = FakeSession(
        FakeResponse({"status": "success", "data": {"result": []}})
    )
    client = GameDataClient("http://prometheus.local", session=session)

    with pytest.raises(PlayerCountNotFoundError, match="No player data found"):
        client.get_player_counts({"appid": "missing"})


def test_get_player_counts_raises_on_prometheus_error_status():
    session = FakeSession(
        FakeResponse(
            {
                "status": "error",
                "errorType": "bad_data",
                "error": "invalid query",
            }
        )
    )
    client = GameDataClient("http://prometheus.local", session=session)

    with pytest.raises(PrometheusQueryError, match="bad_data"):
        client.get_player_counts()


@pytest.mark.parametrize('reject_zero', [False, True])
def test_zero_source_guard_is_explicit_and_preserves_default_zero_support(reject_zero):
    session=FakeSession(FakeResponse({'status':'success','data':{'result':[
        {'metric':{'appid':'730'},'value':[123,'0']}]}}))
    client=GameDataClient('http://unused',session=session,reject_zero_player_counts=reject_zero)
    if reject_zero:
        with pytest.raises(PlayerCountNotFoundError,match='Zero count rejected'):
            client.get_player_count({'appid':'730'})
    else:
        assert client.get_player_count({'appid':'730'})==0


def test_replay_zero_guard_holds_existing_capacity_and_last_decision(monkeypatch):
    from main import load_config,run_loop,build_engine
    from scaler.exporter.autoscaler_metrics import AutoscalerMetrics
    cfg=load_config(['--once','--minimum-instances','0','--reject-zero-player-counts'])
    assert cfg.reject_zero_player_counts
    engine=build_engine(config=cfg)
    engine.evaluate(100000)
    before=engine.vm_controller.running_instances()
    metrics=AutoscalerMetrics()
    def factory(*args,**kwargs):
        assert kwargs['reject_zero_player_counts'] is True
        return GameDataClient('http://unused',session=FakeSession(FakeResponse({
            'status':'success','data':{'result':[{'metric':{},'value':[123,'0']}]}})),
            reject_zero_player_counts=True)
    monkeypatch.setattr('main.GameDataClient',factory)
    run_loop(cfg,engine=engine,metrics=metrics)
    assert engine.vm_controller.running_instances()==before>0
    assert 'input_available 0\n' in metrics.render().decode()
    assert '\nlast_decision_timestamp_seconds ' not in metrics.render().decode()
