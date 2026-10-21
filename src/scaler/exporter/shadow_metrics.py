"""Metrics owned solely by the separate ML process; no live scaling metrics."""
import math


def render_shadow_metrics(snapshot, now):
    lines = []
    def emit(name, value, labels=""):
        if math.isfinite(value):
            lines.append(f"ruby_acorn_ml_{name}{{appid=\"730\"{labels}}} {value:.12g}\n")
    checked = snapshot.get("checked_at", 0)
    fresh = 0 <= now-checked <= 120
    emit("observer_fresh", int(fresh))
    emit("last_poll_timestamp_seconds", checked)
    emit("source_timestamp_seconds", snapshot.get("last_timestamp", 0))
    emit("inference_seconds", snapshot.get("inference_seconds", 0))
    emit("peak_rss_bytes", snapshot.get("peak_rss_bytes", 0))
    active = [row for row in snapshot.get("latest", []) if now < row["target_wall_timestamp"]]
    emit("forecast_available", int(fresh and snapshot.get("status") == "forecast" and bool(active)))
    if fresh and snapshot.get("status") == "forecast":
        for row in active:
            # Source time is historical. Expiry uses wall-clock issue time.
            labels = f',model="{row["model"]}",horizon_minutes="{row["horizon_minutes"]}"'
            for name, key in [("predicted_players", "prediction"),
                              ("required_instances", "required_instances"),
                              ("recommended_instances", "desired_instances"),
                              ("cycle_target_instances", "cycle_target_instances"),
                              ("target_source_timestamp_seconds", "target_timestamp"),
                              ("target_wall_timestamp_seconds", "target_wall_timestamp"),
                              ("issued_timestamp_seconds", "issued_at"),
                              ("live_desired_instances", "live_desired_instances"),
                              ("live_running_instances", "live_running_instances")]:
                emit(name, row[key], labels)
    for key, score in snapshot.get("scores", {}).items():
        model, horizon = key.split(":")
        labels = f',model="{model}",horizon_minutes="{horizon}"'
        for name, field in [("scored_forecasts_total", "count"), ("mae_players", "mae"), ("rmse_players", "rmse")]:
            emit(name, score[field], labels+',evaluation="live_shadow"')
    for horizon, models in snapshot.get("offline_scores", {}).items():
        for model, score in models.items():
            labels = f',model="{model}",horizon_minutes="{horizon}",evaluation="offline_replay"'
            for name, field in [("evaluation_samples", "sample_count"), ("mae_players", "mae"), ("rmse_players", "rmse")]:
                emit(name, score[field], labels)
    return ''.join(lines).encode()
