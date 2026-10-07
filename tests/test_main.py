import logging
import pytest

from main import AutoscalerConfig, evaluate_once, load_config, run_loop
from scaler.data.client import PrometheusQueryError
from scaler.engine.decision_engine import DecisionEngine
from scaler.exporter.autoscaler_metrics import AutoscalerMetrics
from scaler.infrastructure.vm_controller import FakeVMController
from scaler.strategies.threshold import ThresholdStrategy
from scaler.strategies.base import ScalingStrategy
from scaler.strategies.registry import STRATEGIES


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


def test_strategy_defaults_to_threshold(monkeypatch):
    monkeypatch.delenv("AUTOSCALER_STRATEGY", raising=False)
    assert load_config([]).strategy_name == "threshold"


@pytest.mark.parametrize("source", ["cli", "environment"])
@pytest.mark.parametrize("strategy, desired", [("threshold", 4), ("aggressive", 5)])
def test_strategy_selection_scales_for_customer_preference(monkeypatch, source, strategy, desired):
    monkeypatch.setenv("AUTOSCALER_STRATEGY", strategy if source == "environment" else "threshold")
    argv = ["--once"]
    if source == "cli":
        argv.extend(["--strategy", strategy])
    config = load_config(argv)
    metrics = AutoscalerMetrics()

    run_loop(config, client=FakeGameDataClient([700_000]), metrics=metrics)

    assert config.strategy_name == strategy
    assert f"running_instances {desired}" in metrics.render().decode("utf-8")


@pytest.mark.parametrize("argv", [[], ["--strategy", "unknown"]])
def test_unknown_strategy_is_rejected(monkeypatch, argv):
    monkeypatch.setenv("AUTOSCALER_STRATEGY", "unknown")
    with pytest.raises(SystemExit) as error:
        load_config(argv)
    assert error.value.code == 2


def test_cli_strategy_overrides_environment(monkeypatch):
    monkeypatch.setenv("AUTOSCALER_STRATEGY", "unknown")
    assert load_config(["--strategy", "threshold"]).strategy_name == "threshold"


def test_registered_strategy_is_selected_and_called_once(monkeypatch):
    class CustomStrategy(ScalingStrategy):
        def desired_instances(self, player_count):
            calls.append(player_count)
            return 7

    calls = []
    monkeypatch.setitem(STRATEGIES, "custom", CustomStrategy)
    monkeypatch.setenv("AUTOSCALER_STRATEGY", "custom")
    metrics = AutoscalerMetrics()

    config = load_config(["--once"])
    assert config.strategy_name == "custom"
    run_loop(config, client=FakeGameDataClient([150]), metrics=metrics)

    assert calls == [150]
    assert "running_instances 7" in metrics.render().decode("utf-8")


def test_load_config_reads_environment_and_cli_labels(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_URL", "http://prometheus.local")
    monkeypatch.setenv("AUTOSCALER_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AUTOSCALER_METRIC_NAME", "custom_player_count")
    monkeypatch.setenv("AUTOSCALER_LABELS", "appid=730,region=eu")
    monkeypatch.setenv("AUTOSCALER_METRICS_HOST", "127.0.0.1")
    monkeypatch.setenv("AUTOSCALER_METRICS_PORT", "9001")

    config = load_config(["--label", "region=us", "--once"])

    assert config.prometheus_url == "http://prometheus.local"
    assert config.interval_seconds == 5
    assert config.metric_name == "custom_player_count"
    assert config.label_filters == {"appid": "730", "region": "us"}
    assert config.metrics_host == "127.0.0.1"
    assert config.metrics_port == 9001
    assert config.run_once is True


def test_evaluate_once_logs_scaling_decision(caplog):
    client = FakeGameDataClient([150])
    engine = DecisionEngine(ThresholdStrategy(), FakeVMController())
    metrics = AutoscalerMetrics()

    with caplog.at_level(logging.INFO, logger="ruby_acorn.autoscaler"):
        running_instances = evaluate_once(client, engine, {"appid": "730"}, metrics)

    assert running_instances == 2
    assert "running_instances 2" in metrics.render().decode("utf-8")
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
