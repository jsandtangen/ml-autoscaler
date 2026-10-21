"""Compare reactive capacity, original TimesFM capacity and fixed allocation."""
from __future__ import annotations

import argparse
from copy import copy
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import subprocess
import sys
import time

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
    parser.add_argument("--compare-max-instances", type=int, nargs="+",
                        help="Replay identical history/forecasts at each cap; fixed allocation equals each cap.")
    parser.add_argument("--max-scaling-change", type=int, default=2)
    parser.add_argument("--vm-cost-per-hour", type=float, default=0.10)
    parser.add_argument("--window-size", type=int, default=192)
    parser.add_argument("--evaluation-hours", type=int, default=72)
    parser.add_argument("--model-cache-dir", default=".model-cache")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--five-minute-study", action="store_true")
    parser.add_argument("--study-phase", choices=("all", "forecasts", "replay"), default="all")
    parser.add_argument("--startup-seconds", type=int, nargs="+", default=[900, 1800])
    parser.add_argument("--decision-seconds", type=int, default=30)
    parser.add_argument("--cooldown-seconds", type=int, default=120)
    parser.add_argument("--initial-instances", type=int, default=1)
    parser.add_argument("--stress-player-share", type=float)
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


class CachedForecaster:
    """Reuse exactly the same past-only forecast across capacity scenarios."""

    def __init__(self, forecaster):
        self.forecaster = forecaster
        self.predictions = {}

    def predict(self, counts):
        key = tuple(counts)
        if key not in self.predictions:
            self.predictions[key] = self.forecaster.predict(counts)
        return self.predictions[key]


