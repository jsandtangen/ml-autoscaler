"""Hourly shadow proposals and outcomes; no resource controller access."""
from __future__ import annotations

import math
from statistics import fmean
from dataclasses import asdict

from scaler.forecasting import PlayerObservation
from scaler.strategies.capacity import CapacityPolicy
from scaler.engine.scaling_limits import ScalingLimits
from scaler.timesfm_forecasting import MODEL_ID
from scaler.forecasting import PersistenceForecaster, MovingAverageForecaster
from scaler.strategies.predictive import PredictiveCapacityStrategy


class ShadowObserver:
    def __init__(self, appid, forecaster, policy: CapacityPolicy, window_size=192,
                 seed=(), live=(), pending=None, max_sample_age=90, boundary_grace=300, limits=None):
        if type(window_size) is not int or window_size < 1:
            raise ValueError("Shadow window must be a positive integer")
        if not 0 < boundary_grace < 3600 or not 0 < max_sample_age <= boundary_grace:
            raise ValueError("Invalid freshness/boundary limits")
        self.appid, self.forecaster, self.policy = appid, forecaster, policy
        self.window_size = window_size
        self.limits = limits or ScalingLimits(policy.minimum_instances)
        self.max_sample_age, self.boundary_grace = max_sample_age, boundary_grace
        combined = {}
        for row in (*seed, *live):
            if (not math.isfinite(row.timestamp) or row.timestamp < 0 or row.timestamp % 3600
                    or not math.isfinite(row.player_count) or row.player_count < 0):
                raise ValueError("Seed/live history must contain nonnegative counts at exact UTC hours")
            combined[row.timestamp] = row
        self.history = [combined[t] for t in sorted(combined)]
        self.last_live_slot = max((row.timestamp for row in live), default=-1)
        self.pending = pending

    def observe(self, sample: PlayerObservation, now: float, live_decision=None) -> list[dict]:
        if not math.isfinite(now) or not math.isfinite(sample.timestamp) or sample.timestamp < 0:
            raise ValueError("Invalid observation clock")
        self.policy.customer_players(sample.player_count)
        slot = math.floor(now/3600)*3600
        status = {"event": "status", "appid": self.appid, "slot": slot, "checked_at": now}
        if sample.timestamp > now+5 or now-sample.timestamp > self.max_sample_age:
            return [{**status, "status": "stale_source", "source_timestamp": sample.timestamp}]
        if sample.timestamp < slot or now-slot > self.boundary_grace:
            return [{**status, "status": "waiting_for_boundary", "next_boundary": slot+3600}]
        if slot <= self.last_live_slot:
            return [{**status, "status": "already_sampled"}]
        past = [row for row in self.history if row.timestamp < slot]
        # Only a consecutive suffix ending exactly one hour ago may be used.
        tail = []
        expected = slot-3600
        for row in reversed(past):
            if row.timestamp != expected or len(tail) >= self.window_size-1:
                break
            tail.append(row)
            expected -= 3600
        self.history = list(reversed(tail))+[PlayerObservation(slot, sample.player_count)]
        self.last_live_slot = slot
        events = []
        if self.pending is not None:
            pending = self.pending
            if pending["target_timestamp"] == slot:
                error = pending["predicted_global_players"]-sample.player_count
                events.append({"event": "outcome", "appid": self.appid,
                    "target_timestamp": slot, "measured_at": sample.timestamp,
                    "actual_global_players": sample.player_count,
                    "predicted_global_players": pending["predicted_global_players"],
                    "absolute_error_players": abs(error), "squared_error_players": error*error,
                    "proposed_instances": pending["shadow_desired_instances"],
                    "modeled_shortfall_players": max(0.0, self.policy.customer_players(sample.player_count)
                        - pending["shadow_desired_instances"]*self.policy.players_per_instance)})
            elif pending["target_timestamp"] < slot:
                events.append({"event": "outcome_unavailable", "appid": self.appid,
                    "target_timestamp": pending["target_timestamp"], "reason": "missed_hour"})
            self.pending = None
        reactive_required = self.policy.required_instances(sample.player_count)
        reactive = self.limits.plan(reactive_required, 0).desired_instances
        prediction, desired, error_message = None, reactive, None
        state = "warmup"
        required = reactive_required
        mismatch = live_decision and live_decision.get("fresh") and (
            ("capacity_policy" in live_decision and live_decision["capacity_policy"] != asdict(self.policy))
            or ("scaling_limits" in live_decision and live_decision["scaling_limits"] != asdict(self.limits)))
        if mismatch:
            state, error_message = "configuration_mismatch", "Shadow policy/limits differ from the active autoscaler"
        elif len(self.history) == self.window_size:
            try:
                prediction = float(self.forecaster.predict([row.player_count for row in self.history]))
                required = self.policy.required_instances(prediction)
                desired = self.limits.plan(required, 0).desired_instances
                state = "forecast"
            except (ValueError, TypeError, RuntimeError, OSError) as error:
                prediction, state, error_message = None, "fallback", str(error)
        decision = {"event": "decision", "mode": "shadow_only", "appid": self.appid,
            "sample_timestamp": slot, "source_timestamp": sample.timestamp,
            "issued_at": now, "target_timestamp": slot+3600,
            "status": state, "model_id": MODEL_ID, "policy": asdict(self.policy),
            "limits": asdict(self.limits),
            "context_observations": len(self.history), "required_observations": self.window_size,
            "global_players": sample.player_count,
            "customer_players": self.policy.customer_players(sample.player_count),
            "reactive_capacity_desired_instances": reactive,
            "reactive_capacity_required_instances": reactive_required,
            "shadow_required_instances": required,
            "predicted_global_players": prediction, "shadow_desired_instances": desired,
            "live_decision": live_decision, "error": error_message}
        current = live_decision.get("running_instances") if live_decision else None
        verified = live_decision and live_decision.get("fresh") and live_decision.get("appid_verified")
        decision["shadow_cycle_target_instances"] = self.limits.plan(required, current).cycle_target_instances if verified else None
        if mismatch:
            decision.update(shadow_required_instances=None, shadow_desired_instances=None,
                            shadow_cycle_target_instances=None)
        if state == "forecast":
            self.pending = decision
        events.append(decision)
        return events


