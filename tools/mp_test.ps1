# System link playtest on one PC: two minimized gow3.exe, each with its own input script, settings and log, talking
# on loopback only (GOW3_NET_BIND: nothing is exposed to the network, no firewall prompt). The player's own saves,
# mods and settings are never touched: everything lives in build\mp (emptied first: fresh profiles).
# usage: powershell -File tools/mp_test.ps1 -HostScript tests/mp_host.txt -JoinScript tests/mp_join.txt -Seconds 240
#        -Shots "120,200" [-Connect lan|samepc|direct|code|server] [-JoinDelay 5]
# -Connect: how the second game finds the first (the first is the one that creates the party in its script):
#   lan     neither has a code: the first opens its game, the second finds it on the network (the default setup)
#   samepc  like lan, but both use one data folder, as two copies started from one install do (the second copy gets
#           its own profile: data\instance2)
#   direct  the second enters the first's address (127.0.0.1:port)
#   code    the second enters the first's join code (127.0.0.1 as a code)
#   server  both play on a gow3_server.exe started for the test (its log: build\mp\server.log)
#   hairpin the second enters the first's join code, but the code's address cannot be reached from here (as a public
#           address seen from inside the same network, which most routers do not loop back): the game must find the
#           first one on the network instead
#   unreachable  the second enters a code nobody answers at: it must say so, then join the game open on the network
# -HostData <folder>: the host starts with a copy of that data folder's saves and settings (content\,
# profile_settings.bin), e.g. build\testdata after tests/continue.txt made a checkpoint (tests/mp_host_coop.txt)
# -HostMods / -JoinMods "examples\mods\A,...": each game's mods (build\mp\mods_host, mods_join; default none);
# -ServerMods: the mods folder the server requires (--mods), with -Connect server.
# Folders: build\mp\host and build\mp\join (gow3.log, shots\, gow3.ini; data\ unless samepc: build\mp\data).
param([string]$HostScript, [string]$JoinScript = "", [int]$Seconds = 240, [string]$Shots = "", [int]$JoinDelay = 5,
      [string]$HostData = "", [int]$Port = 3174, [ValidateSet('lan', 'samepc', 'direct', 'code', 'server', 'hairpin', 'unreachable')][string]$Connect = 'lan', [string]$Disc = "",
      [string]$HostMods = "", [string]$JoinMods = "", [string]$ServerMods = "")
$build = (Resolve-Path "$PSScriptRoot\..\build").Path
if (-not $Disc) {
    $Disc = "$PSScriptRoot/../../gow3-decomp/disc"
    $local = Join-Path $PSScriptRoot 'local.cmd'
    if (Test-Path -LiteralPath $local) {
        foreach ($l in Get-Content -LiteralPath $local) { if ($l -match '^set "GOW3_GAME=(.*)"$') { $Disc = Join-Path $Matches[1] 'disc' } }
    }
}
$root = Join-Path $build 'mp'
Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
function New-ModsFolder([string]$name, [string]$list) {
    $dir = Join-Path $root $name
    New-Item -ItemType Directory -Force $dir | Out-Null
    foreach ($m in ($list -split ',' | Where-Object { $_.Trim() })) {
        $src = $m.Trim()
        if (-not [IO.Path]::IsPathRooted($src)) { $src = Join-Path "$PSScriptRoot\.." $src }
        Copy-Item -Recurse -Force -LiteralPath $src -Destination $dir
    }
    $dir
}
$modsFor = @{ host = (New-ModsFolder 'mods_host' $HostMods); join = (New-ModsFolder 'mods_join' $JoinMods) }

function Start-Player([string]$name, [string]$script, [string]$network, [string]$gamertag) {
    $dir = Join-Path $root $name
    New-Item -ItemType Directory -Force $dir | Out-Null
    $data = if ($Connect -eq 'samepc') { Join-Path $root 'data' } else { Join-Path $dir 'data' }
    New-Item -ItemType Directory -Force $data | Out-Null
    $ini = Join-Path $dir 'gow3.ini'
    "[display]`nskip_intro_movies = true`n[audio]`nenabled = false`n[network]`nonline = true`nport = $Port`nupnp = false`n$network" |
        Set-Content -Encoding ascii $ini
    if ($name -eq 'host' -and $HostData) {
        Copy-Item -Recurse -Force (Join-Path $HostData 'content') $data
        Copy-Item -Force (Join-Path $HostData 'profile_settings.bin') $data
    }
    $profile = Join-Path $data 'profile.ini'
    if (-not (Test-Path $profile)) { "[profile]`ngamertag = $gamertag" | Set-Content -Encoding ascii $profile }
    $env:GOW3_DATA_DIR = $data
    $env:GOW3_MODS_DIR = $modsFor[$name]
    $env:GOW3_SETTINGS = $ini
    $env:GOW3_NET_BIND = '127.0.0.1'
    $env:GOW3_INPUT_SCRIPT = if ($script) { (Resolve-Path $script).Path } else { '' }
    $env:GOW3_SCREENSHOTS = $Shots
    $env:GOW3_NET_FAKE_CODE = if ($name -eq 'host' -and $Connect -eq 'hairpin') { Get-JoinCode 0xC0000201L $Port } else { '' }
    Start-Process -FilePath "$build\gow3.exe" -ArgumentList "`"$Disc`"" -WorkingDirectory $dir -WindowStyle Minimized -PassThru
}

# a join code (net/code.h): address and port as 10 Crockford base32 characters
function Get-JoinCode([uint32]$ip, [uint16]$port) {
    $alphabet = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
    $v = ([uint64]$ip -shl 16) -bor $port
    $s = -join (9..0 | ForEach-Object { $alphabet[[int](($v -shr ($_ * 5)) -band 31)] })
    $s.Substring(0, 5) + '-' + $s.Substring(5)
}

$server = $null
$hostNet = ''
switch ($Connect) {
    'lan'    { $joinNet = '' }
    'samepc' { $joinNet = '' }
    'direct' { $joinNet = "join = 127.0.0.1:$Port" }
    'code'   { $joinNet = "join = $(Get-JoinCode 0x7F000001 $Port)" }
    'hairpin' { $joinNet = "join = $(Get-JoinCode 0xC0000201L $Port)" }       # 192.0.2.1 (TEST-NET-1): never answers
    'unreachable' { $joinNet = "join = $(Get-JoinCode 0xC0000202L $Port)" }   # 192.0.2.2, which the first game does not claim
    'server' {
        $server = Start-Process -FilePath "$build\gow3_server.exe" -ArgumentList "--port $Port --bind 127.0.0.1 --no-upnp --name TestServer --motd `"Test message`"$(if ($ServerMods) { ' --mods `"' + (New-ModsFolder 'mods_server' $ServerMods) + '`"' })" `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $root 'server.log')
        $hostNet = "server = 127.0.0.1:$Port"; $joinNet = "server = 127.0.0.1:$Port"
    }
}
Write-Output "connect: $Connect ($joinNet)"
$players = @(Start-Player 'host' $HostScript $hostNet 'HostTest')
if ($JoinScript) {
    Start-Sleep -Seconds $JoinDelay
    $players += Start-Player 'join' $JoinScript $joinNet 'JoinTest'
}
$start = Get-Date
while (((Get-Date) - $start).TotalSeconds -lt $Seconds -and ($players | Where-Object { -not $_.HasExited })) { Start-Sleep -Milliseconds 500 }
foreach ($p in $players) {
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force; Write-Output "player $($p.Id): stopped after $Seconds s" }
    else { Write-Output "player $($p.Id): exited $($p.ExitCode)" }
}
if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
