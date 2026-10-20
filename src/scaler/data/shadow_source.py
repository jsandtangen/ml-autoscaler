"""Read-only Prometheus inputs for a separate shadow observer."""
import math

from scaler.data.client import GameDataClient, PlayerCountNotFoundError, PrometheusResponseError
from scaler.forecasting import PlayerObservation


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
        query = '{__name__=~"running_instances|desired_instances|strategy|last_decision_timestamp_seconds",job="autoscaler"}'
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
        except (KeyError, TypeError, ValueError, IndexError) as error:
            raise PrometheusResponseError("Live autoscaler decision unavailable or ambiguous") from error
        return {"strategy": strategy, "running_instances": int(running),
                "desired_instances": int(desired), "decision_timestamp": timestamp,
                "age_seconds": now-timestamp, "fresh": now-timestamp <= 120,
                "appid_verified": selected[0]["metric"].get("appid") == appid,
                "label_mode": "per_game" if selected[0]["metric"].get("appid") else "legacy"}
