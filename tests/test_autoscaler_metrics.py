from scaler.exporter.autoscaler_metrics import AutoscalerMetrics
import pytest


def test_autoscaler_metrics_render_running_instances():
    metrics = AutoscalerMetrics()

    metrics.set_running_instances(4)
    body = metrics.render().decode("utf-8")

    assert "# TYPE running_instances gauge" in body
    assert "running_instances 4" in body


def test_per_game_metrics_are_separate_series():
    metrics = AutoscalerMetrics()
    metrics.set_running_instances(5, appid="730")
    metrics.set_running_instances(4, appid="570")
    metrics.set_running_instances(3, appid="570")
    body = metrics.render().decode()
    assert 'running_instances{appid="730"} 5' in body
    assert 'running_instances{appid="570"} 3' in body
    assert "running_instances 0" not in body


def test_per_game_metric_labels_are_escaped():
    metrics = AutoscalerMetrics()
    metrics.set_running_instances(2, appid='a"b\\c\nd')
    assert 'appid="a\\"b\\\\c\\nd"' in metrics.render().decode()


def test_no_decision_values_before_successful_evaluation():
    metrics = AutoscalerMetrics()
    metrics.set_strategy("aggressive", appid="730")
    body = metrics.render().decode()
    assert 'strategy{appid="730",name="aggressive"} 1' in body
    assert 'running_instances{appid="730"} 0' in body
    samples = [line for line in body.splitlines() if not line.startswith("#")]
    assert len(samples) == 2


@pytest.mark.parametrize("current, desired, action", [(0, 4, 1), (5, 2, -1), (4, 4, 0)])
@pytest.mark.parametrize("appid", [None, "730"])
def test_decision_metrics(current, desired, action, appid, monkeypatch):
    monkeypatch.setattr("scaler.exporter.autoscaler_metrics.time.time", lambda: 1234.5)
    metrics = AutoscalerMetrics()
    metrics.record_decision(700_000, current, desired, desired, "threshold", appid=appid)
    body = metrics.render().decode()
    labels = '{appid="730"}' if appid else ""
    for name, value in [("player_count", 700_000), ("running_instances", desired),
                        ("desired_instances", desired), ("scaling_action", action),
                        ("last_decision_timestamp_seconds", 1234.5)]:
        assert f"# TYPE {name} gauge" in body
        assert f"{name}{labels} {value}\n" in body
    strategy_labels = '{appid="730",name="threshold"}' if appid else '{name="threshold"}'
    assert f"strategy{strategy_labels} 1\n" in body


def test_strategy_change_replaces_old_info_series():
    metrics = AutoscalerMetrics()
    metrics.record_decision(150, 0, 2, 2, "threshold", appid="730")
    metrics.record_decision(50, 2, 2, 2, "aggressive", appid="730")
    body = metrics.render().decode()
    assert 'name="threshold"' not in body
    assert 'strategy{appid="730",name="aggressive"} 1' in body
    assert 'player_count{appid="730"} 50' in body
    assert 'scaling_action{appid="730"} 0' in body
