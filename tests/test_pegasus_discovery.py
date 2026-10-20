from types import SimpleNamespace as NS

import pytest

from scripts.inspect_pegasus import discover, main


PROJECT = "00000000000000000000000000000001"


class ReadOnlyCloud:
    current_project_id = PROJECT

    def __init__(self):
        self.compute = self
        self.image = self
        self.network = self
        self.calls = []
        self.closed = False
        self.cores = 12

    def servers(self, **kwargs):
        self.calls.append("servers")
        return [NS(id="vm", name="unrelated", status="SHUTOFF")]

    def get_limits(self):
        self.calls.append("limits")
        return NS(absolute=NS(max_total_instances=10, total_instances_used=1,
            max_total_cores=self.cores, total_cores_used=2,
            max_total_ram_size=24576, total_ram_used=2048))

    def flavors(self, **kwargs):
        self.calls.append("flavors")
        return [NS(id="small", name="small", vcpus=1, ram=2048, disk=20)]

    def images(self):
        self.calls.append("images")
        return [NS(id="ubuntu", name="Ubuntu", status="active", min_ram=512, min_disk=10)]

    def networks(self):
        self.calls.append("networks")
        return [NS(id="net", name="private", status="ACTIVE", is_shared=False,
                   is_router_external=False, project_id=PROJECT)]

    def close(self):
        self.closed = True


def test_discovery_reads_inventory_and_verifies_quotas():
    cloud = ReadOnlyCloud()
    report, code = discover("pegasus", PROJECT, cloud)
    assert code == 0
    assert report["read_only"]
    assert report["quotas"]["ram_mib"]["limit"] == 24576
    assert report["quotas"]["vcpus"]["matches_expected"]
    assert report["existing_vms"][0]["name"] == "unrelated"
    assert cloud.calls == ["servers", "limits", "flavors", "images", "networks"]
    assert cloud.closed


def test_wrong_project_stops_before_compute():
    cloud = ReadOnlyCloud()
    report, code = discover("pegasus", "f"*32, cloud)
    assert code == 1
    assert report["failure"]["phase"] == "project_match"
    assert cloud.calls == []
    assert cloud.closed


def test_quota_mismatch_is_not_reported_as_success():
    cloud = ReadOnlyCloud()
    cloud.cores = 10
    report, code = discover("pegasus", PROJECT, cloud)
    assert code == 1
    assert report["checks"]["quotas"] == "mismatch"
    assert not report["quotas"]["vcpus"]["matches_expected"]


def test_api_failure_stops_and_does_not_expose_exception():
    cloud = ReadOnlyCloud()
    def fail(**kwargs):
        raise RuntimeError("SECRET TOKEN from SDK body")
    cloud.servers = fail
    report, code = discover("pegasus", PROJECT, cloud)
    assert code == 1
    assert report["failure"]["phase"] == "compute"
    assert "SECRET" not in str(report)
    assert cloud.calls == []
    assert cloud.closed


def test_discover_requires_explicit_expected_project():
    with pytest.raises(SystemExit):
        main(["--discover"])
