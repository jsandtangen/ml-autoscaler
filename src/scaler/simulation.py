"""Offline hourly capacity replay. This module never controls infrastructure."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from scaler.forecasting import PlayerObservation
from scaler.strategies.base import ScalingStrategy
from scaler.strategies.capacity import CapacityPolicy
from scaler.strategies.predictive import PredictiveCapacityStrategy
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
            contextual = getattr(strategy, "desired_for_current", None)
            desired = running if terminal and isinstance(strategy, PredictiveCapacityStrategy) else (contextual(row.player_count, running) if contextual
                                                else strategy.desired_instances(row.player_count))
            if type(desired) is not int or desired < 0:
                raise ValueError("Strategies must request a nonnegative integer fleet")
            forecast = getattr(strategy, "last_prediction", None)
            # A strategy owns its request, including its buffer/reserve/timers.
            required = desired
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


@dataclass
class SimulationClock:
    now: float = 0

    def __call__(self):
        return self.now


def longest_clean_segment(observations, minimum_points):
    """Earliest longest positive contiguous five-minute segment; no score selection."""
    from scaler.shadow import validate_five_minute_history
    validate_five_minute_history(observations)
    best, run = [], []
    for row in observations:
        if row.player_count <= 0 or (run and row.timestamp-run[-1].timestamp != 300):
            if len(run) > len(best):
                best = run
            run = []
        if row.player_count > 0:
            run.append(row)
    if len(run) > len(best):
        best = run
    if len(best) < minimum_points:
        raise ValueError("No clean contiguous segment long enough for warmup and evaluation")
    return best


def simulate_five_minute_scaling(observations, strategies, policy, limits, clock,
                                 evaluation_start, evaluation_stop, *, startup_seconds=900,
                                 decision_seconds=30, cooldown_seconds=120,
                                 initial_instances=1, vm_cost_per_hour=0.10):
    """Replay actual strategy requests, with allocated vs ready capacity separated.

    Source values are held for 300s; the control clock advances by 30s. Starts
    count toward limits/cost immediately, but provide capacity only when ready.
    Stops are immediate and cancel newest starting VMs before ready VMs. All
    strategies use the same cooldown, pending-capacity model and initial fleet.
    No controller or infrastructure client is imported or called.
    """
    from scaler.shadow import validate_five_minute_history, valid_short_window
    from scaler.strategies.predictive import PredictiveCapacityStrategy
    validate_five_minute_history(observations)
    if not valid_short_window(observations):
        raise ValueError("Replay must not cross missing or invalid zero observations")
    if not 0 < evaluation_start < evaluation_stop <= len(observations):
        raise ValueError("Invalid common evaluation interval")
    if (type(decision_seconds) is not int or decision_seconds <= 0 or 300 % decision_seconds
            or not math.isfinite(startup_seconds) or startup_seconds < 0
            or startup_seconds % decision_seconds or not math.isfinite(cooldown_seconds)
            or cooldown_seconds < 0 or cooldown_seconds % decision_seconds
            or type(initial_instances) is not int or not limits.minimum_instances <= initial_instances <= limits.maximum_instances
            or not math.isfinite(vm_cost_per_hour) or vm_cost_per_hour < 0 or not strategies):
        raise ValueError("Invalid replay timing, initial fleet or cost")
    start = observations[evaluation_start].timestamp
    stop = observations[evaluation_stop-1].timestamp+300
    fleets = {name: {"ready": initial_instances, "pending": [], "last_action": -math.inf,
                      "last_sample": None, "request": None} for name in strategies}
    results = {name: {"evaluated_hours": 0., "total_cost_eur": 0., "billable_instance_hours": 0.,
        "ready_instance_hours": 0., "startup_instance_hours": 0., "hours_below_capacity": 0.,
        "unserved_player_hours": 0., "excess_capacity_player_hours": 0.,
        "scale_up_events": 0, "scale_down_events": 0, "instances_started": 0,
        "instances_stopped": 0, "successful_forecasts": 0, "forecast_fallbacks": 0} for name in strategies}
    traces = []
    for elapsed in range(0, int(stop-start), decision_seconds):
        now = start+elapsed
        clock.now = now
        index = evaluation_start+elapsed//300
        players = observations[index].player_count
        demand = policy.customer_players(players)
        hours = decision_seconds/3600
        for name, strategy in strategies.items():
            fleet, result = fleets[name], results[name]
            completed = [due for due in fleet["pending"] if due <= now]
            fleet["ready"] += len(completed)
            fleet["pending"] = [due for due in fleet["pending"] if due > now]
            allocated = fleet["ready"]+len(fleet["pending"])
            # Prediction windows contain observations, never duplicate 30s polls.
            if not isinstance(strategy, PredictiveCapacityStrategy) or fleet["last_sample"] != index:
                contextual = getattr(strategy, "desired_for_current", None)
                fleet["request"] = (contextual(players, allocated) if contextual
                                     else strategy.desired_instances(players))
                fleet["last_sample"] = index
                if isinstance(strategy, PredictiveCapacityStrategy):
                    if strategy.status != "forecast":
                        raise ValueError("Fair replay requires a cached forecast at every evaluation origin")
                    result["successful_forecasts"] += 1
            request = fleet["request"]
            plan = limits.plan(request, allocated)
            delta = plan.cycle_target_instances-allocated
            change = delta if now-fleet["last_action"] >= cooldown_seconds else 0
            if change > 0:
                fleet["pending"].extend([now+startup_seconds]*change)
                result["scale_up_events"] += 1
                result["instances_started"] += change
            elif change < 0:
                count = -change
                fleet["pending"].sort()
                cancel = min(count, len(fleet["pending"]))
                if cancel:
                    del fleet["pending"][-cancel:]
                fleet["ready"] -= count-cancel
                result["scale_down_events"] += 1
                result["instances_stopped"] += count
            if change:
                fleet["last_action"] = now
            # Zero startup is a useful timing control; it is not assumed in production.
            fleet["ready"] += sum(due <= now for due in fleet["pending"])
            fleet["pending"] = [due for due in fleet["pending"] if due > now]
            ready, pending = fleet["ready"], len(fleet["pending"])
            active = ready+pending
            capacity = ready*policy.players_per_instance
            shortfall = max(0., demand-capacity)
            result["evaluated_hours"] += hours
            result["billable_instance_hours"] += active*hours
            result["ready_instance_hours"] += ready*hours
            result["startup_instance_hours"] += pending*hours
            result["total_cost_eur"] += active*hours*vm_cost_per_hour
            result["hours_below_capacity"] += hours if shortfall > 0 else 0
            result["unserved_player_hours"] += shortfall*hours
            result["excess_capacity_player_hours"] += max(0., capacity-demand)*hours
            traces.append({"timestamp": now, "source_timestamp": observations[index].timestamp,
                "strategy": name, "global_players": players, "customer_players": demand,
                "strategy_request": request, "bounded_desired": plan.desired_instances,
                "allocated_before": allocated, "ready_instances": ready, "starting_instances": pending,
                "billable_instances": active, "change": change, "shortfall_players": shortfall,
                "period_cost_eur": active*hours*vm_cost_per_hour})
    for name, fleet in fleets.items():
        results[name].update(final_ready_instances=fleet["ready"], final_starting_instances=len(fleet["pending"]))
    return results, traces