SHORT_HORIZONS = (3, 6)  # Five-minute observations: 15 and 30 minutes.


def validate_five_minute_history(rows):
    """Audit without filling holes or silently treating source zeros as demand."""
    if not rows:
        raise ValueError("Empty five-minute history")
    if any(not math.isfinite(r.timestamp) or r.timestamp < 0 or r.timestamp % 300
           or type(r.player_count) is not int or r.player_count < 0 for r in rows):
        raise ValueError("Expected aligned five-minute timestamps and nonnegative integer counts")
    differences = [b.timestamp-a.timestamp for a, b in zip(rows, rows[1:])]
    if any(d <= 0 for d in differences):
        raise ValueError("History must be ordered with no duplicate timestamps")
    longest = run = 0
    for row in rows:
        run = run+1 if row.player_count == 0 else 0
        longest = max(longest, run)
    return {"rows": len(rows), "interval_seconds": 300,
            "missing_intervals": int(sum(d/300-1 for d in differences)),
            "invalid_zero_rows": sum(r.player_count == 0 for r in rows),
            "longest_zero_run": longest, "first_timestamp": rows[0].timestamp,
            "last_timestamp": rows[-1].timestamp}


def valid_short_window(rows):
    return bool(rows) and all(r.player_count > 0 for r in rows) and all(
        b.timestamp-a.timestamp == 300 for a, b in zip(rows, rows[1:]))


def short_predictions(counts, forecaster):
    steps = forecaster.predict_steps(counts)
    if len(steps) != 6 or any(not math.isfinite(x) or x < 0 for x in steps):
        raise ValueError("Expected six finite, nonnegative TimesFM forecast steps")
    baselines = {"persistence": PersistenceForecaster().predict(counts),
                 "moving_average": MovingAverageForecaster().predict(counts)}
    return {(name, h): (steps[h-1] if name == "timesfm" else baselines[name])
            for name in ("timesfm", *baselines) for h in SHORT_HORIZONS}


def recommended_capacity(prediction, policy, limits, current=0):
    # Use the existing predictive strategy's validation and capacity translation.
    class FixedForecast:
        def predict(self, counts):
            return prediction
    strategy = PredictiveCapacityStrategy(policy, FixedForecast(), 1)
    desired = strategy.desired_instances(1)
    if strategy.status != "forecast":
        raise ValueError("Invalid forecast for capacity conversion")
    plan = limits.plan(desired, current)
    return {"required_instances": policy.required_instances(prediction),
            "desired_instances": plan.desired_instances,
            "cycle_target_instances": plan.cycle_target_instances}


