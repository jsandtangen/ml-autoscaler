"""Observe hourly demand and log TimesFM proposals without scaling resources."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict
import json
import hashlib
import logging
import math
import os
from pathlib import Path
import time

from scaler.data.client import GameDataClientError
from scaler.data.history import PlayerHistoryStore
from scaler.data.shadow_source import ShadowPrometheusClient
from scaler.forecasting import load_player_history
from scaler.shadow import ShadowObserver
from scaler.strategies.capacity import CapacityPolicy
from scaler.engine.scaling_limits import ScalingLimits
from scaler.timesfm_forecasting import TimesFMForecaster, MODEL_ID


logger = logging.getLogger("ruby_acorn.shadow")


@contextmanager
def output_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory/"observer.lock").open("a+b") as file:
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Another shadow observer owns this output directory") from error
        yield


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", default="http://127.0.0.1:9090")
    parser.add_argument("--appid", default="730")
    parser.add_argument("--metric-name", default="steam_player_count")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--seed-history-path", type=Path)
    parser.add_argument("--window-size", type=int, default=192)
    parser.add_argument("--player-share", type=float, default=0.001)
    parser.add_argument("--players-per-instance", type=int, default=100)
    parser.add_argument("--headroom-fraction", type=float, default=0.10)
    parser.add_argument("--minimum-instances", type=int, default=1)
    parser.add_argument("--max-instances", type=int, default=7)
    parser.add_argument("--max-scaling-change", type=int, default=2)
    parser.add_argument("--poll-seconds", type=float, default=30)
    parser.add_argument("--model-cache-dir", default=".model-cache")
    parser.add_argument("--allow-model-download", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not args.appid.isascii() or not args.appid.isdecimal() or int(args.appid) <= 0:
        parser.error("Expected a positive Steam appid")
    if not math.isfinite(args.poll_seconds) or not 0 < args.poll_seconds <= 90:
        parser.error("Poll interval must be finite, positive and at most 90 seconds")
    if args.window_size < 1:
        parser.error("Window size must be positive")
    args.output_dir = args.output_dir or Path("data/shadow-capacity")/args.appid
    return args


def run(args, client=None, forecaster=None, clock=time.time, sleep=time.sleep):
    policy = CapacityPolicy(args.player_share, args.players_per_instance,
                            args.headroom_fraction, args.minimum_instances)
    limits = ScalingLimits(args.minimum_instances, args.max_instances, args.max_scaling_change)
    client = client or ShadowPrometheusClient(args.prometheus_url, metric_name=args.metric_name)
    forecaster = forecaster or TimesFMForecaster(args.window_size, 1, args.model_cache_dir,
                                               local_files_only=not args.allow_model_download)
    with output_lock(args.output_dir):
        config = {"appid": args.appid, "prometheus_url": args.prometheus_url,
            "metric_name": args.metric_name, "model_id": MODEL_ID,
            "window_size": args.window_size, "policy": asdict(policy),
            "limits": asdict(limits),
            "period_seconds": 3600, "boundary_grace_seconds": 300, "max_sample_age_seconds": 90}
        metadata_path = args.output_dir/"metadata.json"
        if metadata_path.exists():
            if json.loads(metadata_path.read_text(encoding="utf-8"))["config"] != config:
                raise ValueError("Shadow configuration differs; use a new output directory")
        else:
            write_json(metadata_path, {"config": config, "created_at": clock()})
        samples_path = args.output_dir/"hourly_observations.csv"
        live = load_player_history(samples_path).get(args.appid, []) if samples_path.exists() else []
        seed = load_player_history(args.seed_history_path).get(args.appid, []) if args.seed_history_path else []
        events_path = args.output_dir/"events.jsonl"
        pending = None
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                if event.get("event") == "decision":
                    pending = event if event["status"] == "forecast" else None
                elif event.get("event") in ("outcome", "outcome_unavailable"):
                    pending = None
        observer = ShadowObserver(args.appid, forecaster, policy, args.window_size, seed, live, pending, limits=limits)
        store = PlayerHistoryStore(samples_path)
        with events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps({"event": "session", "started_at": clock(), "config": config,
                "seed_history_path": str(args.seed_history_path) if args.seed_history_path else None,
                "seed_sha256": hashlib.sha256(args.seed_history_path.read_bytes()).hexdigest()
                    if args.seed_history_path else None})+"\n")
        process_path = args.output_dir/"process.json"
        write_json(process_path, {"pid": os.getpid(), "running": True, "started_at": clock()})
        last_status_key = None
        try:
            while True:
                now = clock()
                try:
                    sample = client.latest_observation(args.appid)
                    # Read live control metrics only when a new hourly sample is due.
                    slot = math.floor(now/3600)*3600
                    reference = None
                    if slot > observer.last_live_slot and now-slot <= 300:
                        try:
                            reference = client.live_decision(args.appid, now)
                        except GameDataClientError as error:
                            reference = {"available": False, "error": str(error)}
                    events = observer.observe(sample, now, reference)
                    source_failed = False
                except GameDataClientError as error:
                    events = [{"event": "status", "status": "source_error", "checked_at": now,
                               "appid": args.appid, "slot": math.floor(now/3600)*3600, "error": str(error)}]
                    source_failed = True
                for event in events:
                    if event["event"] == "decision":
                        event["issued_at"] = clock()
                        store.record(event["global_players"], args.appid, event["sample_timestamp"])
                        write_json(args.output_dir/"latest_decision.json", event)
                    key = (event.get("event"), event.get("status"), event.get("slot"))
                    if event["event"] != "status" or key != last_status_key:
                        with events_path.open("a", encoding="utf-8") as file:
                            file.write(json.dumps(event, allow_nan=False)+"\n")
                        logger.info("shadow %s", json.dumps(event, allow_nan=False))
                    last_status_key = key
                latest = {**events[-1], "observer_pid": os.getpid(), "checked_at": now,
                          "mode": "shadow_only", "loaded_history_rows": len(observer.history)}
                write_json(args.output_dir/"status.json", latest)
                if args.once:
                    return 1 if source_failed else 0
                sleep(args.poll_seconds)
        finally:
            write_json(process_path, {"pid": os.getpid(), "running": False, "stopped_at": clock()})


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        logger.info("Shadow observer stopped")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        logger.error("Shadow observer failed: %s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
