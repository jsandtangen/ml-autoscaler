"""Exercise local setup with synthetic values, never the user's credential file."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows NTFS credential setup")
def test_setup_hides_input_quotes_yaml_and_restricts_file_acl(tmp_path):
    yaml = pytest.importorskip("yaml")
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(tmp_path)
    env["RUBY_ACORN_SETUP_SCRIPT"] = str(Path("scripts/configure_pegasus_credentials.ps1").resolve())
    command = r'''
$script:setupAnswers = @('00000000000000000000000000000001', '', '', 'test-id', 'test-secret:"\value', '')
$script:setupAnswerIndex = 0
function Read-Host {
    param([string]$Prompt, [switch]$AsSecureString)
    $answer = $script:setupAnswers[$script:setupAnswerIndex]
    $script:setupAnswerIndex += 1
    if ($AsSecureString) { return ConvertTo-SecureString -String $answer -AsPlainText -Force }
    return $answer
}
. $env:RUBY_ACORN_SETUP_SCRIPT
$cloudPath = Join-Path $env:LOCALAPPDATA 'RubyAcorn/openstack/clouds.yaml'
$fileAcl = Get-Acl -LiteralPath $cloudPath
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$valid = @($fileAcl.Access | Where-Object { $_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -ne $userSid }).Count -eq 0
Write-Output ('ACL_CURRENT_USER_ONLY=' + $valid)
'''
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                            env=env, capture_output=True, text=True, timeout=25)
    assert result.returncode == 0
    assert "ACL_CURRENT_USER_ONLY=True" in result.stdout
    assert "test-id" not in result.stdout + result.stderr
    assert "test-secret" not in result.stdout + result.stderr
    path = tmp_path/"RubyAcorn/openstack/clouds.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config["clouds"]["pegasus"]["auth"]["application_credential_secret"] == 'test-secret:"\\value'
    assert config["clouds"]["pegasus"]["verify"] is True
    assert config["clouds"]["pegasus"]["auth_type"] == "v3applicationcredential"
    # Simulate retry after a directory-only setup. Its existing user-only ACL
    # must not require another privileged Set-Acl operation.
    path.unlink()
    retried = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command],
                             env=env, capture_output=True, text=True, timeout=25)
    assert retried.returncode == 0
    assert path.is_file()
    assert "Setup stopped" not in retried.stdout
