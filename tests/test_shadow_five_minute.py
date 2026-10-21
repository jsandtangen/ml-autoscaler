import copy
from dataclasses import asdict

import pytest

from scaler.forecasting import PlayerObservation
from scaler.shadow import (FiveMinuteShadowObserver, evaluate_short_horizons,
                           validate_five_minute_history, short_predictions)
from scaler.strategies.capacity import CapacityPolicy
from scaler.engine.scaling_limits import ScalingLimits
from scaler.data.shadow_source import parse_replay_history, replay_cutoff, ShadowPrometheusClient
from scaler.data.shadow_source import observed_replay_rows
from scaler.exporter.shadow_metrics import render_shadow_metrics


POLICY = CapacityPolicy(0.0001, 100, 0.1, 0)
LIMITS = ScalingLimits(0, 1, 1)
REFERENCE = {"fresh": True, "appid_verified": True, "running_instances": 1,
             "desired_instances": 1, "capacity_policy": asdict(POLICY),
             "scaling_limits": asdict(LIMITS)}


class Forecast:
    def __init__(self):
        self.windows = []

    def predict_steps(self, counts):
        self.windows.append(list(counts))
        return [counts[-1]+i for i in range(1, 7)]


def history(n=20):
    return [PlayerObservation(i*300, 100+i) for i in range(n)]


def test_both_horizons_share_one_past_only_inference_and_equal_targets():
    model = Forecast()
    report = evaluate_short_horizons(history(), model, POLICY, LIMITS, 3, 4)
    assert model.windows == [[108, 109, 110], [109, 110, 111], [110, 111, 112], [111, 112, 113]]
    for h in ("15", "30"):
        assert report["scores"][h]["timesfm"]["mae"] == 0
        assert report["scores"][h]["timesfm"]["sample_count"] == 4
        assert report["scores"][h]["persistence"]["mae"] == int(h)//5
        assert report["scores"][h]["moving_average"]["mae"] == int(h)//5+1
    for row in report["predictions"]:
        assert row["target_timestamp"]-row["origin_timestamp"] == row["horizon_minutes"]*60
        assert 0 <= row["cycle_target_instances"] <= row["desired_instances"] <= 1


def test_audit_and_evaluation_reject_zero_windows_and_holes_without_filling():
    rows = history(30)
    rows[15] = PlayerObservation(4500, 0)
    del rows[17]
    audit = validate_five_minute_history(rows)
    assert audit["missing_intervals"] == 1
    assert audit["invalid_zero_rows"] == 1
    report = evaluate_short_horizons(rows, Forecast(), POLICY, LIMITS, 3, 20)
    assert report["rejected_origins"] > 0
    assert report["scores"]["15"]["timesfm"]["mae"] == 0


@pytest.mark.parametrize("rows", [[], [PlayerObservation(1, 1)],
    [PlayerObservation(0, -1)], [PlayerObservation(0, 1), PlayerObservation(0, 2)],
    [PlayerObservation(300, 1), PlayerObservation(0, 2)]])
def test_bad_source_data_rejected(rows):
    with pytest.raises(ValueError):
        validate_five_minute_history(rows)


def test_pending_multi_horizons_survive_restart_and_score_once():
    observer = FiveMinuteShadowObserver(POLICY, LIMITS, 3)
    observer.observe(history(5), Forecast(), REFERENCE, 10000)
    assert len(observer.state["pending"]) == 6
    observer = FiveMinuteShadowObserver(POLICY, LIMITS, 3, copy.deepcopy(observer.state))
    events = observer.observe(history(8), Forecast(), REFERENCE, 10900)
    assert len([e for e in events if e["event"] == "outcome"]) == 3
    assert observer.state["scores"]["timesfm:15"]["mae"] == 0
    events = observer.observe(history(11), Forecast(), REFERENCE, 11800)
    assert len([e for e in events if e["event"] == "outcome"]) == 6
    assert observer.state["scores"]["timesfm:30"]["count"] == 1
    assert observer.observe(history(11), Forecast(), REFERENCE, 11830) == []


def test_zero_or_missing_target_not_scored_against_later_sample():
    observer = FiveMinuteShadowObserver(POLICY, LIMITS, 3)
    observer.observe(history(5), Forecast(), REFERENCE, 10000)
    rows = history(8)
    rows[-1] = PlayerObservation(rows[-1].timestamp, 0)
    events = observer.observe(rows, Forecast(), REFERENCE, 10900)
    assert len([e for e in events if e["event"] == "outcome_unavailable"]) == 3
    assert not observer.state["scores"]
    assert observer.state["status"] == "invalid_context"


