"""Read-only Prometheus inputs for a separate shadow observer."""
import math
from dataclasses import asdict

from scaler.data.client import GameDataClient, PlayerCountNotFoundError, PrometheusResponseError
from scaler.forecasting import PlayerObservation
from scaler.strategies.capacity import CapacityPolicy
from scaler.engine.scaling_limits import ScalingLimits


class ShadowPrometheusClient(GameDataClient):
    def latest_observation(self, appid: str) -> PlayerObservation:
        # A range vector preserves actual scrape timestamps, unlike an instant
        # evaluation timestamp. Never label an old source sample as fresh now.
        selector = self._build_query({"appid": appid})
        results = self._extract_results(self._query_prometheus(selector+"[5m]"))
        if not results:
            raise PlayerCountNotFoundError("No recent player source samples")
        if len(results) != 1:
            raise PrometheusResponseError("Shadow source requires exactly one player series")
        try:
            values = results[0]["values"]
            timestamp, count = values[-1]
            timestamp, count = float(timestamp), float(count)
            if not math.isfinite(timestamp) or timestamp < 0 or not math.isfinite(count) or count < 0 or not count.is_integer():
                raise ValueError("Invalid source observation")
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise PrometheusResponseError("Malformed timestamped player observation") from error
        return PlayerObservation(timestamp, int(count))

    def live_decision(self, appid: str, now: float) -> dict:
        query = '{__name__=~"running_instances|desired_instances|strategy|last_decision_timestamp_seconds|capacity_source_info|capacity_player_share|capacity_players_per_instance|capacity_headroom_fraction|capacity_minimum_instances|maximum_instances|max_scaling_change",job="autoscaler"}'
        results = self._extract_results(self._query_prometheus(query))
        try:
            selected = [row for row in results if row["metric"].get("appid") == appid]
            if not selected:
                selected = [row for row in results if "appid" not in row["metric"]]
            groups = {}
            for row in selected:
                name = row["metric"]["__name__"]
                if name in groups:
                    raise ValueError("Ambiguous autoscaler metrics")
                groups[name] = row
            running = float(groups["running_instances"]["value"][1])
            desired = float(groups["desired_instances"]["value"][1])
            timestamp = float(groups["last_decision_timestamp_seconds"]["value"][1])
            strategy = groups["strategy"]["metric"]["name"]
            if any(not math.isfinite(v) or v < 0 for v in (running, desired, timestamp)):
                raise ValueError("Invalid autoscaler values")
            if not running.is_integer() or not desired.is_integer() or timestamp > now+5:
                raise ValueError("Invalid autoscaler counts or timestamp")
            source_appid = groups.get("capacity_source_info", {}).get("metric", {}).get("steam_appid")
            if source_appid is not None and source_appid != appid:
                raise ValueError("Live source is configured for another game")
        except (KeyError, TypeError, ValueError, IndexError) as error:
            raise PrometheusResponseError("Live autoscaler decision unavailable or ambiguous") from error
        snapshot = {"strategy": strategy, "running_instances": int(running),
                "desired_instances": int(desired), "decision_timestamp": timestamp,
                "age_seconds": now-timestamp, "fresh": now-timestamp <= 120,
                "appid_verified": selected[0]["metric"].get("appid") == appid or source_appid == appid,
                "label_mode": "per_game" if selected[0]["metric"].get("appid") else "legacy"}
        config_names = ("capacity_player_share", "capacity_players_per_instance", "capacity_headroom_fraction",
                        "capacity_minimum_instances", "maximum_instances", "max_scaling_change")
        if all(name in groups for name in config_names):
            try:
                share, per_vm, buffer, minimum, maximum, change = [float(groups[name]["value"][1]) for name in config_names]
                if not all(v.is_integer() for v in (per_vm, minimum, maximum, change)):
                    raise ValueError("Expected integer capacity limits")
                snapshot["capacity_policy"] = asdict(CapacityPolicy(share,int(per_vm),buffer,int(minimum)))
                snapshot["scaling_limits"] = asdict(ScalingLimits(int(minimum),int(maximum),int(change)))
            except (TypeError, ValueError, OverflowError) as error:
                raise PrometheusResponseError("Malformed live capacity configuration") from error
        return snapshot
