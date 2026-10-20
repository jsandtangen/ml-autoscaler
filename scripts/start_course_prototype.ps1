[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Once,
    [string]$GamesConfig = 'games.course.toml'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $repo
$python = Join-Path $repo 'data/pegasus-test-venv/Scripts/python.exe'
$cloudFile = Join-Path $env:LOCALAPPDATA 'RubyAcorn/openstack/clouds.yaml'
$testConfig = Join-Path $repo 'data/pegasus/one-vm-test.json'
$liveConfig = Join-Path $repo 'data/pegasus/course-pool.json'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $cloudFile)) {
    throw 'Install the documented SDK environment and external clouds.yaml first.'
}
# Keep the vm-test pool identity so the already owned test VM is rediscovered.
# The original one-VM receipt/configuration is preserved.
$pool = Get-Content -LiteralPath $testConfig -Raw | ConvertFrom-Json
if ($pool.project_id -ne '234f15222c5e4396851d2713697893b1' -or $pool.pool -ne 'vm-test') {
    throw 'Unexpected project/pool. Review configuration before running.'
}
$pool.maximum_instances = 6
if (Test-Path -LiteralPath $liveConfig) {
    $existing = Get-Content -LiteralPath $liveConfig -Raw | ConvertFrom-Json
    if (($existing | ConvertTo-Json -Compress) -ne ($pool | ConvertTo-Json -Compress)) {
        throw 'Existing course configuration differs. No overwrite performed.'
    }
} else {
    [IO.File]::WriteAllText($liveConfig, ($pool | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
}
$env:OS_CLIENT_CONFIG_FILE = $cloudFile
$arguments = @('main.py','--controller','pegasus','--pegasus-config',$liveConfig,
    '--games-config',$GamesConfig,'--prometheus-url','http://127.0.0.1:9090',
    '--metric-name','acit4410_player_count','--label','source=acit4410_replay',
    '--player-share','0.001','--players-per-instance','100','--headroom-fraction','0.10',
    '--minimum-instances','1','--max-instances','6','--max-scaling-change','2',
    '--metrics-host','127.0.0.1','--metrics-port','8003','--interval','30',
    '--fixed-baseline-instances','3','--vm-cost-per-hour','0.10',
    '--player-history-path','data/pegasus/course-player-history.csv',
    '--shadow-decisions-dir','data/pegasus/course-no-shadow')
if ($Apply) {
    Write-Host 'REAL PEGASUS CONTROL: use only after explicit approval for automatic scaling.'
    $arguments += '--pegasus-apply'
} else {
    Write-Host 'DRY-RUN: real VM state is read; no VM is created, started or stopped.'
}
if ($Once) { $arguments += '--once' }
& $python @arguments
exit $LASTEXITCODE
