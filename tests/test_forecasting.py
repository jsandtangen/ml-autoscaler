import json

import pytest

from scaler.data.history import PlayerHistoryStore
from scaler.forecasting import (
    MovingAverageForecaster,
    PersistenceForecaster,
    PlayerObservation,
    evaluate_forecaster,
    load_player_history,
)
from scripts.evaluate_forecasts import main


def observations(counts):
    return [PlayerObservation(index * 30, count) for index, count in enumerate(counts)]


def test_baseline_predictions():
    assert PersistenceForecaster().predict([100, 200, 301]) == 301
    assert MovingAverageForecaster().predict([100, 200, 301]) == pytest.approx(601 / 3)


@pytest.mark.parametrize("model", [PersistenceForecaster(), MovingAverageForecaster()])
def test_empty_window_is_rejected(model):
    with pytest.raises(ValueError, match="at least one"):
        model.predict([])


def test_baselines_score_same_future_targets():
    series = observations([10, 20, 40, 80])
    persistence = evaluate_forecaster(series, PersistenceForecaster(), window_size=2)
    average = evaluate_forecaster(series, MovingAverageForecaster(), window_size=2)
    assert persistence.sample_count == average.sample_count == 2
    assert persistence.mae == 30
    assert persistence.rmse == pytest.approx((1000) ** 0.5)
    assert average.mae == 37.5
    assert average.rmse == pytest.approx((1562.5) ** 0.5)


def test_horizon_and_windows_do_not_include_future_values():
    windows = []

    class RecordingForecaster:
        def predict(self, window):
            windows.append(window)
            return window[-1]

    score = evaluate_forecaster(
        observations([10, 20, 40, 80, 160]), RecordingForecaster(), window_size=2, horizon=2
    )
    assert windows == [[10, 20], [20, 40]]
    assert score.sample_count == 2
    assert score.mae == 90
    assert score.rmse == pytest.approx(9000 ** 0.5)


def test_constant_zero_series_has_zero_error():
    for model in (PersistenceForecaster(), MovingAverageForecaster()):
        score = evaluate_forecaster(observations([0, 0, 0]), model, window_size=2)
        assert score.sample_count == 1
        assert score.mae == score.rmse == 0


@pytest.mark.parametrize("window,horizon", [(0, 1), (1, 0), (-1, 1), (2, 3)])
def test_invalid_or_insufficient_windows(window, horizon):
    with pytest.raises(ValueError):
        evaluate_forecaster(observations([1, 2, 3]), PersistenceForecaster(), window, horizon)


@pytest.mark.parametrize("timestamps", [[2, 1], [1, 1]])
def test_backtest_rejects_unordered_or_duplicate_timestamps(timestamps):
    with pytest.raises(ValueError, match="increasing"):
        evaluate_forecaster([PlayerObservation(t, 10) for t in timestamps], PersistenceForecaster(), 1)


def test_load_history_keeps_games_separate_and_sorts(tmp_path):
    path = tmp_path / "history.csv"
    store = PlayerHistoryStore(path)
    store.record(30, "730", timestamp=3)
    store.record(900, "570", timestamp=1)
    store.record(10, "730", timestamp=1)
    store.record(5, timestamp=1)
    games = load_player_history(path)
    assert games["730"] == [PlayerObservation(1, 10), PlayerObservation(3, 30)]
    assert games["570"] == [PlayerObservation(1, 900)]
    assert games[""] == [PlayerObservation(1, 5)]


@pytest.mark.parametrize("row", ["nan,730,1", "inf,730,1", "-1,730,1", "1,730,-1",
                                "1,730,1.5", "1,730", "1,730,2,extra"])
def test_malformed_history_is_rejected(tmp_path, row):
    path = tmp_path / "history.csv"
    path.write_text("timestamp,appid,player_count\n" + row + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_player_history(path)


def test_duplicate_history_is_rejected(tmp_path):
    path = tmp_path / "history.csv"
    store = PlayerHistoryStore(path)
    store.record(10, "730", timestamp=1)
    store.record(20, "730", timestamp=1)
    with pytest.raises(ValueError, match="Duplicate"):
        load_player_history(path)


def test_cli_evaluates_history_and_reports_latest_prediction(tmp_path, capsys):
    path = tmp_path / "history.csv"
    store = PlayerHistoryStore(path)
    for timestamp, count in enumerate([10, 20, 40, 80]):
        store.record(count, "730", timestamp=timestamp)
        store.record(500, "570", timestamp=timestamp)
    assert main(["--history-path", str(path), "--window-size", "2"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["horizon_observations"] == 1
    assert report["games"]["730"]["persistence"]["mae"] == 30
    assert report["games"]["730"]["persistence"]["latest_prediction"] == 80
    assert report["games"]["730"]["moving_average"]["latest_prediction"] == 60
    assert report["games"]["570"]["persistence"]["mae"] == 0


@pytest.mark.parametrize("appid", ["730", "999"])
def test_cli_fails_for_short_or_missing_game_history(tmp_path, capsys, appid):
    path = tmp_path / "history.csv"
    PlayerHistoryStore(path).record(10, "730", timestamp=1)
    with pytest.raises(SystemExit) as error:
        main(["--history-path", str(path), "--appid", appid])
    assert error.value.code == 1
    assert "Forecast evaluation failed" in capsys.readouterr().err
