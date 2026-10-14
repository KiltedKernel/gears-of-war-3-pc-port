# Scripted playtest that leaves the desktop alone: the game starts minimized, input comes from a script file played
# inside the runtime (GOW3_INPUT_SCRIPT), screenshots are taken from the guest output (GOW3_SCREENSHOTS).
# usage: powershell -File tools/playtest.ps1 -Script tests\load_and_look.txt -Seconds 180 -Shots "150,160"
# Tests keep their own saves and profile (build\testdata; -Fresh empties it first) and their own mods (none, or the
# mod folders given with -Mods "examples\mods\A,examples\mods\B", copied to build\testmods), so the player's saves in
# %LOCALAPPDATA%\GoW3Recomp and mods in build\mods are never touched, and their own settings (build	estdata\gow3.ini:
# the defaults, or -Settings <ini>). -PlayerData uses the player's own saves, mods and settings (careful).
param([string]$Script, [int]$Seconds = 120, [string]$Shots = "", [string]$Disc = "",
      [string]$Mods = "", [string]$Settings = "", [switch]$Fresh, [switch]$PlayerData)
$build = (Resolve-Path "$PSScriptRoot\..\build").Path
if (-not $Disc) {   # the disc this install uses (tools\local.cmd, from tools\setup.ps1), else the old default
    $Disc = "$PSScriptRoot/../../gow3-decomp/disc"
    $local = Join-Path $PSScriptRoot 'local.cmd'
    if (Test-Path -LiteralPath $local) {
        foreach ($l in Get-Content -LiteralPath $local) { if ($l -match '^set "GOW3_GAME=(.*)"$') { $Disc = Join-Path $Matches[1] 'disc' } }
    }
}
$env:GOW3_INPUT_SCRIPT = $Script
$env:GOW3_SCREENSHOTS = $Shots
if (-not $PlayerData) {
    $data = Join-Path $build 'testdata'
    if ($Fresh) { Remove-Item -Recurse -Force $data -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force $data | Out-Null
    $env:GOW3_DATA_DIR = $data
    $modsDir = Join-Path $build 'testmods'
    Remove-Item -Recurse -Force $modsDir -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force $modsDir | Out-Null
    foreach ($m in ($Mods -split ',' | Where-Object { $_.Trim() })) {
        $src = $m.Trim()
        if (-not [IO.Path]::IsPathRooted($src)) { $src = Join-Path "$PSScriptRoot\.." $src }
        Copy-Item -Recurse -Force -LiteralPath $src -Destination $modsDir
    }
    $env:GOW3_MODS_DIR = $modsDir
    # settings: the defaults (the game writes them on first use), or -Settings <ini> (copied)
    $ini = Join-Path $data 'gow3.ini'
    if ($Settings) { Copy-Item -Force -LiteralPath $Settings -Destination $ini }
    elseif ($Fresh) { Remove-Item -Force $ini -ErrorAction SilentlyContinue }
    $env:GOW3_SETTINGS = $ini
}
Remove-Item -Recurse -Force "$build\shots" -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$build\gow3.exe" -ArgumentList "`"$Disc`"" -WorkingDirectory $build -WindowStyle Minimized -PassThru
$start = Get-Date
while (((Get-Date) - $start).TotalSeconds -lt $Seconds -and -not $p.HasExited) { Start-Sleep -Milliseconds 500 }
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force; Write-Output "stopped after $Seconds s" } else { Write-Output ("exited " + $p.ExitCode) }