def evaluate_short_horizons(rows, forecaster, policy, limits, window_size=128, origins=144):
    """Chronological rolling holdout; reject windows crossing zeros or gaps.

    The last `origins` potential origins are held out. No fitting or selection on
    their scores occurs; every method/horizon uses the identical accepted origins.
    """
    audit = validate_five_minute_history(rows)
    if window_size < 1 or origins < 1 or len(rows) < window_size+6:
        raise ValueError("Insufficient history or invalid evaluation dimensions")
    traces, errors, rejected = [], {}, 0
    start = max(window_size-1, len(rows)-6-origins)
    for end in range(start, len(rows)-6):
        block = rows[end-window_size+1:end+7]
        if not valid_short_window(block):
            rejected += 1
            continue
        counts = [r.player_count for r in rows[end-window_size+1:end+1]]
        predictions = short_predictions(counts, forecaster)
        for (model, horizon), prediction in predictions.items():
            actual = rows[end+horizon].player_count
            error = prediction-actual
            errors.setdefault((model, horizon), []).append(error)
            traces.append({"model": model, "horizon_minutes": horizon*5,
                           "origin_timestamp": rows[end].timestamp,
                           "target_timestamp": rows[end+horizon].timestamp,
                           "prediction": prediction, "actual": actual,
                           **recommended_capacity(prediction, policy, limits)})
    if not errors:
        raise ValueError("No valid evaluation windows after zero/gap rejection")
    scores = {str(h*5): {name: {"sample_count": len(values),
              "mae": fmean(abs(x) for x in values),
              "rmse": math.sqrt(fmean(x*x for x in values))}
              for (name, horizon), values in errors.items() if horizon == h}
              for h in SHORT_HORIZONS}
    return {"mode": "offline_replay", "model_id": MODEL_ID, "audit": audit,
            "window_size": window_size, "requested_origins": origins,
            "rejected_origins": rejected, "policy": asdict(policy), "limits": asdict(limits),
            "scores": scores, "predictions": traces,
            "limitations": ["Historical CS:GO replay for appid 730, not current CS2 demand.",
                            "Exploratory rolling holdout; model pretraining overlap unknown.",
                            "VM capacity is modeled, not measured game-server readiness."]}


class FiveMinuteShadowObserver:
    """Multi-horizon observer with persisted pending forecasts, no controller.

    Timestamps here are original source times. Wall-clock issue times are stored
    separately by the runner. Future source records must never enter `observe`.
    """
    def __init__(self, policy, limits, window_size=128, state=None):
        self.policy, self.limits, self.window_size = policy, limits, window_size
        self.state = state or {"last_timestamp": -1, "pending": [], "scores": {}, "latest": []}

    def observe(self, rows, forecaster, reference, issued_at, wall_origin_timestamp=None):
        validate_five_minute_history(rows)
        timestamp = rows[-1].timestamp
        if timestamp <= self.state["last_timestamp"]:
            return []
        events, remaining = [], []
        by_time = {row.timestamp: row.player_count for row in rows}
        for pending in self.state["pending"]:
            target = pending["target_timestamp"]
            if target > timestamp:
                remaining.append(pending)
                continue
            actual = by_time.get(target)
            # No scoring against a later sample, or across a missing/zero period.
            block = [r for r in rows if pending["origin_timestamp"] <= r.timestamp <= target]
            if (actual is None or not valid_short_window(block)
                    or len(block) != pending["horizon_minutes"]//5+1):
                events.append({**pending, "event": "outcome_unavailable"})
                continue
            error = pending["prediction"]-actual
            key = pending["model"]+":"+str(pending["horizon_minutes"])
            score = self.state["scores"].setdefault(key, {"count": 0, "absolute_sum": 0., "squared_sum": 0.})
            score["count"] += 1
            score["absolute_sum"] += abs(error)
            score["squared_sum"] += error*error
            score["mae"] = score["absolute_sum"]/score["count"]
            score["rmse"] = math.sqrt(score["squared_sum"]/score["count"])
            events.append({**pending, "event": "outcome", "actual": actual,
                           "absolute_error": abs(error), "squared_error": error*error})
        self.state.update(last_timestamp=timestamp, pending=remaining, latest=[])
        context = rows[-self.window_size:]
        if len(context) != self.window_size or not valid_short_window(context):
            self.state["status"] = "invalid_context"
            return events
        if (not reference.get("fresh") or not reference.get("appid_verified")
                or reference.get("capacity_policy") != asdict(self.policy)
                or reference.get("scaling_limits") != asdict(self.limits)):
            self.state["status"] = "configuration_unavailable_or_mismatch"
            return events
        predictions = short_predictions([r.player_count for r in context], forecaster)
        for (model, h), prediction in predictions.items():
            proposal = {"event": "forecast", "model": model, "horizon_minutes": h*5,
                        "issued_at": issued_at, "origin_timestamp": timestamp,
                        "target_timestamp": timestamp+h*300, "prediction": prediction,
                        "target_wall_timestamp": (issued_at if wall_origin_timestamp is None
                                                   else wall_origin_timestamp)+h*300,
                        "live_desired_instances": reference["desired_instances"],
                        "live_running_instances": reference["running_instances"],
                        **recommended_capacity(prediction, self.policy, self.limits,
                                               reference["running_instances"])}
            remaining.append(proposal)
            self.state["latest"].append(proposal)
            events.append(proposal)
        self.state["status"] = "forecast"
        return events
