# One-step setup of the Gears of War 3 PC port from the player's own disc image.
#
#   powershell -ExecutionPolicy Bypass -File tools\setup.ps1 -Iso "D:\path\to\Gears of War 3.iso"
#
# Needs: Visual Studio 2022 ("Desktop development with C++"), clang-cl, CMake, Ninja, Git for Windows (bash), Python 3
# with pycryptodome. Whatever is missing is listed and installed with winget when you agree (-InstallPrerequisites:
# without asking; one Visual Studio Build Tools install brings the compiler, Windows SDK, clang-cl, CMake and Ninja).
#   tools\setup.ps1 -CheckOnly        only report the missing tools
# Steps (each is skipped when already done, so it can be rerun after a failure):
#   1. extract the ISO to <Game>\disc and check default.xex is the version this port was made for
#   2. decrypt default.xex to <Game>\default.bin (analysis copy for the tools)
#   3. fetch + patch + build XenonRecomp (tools\XenonRecomp)        4. fetch + patch + build Xenia's GPU (<Xenia>)
#   5. recompile the game code into ppc\ and run the instruction tests  6. build build\gow3.exe   7. desktop shortcut
# No game files are part of this repository: everything game-derived is made here from the player's disc.
param(
    [string]$Iso = "",
    [string]$Game = "",           # default: gow3-decomp next to the repository
    [string]$Xenia = "",          # default: xenia next to the repository
    [switch]$NoShortcut,
    [switch]$InstallPrerequisites, # install missing tools with winget without asking
    [switch]$CheckOnly             # only report missing tools
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

# ---- prerequisites: found, or installed with winget (asked first, or -InstallPrerequisites) ----
function Find-Python {
    foreach ($c in @((Get-Command python -ErrorAction SilentlyContinue).Source, (Get-Command py -ErrorAction SilentlyContinue).Source)) {
        if ($c -and $c -notmatch 'WindowsApps') { return $c }     # (not the Microsoft Store alias, which only opens the Store)
    }
    foreach ($c in Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue) { return $c.FullName }
    return $null
}
function Find-Bash {
    foreach ($c in @("$env:ProgramFiles\Git\bin\bash.exe", "${env:ProgramFiles(x86)}\Git\bin\bash.exe", (Get-Command bash -ErrorAction SilentlyContinue).Source)) {
        if ($c -and (Test-Path -LiteralPath $c) -and $c -notmatch 'System32') { return $c }   # (System32\bash.exe is WSL)
    }
    return $null
}
function Find-VS {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path -LiteralPath $vswhere)) { return $null }
    $p = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($p) { return $p } else { return $null }
}
function Test-Tool($name, $vsSub) {
    if (Get-Command $name -ErrorAction SilentlyContinue) { return $true }
    foreach ($d in @("$env:USERPROFILE\scoop\apps\llvm\current\bin", "$env:USERPROFILE\scoop\apps\ninja\current", "$env:ProgramFiles\LLVM\bin")) {
        if (Test-Path -LiteralPath (Join-Path $d "$name.exe")) { return $true }
    }
    $vs = Find-VS
    if ($vs -and $vsSub -and (Test-Path -LiteralPath (Join-Path $vs "$vsSub\$name.exe"))) { return $true }
    return $false
}
$vsTools = 'VC\Tools\Llvm\x64\bin'
$vsCMake = 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin'
$vsNinja = 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja'
$pretend = "$env:GOW3_SETUP_PRETEND_MISSING".ToLower()    # (testing: python,git,vs,clang,cmake,ninja reported as missing)
$missing = @()
if (-not (Find-Python) -or $pretend -match 'python') { $missing += [pscustomobject]@{ What = 'Python 3'; Id = 'Python.Python.3.12'; Args = @() } }
if ($pretend -match 'git' -or -not (Find-Bash) -or (-not (Get-Command git -ErrorAction SilentlyContinue) -and -not (Test-Path "$env:ProgramFiles\Git\cmd\git.exe"))) {
    $missing += [pscustomobject]@{ What = 'Git for Windows'; Id = 'Git.Git'; Args = @() }
}
$needVS = -not (Find-VS) -or $pretend -match 'vs'
$needClang = -not (Test-Tool 'clang-cl' $vsTools) -or $pretend -match 'clang'
$needCMake = -not (Test-Tool 'cmake' $vsCMake) -or $pretend -match 'cmake'
$needNinja = -not (Test-Tool 'ninja' $vsNinja) -or $pretend -match 'ninja'
if ($needVS -or $needClang -or $needCMake -or $needNinja) {
    # one package brings all four: the Build Tools with the C++ workload, the Windows SDK, its Clang and CMake (+Ninja)
    $components = '--add Microsoft.VisualStudio.Workload.VCTools --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 ' +
                  '--add Microsoft.VisualStudio.Component.Windows11SDK.22621 --add Microsoft.VisualStudio.Component.VC.Llvm.Clang ' +
                  '--add Microsoft.VisualStudio.Component.VC.CMake.Project'
    if ($needVS) {
        $missing += [pscustomobject]@{ What = 'Visual Studio 2022 Build Tools (C++, Windows SDK, Clang, CMake, Ninja)'; Id = 'Microsoft.VisualStudio.2022.BuildTools';
                                       Args = @('--override', "--quiet --wait --norestart $components") }
    } else {
        $what = (@(@('clang-cl', $needClang), @('CMake', $needCMake), @('Ninja', $needNinja)) | Where-Object { $_[1] } | ForEach-Object { $_[0] }) -join ', '
        $missing += [pscustomobject]@{ What = "$what (with the Visual Studio 2022 Build Tools)"; Id = 'Microsoft.VisualStudio.2022.BuildTools';
                                       Args = @('--override', "--quiet --wait --norestart $components") }
    }
}
if ($missing.Count -gt 0) {
    Write-Host "Missing to build the port:" -ForegroundColor Yellow
    $missing | ForEach-Object { Write-Host "  - $($_.What)   (winget: $($_.Id))" }
    if ($CheckOnly) { exit 1 }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { Fail "install them (winget is not available here), then run the setup again" }
    $go = $InstallPrerequisites
    if (-not $go) { $go = (Read-Host "Install them now with winget? (Y/n)") -notmatch '^[nN]' }
    if (-not $go) { Fail "install the missing tools, then run the setup again" }
    foreach ($m in $missing) {
        Write-Host "installing $($m.What)..." -ForegroundColor Cyan
        & winget install --id $m.Id -e --accept-source-agreements --accept-package-agreements @($m.Args)
        if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1978335189) { Fail "winget could not install $($m.Id) (exit $LASTEXITCODE)" }   # (-1978335189: already installed)
    }
    # new installs are on the machine PATH, not yet in this session's
    $env:PATH = [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User')
} elseif ($CheckOnly) { Write-Host "All prerequisites found." -ForegroundColor Green; exit 0 }

$python = Find-Python
if (-not $python) { Fail "Python 3 not found (https://www.python.org, or: winget install Python.Python.3.12)" }
& $python -c "import Crypto" 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host "installing pycryptodome"; Run $python @('-m', 'pip', 'install', '--user', 'pycryptodome') }
$bash = (Get-Command bash -ErrorAction SilentlyContinue).Source
foreach ($c in @("$env:ProgramFiles\Git\bin\bash.exe", "${env:ProgramFiles(x86)}\Git\bin\bash.exe")) { if (-not $bash -and (Test-Path -LiteralPath $c)) { $bash = $c } }
if (-not $bash) { Fail "Git for Windows not found (bash is needed: winget install Git.Git)" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "git not found (winget install Git.Git)" }
cmd /c "`"$Root\tools\env.cmd`""
if ($LASTEXITCODE -ne 0) { Fail "build tools missing (see the message above)" }
if (-not $Iso) { Fail "give your Gears of War 3 disc image: tools\setup.ps1 -Iso <path to the .iso>" }
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
