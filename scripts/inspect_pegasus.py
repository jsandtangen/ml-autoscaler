"""Read-only Pegasus preflight and VM inventory. This script cannot apply changes."""
import argparse
import json
from types import SimpleNamespace
from uuid import UUID

from scaler.infrastructure.pegasus_controller import PegasusConfig, PegasusController, connect
from scaler.infrastructure.vm_controller import ControllerError


def discover(cloud, expected_project_id, connection=None):
    """Inventory only: no controller, resource selection or mutation methods."""
    phase = "configuration"
    conn = None
    report = {"read_only": True, "checks": {}}
    try:
        expected = UUID(expected_project_id)
        conn = connection or connect(SimpleNamespace(cloud=cloud))
        phase = "authentication"
        actual = conn.current_project_id
        report["authenticated_project_id"] = actual
        report["checks"][phase] = "passed"
        phase = "project_match"
        if UUID(actual) != expected:
            raise ValueError("Project mismatch")
        report["checks"][phase] = "passed"
        phase = "compute"
        servers = list(conn.compute.servers(details=True))
        report["existing_vms"] = [{"id": s.id, "name": s.name, "status": s.status} for s in servers]
        report["checks"][phase] = "passed"
        phase = "quotas"
        limits = conn.compute.get_limits().absolute
        values = {"instances": (limits.max_total_instances, limits.total_instances_used),
                  "vcpus": (limits.max_total_cores, limits.total_cores_used),
                  "ram_mib": (limits.max_total_ram_size, limits.total_ram_used)}
        expected_limits = {"instances": 10, "vcpus": 12, "ram_mib": 24576}
        if any(type(n) is not int for pair in values.values() for n in pair):
            raise ValueError("Invalid quota response")
        report["quotas"] = {k: {"limit": v[0], "used": v[1], "expected": expected_limits[k],
                                "matches_expected": v[0] == expected_limits[k]}
                            for k, v in values.items()}
        report["checks"][phase] = "passed" if all(v[0] == expected_limits[k] for k, v in values.items()) else "mismatch"
        phase = "flavors"
        report["flavors"] = [{"id": f.id, "name": f.name, "vcpus": f.vcpus,
                              "ram_mib": f.ram, "disk_gib": f.disk}
                             for f in conn.compute.flavors(details=True)]
        report["checks"][phase] = "passed"
        phase = "images"
        report["images"] = [{"id": i.id, "name": i.name, "status": i.status,
                             "min_ram_mib": i.min_ram, "min_disk_gib": i.min_disk}
                            for i in conn.image.images()]
        report["checks"][phase] = "passed"
        phase = "networks"
        report["networks"] = [{"id": n.id, "name": n.name, "status": n.status,
                               "shared": n.is_shared, "external": n.is_router_external,
                               "project_id": n.project_id}
                              for n in conn.network.networks()]
        report["checks"][phase] = "passed"
        return report, 0 if report["checks"]["quotas"] == "passed" else 1
    except Exception as error:
        # Never render SDK exceptions: bodies/headers may contain credentials.
        report["checks"][phase] = "failed"
        report["failure"] = {"phase": phase, "error_type": type(error).__name__}
        return report, 1
    finally:
        if conn is not None:
            conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config")
    mode.add_argument("--discover", action="store_true", help="Read project, quota and available resource inventory without a VM configuration.")
    parser.add_argument("--cloud", default="pegasus")
    parser.add_argument("--expected-project-id", help="Project ID copied locally from Horizon (not a credential).")
    args = parser.parse_args(argv)
    if args.discover:
        if not args.expected_project_id:
            parser.error("--discover requires --expected-project-id")
        report, code = discover(args.cloud, args.expected_project_id)
        print(json.dumps(report, indent=2))
        return code
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
