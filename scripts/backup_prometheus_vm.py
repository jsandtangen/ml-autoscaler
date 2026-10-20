"""Prepare and verify the explicitly approved backup; password entry stays local."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from urllib.request import urlopen
from uuid import uuid4


ROOT = Path("C:/Users/jsand/RubyAcornBackups/prometheus-vm/2026-10-20")


def ssh(remote):
    inner = ("ssh -i ~/.ssh/manager_key -o IdentitiesOnly=yes -o BatchMode=yes "
             "-o StrictHostKeyChecking=yes -o ConnectTimeout=10 -T ubuntu@192.168.1.179 " + remote)
    return ["ssh", "-i", str(Path.home() / ".ssh/acit4410_key"), "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "-T", "ubuntu@moriavm", inner]


def private_directory(path):
    path.mkdir()
    code = r'''$p = '%s'; $sid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User;
$acl=New-Object System.Security.AccessControl.DirectorySecurity;
$acl.SetAccessRuleProtection($true,$false);
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid,'FullControl','ContainerInherit,ObjectInherit','None','Allow')));
$system=New-Object System.Security.Principal.SecurityIdentifier('S-1-5-18');
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($system,'FullControl','ContainerInherit,ObjectInherit','None','Allow')));
Set-Acl -LiteralPath $p -AclObject $acl;''' % str(path).replace("'", "''")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", code], check=True)


def install_seven_zip(work):
    destination = Path(os.environ["LOCALAPPDATA"]) / "Programs/7-Zip-Console-26.04"
    existing = [Path("C:/Program Files/7-Zip/7z.exe"), Path(os.environ["LOCALAPPDATA"]) / "Programs/7-Zip/7z.exe", destination / "x64/7za.exe"]
    for path in existing:
        if path.is_file():
            return path
    with urlopen("https://api.github.com/repos/ip7z/7zip/releases/tags/26.04", timeout=30) as response:
        release = json.load(response)
    for name in ["7zr.exe", "7z2604-extra.7z"]:
        asset = next(a for a in release["assets"] if a["name"] == name)
        url = asset["browser_download_url"]
        if url != "https://github.com/ip7z/7zip/releases/download/26.04/" + name:
            raise ValueError("Unexpected software source")
        path = work / name
        with urlopen(url, timeout=60) as response, path.open("xb") as file:
            shutil.copyfileobj(response, file)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if asset.get("digest") != "sha256:" + digest:
            raise ValueError("Official release digest did not match")
    subprocess.run([str(work / "7zr.exe"), "x", str(work / "7z2604-extra.7z"), "-o" + str(destination), "-y"],
                   check=True, timeout=90, stdout=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    executable = destination / "x64/7za.exe"
    if not executable.is_file():
        raise RuntimeError("7-Zip executable missing after installation")
    subprocess.run([str(executable), "i"], check=True, stdout=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    return executable


def verify_archive(path):
    with tarfile.open(path, "r:gz") as archive:
        manifest = json.load(archive.extractfile("./manifest.json"))
        checked = set()
        for member in archive:
            name = member.name.removeprefix("./")
            if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                raise ValueError("Unsafe backup archive path")
            if (member.isfile() or member.islnk()) and name != "manifest.json":
                expected = manifest["files"][name]
                with archive.extractfile(member) as file:
                    digest = hashlib.file_digest(file, "sha256").hexdigest()
                if digest != expected["sha256"] or member.size not in (0, expected["bytes"]):
                    raise ValueError("Transferred backup content checksum failed")
                checked.add(name)
        if checked != set(manifest["files"]):
            raise ValueError("Incomplete backup archive")
    return len(checked)


def cleanup_verified(status_path):
    status_path = Path(status_path).resolve()
    if status_path.parent != ROOT.resolve():
        raise ValueError("Status must be in the explicitly approved backup directory")
    summary = json.loads(status_path.read_text(encoding="utf-8-sig"))
    run_id = summary["run_id"]
    if not re.fullmatch(r"backup-\d{8}T\d{6}Z-[a-f0-9]{6}", run_id) or summary["status"] != "encrypted_and_verified":
        raise ValueError("Encrypted archive must be verified before temporary cleanup")
    archive = Path(summary["archive_path"]).resolve()
    if archive != ROOT.resolve() / (run_id + ".7z"):
        raise ValueError("Unexpected encrypted archive location")
    with archive.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != summary["archive_sha256"]:
            raise ValueError("Encrypted archive checksum no longer matches")
    # Only directories created by this exact backup run may be removed.
    remote_script = '''import json,re,shutil,sys
from pathlib import Path
run_id=sys.argv[1]
if not re.fullmatch(r"backup-\\d{8}T\\d{6}Z-[a-f0-9]{6}",run_id):raise ValueError("Invalid run ID")
base=Path("/var/backups/ruby-acorn")
stage=base/run_id
if stage.is_symlink() or stage.resolve().parent!=base.resolve():raise ValueError("Unsafe staging path")
manifest=json.loads((stage/"manifest.json").read_text())
if manifest["run_id"]!=run_id:raise ValueError("Manifest mismatch")
snapshot=Path(manifest["prometheus_snapshot"])
snapshot_root=Path("/var/lib/docker/volumes/prom_prometheus_data/_data/snapshots")
if snapshot.is_symlink() or snapshot.resolve().parent!=snapshot_root.resolve() or not re.fullmatch(r"[A-Za-z0-9-]+",snapshot.name):raise ValueError("Unsafe snapshot path")
if not snapshot.is_dir() or not stage.is_dir():raise ValueError("Expected created backup directories")
shutil.rmtree(stage)
shutil.rmtree(snapshot)
print(json.dumps({"temporary_stage_removed":not stage.exists(),"created_snapshot_removed":not snapshot.exists()}))
'''
    result = subprocess.run(ssh("sudo -n python3 - " + run_id), input=remote_script.encode(), capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Temporary cleanup failed; inspect exact paths before retrying")
    summary["remote_cleanup"] = json.loads(result.stdout)
    status_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["remote_cleanup"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup-verified", type=Path,
                        help="Remove only artifacts created by a completed, encrypted and verified backup run.")
    args = parser.parse_args()
    if args.cleanup_verified:
        cleanup_verified(args.cleanup_verified)
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    run_id = "backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:6]
    work = ROOT / ("work-" + run_id)
    private_directory(work)
    executable = install_seven_zip(work)
    print("Official 7-Zip installed/verified; creating remote snapshots", flush=True)
    script = Path(__file__).with_name("prepare_prometheus_backup.py").read_bytes()
    result = subprocess.run(ssh("sudo -n nice -n 10 python3 - " + run_id), input=script,
                            capture_output=True, timeout=300)
    if result.returncode:
        raise RuntimeError("Remote backup preparation failed: " + result.stderr.decode(errors="replace")[-1200:])
    summary = json.loads(result.stdout)
    expected_remote = "/var/backups/ruby-acorn/" + run_id
    if summary["remote_stage"] != expected_remote:
        raise ValueError("Unexpected remote backup location")
    payload = work / "payload.tar.gz"
    print("Remote snapshot and SQLite integrity verified; transferring private archive", flush=True)
    with payload.open("xb") as file:
        result = subprocess.run(ssh("sudo -n nice -n 10 tar -C " + expected_remote + " -czf - ."),
                                stdout=file, stderr=subprocess.PIPE, timeout=900)
    if result.returncode:
        raise RuntimeError("Backup transfer failed; private staging retained")
    print("Transfer finished; verifying every regular file", flush=True)
    count = verify_archive(payload)
    with payload.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    summary.update(status="awaiting_local_password", payload_sha256=digest,
                   verified_files=count, seven_zip=str(executable), work_directory=str(work))
    (work / "prepared.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    helper = work / "finish-encryption.ps1"
    shutil.copyfile(Path(__file__).with_name("finish_backup_encryption.ps1"), helper)
    status_path = ROOT / (run_id + "-status.json")
    status_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    # Visible window is intentional: the user enters a password directly into
    # 7-Zip. It never reaches Python, PowerShell variables, logs or this chat.
    subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                    "Start-Process powershell.exe -WindowStyle Normal -ArgumentList "
                    "'-NoProfile -ExecutionPolicy Bypass -File \"" + str(helper) + "\"'"], check=True)


if __name__ == "__main__":
    main()
