# Scripted playtest that leaves the desktop alone: the game starts minimized, input comes from a script file played
# inside the runtime (GOW3_INPUT_SCRIPT), screenshots are taken from the guest output (GOW3_SCREENSHOTS).
# usage: powershell -File tools/playtest.ps1 -Script tests\load_and_look.txt -Seconds 180 -Shots "150,160"
param([string]$Script, [int]$Seconds = 120, [string]$Shots = "", [string]$Disc = "$PSScriptRoot/../../gow3-decomp/disc")
$env:GOW3_INPUT_SCRIPT = $Script
$env:GOW3_SCREENSHOTS = $Shots
Remove-Item -Recurse -Force "$PSScriptRoot\..\build\shots" -ErrorAction SilentlyContinue
$p = Start-Process -FilePath "$PSScriptRoot\..\build\gow3.exe" -ArgumentList "`"$Disc`"" -WorkingDirectory "$PSScriptRoot\..\build" -WindowStyle Minimized -PassThru
$start = Get-Date
while (((Get-Date) - $start).TotalSeconds -lt $Seconds -and -not $p.HasExited) { Start-Sleep -Milliseconds 500 }
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force; Write-Output "stopped after $Seconds s" } else { Write-Output ("exited " + $p.ExitCode) }
