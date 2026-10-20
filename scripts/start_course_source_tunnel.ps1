# Local forwarding only. Does not change services/configuration on Moria.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Write-Host 'Keep this tunnel open: local 19090 -> existing school Prometheus via Moria.'
& ssh -i (Join-Path $env:USERPROFILE '.ssh/acit4410_key') `
    -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes `
    -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 `
    -o ServerAliveCountMax=3 -N -T -L '127.0.0.1:19090:192.168.1.179:9090' ubuntu@moriavm
exit $LASTEXITCODE
