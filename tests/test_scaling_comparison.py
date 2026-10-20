import json

import pytest

from scripts.simulate_scaling import parse_args, run_comparison
from scaler.exporter.autoscaler_metrics import AutoscalerMetrics


class RecordingModel:
    def __init__(self):
        self.windows = []

    def predict(self, counts):
        self.windows.append(list(counts))
        return counts[-1]


def comparison(tmp_path, caps=(7, 10, 15)):
    history = tmp_path / "history.csv"
    history.write_text("timestamp,appid,player_count\n" + "".join(
        f"{i*3600},730,{n}\n" for i, n in enumerate([500000, 700000, 1200000, 1300000, 400000])))
    args = parse_args(["--history-path", str(history), "--appid", "730",
                       "--output-dir", str(tmp_path / "report"), "--window-size", "2",
                       "--evaluation-hours", "3", "--compare-max-instances", *map(str, caps)])
    model = RecordingModel()
    report = run_comparison(args, model)
    return args, report, model


def test_comparison_reuses_past_windows_and_shared_period_with_bounded_resource_changes(tmp_path):
    args, report, model = comparison(tmp_path)
    assert model.windows == [[500000, 700000], [700000, 1200000], [1200000, 1300000]]
    assert report["mode"] == "offline_limit_comparison"
    assert set(report["scenarios"]) == {"7", "10", "15"}
    for cap, scenario in report["scenarios"].items():
        assert scenario["assumptions"]["fixed_instances"] == int(cap)
        assert scenario["assumptions"]["scaling_limits"]["maximum_instances"] == int(cap)
        assert scenario["evaluation"] == report["scenarios"]["7"]["evaluation"]
        assert scenario["history_sha256"] == report["scenarios"]["7"]["history_sha256"]
        import csv
        with (args.output_dir / f"max-{cap}" / "decisions.csv").open() as file:
            for row in csv.DictReader(file):
                assert abs(int(row["requested_next_instances"])-int(row["running_instances"])) <= 2
                assert int(row["requested_next_instances"]) <= int(cap)
    assert (args.output_dir / "comparison.json").exists()


@pytest.mark.parametrize("caps", [(7, 7), (0, 7)])
def test_invalid_comparison_limits_produce_no_report(tmp_path, caps):
    with pytest.raises(ValueError):
        comparison(tmp_path, caps)
    assert not (tmp_path / "report").exists()


def test_metrics_keep_simulation_totals_separate_from_live_allocation(tmp_path):
    args, report, _ = comparison(tmp_path)
    metrics = AutoscalerMetrics()
    metrics.set_running_instances(3)
    metrics.set_simulation_report(args.output_dir / "comparison.json")
    body = metrics.render().decode()
    assert "simulation_report_available 1\n" in body
    for cap, scenario in report["scenarios"].items():
        for strategy, result in scenario["results"].items():
            tag = f'{{appid="730",strategy="{strategy}",max_instances="{cap}"}}'
            assert f'simulation_cost_eur{tag} {result["total_cost_eur"]:.12g}\n' in body
    assert "running_instances 3\n" in body
    assert "dynamic_cost 0.3\n" in body
    assert len([line for line in body.splitlines() if line.startswith("simulation_cost_eur{")]) == 9


def test_metrics_identify_actual_backend_and_replace_old_backend():
    metrics = AutoscalerMetrics()
    metrics.set_controller("fake", "730")
    assert 'backend="fake",resource_type="simulated_instances"' in metrics.render().decode()
    metrics.set_controller("docker", "730")
    body = metrics.render().decode()
    assert 'controller_info{appid="730",backend="docker",resource_type="local_containers"} 1' in body
    assert 'backend="fake"' not in body


@pytest.mark.parametrize("damage", ["missing", "invalid_json", "nan", "period", "source", "shape"])
def test_invalid_or_inconsistent_reports_show_unavailable_without_zero_costs(tmp_path, damage):
    args, report, _ = comparison(tmp_path)
    path = args.output_dir / "comparison.json"
    if damage == "missing":
        path.unlink()
    elif damage == "invalid_json":
        path.write_text("{")
    else:
        if damage == "nan":
            report["scenarios"]["15"]["results"]["reactive_capacity"]["total_cost_eur"] = float("nan")
        elif damage == "period":
            report["scenarios"]["15"]["evaluation"]["hours"] = 999
        elif damage == "source":
            report["scenarios"]["15"]["history_sha256"] = "other"
        else:
            report["scenarios"] = []
        path.write_text(json.dumps(report))
    metrics = AutoscalerMetrics()
    metrics.set_simulation_report(path)
    body = metrics.render().decode()
    assert "simulation_report_available 0\n" in body
    assert "simulation_cost_eur{" not in body
