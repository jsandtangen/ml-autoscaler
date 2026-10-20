"""Offline hourly capacity replay. This module never controls infrastructure."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scaler.forecasting import PlayerObservation
from scaler.strategies.base import ScalingStrategy
from scaler.strategies.capacity import CapacityPolicy
from scaler.engine.scaling_limits import ScalingLimits


@dataclass(frozen=True)
class FixedCapacityStrategy(ScalingStrategy):
    instances: int

    def __post_init__(self):
        if type(self.instances) is not int or self.instances < 0:
            raise ValueError("Fixed instances must be a nonnegative integer")

    def desired_instances(self, player_count: int) -> int:
        return self.instances


def validate_replay(observations: Sequence[PlayerObservation], evaluation_start: int) -> float:
    if len(observations) < 2 or not 1 <= evaluation_start < len(observations):
        raise ValueError("Replay needs warmup and a nonempty evaluation period")
    gaps = [b.timestamp-a.timestamp for a, b in zip(observations, observations[1:])]
    if any(not math.isfinite(gap) or not math.isclose(gap, 3600, abs_tol=0.001, rel_tol=0)
           for gap in gaps):
        raise ValueError("This replay requires consecutive hourly observations")
    if any(not math.isfinite(row.timestamp) or not math.isfinite(row.player_count)
           or row.player_count < 0 for row in observations):
        raise ValueError("Observations must have finite timestamps and nonnegative counts")
    return 1.0


def simulate_scaling(
    observations: Sequence[PlayerObservation],
    strategies: Mapping[str, ScalingStrategy],
    policy: CapacityPolicy,
    evaluation_start: int,
    vm_cost_per_hour: float = 0.10,
    limits: ScalingLimits | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """At hour t, decide the ready fleet at t+1 using only counts through t.

    Both starts and removals reconcile at the next hourly boundary. New starts
    are billed during their one-hour startup; removals are billed until that
    boundary. Actual sampled demand is held constant for its one-hour period.
    Warmup is replayed but excluded from evaluation totals. The final period
    holds its existing fleet, since no subsequent period is evaluated.
    """
    hours = validate_replay(observations, evaluation_start)
    limits = limits or ScalingLimits(policy.minimum_instances)
    if not math.isfinite(vm_cost_per_hour) or vm_cost_per_hour < 0:
        raise ValueError("VM price must be finite and nonnegative")
    if not strategies:
        raise ValueError("At least one strategy is required")
    initial = limits.plan(policy.required_instances(observations[0].player_count), 0).cycle_target_instances
    ready = {name: initial for name in strategies}
    results = {name: {
        "evaluated_hours": 0, "total_cost_eur": 0.0,
        "billable_instance_hours": 0.0, "ready_instance_hours": 0.0,
        "startup_instance_hours": 0.0, "hours_below_capacity": 0.0,
        "unserved_player_hours": 0.0, "excess_capacity_player_hours": 0.0,
        "scale_up_events": 0, "scale_down_events": 0,
        "instances_started": 0, "instances_stopped": 0,
        "forecast_fallbacks": 0, "successful_forecasts": 0,
    } for name in strategies}
    traces = []
    for index, row in enumerate(observations):
        demand = policy.customer_players(row.player_count)
        for name, strategy in strategies.items():
            running = ready[name]
            terminal = index == len(observations)-1
            desired = running if terminal else strategy.desired_instances(row.player_count)
            if type(desired) is not int or desired < 0:
                raise ValueError("Strategies must request a nonnegative integer fleet")
            forecast = getattr(strategy, "last_prediction", None)
            required = (policy.required_instances(forecast if forecast is not None else row.player_count)
                        if getattr(strategy, "policy", None) is not None else desired)
            plan = limits.plan(required, running)
            desired = running if terminal else plan.cycle_target_instances
            starting = max(0, desired-running)
            stopping = max(0, running-desired)
            capacity = running * policy.players_per_instance
            shortfall = max(0.0, demand-capacity)
            billable = running+starting
            cost = billable*hours*vm_cost_per_hour
            if index >= evaluation_start:
                state = results[name]
                state["evaluated_hours"] += hours
                state["total_cost_eur"] += cost
                state["billable_instance_hours"] += billable*hours
                state["ready_instance_hours"] += running*hours
                state["startup_instance_hours"] += starting*hours
                state["hours_below_capacity"] += hours if shortfall > 0 else 0
                state["unserved_player_hours"] += shortfall*hours
                state["excess_capacity_player_hours"] += max(0.0, capacity-demand)*hours
                state["scale_up_events"] += starting > 0
                state["scale_down_events"] += stopping > 0
                state["instances_started"] += starting
                state["instances_stopped"] += stopping
                status = "terminal_hold" if terminal else getattr(strategy, "status", "rule")
                state["forecast_fallbacks"] += status == "fallback"
                state["successful_forecasts"] += status == "forecast"
                traces.append({
                    "timestamp": row.timestamp, "strategy": name,
                    "global_players": row.player_count, "customer_players": demand,
                    "running_instances": running, "capacity_players": capacity,
                    "requested_next_instances": desired, "starting_instances": starting,
                    "required_instances": required, "desired_instances": plan.desired_instances,
                    "unmet_instances": max(0, policy.required_instances(row.player_count)-running),
                    "billable_instances": billable, "shortfall_players": shortfall,
                    "period_cost_eur": cost, "forecast_status": status,
                    "predicted_next_global_players": None if terminal else
                        getattr(strategy, "last_prediction", None),
                })
            ready[name] = desired
    return results, traces