def test_missing_or_different_live_configuration_suppresses_proposals():
    observer = FiveMinuteShadowObserver(POLICY, LIMITS, 3)
    assert observer.observe(history(5), Forecast(), {}, 10000) == []
    assert observer.state["status"] == "configuration_unavailable_or_mismatch"


def test_metrics_separate_offline_from_live_and_expire_predictions():
    observer = FiveMinuteShadowObserver(POLICY, LIMITS, 3)
    observer.observe(history(5), Forecast(), REFERENCE, 10000, 9900)
    observer.state["checked_at"] = 10000
    observer.state["offline_scores"] = {"15": {"timesfm": {"sample_count": 3, "mae": 2, "rmse": 3}}}
    rendered = render_shadow_metrics(observer.state, 10010).decode()
    assert 'ruby_acorn_ml_predicted_players{appid="730",model="timesfm",horizon_minutes="15"} 107' in rendered
    assert 'evaluation="offline_replay"' in rendered
    assert '\nrunning_instances{' not in rendered
    assert 'ruby_acorn_ml_predicted_players{' not in render_shadow_metrics(observer.state, 12000).decode()
    observer.state["checked_at"] = 12000
    assert 'ruby_acorn_ml_forecast_available{appid="730"} 0' in render_shadow_metrics(observer.state, 12000).decode()


def test_replay_cutoff_excludes_future_source_rows_and_rejects_stale_health():
    payload = {"appid": 730, "interval_seconds": 300, "data_epoch": 0,
               "data": [{"interval": i, "timestamp": i*300, "count": 10+i} for i in range(10)]}
    rows = parse_replay_history(payload)
    health = {"status": "ok", "current_interval": 3, "max_interval": 9, "replay_epoch": 10000}
    cutoff = replay_cutoff(payload, health, 10910)
    assert [r.player_count for r in rows if r.timestamp <= cutoff] == [10, 11, 12, 13]
    with pytest.raises(ValueError):
        replay_cutoff(payload, health, 12000)
    delayed = {**health, "elapsed_seconds": 1350}
    assert replay_cutoff(payload, delayed, 11396) == 900
    with pytest.raises(ValueError):
        replay_cutoff(payload, delayed, 11600)
    payload["data"][1]["timestamp"] = 600
    with pytest.raises(ValueError, match="disagree"):
        parse_replay_history(payload)


def test_pegasus_jobs_are_explicit_and_separate_from_original_defaults():
    client = ShadowPrometheusClient("http://test", source_job="games", autoscaler_job="ruby_acorn_pegasus")
    queries = []
    def query(q):
        queries.append(q)
        return {"data": {"result": []}}
    client._query_prometheus = query
    with pytest.raises(Exception):
        client.latest_observation("730")
    with pytest.raises(Exception):
        client.live_decision("730", 0)
    assert 'job="games"' in queries[0]
    assert 'job="ruby_acorn_pegasus"' in queries[1]


def test_cached_source_is_matched_to_past_only_and_ambiguous_matches_fail():
    rows = history()
    result = observed_replay_rows(rows, 3000, PlayerObservation(10000, 108), 10005, 3)
    assert result[-1].timestamp == 2400
    with pytest.raises(ValueError):
        observed_replay_rows(rows, 3000, PlayerObservation(10000, 111), 10005, 3)
    with pytest.raises(ValueError):
        observed_replay_rows(rows, 3000, PlayerObservation(9000, 108), 10005, 3)
    rows[9] = PlayerObservation(2700, 108)
    with pytest.raises(ValueError, match="uniquely"):
        observed_replay_rows(rows, 3000, PlayerObservation(10000, 108), 10005, 3)


@pytest.mark.parametrize("compressed", [False, True])
def test_health_read_bypasses_varnish_cache(monkeypatch, compressed):
    from scripts.shadow_autoscaler import read_source_json
    import requests
    from types import SimpleNamespace
    import gzip
    urls = []
    def get(url, **kwargs):
        urls.append(url)
        raw = b'{"status":"ok"}'
        return SimpleNamespace(content=gzip.compress(raw) if compressed else raw,
                               raise_for_status=lambda: None)
    monkeypatch.setattr(requests, "get", get)
    assert read_source_json("http://source/health")["status"] == "ok"
    assert urls[0].startswith("http://source/health?ml_read=")


