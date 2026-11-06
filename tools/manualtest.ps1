# A manual playtest of the trilogy chapters with the logging the converter work needs, then one zip to hand back.
# usage: powershell -File tools\manualtest.ps1 [-From fresh|s04] [-Mods <folder holding Gears1_Chapter1..3>] [-God]
#   fresh: a new profile, start from Campaign > Select Chapter; s04: Continue from chapter 1's S04 checkpoint
#   (build\scratch\snap_s04h). Its own saves and mods (build\manualdata, build\manualmods): the player's are untouched.
# In game, F8 writes "MARK n" to the log with Marcus's and Dom's positions: press it at anything worth a note.
# When the game closes, build\manualtest-<time>.zip holds the logs, the saves and the settings.
param([ValidateSet('fresh', 's04')][string]$From = 'fresh', [string]$Mods = '', [switch]$God, [string]$Disc = '')
$build = (Resolve-Path "$PSScriptRoot\..\build").Path
if (-not $Mods) { $Mods = Join-Path $build 'scratch\trilogy3' }
if (-not $Disc) {
    $Disc = "$PSScriptRoot/../../gow3-decomp/disc"
    $local = Join-Path $PSScriptRoot 'local.cmd'
    if (Test-Path -LiteralPath $local) {
        foreach ($l in Get-Content -LiteralPath $local) { if ($l -match '^set "GOW3_GAME=(.*)"$') { $Disc = Join-Path $Matches[1] 'disc' } }
    }
}
$data = Join-Path $build 'manualdata'
Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $data | Out-Null
if ($From -eq 's04') { robocopy (Join-Path $build 'scratch\snap_s04h') $data /MIR /NFL /NDL /NJH /NJS | Out-Null }
$modsDir = Join-Path $build 'manualmods'
Remove-Item -Recurse -Force $modsDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $modsDir | Out-Null
foreach ($m in Get-ChildItem -Directory -LiteralPath $Mods -Filter 'Gears1_Chapter*') { Copy-Item -Recurse -Force $m.FullName $modsDir }

$env:GOW3_DATA_DIR = $data
$env:GOW3_MODS_DIR = $modsDir
$env:GOW3_SETTINGS = Join-Path $data 'gow3.ini'
$env:GOW3_INPUT_SCRIPT = ''
$env:GOW3_SCREENSHOTS = ''
# the squad every 5 s (where they are, what they stand on, health), every Gears 1 Kismet op that runs, fatal errors
$env:GOW3_LOG_PROPS = 'GearPawn_COGMarcusG2MP:Location,Location+4,Location+8,Base@,Health;GearPawn_COGDomG2:Location,Location+4,Location+8,Base@,Health'
$env:GOW3_LOG_PROPS_MS = '5000'
$env:GOW3_KISMET_TRACE = 'GOW1_'
$env:GOW3_SET_PROPS = if ($God) { 'GearPC:bGodMode=1' } else { '' }

Get-ChildItem $build -Filter 'gow3*.log' | Remove-Item -Force -ErrorAction SilentlyContinue
$start = Get-Date
Write-Output "Starting Gears 3 ($From). F8 = log marker. Quit the game normally when done."
$p = Start-Process -FilePath "$build\gow3.exe" -ArgumentList "`"$Disc`"" -WorkingDirectory $build -PassThru
$p.WaitForExit()

$stamp = $start.ToString('yyyyMMdd-HHmm')
$stage = Join-Path $build "manualtest-$stamp"
New-Item -ItemType Directory -Force $stage | Out-Null
Get-ChildItem $build -Filter 'gow3*.log' | Copy-Item -Destination $stage
if (Test-Path (Join-Path $data 'content')) { Copy-Item -Recurse (Join-Path $data 'content') (Join-Path $stage 'saves') }
if (Test-Path $env:GOW3_SETTINGS) { Copy-Item $env:GOW3_SETTINGS $stage }
"from=$From god=$God started=$start ended=$(Get-Date) exit=$($p.ExitCode)" | Set-Content (Join-Path $stage 'session.txt')
$zip = "$stage.zip"
Compress-Archive -Path "$stage\*" -DestinationPath $zip -Force
Write-Output "Done: $zip (add your notes and screenshots to it, or next to it)"