def run_comparison(args, forecaster=None):
    caps = args.compare_max_instances
    if not caps or len(set(caps)) != len(caps):
        raise ValueError("Comparison needs distinct capacity limits")
    for cap in caps:
        ScalingLimits(args.minimum_instances, cap, args.max_scaling_change)
    if args.output_dir.exists():
        raise ValueError("Output directory already exists; choose a new directory")
    cached = CachedForecaster(forecaster if forecaster is not None else TimesFMForecaster(
        args.window_size, 1, args.model_cache_dir, local_files_only=not args.allow_model_download))
    scenarios = {}
    for cap in caps:
        scenario_args = copy(args)
        scenario_args.max_instances = scenario_args.fixed_instances = cap
        scenario_args.output_dir = args.output_dir / f"max-{cap}"
        scenarios[str(cap)] = run(scenario_args, cached)
        if scenarios[str(cap)]["results"]["timesfm_capacity"]["forecast_fallbacks"]:
            raise TimesFMError("Comparison requires successful forecasts in every evaluated scenario")
    # Publish the summary only after every scenario completed successfully.
    report = {"mode": "offline_limit_comparison", "appid": args.appid,
              "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "scenarios": scenarios}
    (args.output_dir / "comparison.json").write_text(
        json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    return report


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def study_data(args):
    from scaler.data.shadow_source import parse_replay_history
    from scaler.simulation import longest_clean_segment
    from scaler.shadow import validate_five_minute_history
    if args.appid != '730' or args.window_size < 1:
        raise ValueError('Study requires appid 730 and a positive context window')
    payload = json.loads(args.history_path.read_text(encoding='utf-8'))
    rows = parse_replay_history(payload, args.appid)
    audit = validate_five_minute_history(rows)
    segment = longest_clean_segment(rows, args.window_size+7)
    start, stop = args.window_size, len(segment)-6
    revision = Path(args.model_cache_dir)/'models--google--timesfm-2.5-200m-pytorch'/'refs'/'main'
    signature = {'source_sha256': hashlib.sha256(args.history_path.read_bytes()).hexdigest(),
        'segment_sha256': hashlib.sha256(json.dumps([(r.timestamp, r.player_count) for r in segment]).encode()).hexdigest(),
        'model_id': MODEL_ID, 'model_revision': revision.read_text().strip() if revision.exists() else None,
        'window_size': args.window_size, 'horizon_steps': [3, 6],
        'segment_first_timestamp': segment[0].timestamp, 'segment_last_timestamp': segment[-1].timestamp,
        'segment_observations': len(segment), 'evaluation_start_index': start, 'evaluation_stop_index': stop}
    return segment, start, stop, signature, audit


def context_hash(rows, index, window):
    counts = [r.player_count for r in rows[index-window+1:index+1]]
    return hashlib.sha256(json.dumps(counts).encode()).hexdigest()


def load_study_cache(path, rows, start, stop, window):
    cached = {}
    if not path.exists():
        return cached
    for line in path.read_text(encoding='utf-8').splitlines():
        item = json.loads(line)
        index = item['origin_index']
        if (type(index) is not int or not start <= index < stop or index in cached
                or item['origin_timestamp'] != rows[index].timestamp
                or item['context_sha256'] != context_hash(rows, index, window)
                or any(not math.isfinite(item[str(h)]) or item[str(h)] < 0 for h in (15, 30))):
            raise ValueError('Forecast cache contains duplicate, invalid or mismatched origins')
        cached[index] = item
    return cached


class StudyCachedForecaster:
    def __init__(self, rows, cached, clock, horizon):
        self.rows, self.cached, self.clock, self.horizon = rows, cached, clock, horizon

    def predict(self, counts):
        index = int((self.clock()-self.rows[0].timestamp)//300)
        item = self.cached[index]
        if item['context_sha256'] != hashlib.sha256(json.dumps(list(counts)).encode()).hexdigest():
            raise ValueError('Predictive strategy input differs from the cached past-only window')
        return item[str(self.horizon)]


def run_study(args, forecaster=None, raw_args=None):
    import math
    import gzip
    from scaler.simulation import SimulationClock, simulate_five_minute_scaling
    from scaler.strategies.demand import AggressiveCapacityStrategy, CostOptimizedStrategy, DemandTuning
    if any(name.startswith('OS_') for name in __import__('os').environ):
        raise ValueError('Simulation must run without OpenStack environment variables')
    rows, start, stop, signature, audit = study_data(args)
    caps = args.compare_max_instances or [1, args.max_instances]
    if len(set(caps)) != len(caps) or len(set(args.startup_seconds)) != len(args.startup_seconds):
        raise ValueError('Study scenarios must have distinct limits and startup delays')
    shares = [('production_assumptions', args.player_share)]
    if args.stress_player_share is not None:
        if args.stress_player_share == args.player_share:
            raise ValueError('Stress share must differ and be labeled separately')
        shares.append(('simulated_load_stress', args.stress_player_share))
    for _, share in shares:
        CapacityPolicy(share, args.players_per_instance, args.headroom_fraction, args.minimum_instances)
    for cap in caps:
        limit = ScalingLimits(args.minimum_instances, cap, args.max_scaling_change)
        if not limit.minimum_instances <= args.initial_instances <= cap:
            raise ValueError('Common initial fleet must fit every capacity limit')
    if (args.decision_seconds < 1 or 300 % args.decision_seconds
            or args.cooldown_seconds < 0 or args.cooldown_seconds % args.decision_seconds
            or any(s < 0 or s % args.decision_seconds for s in args.startup_seconds)
            or not math.isfinite(args.vm_cost_per_hour) or args.vm_cost_per_hour < 0):
        raise ValueError('Invalid study timings or price')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metadata = args.output_dir/'forecast-metadata.json'
    if metadata.exists():
        if json.loads(metadata.read_text()) != signature:
            raise ValueError('Forecast cache signature differs; choose a new study directory')
    else:
        atomic_json(metadata, signature)
    cache_path = args.output_dir/'forecasts.jsonl'
    cached = load_study_cache(cache_path, rows, start, stop, args.window_size)
    if args.study_phase == 'all' and forecaster is None and len(cached) < stop-start:
        # The model worker exits before replay, releasing Torch/model memory.
        subprocess.run([sys.executable, '-m', 'scripts.simulate_scaling', *(raw_args or []),
                        '--study-phase', 'forecasts'], check=True)
        cached = load_study_cache(cache_path, rows, start, stop, args.window_size)
    elif args.study_phase in ('all', 'forecasts'):
        if len(cached) < stop-start:
            if forecaster is None:
                import torch
                torch.set_num_threads(1)
                forecaster = TimesFMForecaster(args.window_size, 6, args.model_cache_dir, local_files_only=True)
            began, cpu = time.perf_counter(), time.process_time()
            generated = 0
            with cache_path.open('a', encoding='utf-8') as file:
                for index in range(start, stop):
                    if index in cached:
                        continue
                    counts = [r.player_count for r in rows[index-args.window_size+1:index+1]]
                    points = forecaster.predict_steps(counts)
                    if len(points) != 6 or any(not math.isfinite(v) or v < 0 for v in points):
                        raise TimesFMError('Invalid six-step forecast; no reactive fallback is permitted')
                    item = {'origin_index': index, 'origin_timestamp': rows[index].timestamp,
                            'context_sha256': context_hash(rows, index, args.window_size),
                            '15': float(points[2]), '30': float(points[5])}
                    file.write(json.dumps(item, allow_nan=False)+'\n')
                    file.flush()
                    cached[index] = item
                    generated += 1
                    if generated % 100 == 0:
                        logging.info('Cached %s/%s origins', len(cached), stop-start)
            metrics = {'generated_origins': generated, 'cached_origins': len(cached),
                       'elapsed_seconds': time.perf_counter()-began, 'cpu_seconds': time.process_time()-cpu}
            try:
                import resource
                metrics['peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
            except ImportError:
                pass
            atomic_json(args.output_dir/'forecast-generation.json', metrics)
    if len(cached) != stop-start:
        raise ValueError('Incomplete forecast cache; replay cannot silently fall back')
    if args.study_phase == 'forecasts':
        return {'mode': 'forecast_cache_complete', 'origins': len(cached)}
    report_path = args.output_dir/'study.json'
    if report_path.exists():
        raise ValueError('Completed study already exists; preserve results instead of overwriting')
    report = {'mode': 'offline_five_minute_scaling_study', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'dataset': signature, 'source_audit': audit,
        'evaluation': {'start_timestamp': rows[start].timestamp, 'end_exclusive_timestamp': rows[stop-1].timestamp+300,
                       'origins': stop-start, 'hours': (stop-start)/12,
                       'warmup_observations': start, 'reserved_future_observations': 6},
        'forecast_cache_sha256': hashlib.sha256(cache_path.read_bytes()).hexdigest(),
        'assumptions': {'decision_seconds': args.decision_seconds, 'cooldown_seconds': args.cooldown_seconds,
                       'initial_ready_instances': args.initial_instances, 'initial_starting_instances': 0,
                       'vm_cost_per_hour': args.vm_cost_per_hour, 'source_interval_seconds': 300,
                       'stop_delay_seconds': 0, 'demand_between_samples': 'last value held, no interpolation',
                       'start_capacity': 'starting VMs count toward limits/cost but provide no player capacity',
                       'fixed_reference': 'fixed target equal to cap; identical initial fleet and ramp limits',
                       'tuning': asdict(DemandTuning())},
        'limitations': ['Historical CS:GO; model pretraining overlap unknown.',
                       'VM price, capacity and startup readiness are assumptions, not measured billing/readiness.',
                       'Stopped disks, management/monitoring and ML compute are excluded from VM cost.',
                       'No OpenStack calls; higher caps and stress customer shares are simulation only.',
                       'Finite test window: costs/capacity after its end are not counted.'], 'scenarios': []}
    summaries = []
    for label, share in shares:
        policy = CapacityPolicy(share, args.players_per_instance, args.headroom_fraction, args.minimum_instances)
        for cap in caps:
            limits = ScalingLimits(args.minimum_instances, cap, args.max_scaling_change)
            for startup in args.startup_seconds:
                clock = SimulationClock(rows[start].timestamp)
                strategies = {'aggressive_capacity': AggressiveCapacityStrategy(policy),
                              'cost_optimized': CostOptimizedStrategy(policy, clock=clock),
                              'timesfm_15m': PredictiveCapacityStrategy(policy, StudyCachedForecaster(rows, cached, clock, 15), args.window_size),
                              'timesfm_30m': PredictiveCapacityStrategy(policy, StudyCachedForecaster(rows, cached, clock, 30), args.window_size),
                              'fixed_capacity': FixedCapacityStrategy(cap)}
                for strategy in strategies.values():
                    if isinstance(strategy, PredictiveCapacityStrategy):
                        strategy.history.extend(r.player_count for r in rows[start-args.window_size:start])
                results, traces = simulate_five_minute_scaling(rows, strategies, policy, limits, clock, start, stop,
                    startup_seconds=startup, decision_seconds=args.decision_seconds,
                    cooldown_seconds=args.cooldown_seconds, initial_instances=args.initial_instances,
                    vm_cost_per_hour=args.vm_cost_per_hour)
                scenario = {'label': label, 'startup_seconds': startup, 'policy': asdict(policy), 'limits': asdict(limits), 'results': results}
                fixed_cost = results['fixed_capacity']['total_cost_eur']
                for name, result in results.items():
                    result['cost_savings_vs_fixed_eur'] = fixed_cost-result['total_cost_eur']
                    summaries.append({'scenario': label, 'max_instances': cap, 'startup_seconds': startup,
                                      'strategy': name, **result})
                directory = args.output_dir/f'{label}-max{cap}-startup{startup}'
                directory.mkdir()
                with gzip.open(directory/'decisions.csv.gz', 'wt', newline='', encoding='utf-8') as file:
                    writer = csv.DictWriter(file, fieldnames=list(traces[0]))
                    writer.writeheader()
                    writer.writerows(traces)
                atomic_json(directory/'report.json', scenario)
                report['scenarios'].append(scenario)
                logging.info('Replayed %s cap=%s startup=%ss', label, cap, startup)
    with (args.output_dir/'summary.csv').open('w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    atomic_json(report_path, report)
    return report


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raw_args = sys.argv[1:] if argv is None else list(argv)
    args = parse_args(raw_args)
    try:
        report = (run_study(args, raw_args=raw_args) if args.five_minute_study else
                  run_comparison(args) if args.compare_max_instances else run(args))
    except (OSError, ValueError, TimesFMError, subprocess.CalledProcessError) as error:
        logging.error("Scaling simulation failed: %s", error)
        return 1
    if args.five_minute_study:
        print(json.dumps({'mode': report['mode'], 'output_dir': str(args.output_dir)}, indent=2))
        return 0
    print(json.dumps({cap: scenario["results"] for cap, scenario in report["scenarios"].items()}
                     if "scenarios" in report else report["results"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
