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
    parser.add_argument("--five-minute", action="store_true",
                        help="Isolated Pegasus replay observer: 15/30 minute horizons, reject source zeros.")
    parser.add_argument("--source-url", default="http://10.196.242.62")
    parser.add_argument("--history-json", type=Path)
    parser.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--evaluation-origins", type=int, default=144)
    parser.add_argument("--source-job", default="games")
    parser.add_argument("--autoscaler-job", default="ruby_acorn_pegasus")
    parser.add_argument("--metrics-host", default="127.0.0.1")
    parser.add_argument("--metrics-port", type=int, default=8003)
    args = parser.parse_args(argv)
    if not args.appid.isascii() or not args.appid.isdecimal() or int(args.appid) <= 0:
        parser.error("Expected a positive Steam appid")
    if not math.isfinite(args.poll_seconds) or not 0 < args.poll_seconds <= 90:
        parser.error("Poll interval must be finite, positive and at most 90 seconds")
    if args.window_size < 1:
        parser.error("Window size must be positive")
    if args.evaluate_only and not args.five_minute:
        parser.error("--evaluate-only requires --five-minute")
    if args.five_minute and (args.appid != "730" or args.evaluation_origins < 1):
        parser.error("Five-minute replay mode requires appid 730 and positive evaluation origins")
    args.output_dir = args.output_dir or Path("data/shadow-five-minute" if args.five_minute else "data/shadow-capacity")/args.appid
    return args


def run(args, client=None, forecaster=None, clock=time.time, sleep=time.sleep):
    if args.five_minute:
        return run_five_minute(args, client, forecaster, clock, sleep)
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


