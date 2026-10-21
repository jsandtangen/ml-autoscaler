"""Generate the additive ML dashboard without changing existing dashboards."""
import json
from pathlib import Path


def build():
    selection = '{job="ruby_acorn_pegasus",appid="730"}'
    panels = [
        ("Fresh observer / forecasts available", ["ruby_acorn_ml_observer_fresh"+selection,
                                                 "ruby_acorn_ml_forecast_available"+selection]),
        ("Forecasts at issue time (global replay players)", ["ruby_acorn_ml_predicted_players"+selection]),
        ("Observed source (historical replay)", ['steam_player_count{job="games",appid="730"}']),
        ("Bounded ML and baseline VM recommendations", ["ruby_acorn_ml_recommended_instances"+selection,
                                                        "desired_instances"+selection,
                                                        "pegasus_running_instances"+selection]),
        ("Unbounded modeled requirement", ["ruby_acorn_ml_required_instances"+selection]),
        ("MAE (offline and live are separate series)", ["ruby_acorn_ml_mae_players"+selection]),
        ("RMSE (offline and live are separate series)", ["ruby_acorn_ml_rmse_players"+selection]),
        ("Scored live forecasts / offline samples", ["ruby_acorn_ml_scored_forecasts_total"+selection,
                                                    "ruby_acorn_ml_evaluation_samples"+selection]),
        ("Inference seconds / process peak MiB", ["ruby_acorn_ml_inference_seconds"+selection,
                                                 "ruby_acorn_ml_peak_rss_bytes"+selection+" / 1024 / 1024"]),
    ]
    doc = {"uid": "ruby-acorn-ml-shadow", "title": "Ruby Acorn ML Shadow — 730 replay",
           "schemaVersion": 39, "version": 1, "tags": ["ruby-acorn", "ml", "shadow"],
           "refresh": "15s", "time": {"from": "now-1h", "to": "now"},
           "templating": {"list": [{"name": "datasource", "type": "datasource", "query": "prometheus",
                                     "current": {"text": "Prometheus", "value": "Prometheus"}}]},
           "panels": [{"id": 1000, "type": "text", "title": "Shadow only; historical source",
                       "gridPos": {"x": 0, "y": 0, "w": 24, "h": 4},
                       "options": {"mode": "markdown", "content":
                           "**No VM control.** TimesFM 2.5 predicts 3/6 five-minute steps (15/30 min). "
                           "Appid 730 here is historical CS:GO replay, not current CS2 demand. "
                           "Forecast curves are plotted at issue time; errors are scored only when "
                           "the exact source target arrives. Offline replay and observed live-shadow "
                           "errors have separate evaluation labels. Zero/gap windows are rejected. "
                           "Capacity uses the live policy and max=1 per game; it is a prototype estimate."}}]}
    for i, (title, expressions) in enumerate(panels):
        doc["panels"].append({"id": i+1, "title": title, "type": "timeseries",
            "datasource": {"type": "prometheus", "uid": "$datasource"},
            "gridPos": {"x": i % 2*12, "y": 4+i//2*8, "w": 12, "h": 8},
            "targets": [{"refId": chr(65+j), "expr": expr,
                         "legendFormat": "{{model}} {{horizon_minutes}}m {{evaluation}} {{__name__}}"}
                        for j, expr in enumerate(expressions)],
            "fieldConfig": {"defaults": {"min": 0}, "overrides": []}})
    path = Path("deploy/pegasus/dashboards/ruby-acorn-ml-shadow.json")
    path.write_text(json.dumps(doc, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    build()
