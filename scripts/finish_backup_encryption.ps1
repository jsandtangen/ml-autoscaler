$ErrorActionPreference = 'Stop'
$Host.UI.RawUI.WindowTitle = 'Ruby Acorn backup - lokalt passord'
$work = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$prepared = Get-Content -LiteralPath (Join-Path $work 'prepared.json') -Raw | ConvertFrom-Json
$root = Split-Path -Parent $work
$statusPath = Join-Path $root ($prepared.run_id + '-status.json')
try {
    if ($prepared.run_id -notmatch '^backup-\d{8}T\d{6}Z-[a-f0-9]{6}$') { throw 'Ugyldig backup-ID' }
    if ((Split-Path -Leaf $work) -ne ('work-' + $prepared.run_id)) { throw 'Uventet arbeidsmappe' }
    if ($root -ne 'C:\Users\jsand\RubyAcornBackups\prometheus-vm\2026-10-20') { throw 'Uventet backupmal' }
    $payload = Join-Path $work 'payload.tar.gz'
    if ((Get-FileHash -LiteralPath $payload -Algorithm SHA256).Hash.ToLowerInvariant() -ne $prepared.payload_sha256) {
        throw 'Kontrollsummen til den kontrollerte backupen har endret seg'
    }
    $archive = Join-Path $root ($prepared.run_id + '.7z')
    if (Test-Path -LiteralPath $archive) { throw 'Arkivet finnes allerede; ingen overskriving utfores' }
    Write-Host 'Backupen er overfort og kontrollert. Lagre et langt, unikt passord i passordbehandleren din.'
    Write-Host 'Angi passordet direkte til 7-Zip nedenfor. Ikke send det i chatten.'
    & $prepared.seven_zip a -t7z -mx=0 -mhe=on -p $archive $payload
    if ($LASTEXITCODE -ne 0) { throw ('Kryptering feilet: ' + $LASTEXITCODE) }
    Write-Host 'Kontroller arkivet: angi det samme passordet en gang til.'
    & $prepared.seven_zip t -p $archive
    if ($LASTEXITCODE -ne 0) { throw ('Arkivkontroll feilet: ' + $LASTEXITCODE) }
    $hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $prepared.status = 'encrypted_and_verified'
    $prepared | Add-Member -NotePropertyName archive_path -NotePropertyValue $archive -Force
    $prepared | Add-Member -NotePropertyName archive_sha256 -NotePropertyValue $hash -Force
    $prepared | Add-Member -NotePropertyName archive_bytes -NotePropertyValue (Get-Item -LiteralPath $archive).Length -Force
    $prepared | Add-Member -NotePropertyName completed_at -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
    $prepared | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $statusPath -Encoding UTF8
    ($hash + '  ' + (Split-Path -Leaf $archive)) | Set-Content -LiteralPath ($archive + '.sha256') -Encoding ASCII
    # Delete only this newly created staging tree after verifying the final
    # encrypted archive. Check resolved absolute paths before recursive delete.
    $resolvedRoot = (Resolve-Path -LiteralPath $root).Path.TrimEnd('\') + '\'
    $resolvedWork = (Resolve-Path -LiteralPath $work).Path
    if (-not $resolvedWork.StartsWith($resolvedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Utrygg oppryddingssti' }
    if ((Split-Path -Leaf $resolvedWork) -ne ('work-' + $prepared.run_id)) { throw 'Arbeidsmappe-ID stemmer ikke' }
    Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    Write-Host 'FERDIG: Kryptert backup er kontrollert. Lokal ukryptert arbeidskopi er fjernet.'
    Write-Host $archive
    Write-Host 'Midlertidige backupfiler pa VM-en beholdes til sluttkontroll fra Codex.'
} catch {
    Write-Host ('STOPPET: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Arbeidskopien er beholdt. Ingen originaldata er slettet.'
}
Read-Host 'Trykk Enter for a lukke vinduet'
