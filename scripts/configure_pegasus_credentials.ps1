# Interactive local setup only. Never supply credentials as command arguments.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$stage = 'directory'
$credentialId = $null
$credentialSecret = $null
$idPointer = [IntPtr]::Zero
$secretPointer = [IntPtr]::Zero
try {
    $configDirectory = Join-Path $env:LOCALAPPDATA 'RubyAcorn\openstack'
    $cloudPath = Join-Path $configDirectory 'clouds.yaml'
    if (Test-Path -LiteralPath $cloudPath) {
        Write-Host 'clouds.yaml already exists. No file was overwritten.'
        exit 1
    }
    New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
    $userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $stage = 'directory-permissions'
    $existingAcl = Get-Acl -LiteralPath $configDirectory
    $existingRules = @($existingAcl.Access)
    $alreadyProtected = $existingAcl.AreAccessRulesProtected -and $existingRules.Count -eq 1 -and
        $existingRules[0].IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value -eq $userSid.Value -and
        $existingRules[0].AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Allow -and
        ($existingRules[0].FileSystemRights -band [System.Security.AccessControl.FileSystemRights]::FullControl) -eq [System.Security.AccessControl.FileSystemRights]::FullControl
    # Reapplying Set-Acl can request owner privileges even when its DACL already
    # has the correct protection. Leave an already protected directory alone.
    if (-not $alreadyProtected) {
        $acl = New-Object System.Security.AccessControl.DirectorySecurity
        $acl.SetAccessRuleProtection($true, $false)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $userSid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
        Set-Acl -LiteralPath $configDirectory -AclObject $acl
    }

    Write-Host 'Pegasus setup: local files only. No cloud resources will be changed.'
    Write-Host 'Credential ID and Secret input are hidden. Do not paste them into chat.'
    $stage = 'input'
    $projectId = (Read-Host 'Project ID from Horizon (NOT Credential ID)').Trim()
    $parsedProject = [Guid]::Empty
    if (-not [Guid]::TryParse($projectId, [ref]$parsedProject)) {
        throw 'Invalid project ID'
    }
    $projectId = $parsedProject.ToString('N')
    $authUrl = Read-Host 'Identity auth URL [https://pegasus.sky.oslomet.no:5000/v3]'
    if ([string]::IsNullOrWhiteSpace($authUrl)) {
        $authUrl = 'https://pegasus.sky.oslomet.no:5000/v3'
    }
    $uri = [Uri]$authUrl
    if (-not $uri.IsAbsoluteUri -or $uri.Scheme -ne 'https' -or
        $uri.Host -ne 'pegasus.sky.oslomet.no' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment) {
        throw 'Expected official Pegasus HTTPS auth URL'
    }
    $region = (Read-Host 'Region from API Access (Enter if unknown)').Trim()
    $credentialId = Read-Host 'Application Credential ID (hidden)' -AsSecureString
    $credentialSecret = Read-Host 'Application Credential Secret (hidden)' -AsSecureString
    if ($credentialId.Length -eq 0 -or $credentialSecret.Length -eq 0) {
        throw 'Empty credential'
    }
    $idPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credentialId)
    $secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($credentialSecret)
    $idText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($idPointer)
    $secretText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer)
    $stage = 'save'
    # JSON-quoted scalar values are valid YAML and safely escape special characters.
    $lines = @(
        'clouds:',
        '  pegasus:',
        '    auth_type: v3applicationcredential',
        '    auth:',
        ('      auth_url: ' + (ConvertTo-Json -InputObject $authUrl -Compress)),
        ('      application_credential_id: ' + (ConvertTo-Json -InputObject $idText -Compress)),
        ('      application_credential_secret: ' + (ConvertTo-Json -InputObject $secretText -Compress)),
        '    interface: public',
        '    identity_api_version: 3',
        '    verify: true'
    )
    if ($region) {
        $lines += '    region_name: ' + (ConvertTo-Json -InputObject $region -Compress)
    }
    $encoding = New-Object System.Text.UTF8Encoding($false)
    # CreateNew prevents accidental replacement if another process saved a file.
    $file = [IO.File]::Open($cloudPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = $encoding.GetBytes(($lines -join "`n") + "`n")
        $file.Write($bytes, 0, $bytes.Length)
        $file.Flush($true)
    } finally {
        $file.Dispose()
    }
    [IO.File]::WriteAllText((Join-Path $configDirectory 'project-id.txt'), $projectId, $encoding)
    Write-Host 'Configuration saved with access restricted to your Windows user.'
    Write-Host $cloudPath
    Write-Host 'No credentials were printed. Return to chat and report that setup is complete.'
} catch {
    Write-Host ('Setup stopped at stage: ' + $stage + '. No cloud resources were changed.')
    Write-Host ('Error type: ' + $_.Exception.GetType().Name)
    Write-Host 'Do not send credentials or file contents to chat.'
} finally {
    if ($idPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($idPointer)
    }
    if ($secretPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer)
    }
    if ($credentialId) { $credentialId.Dispose() }
    if ($credentialSecret) { $credentialSecret.Dispose() }
    $idText = $null
    $secretText = $null
    $lines = $null
    if ($bytes) { [Array]::Clear($bytes, 0, $bytes.Length) }
    Read-Host 'Press Enter to close this window' | Out-Null
}
