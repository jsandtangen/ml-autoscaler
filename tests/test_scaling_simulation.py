import json
import math

import pytest

from scaler.forecasting import PlayerObservation
from scaler.simulation import FixedCapacityStrategy, simulate_scaling
from scaler.strategies.capacity import CapacityPolicy, ReactiveCapacityStrategy
from scaler.strategies.predictive import PredictiveCapacityStrategy
from scripts.simulate_scaling import main, parse_args, run


class RecordingForecaster:
    def __init__(self, predictions):
        self.predictions = iter(predictions)
        self.windows = []

    def predict(self, counts):
        self.windows.append(list(counts))
        return next(self.predictions)


def observations(counts):
    return [PlayerObservation(i*3600, count) for i, count in enumerate(counts)]


@pytest.mark.parametrize("players,expected", [(0,1), (99,1), (100,1), (101,2), (300,3)])
def test_capacity_boundaries(players, expected):
    assert CapacityPolicy(1, 100, 0).desired_instances(players) == expected


def test_customer_share_and_buffer_do_not_round_up_exact_boundary():
    assert CapacityPolicy(0.001,100,0).desired_instances(100_000) == 1
    assert CapacityPolicy(1,110,0.1).desired_instances(100) == 1
    assert CapacityPolicy(0.001,100,0.1).desired_instances(100_000) == 2
    assert CapacityPolicy(0.001,100).customer_players(100_000) == 100


@pytest.mark.parametrize("options", [dict(player_share=0),dict(player_share=1.1),
    dict(player_share=float("nan")),dict(players_per_instance=0),
    dict(headroom_fraction=-1),dict(headroom_fraction=float("inf")),dict(minimum_instances=-1)])
def test_invalid_capacity_assumptions(options):
    with pytest.raises(ValueError):
        CapacityPolicy(**{"player_share":1,"players_per_instance":100,**options})


def test_strategy_warms_up_then_forecasts_only_past_counts():
    forecaster = RecordingForecaster([250,50])
    strategy = PredictiveCapacityStrategy(CapacityPolicy(1,100,0),forecaster,2)
    assert strategy.desired_instances(50) == 1
    assert strategy.status == "warmup"
    assert strategy.desired_instances(100) == 3
    assert strategy.desired_instances(250) == 1
    assert forecaster.windows == [[50,100],[100,250]]


@pytest.mark.parametrize("failure", [ValueError("invalid"),RuntimeError("model unavailable"),
                                    OSError("cache unavailable")])
def test_forecast_failure_falls_back_to_current_demand(failure):
    class Failing:
        def predict(self, counts):
            raise failure
    strategy = PredictiveCapacityStrategy(CapacityPolicy(1,100,0),Failing(),1)
    assert strategy.desired_instances(250) == 3
    assert strategy.status == "fallback"
    assert strategy.last_prediction is None


@pytest.mark.parametrize("value", [-1,float("nan"),float("inf")])
def test_invalid_prediction_falls_back_without_zeroing_load(value):
    strategy = PredictiveCapacityStrategy(CapacityPolicy(1,100,0),RecordingForecaster([value]),1)
    assert strategy.desired_instances(250) == 3
    assert strategy.status == "fallback"


def test_one_hour_startup_changes_capacity_only_at_next_boundary():
    policy=CapacityPolicy(1,100,0)
    forecast=RecordingForecaster([100,300,100])
    results,traces=simulate_scaling(observations([100,100,300,100]),{
        "reactive":ReactiveCapacityStrategy(policy),
        "predictive":PredictiveCapacityStrategy(policy,forecast,1),
    },policy,evaluation_start=1)
    reactive=[t for t in traces if t["strategy"]=="reactive"]
    predictive=[t for t in traces if t["strategy"]=="predictive"]
    assert [t["running_instances"] for t in reactive] == [1,1,3]
    assert [t["running_instances"] for t in predictive] == [1,3,1]
    assert results["reactive"]["hours_below_capacity"] == 1
    assert results["reactive"]["unserved_player_hours"] == 200
    assert results["predictive"]["hours_below_capacity"] == 0
    assert predictive[0]["starting_instances"] == 2
    assert predictive[0]["billable_instances"] == 3
    assert predictive[0]["period_cost_eur"] == pytest.approx(0.3)
    assert results["predictive"]["total_cost_eur"] == pytest.approx(0.7)
    assert predictive[-1]["forecast_status"] == "terminal_hold"
    assert forecast.windows == [[100],[100],[300]]


def test_initial_allocation_and_warmup_do_not_use_future_peak_or_charge_evaluation():
    policy=CapacityPolicy(1,100,0)
    results,traces=simulate_scaling(observations([100,100,500,500]),{
        "fixed":FixedCapacityStrategy(2),
    },policy,2,0.25)
    assert len(traces)==2
    assert results["fixed"]["total_cost_eur"] == 1.0
    assert results["fixed"]["hours_below_capacity"] == 2
    assert results["fixed"]["scale_up_events"] == 0


@pytest.mark.parametrize("timestamps", [[0,3600,10800],[0,3600,3600],[0,1800,3600]])
def test_irregular_or_nonhourly_history_is_rejected(timestamps):
    with pytest.raises(ValueError,match="hourly"):
        simulate_scaling([PlayerObservation(t,100) for t in timestamps],
            {"fixed":FixedCapacityStrategy(1)},CapacityPolicy(1,100),1)


@pytest.mark.parametrize("price", [-1,float("nan"),float("inf")])
def test_invalid_cost_cannot_produce_report(price):
    with pytest.raises(ValueError):
        simulate_scaling(observations([1,2]),{"fixed":FixedCapacityStrategy(1)},
            CapacityPolicy(1,100),1,price)


def test_cli_saves_reviewable_report_and_decisions_with_fake_model(tmp_path):
    history=tmp_path/"history.csv"
    history.write_text("timestamp,appid,player_count\n"+"".join(
        f"{i*3600},730,{n}\n" for i,n in enumerate([100,100,200,200,100])))
    output=tmp_path/"report"
    args=parse_args(["--history-path",str(history),"--appid","730","--output-dir",str(output),
                     "--window-size","2","--evaluation-hours","2"])
    report=run(args,RecordingForecaster([200,200,100]))
    assert report["mode"]=="offline_simulation"
    assert set(report["results"])=={"reactive_capacity","timesfm_capacity","fixed_capacity"}
    assert all(r["evaluated_hours"]==2 for r in report["results"].values())
    assert json.loads((output/"report.json").read_text())["assumptions"]["startup_hours"]==1
    assert len((output/"decisions.csv").read_text().splitlines())==7
    with pytest.raises(ValueError,match="already exists"):
        run(args,RecordingForecaster([]))


def test_short_history_fails_without_output(tmp_path):
    history=tmp_path/"history.csv"
    history.write_text("timestamp,appid,player_count\n0,730,100\n3600,730,200\n")
    output=tmp_path/"report"
    assert main(["--history-path",str(history),"--appid","730","--output-dir",str(output)])==1
    assert not output.exists()
