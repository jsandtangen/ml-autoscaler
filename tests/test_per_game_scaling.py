import logging

import pytest

from main import AutoscalerConfig, run_loop
from scaler.data.client import PrometheusQueryError
from scaler.exporter.autoscaler_metrics import AutoscalerMetrics
from scaler.game_config import GameConfig


class PerGameClient:
    def __init__(self, samples):
        self.samples = {appid: iter(values) for appid, values in samples.items()}
        self.requests = []

    def get_player_count(self, labels):
        self.requests.append(labels)
        value = next(self.samples[labels["appid"]])
        if isinstance(value, Exception):
            raise value
        return value


def config(run_once=True):
    return AutoscalerConfig(
        prometheus_url="http://prometheus.local",
        interval_seconds=1,
        metric_name="steam_player_count",
        label_filters={"appid": "999", "region": "eu"},
        run_once=run_once,
        games=(GameConfig("730", "aggressive"), GameConfig("570", "threshold")),
    )


def test_each_game_uses_its_own_strategy_and_query(caplog):
    client = PerGameClient({"730": [700_000], "570": [700_000]})
    metrics = AutoscalerMetrics()
    with caplog.at_level(logging.INFO):
        run_loop(config(), client=client, metrics=metrics)
    assert client.requests == [
        {"appid": "730", "region": "eu"}, {"appid": "570", "region": "eu"}
    ]
    body = metrics.render().decode()
    assert 'running_instances{appid="730"} 5' in body
    assert 'running_instances{appid="570"} 4' in body
    assert "appid=730 strategy=aggressive" in caplog.text
    assert "appid=570 strategy=threshold" in caplog.text


def test_each_game_retains_independent_state_across_cycles(monkeypatch, caplog):
    client = PerGameClient({"730": [700_000, 50], "570": [700_000, 150]})
    metrics = AutoscalerMetrics()
    monkeypatch.setattr("main.start_metrics_server", lambda *args: None)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt

    with caplog.at_level(logging.INFO), pytest.raises(KeyboardInterrupt):
        run_loop(config(False), client=client, metrics=metrics, sleep=sleep)

    assert sleeps == [1, 1]
    assert "players=50 current_instances=5 desired_instances=2" in caplog.text
    assert "players=150 current_instances=4 desired_instances=2" in caplog.text
    body = metrics.render().decode()
    assert 'running_instances{appid="730"} 2' in body
    assert 'running_instances{appid="570"} 2' in body


def test_failure_for_one_game_preserves_state_and_other_game_continues(monkeypatch, caplog):
    error = PrometheusQueryError("missing sample")
    client = PerGameClient({"730": [700_000, error], "570": [error, 150]})
    metrics = AutoscalerMetrics()
    monkeypatch.setattr("main.start_metrics_server", lambda *args: None)
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            assert 'running_instances{appid="570"} 0' in metrics.render().decode()
        else:
            raise KeyboardInterrupt

    with caplog.at_level(logging.WARNING), pytest.raises(KeyboardInterrupt):
        run_loop(config(False), client=client, metrics=metrics, sleep=sleep)

    body = metrics.render().decode()
    assert 'running_instances{appid="730"} 5' in body
    assert 'running_instances{appid="570"} 2' in body
    assert "missing sample appid=730" in caplog.text
    assert "missing sample appid=570" in caplog.text
