import logging

from main import AutoscalerConfig, evaluate_once, load_config, run_loop
from scaler.data.client import PrometheusQueryError
from scaler.engine.decision_engine import DecisionEngine
from scaler.infrastructure.vm_controller import FakeVMController
from scaler.strategies.threshold import ThresholdStrategy


class FakeGameDataClient:
    def __init__(self, player_counts=None, error=None):
        self.player_counts = list(player_counts or [])
        self.error = error
        self.requests = []

    def get_player_count(self, label_filters):
        self.requests.append(label_filters)

        if self.error:
            raise self.error

        return self.player_counts.pop(0)


def test_load_config_reads_environment_and_cli_labels(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local")
    monkeypatch.setenv("AUTOSCALER_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AUTOSCALER_METRIC_NAME", "custom_player_count")
    monkeypatch.setenv("AUTOSCALER_LABELS", "appid=730,region=eu")

    config = load_config(["--label", "region=us", "--once"])

    assert config.prometheus_url == "http://prometheus.local"
    assert config.interval_seconds == 5
    assert config.metric_name == "custom_player_count"
    assert config.label_filters == {"appid": "730", "region": "us"}
    assert config.run_once is True


def test_evaluate_once_logs_scaling_decision(caplog):
    client = FakeGameDataClient([150])
    engine = DecisionEngine(ThresholdStrategy(), FakeVMController())

    with caplog.at_level(logging.INFO, logger="ruby_acorn.autoscaler"):
        running_instances = evaluate_once(client, engine, {"appid": "730"})

    assert running_instances == 2
    assert client.requests == [{"appid": "730"}]
    assert "players=150" in caplog.text
    assert "desired_instances=2" in caplog.text
    assert "scaled up by 2" in caplog.text


def test_run_loop_once_skips_failed_prometheus_query(caplog):
    config = AutoscalerConfig(
        prometheus_url="http://prometheus.local",
        interval_seconds=1,
        metric_name="steam_player_count",
        label_filters={"appid": "730"},
        run_once=True,
    )
    client = FakeGameDataClient(error=PrometheusQueryError("connection refused"))

    with caplog.at_level(logging.WARNING, logger="ruby_acorn.autoscaler"):
        run_loop(config, client=client, sleep=lambda seconds: None)

    assert "Skipping autoscaler evaluation" in caplog.text
    assert "connection refused" in caplog.text


def test_run_loop_once_does_not_sleep():
    config = AutoscalerConfig(
        prometheus_url="http://prometheus.local",
        interval_seconds=1,
        metric_name="steam_player_count",
        label_filters={},
        run_once=True,
    )
    slept = []

    run_loop(
        config,
        client=FakeGameDataClient([50]),
        sleep=lambda seconds: slept.append(seconds),
    )

    assert slept == []
