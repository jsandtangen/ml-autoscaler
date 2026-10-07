from scaler.exporter.autoscaler_metrics import AutoscalerMetrics


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
