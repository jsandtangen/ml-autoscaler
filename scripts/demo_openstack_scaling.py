"""Inspect an owned VM pool, or explicitly demonstrate start/stop with JSON evidence."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from scaler.engine.decision_engine import DecisionEngine
from scaler.engine.scaling_limits import ScalingLimits
from scaler.infrastructure.openstack_controller import OpenStackController, OpenStackPool
from scaler.infrastructure.vm_controller import ControllerError
from scaler.strategies.capacity import CapacityPolicy, ReactiveCapacityStrategy


def demonstrate(controller):
    initial = controller.snapshot()
    if any(state != ('SHUTOFF', None) for state in initial.values()):
        raise ControllerError('Demo requires the entire owned pool initially SHUTOFF')
    size = len(initial)
    policy = CapacityPolicy(minimum_instances=0)
    engine = DecisionEngine(ReactiveCapacityStrategy(policy), controller,
                            ScalingLimits(0, size, 2), policy)
    report = {'mode': 'real_openstack_fixed_pool', 'input': 'synthetic_demo_counts',
              'started_at': datetime.now(timezone.utc).isoformat(), 'steps': []}
    try:
        # Repeated decisions exercise the same engine and two-change bound as main.py.
        high = size * 100000
        for players in [high]*((size+1)//2) + [0]*((size+1)//2):
            before = controller.snapshot()
            running = engine.evaluate(players)
            after = controller.snapshot()
            changed = [sid for sid in before if before[sid] != after[sid]]
            if len(changed) > 2:
                raise ControllerError('Unexpectedly more than two changed VM identities')
            report['steps'].append({'at': datetime.now(timezone.utc).isoformat(),
                'players': players, 'running_instances': running,
                'before': before, 'after': after, 'changed_vm_ids': changed})
        report['success'] = True
    except ControllerError as error:
        report.update(success=False, error=str(error))
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool', required=True)
    parser.add_argument('--apply', action='store_true', help='Start/stop owned pool VMs; needs explicit approval.')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    if args.apply and (args.report is None or args.report.exists()):
        parser.error('--apply requires a fresh --report path')
    controller = OpenStackController(OpenStackPool.load(args.pool))
    result = demonstrate(controller) if args.apply else {'mode': 'read_only', 'states': controller.snapshot()}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open('x', encoding='utf-8') as file:
            json.dump(result, file, indent=2)
    print(json.dumps(result, indent=2))
    return 0 if result.get('success', True) else 1


if __name__ == '__main__':
    raise SystemExit(main())
