from scaler.exporter.autoscaler_metrics import AutoscalerMetrics


def test_autoscaler_metrics_render_running_instances():
    metrics = AutoscalerMetrics()

    metrics.set_running_instances(4)
    body = metrics.render().decode("utf-8")

    assert "# TYPE running_instances gauge" in body
    assert "running_instances 4" in body
