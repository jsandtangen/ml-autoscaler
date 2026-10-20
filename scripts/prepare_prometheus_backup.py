"""Executed via SSH as root: prepare a non-disruptive, private backup staging tree.

No service configuration is changed. Only new backup files and a new TSDB
snapshot are created. Never print config contents or credential values.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, check=True, timeout=30)
    return result.stdout.strip()


def copy_owned(source, target):
    result = shutil.copy2(source, target)
    info = os.stat(source)
    os.chown(target, info.st_uid, info.st_gid)
    return result


def copy_tree(source, target, ignore=None):
    shutil.copytree(source, target, symlinks=True, copy_function=copy_owned, ignore=ignore)
    for directory, dirs, files in os.walk(source, followlinks=False):
        for name in [".", *dirs, *files]:
            original = Path(directory) / name
            relative = original.relative_to(source)
            copied = target / relative
            if copied.exists() or copied.is_symlink():
                info = original.lstat()
                os.chown(copied, info.st_uid, info.st_gid, follow_symlinks=False)


def mounts(service):
    ids = command("docker", "ps", "--filter", f"label=com.docker.swarm.service.name={service}", "--format", "{{.ID}}").splitlines()
    if len(ids) != 1:
        raise RuntimeError(f"Expected one running task for {service}")
    rows = json.loads(command("docker", "inspect", "--format", "{{json .Mounts}}", ids[0]))
    return {row["Destination"]: Path(row["Source"]) for row in rows}


def main(run_id):
    if os.geteuid() != 0 or not re.fullmatch(r"backup-\d{8}T\d{6}Z-[a-f0-9]{6}", run_id):
        raise ValueError("Root execution and a unique validated run ID are required")
    os.umask(0o077)
    base = Path("/var/backups/ruby-acorn")
    base.mkdir(mode=0o700, exist_ok=True)
    if base.is_symlink() or base.resolve() != base:
        raise ValueError("Unexpected staging base")
    stage = base / run_id
    stage.mkdir(mode=0o700)  # Refuse to reuse or overwrite an earlier backup.
    before = command("docker", "ps", "--filter", "label=com.docker.stack.namespace=prom", "--format", "{{.ID}} {{.Names}}")
    manifest = {"run_id": run_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_host": command("hostname"), "services_before": before.splitlines(),
                "consistency": {"prometheus": "TSDB snapshot including head", "grafana": "SQLite online backup; other assets copied live",
                                "alertmanager": "Persisted state copied live; not a cross-service atomic snapshot"}}
    prom = mounts("prom_prometheus")["/prometheus"]
    grafana = mounts("prom_grafana")["/var/lib/grafana"]
    alert = mounts("prom_alertmanager")["/alertmanager"]
    with urlopen(Request("http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot?skip_head=false", method="POST"), timeout=90) as response:
        snapshot = json.load(response)["data"]["name"]
    if not re.fullmatch(r"[A-Za-z0-9-]+", snapshot):
        raise ValueError("Unexpected snapshot name")
    snapshot_path = prom / "snapshots" / snapshot
    if snapshot_path.resolve().parent != (prom / "snapshots").resolve():
        raise ValueError("Unexpected snapshot location")
    manifest["prometheus_snapshot"] = str(snapshot_path)

    def link_or_copy(source, target):
        try:
            os.link(source, target)
            return target
        except OSError:
            return shutil.copy2(source, target)

    # Snapshot blocks are immutable. Do not chmod/chown hardlinked files.
    shutil.copytree(snapshot_path, stage / "prometheus-tsdb", copy_function=link_or_copy)
    excluded = {"grafana.db", "grafana.db-wal", "grafana.db-shm", "grafana.db-journal"}
    copy_tree(grafana, stage / "grafana-data", ignore=lambda directory, names: excluded.intersection(names) if Path(directory) == grafana else set())
    source_db = grafana / "grafana.db"
    target_db = stage / "grafana-data" / "grafana.db"
    started = time.monotonic()

    def progress(status, remaining, total):
        if time.monotonic() - started > 90:
            raise TimeoutError("SQLite online backup exceeded deadline")

    with sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True, timeout=30) as source:
        with sqlite3.connect(target_db) as target:
            source.backup(target, pages=256, progress=progress, sleep=0.1)
            if target.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("Copied SQLite database failed integrity check")
    info = source_db.stat()
    os.chown(target_db, info.st_uid, info.st_gid)
    os.chmod(target_db, stat.S_IMODE(info.st_mode))
    manifest["grafana_integrity_check"] = "ok"
    copy_tree(Path("/home/ubuntu/prometheus"), stage / "configuration-repository")
    copy_tree(alert, stage / "alertmanager-state")
    manifest["restore_images"] = {service: command("docker", "service", "inspect", "--format", "{{.Spec.TaskTemplate.ContainerSpec.Image}}", service)
                                  for service in ["prom_prometheus", "prom_grafana", "prom_alertmanager", "prom_node-exporter", "prom_cadvisor"]}
    manifest["services_after"] = command("docker", "ps", "--filter", "label=com.docker.stack.namespace=prom", "--format", "{{.ID}} {{.Names}}").splitlines()
    if manifest["services_after"] != manifest["services_before"]:
        raise RuntimeError("Service task identities changed during backup; inspect before continuing")
    manifest["files"] = {}
    for path in sorted(stage.rglob("*")):
        if stat.S_ISREG(path.lstat().st_mode):
            with path.open("rb") as file:
                digest = hashlib.file_digest(file, "sha256").hexdigest()
            manifest["files"][path.relative_to(stage).as_posix()] = {"sha256": digest, "bytes": path.stat().st_size}
    (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"run_id": run_id, "remote_stage": str(stage), "snapshot": snapshot,
                      "files": len(manifest["files"]), "bytes": sum(f["bytes"] for f in manifest["files"].values()),
                      "sqlite_integrity": "ok", "service_tasks_unchanged": True}), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
