"""Read-only export of a completed offline capacity-limit comparison."""
from datetime import datetime
import json
import math
from pathlib import Path


FIELDS = {
    "total_cost_eur": ("simulation_cost_eur", "Modeled cost over the offline evaluation period in EUR."),
    "hours_below_capacity": ("simulation_hours_below_capacity", "Offline hours below modeled nominal player capacity."),
    "unserved_player_hours": ("simulation_unserved_player_hours", "Offline modeled customer player-hours beyond ready capacity."),
    "evaluated_hours": ("simulation_evaluated_hours", "Duration of the offline evaluation in hours."),
}


def render_simulation_metrics(path, labels):
    """Reject incomplete/invalid reports as a whole; never substitute zero results."""
    lines = []
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
        if report["mode"] != "offline_limit_comparison" or not report["scenarios"]:
            raise ValueError("Not a completed comparison")
        timestamp = datetime.fromisoformat(report["created_at_utc"]).timestamp()
        if not math.isfinite(timestamp):
            raise ValueError("Invalid report timestamp")
        identity = None
        for cap, scenario in report["scenarios"].items():
            assumptions = scenario["assumptions"]
            if (scenario["mode"] != "offline_simulation" or scenario["appid"] != report["appid"]
                    or str(assumptions["scaling_limits"]["maximum_instances"]) != cap
                    or assumptions["fixed_instances"] != int(cap)):
                raise ValueError("Inconsistent comparison scenario")
            current_identity = (scenario["history_sha256"], scenario["evaluation"],
                                scenario["model_id"], scenario.get("checkpoint_revision"),
                                {k: v for k, v in assumptions.items()
                                 if k not in {"fixed_instances", "scaling_limits"}},
                                assumptions["scaling_limits"]["minimum_instances"],
                                assumptions["scaling_limits"]["max_change"])
            if identity is not None and identity != current_identity:
                raise ValueError("Comparison scenarios must share data, period, policy and model")
            identity = current_identity
            if set(scenario["results"]) != {"reactive_capacity", "timesfm_capacity", "fixed_capacity"}:
                raise ValueError("Missing comparison strategy")
            for strategy, result in scenario["results"].items():
                for field, (name, _) in FIELDS.items():
                    value = result[field]
                    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                        raise ValueError("Invalid simulation result")
                    if (result["evaluated_hours"] != scenario["evaluation"]["hours"]
                            or result["hours_below_capacity"] > result["evaluated_hours"]):
                        raise ValueError("Inconsistent simulation duration")
                    tag = labels(report["appid"], strategy=strategy, max_instances=cap)
                    lines.append(f"{name}{tag} {value:.12g}\n")
        lines.append(f'simulation_report_timestamp_seconds{labels(report["appid"])} {timestamp:.12g}\n')
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return b"# HELP simulation_report_available One if a valid offline comparison report is readable.\n# TYPE simulation_report_available gauge\nsimulation_report_available 0\n"
    descriptions = {name: description for name, description in FIELDS.values()}
    descriptions.update(simulation_report_available="One if a valid offline comparison report is readable.",
                        simulation_report_timestamp_seconds="Creation time of the offline comparison, not a live decision.")
    return ("".join(f"# HELP {name} {description}\n# TYPE {name} gauge\n"
                    for name, description in descriptions.items())
            + "simulation_report_available 1\n" + "".join(lines)).encode("utf-8")
