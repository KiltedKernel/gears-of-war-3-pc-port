# One-step setup of the Gears of War 3 PC port from the player's own disc image.
#
#   powershell -ExecutionPolicy Bypass -File tools\setup.ps1 -Iso "D:\path\to\Gears of War 3.iso"
#
# Needs: Visual Studio 2022 ("Desktop development with C++"), LLVM (clang-cl), CMake, Ninja, Git for Windows (bash),
# Python 3 with pycryptodome. Steps (each is skipped when already done, so it can be rerun after a failure):
#   1. extract the ISO to <Game>\disc and check default.xex is the version this port was made for
#   2. decrypt default.xex to <Game>\default.bin (analysis copy for the tools)
#   3. fetch + patch + build XenonRecomp (tools\XenonRecomp)        4. fetch + patch + build Xenia's GPU (<Xenia>)
#   5. recompile the game code into ppc\ and run the instruction tests  6. build build\gow3.exe   7. desktop shortcut
# No game files are part of this repository: everything game-derived is made here from the player's disc.
param(
    [Parameter(Mandatory = $true)][string]$Iso,
    [string]$Game = "",           # default: gow3-decomp next to the repository
    [string]$Xenia = "",          # default: xenia next to the repository
    [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Game) { $Game = [IO.Path]::GetFullPath((Join-Path $Root '..\gow3-decomp')) }
if (-not $Xenia) { $Xenia = [IO.Path]::GetFullPath((Join-Path $Root '..\xenia')) }
$env:GOW3_GAME = $Game
$env:GOW3_XENIA = $Xenia

function Step($n, $text) { Write-Host ""; Write-Host "[$n/7] $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "setup failed: $text" -ForegroundColor Red; exit 1 }
function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { Fail "$exe $($arguments -join ' ') (exit $LASTEXITCODE)" }
}

# ---- prerequisites ----
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { Fail "Python 3 not found (https://www.python.org, or: winget install Python.Python.3.12)" }
& $python -c "import Crypto" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "installing pycryptodome"; Run $python @('-m', 'pip', 'install', '--user', 'pycryptodome') }
$bash = (Get-Command bash -ErrorAction SilentlyContinue).Source
foreach ($c in @("$env:ProgramFiles\Git\bin\bash.exe", "${env:ProgramFiles(x86)}\Git\bin\bash.exe")) { if (-not $bash -and (Test-Path -LiteralPath $c)) { $bash = $c } }
if (-not $bash) { Fail "Git for Windows not found (bash is needed: winget install Git.Git)" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git not found (winget install Git.Git)" }
cmd /c "`"$Root\tools\env.cmd`""
if ($LASTEXITCODE -ne 0) { Fail "build tools missing (see the message above)" }
if (-not (Test-Path -LiteralPath $Iso)) { Fail "ISO not found: $Iso" }

# ---- 1. disc ----
Step 1 "extracting the disc to $Game\disc"
New-Item -ItemType Directory -Force "$Game\disc" | Out-Null
Run $python @("$Root\tools\xdvdfs.py", $Iso, 'extract-all', "$Game\disc")
if (-not (Test-Path -LiteralPath "$Game\disc\default.xex")) { Fail "no default.xex on the disc: is this the Gears of War 3 game disc?" }
$want = (Get-Content "$Root\tools\default_xex.sha256" -Raw).Trim().ToLower()
$have = (Get-FileHash -LiteralPath "$Game\disc\default.xex" -Algorithm SHA256).Hash.ToLower()
if ($have -ne $want) {
    Fail "default.xex is a different version (SHA-256 $have). The port's function list, jump tables and patches were made for $want."
}
Copy-Item -LiteralPath "$Game\disc\default.xex" -Destination "$Game\default.xex" -Force

# ---- 2. decrypted image ----
Step 2 "decrypting default.xex"
if (-not (Test-Path -LiteralPath "$Game\default.bin")) { Run $python @("$Root\tools\xex_unpack.py", "$Game\default.xex", "$Game\default.bin") }

# ---- 3. XenonRecomp ----
Step 3 "XenonRecomp (tools\XenonRecomp)"
Run $bash @("$Root/tools/setup_xenonrecomp.sh")
Run cmd @('/c', "`"$Root\tools\build_xenon.cmd`"")

# ---- 4. Xenia ----
Step 4 "Xenia GPU and audio ($Xenia) - the first build takes a while"
Run $bash @("$Root/tools/setup_xenia.sh")
Run cmd @('/c', "`"$Root\tools\build_xenia.cmd`"")

# ---- 5. recompile ----
Step 5 "recompiling the game code (ppc\)"
Run $bash @("$Root/tools/recomp.sh")

# ---- 6. build ----
Step 6 "building build\gow3.exe - the first build takes a while"
Run cmd @('/c', "`"$Root\tools\build.cmd`"")
# keyboard/mouse button prompts: icons from Gears of War: Reloaded when it is installed (Steam), else drawn ones
Run $python @('-P', "$Root\tools\make_prompts.py", 'auto', "$Root\build\prompts")

# ---- 7. shortcut ----
Step 7 "shortcut"
if (-not $NoShortcut) {
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Gears of War 3.lnk'
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = "$Root\build\gow3.exe"
    $s.Arguments = "`"$Game\disc`""
    $s.WorkingDirectory = "$Root\build"
    $s.Description = 'Gears of War 3 (PC port)'
    $s.Save()
    Write-Host "created $lnk"
    # the launcher: options window (gow3.ini) + Play; forwards its arguments to gow3.exe
    $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Gears of War 3 Launcher.lnk'
    $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
    $s.TargetPath = "$Root\build\gow3_launcher.exe"
    $s.Arguments = "`"$Game\disc`""
    $s.WorkingDirectory = "$Root\build"
    $s.IconLocation = "$Root\build\gow3.exe,0"
    $s.Description = 'Gears of War 3 (PC port) - options and Play'
    $s.Save()
    Write-Host "created $lnk"
}
Write-Host ""
Write-Host "done: run build\gow3.exe (settings: build\gow3.ini, saves: %LOCALAPPDATA%\GoW3Recomp)" -ForegroundColor Green
