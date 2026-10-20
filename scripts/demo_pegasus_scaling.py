"""Deterministic one-worker lifecycle demo. Never deletes or adopts existing VMs."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from scaler.engine.decision_engine import DecisionEngine
from scaler.engine.scaling_limits import ScalingLimits
from scaler.exporter.autoscaler_metrics import AutoscalerMetrics, AutoscalerMetricsConfig, start_metrics_server
from scaler.infrastructure.pegasus_controller import PegasusConfig, PegasusController
from scaler.strategies.capacity import CapacityPolicy
from scaler.strategies.demand import AggressiveCapacityStrategy
from main import evaluate_once


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--metrics-host', default='127.0.0.1')
    parser.add_argument('--metrics-port', type=int, default=8001)
    parser.add_argument('--scrape-wait', type=float, default=20)
    args = parser.parse_args(argv)
    if not 0 <= args.scrape_wait <= 60:
        parser.error('scrape-wait must be between 0 and 60 seconds')
    report_path = Path(args.report)
    # Reserve before any operations; an interrupted run leaves explicit evidence.
    with report_path.open('x') as output:
        json.dump({'status': 'started', 'apply': args.apply, 'timestamp': time.time()}, output)
    config = PegasusConfig.load(args.config)
    controller = PegasusController(config, dry_run=not args.apply)
    metrics = AutoscalerMetrics()
    metrics.set_controller('pegasus', config.appid)
    metrics.set_pegasus_provider(controller.telemetry, config.appid)
    metrics.set_source_appid(config.appid, config.appid)
    server = None
    report = {'apply': args.apply, 'config': asdict(config), 'steps': [],
              'input_kind': 'deterministic synthetic counts; not normal game data'}
    try:
        controller.validate_resources()
        if controller.snapshot():
            raise ValueError('Demo requires zero allocated workers for this game; use an isolated fresh pool')
        def unrelated():
            return {s.id: {'name': s.name, 'status': s.status, 'task': s.task_state}
                    for s in controller.connection.compute.servers(details=True)
                    if not controller._owned(s)}
        report['protected_before'] = unrelated()
        report['initial_workers'] = []
        report['status'] = 'ready_for_demo'
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        policy = CapacityPolicy(1, 100, 0, 0)
        engine = DecisionEngine(AggressiveCapacityStrategy(policy), controller,
                                ScalingLimits(0, 1, 1), policy)
        server = start_metrics_server(AutoscalerMetricsConfig(args.metrics_host, args.metrics_port), metrics)
        for count in (100, 0):
            start = time.time()
            client = type('Input', (), {'get_player_count': lambda self, labels: count})()
            running = evaluate_once(client, engine, {'appid': config.appid}, metrics, config.appid)
            report['steps'].append({'players': count, 'running': running, 'plan': controller.plan,
                                    'servers': [{'id': s.id, 'name': s.name, 'status': s.status}
                                                for s in controller.snapshot()],
                                    'started_at': start, 'duration_seconds': time.time()-start,
                                    'telemetry': controller.telemetry()})
            report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
            time.sleep(args.scrape_wait)
            if count and args.apply:
                time.sleep(max(0, config.cooldown_seconds - args.scrape_wait + 1))
        report['protected_after'] = unrelated()
        if report['protected_before'] != report['protected_after']:
            raise ValueError('Unmanaged inventory changed; inspect independently')
        if args.apply and [s['running'] for s in report['steps']] != [1, 0]:
            raise ValueError('Live demo failed final state assertions')
        report['status'] = 'verified' if args.apply else 'dry_run'
        report['metrics'] = metrics.render().decode()
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        return 0
    finally:
        if server:
            server.shutdown()
            server.server_close()
        controller.close()


if __name__ == '__main__':
    raise SystemExit(main())