def read_source_json(url):
    import gzip
    import requests
    # Varnish caches /health despite no-cache headers. Read the actual cutoff.
    if url.endswith('/health'):
        url += '?ml_read='+str(time.time_ns())
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    raw = response.content
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def run_five_minute(args, client=None, forecaster=None, clock=time.time, sleep=time.sleep):
    if any(name.startswith("OS_") for name in os.environ):
        raise ValueError("ML shadow must run without OpenStack environment variables")
    from scaler.data.shadow_source import parse_replay_history, replay_cutoff, observed_replay_rows
    from scaler.shadow import FiveMinuteShadowObserver, evaluate_short_horizons, validate_five_minute_history
    from scaler.exporter.shadow_metrics import render_shadow_metrics
    from scaler.exporter.autoscaler_metrics import start_metrics_server, AutoscalerMetricsConfig
    policy = CapacityPolicy(args.player_share, args.players_per_instance,
                            args.headroom_fraction, args.minimum_instances)
    limits = ScalingLimits(args.minimum_instances, args.max_instances, args.max_scaling_change)
    client = client or ShadowPrometheusClient(args.prometheus_url, source_job=args.source_job,
                                             autoscaler_job=args.autoscaler_job)
    forecaster = forecaster or TimesFMForecaster(args.window_size, 6, args.model_cache_dir,
                                                local_files_only=not args.allow_model_download)
    # Unit tests inject a lightweight forecaster. Real CPU inference uses one thread.
    if isinstance(forecaster, TimesFMForecaster):
        import torch
        torch.set_num_threads(1)
    with output_lock(args.output_dir):
        history_path = args.history_json or args.output_dir/"source-history.json"
        if not history_path.exists():
            write_json(history_path, read_source_json(args.source_url+"/games/730/history"))
        payload = json.loads(history_path.read_text(encoding="utf-8"))
        rows = parse_replay_history(payload)
        audit = validate_five_minute_history(rows)
        health = read_source_json(args.source_url+"/health")
        cutoff = replay_cutoff(payload, health, clock())
        # Full history may include future replay rows. Never expose them to models.
        past = [r for r in rows if r.timestamp <= cutoff]
        config = {"mode": "five_minute_replay_shadow", "appid": args.appid,
                  "source_url": args.source_url, "prometheus_url": args.prometheus_url,
                  "source_job": args.source_job, "autoscaler_job": args.autoscaler_job,
                  "model_id": MODEL_ID, "window_size": args.window_size,
                  "policy": asdict(policy), "limits": asdict(limits),
                  "replay_epoch": health["replay_epoch"],
                  "history_sha256": hashlib.sha256(history_path.read_bytes()).hexdigest()}
        metadata_path = args.output_dir/"metadata.json"
        if metadata_path.exists() and json.loads(metadata_path.read_text())["config"] != config:
            raise ValueError("Shadow configuration differs; use a new output directory")
        write_json(metadata_path, {"config": config, "audit": audit})
        if args.evaluate_only:
            started = time.perf_counter()
            cpu_started = time.process_time()
            report = evaluate_short_horizons(past, forecaster, policy, limits,
                                             args.window_size, args.evaluation_origins)
            report.update(config=config, evaluated_at=clock(),
                          source_cutoff_timestamp=cutoff, elapsed_seconds=time.perf_counter()-started)
            report["cpu_seconds"] = time.process_time()-cpu_started
            try:
                import resource
                report["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
            except ImportError:
                pass
            write_json(args.output_dir/"offline-report.json", report)
            print(json.dumps({k: v for k, v in report.items() if k != "predictions"}, indent=2))
            return 0
        state_path = args.output_dir/"status.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else None
        observer = FiveMinuteShadowObserver(policy, limits, args.window_size, state)
        offline = args.output_dir/"offline-report.json"
        if offline.exists():
            report = json.loads(offline.read_text())
            if report.get("config") != config:
                raise ValueError("Offline report configuration differs")
            observer.state["offline_scores"] = report["scores"]
        class Metrics:
            def render(self):
                snapshot = json.loads(state_path.read_text()) if state_path.exists() else {}
                return render_shadow_metrics(snapshot, clock())
        server = start_metrics_server(AutoscalerMetricsConfig(args.metrics_host, args.metrics_port), Metrics())
        write_json(args.output_dir/"process.json", {"pid": os.getpid(), "running": True, "started_at": clock()})
        try:
            while True:
                now = clock()
                try:
                    health = read_source_json(args.source_url+"/health")
                    if health["replay_epoch"] != config["replay_epoch"]:
                        raise ValueError("Replay reset; a new output directory is required")
                    cutoff = replay_cutoff(payload, health, now)
                    if cutoff < observer.state["last_timestamp"]:
                        raise ValueError("Replay clock moved backwards")
                    sample = client.latest_observation(args.appid)
                    available = observed_replay_rows(rows, cutoff, sample, now, args.window_size)
                    if available[-1].timestamp < observer.state["last_timestamp"]:
                        raise ValueError("Observed Prometheus replay moved backwards")
                    reference = client.live_decision(args.appid, now)
                    started = time.perf_counter()
                    events = observer.observe(available, forecaster, reference, now)
                    if events:
                        observer.state["inference_seconds"] = time.perf_counter()-started
                        # State is authoritative. Commit pending outcomes before
                        # the diagnostic journal, closing the crash/reissue gap.
                        observer.state["checked_at"] = clock()
                        write_json(state_path, observer.state)
                        with (args.output_dir/"events.jsonl").open("a", encoding="utf-8") as file:
                            for event in events:
                                file.write(json.dumps(event, allow_nan=False)+"\n")
                    # A recovered connection in the same interval must not revive stale proposals.
                except (GameDataClientError, ValueError, RuntimeError, OSError) as error:
                    observer.state.update(status="source_or_model_error", latest=[], error=str(error))
                    logger.warning("Five-minute observer: %s", error)
                observer.state["checked_at"] = clock()
                try:
                    import resource
                    observer.state["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
                except ImportError:
                    pass
                write_json(state_path, observer.state)
                if args.once:
                    return 0 if observer.state.get("status") == "forecast" else 1
                sleep(args.poll_seconds)
        finally:
            server.shutdown()
            server.server_close()
            write_json(args.output_dir/"process.json", {"pid": os.getpid(), "running": False, "stopped_at": clock()})


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
