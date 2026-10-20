import hashlib
import io
import json
import tarfile

import pytest

from scripts.backup_prometheus_vm import verify_archive
from scripts import backup_prometheus_vm


def archive(tmp_path, *, content=b"database-copy", expected=None, omit=False, name="grafana-data/grafana.db"):
    expected = content if expected is None else expected
    manifest = {"files": {name: {"sha256": hashlib.sha256(expected).hexdigest(), "bytes": len(expected)}}}
    path = tmp_path / "backup.tar.gz"
    with tarfile.open(path, "w:gz") as file:
        for entry, data in [("./manifest.json", json.dumps(manifest).encode()), *([] if omit else [("./" + name, content)])]:
            member = tarfile.TarInfo(entry)
            member.size = len(data)
            file.addfile(member, io.BytesIO(data))
    return path


def test_transferred_backup_is_checked_against_its_manifest(tmp_path):
    assert verify_archive(archive(tmp_path)) == 1


def test_changed_backup_content_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="checksum"):
        verify_archive(archive(tmp_path, expected=b"different-copy"))


def test_missing_backup_file_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Incomplete"):
        verify_archive(archive(tmp_path, omit=True))


def test_path_outside_backup_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unsafe"):
        verify_archive(archive(tmp_path, name="../outside"))


@pytest.mark.parametrize("status,digest", [("awaiting_local_password", None), ("encrypted_and_verified", "wrong")])
def test_temporary_cleanup_cannot_run_before_encryption_and_checksum_verification(tmp_path, monkeypatch, status, digest):
    monkeypatch.setattr(backup_prometheus_vm, "ROOT", tmp_path)
    monkeypatch.setattr(backup_prometheus_vm.subprocess, "run", lambda *a, **k: pytest.fail("Must not invoke SSH"))
    run_id = "backup-20261020T120000Z-abcdef"
    encrypted = tmp_path / (run_id + ".7z")
    encrypted.write_bytes(b"test archive")
    path = tmp_path / (run_id + "-status.json")
    path.write_text(json.dumps({"run_id": run_id, "status": status,
                                "archive_path": str(encrypted), "archive_sha256": digest}))
    with pytest.raises(ValueError):
        backup_prometheus_vm.cleanup_verified(path)


def test_cleanup_rejects_status_outside_approved_backup_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(backup_prometheus_vm, "ROOT", tmp_path / "approved")
    monkeypatch.setattr(backup_prometheus_vm.subprocess, "run", lambda *a, **k: pytest.fail("Must not invoke SSH"))
    with pytest.raises(ValueError, match="approved backup directory"):
        backup_prometheus_vm.cleanup_verified(tmp_path / "status.json")
