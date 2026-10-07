import pytest

from scaler.cost import CostModel


@pytest.mark.parametrize("instances, dynamic, savings", [
    (0, 0.0, 0.4), (1, 0.1, 0.3), (2, 0.2, 0.2), (4, 0.4, 0.0), (5, 0.5, -0.1),
])
def test_default_hourly_costs(instances, dynamic, savings):
    estimate = CostModel().estimate_hourly(instances)
    assert estimate.dynamic_cost == pytest.approx(dynamic)
    assert estimate.fixed_baseline_cost == pytest.approx(0.4)
    assert estimate.savings == pytest.approx(savings)


def test_custom_price_and_baseline():
    estimate = CostModel(0.25, 6).estimate_hourly(2)
    assert estimate.dynamic_cost == 0.5
    assert estimate.fixed_baseline_cost == 1.5
    assert estimate.savings == 1.0


def test_zero_price_and_baseline_are_allowed():
    estimate = CostModel(0, 0).estimate_hourly(5)
    assert estimate.dynamic_cost == estimate.fixed_baseline_cost == estimate.savings == 0


@pytest.mark.parametrize("price, baseline", [
    (-0.1, 4), (float("nan"), 4), (float("inf"), 4), (0.1, -1), (0.1, 1.5),
])
def test_invalid_model_configuration(price, baseline):
    with pytest.raises(ValueError):
        CostModel(price, baseline)


def test_negative_running_count_is_rejected():
    with pytest.raises(ValueError):
        CostModel().estimate_hourly(-1)
