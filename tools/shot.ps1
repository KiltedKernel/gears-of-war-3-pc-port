# Run the game for a while: inject input on a schedule and save screenshots of its window (dev tool).
# usage: powershell -File tools/shot.ps1 -Seconds 60 -Shots "20,40,55" -Keys "50:0D,62:57:2000" -Mouse "70:300:0" -Out shots
#   -Keys  "t:vk[:holdMs]"   at t s press virtual key vk (hex), held holdMs (default 250)
#   -Mouse "t:dx:dy"         at t s move the mouse by dx,dy pixels (in 20 steps over 200 ms); "t:L[:holdMs]" / "t:R[:holdMs]" click
# Safety: input is only sent while the game's own window is in the foreground; otherwise the event is skipped and
# logged (never type into a system dialog).
param([int]$Seconds = 60, [string]$Shots = "30,55", [string]$Out = "$PSScriptRoot\..\shots", [string]$Disc = "$PSScriptRoot/../../gow3-decomp/disc",
      [string]$Keys = "", [string]$Mouse = "")
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr l);
  [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out int pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern void mouse_event(uint flags, int dx, int dy, uint data, UIntPtr extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static IntPtr Find(int pid) {
    IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => { int p; GetWindowThreadProcessId(h, out p);
      if (p == pid && IsWindowVisible(h)) { found = h; return false; } return true; }, IntPtr.Zero);
    return found;
  }
  public static bool ForegroundIs(int pid) { int p; GetWindowThreadProcessId(GetForegroundWindow(), out p); return p == pid; }
}
"@
[W]::SetProcessDPIAware() | Out-Null   # real pixel coordinates for GetWindowRect / CopyFromScreen
New-Item -ItemType Directory -Force $Out | Out-Null
$events = @()
foreach ($t in $Shots.Split(',')) { if ($t) { $events += [pscustomobject]@{ T = [double]$t; Kind = 'shot'; A = $null } } }
foreach ($k in $Keys.Split(',')) { if ($k) { $events += [pscustomobject]@{ T = [double]($k.Split(':')[0]); Kind = 'key'; A = $k.Split(':') } } }
foreach ($m in $Mouse.Split(',')) { if ($m) { $events += [pscustomobject]@{ T = [double]($m.Split(':')[0]); Kind = 'mouse'; A = $m.Split(':') } } }
$events = $events | Sort-Object T
$p = Start-Process -FilePath "$PSScriptRoot\..\build\gow3.exe" -ArgumentList "`"$Disc`"" -WorkingDirectory "$PSScriptRoot\..\build" -PassThru
$start = Get-Date
foreach ($e in $events) {
  $t = $e.T
  while (((Get-Date) - $start).TotalSeconds -lt $t -and -not $p.HasExited) { Start-Sleep -Milliseconds 50 }
  if ($p.HasExited) { Write-Output ("exited early: " + $p.ExitCode); break }
  $h = [W]::Find($p.Id)
  if ($h -eq [IntPtr]::Zero) { Write-Output "no window at $t s"; continue }
  if ($e.Kind -ne 'shot') {
    if (-not [W]::ForegroundIs($p.Id)) { [W]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 200 }
    if (-not [W]::ForegroundIs($p.Id)) { Write-Output "skipped input at $t s: another window is in the foreground"; continue }
  }
  if ($e.Kind -eq 'key') {
    $vk = [Convert]::ToByte($e.A[1], 16); $hold = if ($e.A.Count -gt 2) { [int]$e.A[2] } else { 250 }
    [W]::keybd_event($vk, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds $hold; [W]::keybd_event($vk, 0, 2, [UIntPtr]::Zero)
    Write-Output ("key {0:X2} ({1} ms) at $t s" -f $vk, $hold); continue
  }
  if ($e.Kind -eq 'mouse') {
    if ($e.A[1] -eq 'L' -or $e.A[1] -eq 'R') {
      $down, $up = if ($e.A[1] -eq 'L') { 0x2, 0x4 } else { 0x8, 0x10 }
      $hold = if ($e.A.Count -gt 2) { [int]$e.A[2] } else { 100 }
      $r = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$r) | Out-Null
      if ($e.A[1] -eq 'L') { [W]::SetCursorPos([int](($r.L + $r.R) / 2), [int](($r.T + $r.B) / 2)) | Out-Null }
      [W]::mouse_event($down, 0, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds $hold; [W]::mouse_event($up, 0, 0, 0, [UIntPtr]::Zero)
      Write-Output ("mouse {0} ({1} ms) at $t s" -f $e.A[1], $hold); continue
    }
    $dx = [int]$e.A[1]; $dy = [int]$e.A[2]
    for ($i = 0; $i -lt 20; $i++) { [W]::mouse_event(0x1, [int]($dx / 20), [int]($dy / 20), 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 10 }
    Write-Output "mouse move $dx,$dy at $t s"; continue
  }
  [W]::SetForegroundWindow($h) | Out-Null; Start-Sleep -Milliseconds 200
  $r = New-Object W+RECT; [W]::GetWindowRect($h, [ref]$r) | Out-Null
  $w = $r.R - $r.L; $hgt = $r.B - $r.T
  $bmp = New-Object System.Drawing.Bitmap $w, $hgt
  $g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($r.L, $r.T, 0, 0, $bmp.Size)
  $file = Join-Path $Out ("shot_{0:D3}s.png" -f [int]$t); $bmp.Save($file); $g.Dispose(); $bmp.Dispose()
  Write-Output "saved $file ($w x $hgt)"
}
while (((Get-Date) - $start).TotalSeconds -lt $Seconds -and -not $p.HasExited) { Start-Sleep -Milliseconds 500 }
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force; Write-Output "stopped after $Seconds s" } else { Write-Output ("exited " + $p.ExitCode) }
