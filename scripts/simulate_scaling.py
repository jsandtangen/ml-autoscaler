"""Compare reactive capacity, original TimesFM capacity and fixed allocation."""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path

from scaler.forecasting import load_player_history
from scaler.simulation import FixedCapacityStrategy, simulate_scaling, validate_replay
from scaler.strategies.capacity import CapacityPolicy, ReactiveCapacityStrategy
from scaler.strategies.predictive import PredictiveCapacityStrategy
from scaler.timesfm_forecasting import MODEL_ID, TimesFMForecaster, TimesFMError
from scaler.engine.scaling_limits import ScalingLimits


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history-path", type=Path, required=True)
    parser.add_argument("--appid", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--player-share", type=float, default=0.001)
    parser.add_argument("--players-per-instance", type=int, default=100)
    parser.add_argument("--headroom-fraction", type=float, default=0.10)
    parser.add_argument("--minimum-instances", type=int, default=1)
    parser.add_argument("--fixed-instances", type=int, default=7)
    parser.add_argument("--max-instances", type=int, default=7)
    parser.add_argument("--max-scaling-change", type=int, default=2)
    parser.add_argument("--vm-cost-per-hour", type=float, default=0.10)
    parser.add_argument("--window-size", type=int, default=192)
    parser.add_argument("--evaluation-hours", type=int, default=72)
    parser.add_argument("--model-cache-dir", default=".model-cache")
    parser.add_argument("--allow-model-download", action="store_true")
    return parser.parse_args(argv)


def run(args, forecaster=None):
    policy = CapacityPolicy(args.player_share, args.players_per_instance,
                            args.headroom_fraction, args.minimum_instances)
    limits = ScalingLimits(args.minimum_instances, args.max_instances, args.max_scaling_change)
    games = load_player_history(args.history_path)
    if args.appid not in games:
        raise ValueError(f"No history for appid {args.appid!r}")
    observations = games[args.appid]
    start = len(observations)-args.evaluation_hours
    validate_replay(observations, start)
    if args.evaluation_hours < 1 or args.window_size < 1 or start < args.window_size:
        raise ValueError("Need a positive evaluation period after a full past input window")
    if args.output_dir.exists():
        raise ValueError("Output directory already exists; choose a new directory")
    if forecaster is None:
        forecaster = TimesFMForecaster(args.window_size, 1, args.model_cache_dir,
                                      local_files_only=not args.allow_model_download)
    strategies = {
        "reactive_capacity": ReactiveCapacityStrategy(policy),
        "timesfm_capacity": PredictiveCapacityStrategy(policy, forecaster, args.window_size),
        "fixed_capacity": FixedCapacityStrategy(args.fixed_instances),
    }
    results, traces = simulate_scaling(observations, strategies, policy, start, args.vm_cost_per_hour, limits)
    fixed_cost = results["fixed_capacity"]["total_cost_eur"]
    for result in results.values():
        result["cost_savings_vs_fixed_eur"] = fixed_cost-result["total_cost_eur"]
    report = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "offline_simulation", "appid": args.appid,
        "history_path": str(args.history_path),
        "history_sha256": hashlib.sha256(args.history_path.read_bytes()).hexdigest(),
        "model_id": MODEL_ID, "model_mode": "pretrained_zero_shot",
        "assumptions": {
            **asdict(policy), "fixed_instances": args.fixed_instances,
            "scaling_limits": asdict(limits),
            "vm_cost_per_hour": args.vm_cost_per_hour,
            "forecast_horizon_hours": 1, "startup_hours": 1, "reconciliation_hours": 1,
            "window_observations": args.window_size,
            "capacity_values_are_demo_assumptions": True,
            "cost_basis": "Ready and starting instances billed; removals at next hourly boundary",
            "demand_basis": "Each hourly snapshot held for one hour; modeled customer share, not measured server load",
        },
        "evaluation": {
            "hours": args.evaluation_hours,
            "start_utc": datetime.fromtimestamp(observations[start].timestamp, timezone.utc).isoformat(),
            "last_observation_utc": datetime.fromtimestamp(observations[-1].timestamp, timezone.utc).isoformat(),
            "end_exclusive_utc": datetime.fromtimestamp(observations[-1].timestamp+3600, timezone.utc).isoformat(),
            "warmup_observations": start,
        },
        "results": results,
        "limitations": [
            "Exploratory replay on previously inspected history; does not demonstrate production savings or an SLA.",
            "Original threshold/aggressive policies are unchanged; reactive_capacity is the shared capacity rule using current demand.",
            "Scale changes reconcile only at hourly boundaries; subhour startup or workload behavior is not modeled.",
            "No real containers, VMs or game servers are started or stopped.",
        ],
    }
    revision_file = Path(args.model_cache_dir)/"models--google--timesfm-2.5-200m-pytorch"/"refs"/"main"
    if revision_file.exists():
        report["checkpoint_revision"] = revision_file.read_text(encoding="utf-8").strip()
    args.output_dir.mkdir(parents=True)
    (args.output_dir/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    with (args.output_dir/"decisions.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(traces[0]))
        writer.writeheader()
        writer.writerows(traces)
    return report


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        report = run(args)
    except (OSError, ValueError, TimesFMError) as error:
        logging.error("Scaling simulation failed: %s", error)
        return 1
    print(json.dumps(report["results"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
