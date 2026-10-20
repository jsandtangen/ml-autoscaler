import pytest

from scaler.forecasting import PlayerObservation
from scaler.training import chronological_split, score_predictions
from scripts.finetune_timesfm import main, parse_args


def observations(size):
    return [PlayerObservation(i*3600, i) for i in range(size)]


def test_chronological_targets_are_disjoint_and_context_is_past_only():
    split = chronological_split(observations(30), 4, 3, 6, 6)
    assert split.train_end == 18
    assert split.validation_end == 24
    assert (len(split.train), len(split.validation), len(split.test)) == (12, 4, 4)
    train_targets = {i for w in split.train for i in w.target_indices}
    validation_targets = {i for w in split.validation for i in w.target_indices}
    test_targets = {i for w in split.test for i in w.target_indices}
    assert max(train_targets) < min(validation_targets)
    assert max(validation_targets) < min(test_targets)
    for windows in (split.train, split.validation, split.test):
        for window in windows:
            assert window.context == tuple(range(window.target_indices[0]-4, window.target_indices[0]))
            assert window.targets == window.target_indices
    assert split.validation[0].context == (14, 15, 16, 17)
    assert split.test[-1].target_indices == (27, 28, 29)


def test_actual_experiment_split_counts():
    split = chronological_split(observations(444))
    assert split.train_end == 300
    assert split.validation_end == 372
    assert (len(split.train), len(split.validation), len(split.test)) == (108, 72, 72)
    assert split.interval_seconds == 3600


@pytest.mark.parametrize("timestamps", [[0, 3600, 7200, 7200, 14400, 18000],
                                       [0, 3600, 7200, 14400, 18000, 21600],
                                       [0, 3600, 7200, 10800, 14400, float("nan")]])
def test_training_rejects_duplicates_gaps_and_nonfinite_intervals(timestamps):
    with pytest.raises(ValueError, match="regularly spaced"):
        chronological_split([PlayerObservation(t, 10) for t in timestamps], 1, 1, 1, 1)


@pytest.mark.parametrize("context,horizon,validation,test", [(0,1,2,2), (4,1,4,4), (2,3,2,2)])
def test_training_rejects_empty_or_insufficient_splits(context,horizon,validation,test):
    with pytest.raises(ValueError):
        chronological_split(observations(10), context, horizon, validation, test)


def test_score_forecasts_on_identical_targets():
    score = score_predictions([12, 17], [10, 20])
    assert score.sample_count == 2
    assert score.mae == 2.5
    assert score.rmse == pytest.approx((13/2)**0.5)


@pytest.mark.parametrize("predictions,targets", [([],[]), ([1],[1,2]), ([float("nan")],[1]), ([-1],[1])])
def test_invalid_forecasts_cannot_be_reported_as_success(predictions, targets):
    with pytest.raises(ValueError):
        score_predictions(predictions, targets)


@pytest.mark.parametrize("options", [["--context-length","168"], ["--horizon","129"],
                                     ["--learning-rate","nan"], ["--epochs","0"]])
def test_cli_rejects_invalid_training_configuration(options):
    with pytest.raises(SystemExit) as error:
        parse_args(["--history-path","unused.csv","--appid","730","--output-dir","unused",*options])
    assert error.value.code == 2


def test_bad_history_fails_before_creating_run_directory(tmp_path):
    path = tmp_path/"short.csv"
    path.write_text("timestamp,appid,player_count\n0,730,100\n")
    output = tmp_path/"run"
    assert main(["--history-path",str(path),"--appid","730","--output-dir",str(output)]) == 1
    assert not output.exists()


def test_existing_run_is_preserved(tmp_path):
    from scripts.finetune_timesfm import train

    path = tmp_path/"history.csv"
    path.write_text("timestamp,appid,player_count\n"+"".join(f"{i*3600},730,{i}\n" for i in range(444)))
    output = tmp_path/"existing"
    output.mkdir()
    marker = output/"report.json"
    marker.write_text('{"status":"complete"}')
    args = parse_args(["--history-path",str(path),"--appid","730","--output-dir",str(output)])
    with pytest.raises(ValueError, match="already exists"):
        train(args)
    assert marker.read_text() == '{"status":"complete"}'


def test_failed_training_report_is_not_left_as_running(monkeypatch, tmp_path):
    import json
    from scripts import finetune_timesfm

    output = tmp_path/"failed-run"
    def fail(args):
        args.output_dir.mkdir()
        (args.output_dir/"report.json").write_text('{"status":"running"}')
        raise RuntimeError("simulated training failure")
    monkeypatch.setattr(finetune_timesfm, "train", fail)
    assert main(["--history-path","unused.csv","--appid","730","--output-dir",str(output)]) == 1
    report = json.loads((output/"report.json").read_text())
    assert report["status"] == "failed"
    assert "simulated training failure" in report["error"]
