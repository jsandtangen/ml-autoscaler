import pytest

from scaler.exporter.steam_player_exporter import (
    SteamPlayerClient,
    SteamPlayerExporterError,
    SteamPlayerFetchError,
    load_config,
    parse_current_players,
    render_metrics,
)


class FakeResponse:
    def __init__(self, payload, status_error=None, json_error=None):
        self.payload = payload
        self.status_error = status_error
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_error:
            raise self.status_error

    def json(self):
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def get(self, url, params, timeout):
        self.requests.append({"url": url, "params": params, "timeout": timeout})
        return self.response


def test_client_fetches_current_players():
    session = FakeSession(FakeResponse({"response": {"result": 1, "player_count": 42}}))
    client = SteamPlayerClient(
        "https://steam.example/current_players",
        session=session,
        timeout_seconds=2,
    )

    player_count = client.get_current_players("730")

    assert player_count == 42
    assert session.requests == [
        {
            "url": "https://steam.example/current_players",
            "params": {"appid": "730"},
            "timeout": 2,
        }
    ]


def test_parse_current_players_rejects_missing_count():
    with pytest.raises(SteamPlayerFetchError, match="missing player_count"):
        parse_current_players({"response": {"result": 1}})


def test_parse_current_players_rejects_negative_count():
    with pytest.raises(SteamPlayerFetchError, match="cannot be negative"):
        parse_current_players({"response": {"result": 1, "player_count": -1}})


def test_parse_current_players_rejects_error_result():
    with pytest.raises(SteamPlayerFetchError, match="result=42"):
        parse_current_players({"response": {"result": 42, "player_count": 0}})


def test_render_metrics_escapes_labels():
    body = render_metrics("steam_player_count", '7"30', 150).decode("utf-8")

    assert "# TYPE steam_player_count gauge" in body
    assert 'steam_player_count{appid="7\\"30"} 150' in body


def test_load_config_reads_environment_and_cli(monkeypatch):
    monkeypatch.setenv("STEAM_APPID", "570")
    monkeypatch.setenv("STEAM_EXPORTER_HOST", "127.0.0.1")
    monkeypatch.setenv("STEAM_EXPORTER_PORT", "9000")
    monkeypatch.setenv("STEAM_EXPORTER_METRIC_NAME", "custom_count")
    monkeypatch.setenv("STEAM_API_URL", "https://steam.example")
    monkeypatch.setenv("STEAM_REQUEST_TIMEOUT_SECONDS", "3")

    config = load_config(["--appid", "730", "--timeout", "1.5"])

    assert config.appid == "730"
    assert config.host == "127.0.0.1"
    assert config.port == 9000
    assert config.metric_name == "custom_count"
    assert config.steam_api_url == "https://steam.example"
    assert config.timeout_seconds == 1.5


def test_fetch_errors_share_exporter_base_type():
    assert issubclass(SteamPlayerFetchError, SteamPlayerExporterError)
