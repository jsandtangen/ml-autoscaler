"""Read-only Pegasus preflight and VM inventory. This script cannot apply changes."""
import argparse
import json

from scaler.infrastructure.pegasus_controller import PegasusConfig, PegasusController
from scaler.infrastructure.vm_controller import ControllerError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    controller = None
    try:
        config = PegasusConfig.load(args.config)
        controller = PegasusController(config)
        servers = controller.snapshot()
        inventory = controller._api(lambda: list(controller.connection.compute.servers(details=True)))
        controller.validate_resources()
        print(json.dumps({"read_only": True, "resource_validation": "passed",
            "existing_vms": [{"id": s.id, "name": s.name, "status": s.status,
                              "managed": controller._owned(s)} for s in inventory],
            "managed_vms": [{"id": s.id, "name": s.name, "status": s.status,
                             "task_state": s.task_state} for s in servers],
            "telemetry": controller.telemetry()}, indent=2))
        return 0
    except (ControllerError, OSError, ValueError, KeyError, TypeError):
        print("Pegasus preflight failed. Check external authentication, project, endpoints, quotas and resource IDs; see docs/pegasus.md.")
        return 1
    finally:
        if controller:
            controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
