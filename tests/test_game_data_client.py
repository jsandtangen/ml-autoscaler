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