def test_truncated_history_is_rejected_even_without_an_interior_gap():
    payload = {"appid": 730, "interval_seconds": 300, "data_epoch": 0, "data_points": 2,
               "data": [{"interval": 0, "timestamp": 0, "count": 100}]}
    with pytest.raises(ValueError, match="Truncated"):
        parse_replay_history(payload)


def test_runner_refuses_openstack_environment_before_reading_data(monkeypatch, tmp_path):
    from scripts.shadow_autoscaler import run, parse_args
    monkeypatch.setenv("OS_APPLICATION_CREDENTIAL_SECRET", "synthetic-never-used")
    args = parse_args(["--five-minute", "--once", "--output-dir", str(tmp_path)])
    with pytest.raises(ValueError, match="without OpenStack"):
        run(args, forecaster=Forecast())
    assert not list(tmp_path.iterdir())


def test_five_minute_runner_persists_and_restores_without_duplicate_forecasts(monkeypatch, tmp_path):
    import json
    import scripts.shadow_autoscaler as runner
    payload = {"appid": 730, "interval_seconds": 300, "data_epoch": 0,
               "data": [{"interval": i, "timestamp": i*300, "count": 100+i} for i in range(30)]}
    health = {"status": "ok", "current_interval": 19, "max_interval": 29,
              "replay_epoch": 10000, "elapsed_seconds": 5710}
    monkeypatch.setattr(runner, "read_source_json", lambda url: health if url.endswith("health") else payload)
    class Client:
        def latest_observation(self, appid):
            return PlayerObservation(15710, 119)
        def live_decision(self, appid, now):
            return REFERENCE
    args = runner.parse_args(["--five-minute", "--once", "--window-size", "3",
                              "--output-dir", str(tmp_path), "--metrics-port", "0",
                              "--player-share", "0.0001", "--minimum-instances", "0",
                              "--max-instances", "1", "--max-scaling-change", "1"])
    model = Forecast()
    assert runner.run(args, Client(), model, clock=lambda: 15710) == 0
    assert model.windows == [[117, 118, 119]]
    assert runner.run(args, Client(), model, clock=lambda: 15710) == 0
    assert len(model.windows) == 1
    events = [json.loads(line) for line in (tmp_path/"events.jsonl").read_text().splitlines()]
    assert len(events) == 6
    assert all(e["target_timestamp"] > 5700 for e in events)
    assert json.loads((tmp_path/"process.json").read_text())["running"] is False


def test_model_failure_is_visible_and_does_not_create_zero_forecasts():
    class Failing:
        def predict_steps(self, counts):
            raise RuntimeError("model unavailable")
    observer = FiveMinuteShadowObserver(POLICY, LIMITS, 3)
    with pytest.raises(RuntimeError, match="unavailable"):
        observer.observe(history(), Failing(), REFERENCE, 10000)
    assert observer.state["latest"] == []
    assert not observer.state["pending"]


def test_crash_between_state_commit_and_journal_does_not_reissue(monkeypatch, tmp_path):
    import json
    import scripts.shadow_autoscaler as runner
    from pathlib import Path
    payload = {"appid": 730, "interval_seconds": 300, "data_epoch": 0,
               "data": [{"interval": i, "timestamp": i*300, "count": 100+i} for i in range(20)]}
    health = {"status": "ok", "current_interval": 19, "max_interval": 19,
              "replay_epoch": 10000, "elapsed_seconds": 5710}
    monkeypatch.setattr(runner, "read_source_json", lambda url: health if url.endswith("health") else payload)
    class Client:
        def latest_observation(self, appid):
            return PlayerObservation(15710, 119)
        def live_decision(self, appid, now):
            return REFERENCE
    args = runner.parse_args(["--five-minute", "--once", "--window-size", "3",
        "--output-dir", str(tmp_path), "--metrics-port", "0", "--player-share", "0.0001",
        "--minimum-instances", "0", "--max-instances", "1", "--max-scaling-change", "1"])
    original = Path.open
    def crash(path, *args, **kwargs):
        if path.name == "events.jsonl":
            raise SystemExit("simulated crash")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", crash)
    model = Forecast()
    with pytest.raises(SystemExit, match="simulated crash"):
        runner.run(args, Client(), model, clock=lambda: 15710)
    monkeypatch.setattr(Path, "open", original)
    assert len(json.loads((tmp_path/"status.json").read_text())["pending"]) == 6
    assert runner.run(args, Client(), model, clock=lambda: 15710) == 0
    assert len(model.windows) == 1
