import sys
from types import SimpleNamespace

import pytest

from scaler.forecasting import PlayerObservation, evaluate_forecaster
from scaler.timesfm_forecasting import MODEL_ID, TimesFMError, TimesFMForecaster


def fake_library(monkeypatch, points=None):
    calls = []

    class Backend:
        def compile(self, config):
            calls.append(("compile", config))

        def forecast(self, **kwargs):
            calls.append(("forecast", kwargs))
            return ([[11, 22]] if points is None else points), None

    def load(model_id, **kwargs):
        calls.append(("load", model_id, kwargs))
        return Backend()

    monkeypatch.setitem(sys.modules, "timesfm", SimpleNamespace(
        TimesFM_2p5_200M_torch=SimpleNamespace(from_pretrained=load),
        ForecastConfig=lambda **kwargs: kwargs,
    ))
    return calls


def test_loads_once_and_selects_last_horizon_step(monkeypatch, tmp_path):
    calls = fake_library(monkeypatch)
    model = TimesFMForecaster(2, 2, cache_dir=tmp_path, local_files_only=True)
    assert calls == []
    assert model.predict([10, 20]) == 22
    assert model.predict([20, 30]) == 22
    loads = [call for call in calls if call[0] == "load"]
    assert loads == [("load", MODEL_ID, {
        "cache_dir": str(tmp_path), "local_files_only": True,
        "force_download": False, "torch_compile": False,
    })]
    assert calls[1] == ("compile", {
        "max_context": 2, "max_horizon": 2, "normalize_inputs": True,
        "infer_is_positive": True, "per_core_batch_size": 1,
    })


def test_rolling_evaluation_passes_only_past_windows(monkeypatch):
    calls = fake_library(monkeypatch)
    series = [PlayerObservation(i * 30, count) for i, count in enumerate([1, 2, 3, 4, 5])]
    score = evaluate_forecaster(series, TimesFMForecaster(2, 2), 2, 2)
    assert [call[1]["inputs"] for call in calls if call[0] == "forecast"] == [
        [[1, 2]], [[2, 3]],
    ]
    assert score.sample_count == 2
    assert score.mae == 17.5


@pytest.mark.parametrize("points", [[], [[1]], [[1, 2], [3, 4]], [[1, float("nan")]],
                                  [[1, float("inf")]], [[1, -1]]])
def test_invalid_output_fails_instead_of_falling_back(monkeypatch, points):
    fake_library(monkeypatch, points)
    with pytest.raises(TimesFMError, match="forecast failed"):
        TimesFMForecaster(2, 2).predict([1, 2])


@pytest.mark.parametrize("counts", [[], [1], [1, 2, 3], [1, -1], [1, float("nan")]])
def test_invalid_input_does_not_load_model(monkeypatch, counts):
    calls = fake_library(monkeypatch)
    with pytest.raises(ValueError):
        TimesFMForecaster(2).predict(counts)
    assert calls == []


@pytest.mark.parametrize("window,horizon", [(0, 1), (1, 0), (16384, 1), (16257, 1)])
def test_invalid_configuration(window, horizon):
    with pytest.raises(ValueError):
        TimesFMForecaster(window, horizon)


def test_missing_optional_dependency_has_install_instructions(monkeypatch):
    monkeypatch.setitem(sys.modules, "timesfm", None)
    with pytest.raises(TimesFMError, match="ML extra"):
        TimesFMForecaster(2).predict([1, 2])


def test_checkpoint_failure_is_actionable(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("weights unavailable")
    monkeypatch.setitem(sys.modules, "timesfm", SimpleNamespace(
        TimesFM_2p5_200M_torch=SimpleNamespace(from_pretrained=fail),
    ))
    with pytest.raises(TimesFMError, match="weights unavailable"):
        TimesFMForecaster(2).predict([1, 2])


def test_cli_compares_three_models_and_reports_checkpoint(monkeypatch, tmp_path, capsys):
    import json
    from scripts.evaluate_forecasts import main

    fake_library(monkeypatch)
    history = tmp_path / "history.csv"
    history.write_text("timestamp,appid,player_count\n0,730,1\n30,730,2\n60,730,3\n90,730,4\n")
    assert main(["--history-path", str(history), "--include-timesfm",
                 "--window-size", "2", "--horizon", "2", "--local-files-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    models = report["games"]["730"]
    assert set(models) == {"persistence", "moving_average", "timesfm"}
    assert {model["sample_count"] for model in models.values()} == {1}
    assert models["timesfm"]["latest_prediction"] == 22
    assert report["timesfm"]["model_id"] == MODEL_ID


def test_cli_validates_all_games_before_loading_weights(monkeypatch, tmp_path, capsys):
    from scripts.evaluate_forecasts import main

    calls = fake_library(monkeypatch)
    history = tmp_path / "history.csv"
    history.write_text("timestamp,appid,player_count\n0,730,1\n30,730,2\n60,730,3\n0,570,4\n")
    with pytest.raises(SystemExit) as error:
        main(["--history-path", str(history), "--include-timesfm", "--window-size", "2"])
    assert error.value.code == 1
    assert "appid '570': Need at least 3" in capsys.readouterr().err
    assert calls == []


def test_cli_model_failure_does_not_emit_a_comparison_report(monkeypatch, tmp_path, capsys):
    from scripts.evaluate_forecasts import main

    monkeypatch.setitem(sys.modules, "timesfm", None)
    history = tmp_path / "history.csv"
    history.write_text("timestamp,appid,player_count\n0,730,1\n30,730,2\n60,730,3\n")
    with pytest.raises(SystemExit) as error:
        main(["--history-path", str(history), "--include-timesfm", "--window-size", "2"])
    captured = capsys.readouterr()
    assert error.value.code == 1
    assert captured.out == ""
    assert "ML extra" in captured.err
