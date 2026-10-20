"""Hourly shadow proposals and outcomes; no resource controller access."""
from __future__ import annotations

import math
from dataclasses import asdict

from scaler.forecasting import PlayerObservation
from scaler.strategies.capacity import CapacityPolicy
from scaler.engine.scaling_limits import ScalingLimits
from scaler.timesfm_forecasting import MODEL_ID


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
