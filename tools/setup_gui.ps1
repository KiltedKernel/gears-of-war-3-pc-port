# The converter window: your Gears of War 3 disc image to the PC port, without a console.
#   Setup.cmd (repository root), or: powershell -ExecutionPolicy Bypass -File tools\setup_gui.ps1
# It checks the build tools (tools\setup.ps1 -CheckOnly), offers to install the missing ones, then runs tools\setup.ps1
# and follows it: the step lines "[n/7] ..." drive the progress bar, the rest goes to the log box (also saved to
# build\setup.log). Closing the window or Cancel stops the setup; rerunning it skips the finished steps.
# Testing: GOW3_SETUP_GUI_CHILD=<script> runs that script instead of setup.ps1 (same arguments);
# GOW3_SETUP_GUI_SHOT=<folder> saves the window as BMPs (checked.bmp, running.bmp, done.bmp) and
# GOW3_SETUP_GUI_AUTO=<iso> converts at once and closes when done.
param([string]$Iso = "")
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Setup = if ($env:GOW3_SETUP_GUI_CHILD) { $env:GOW3_SETUP_GUI_CHILD } else { Join-Path $Root 'tools\setup.ps1' }
$Shot = $env:GOW3_SETUP_GUI_SHOT
$Auto = $env:GOW3_SETUP_GUI_AUTO
$StateDir = Join-Path $env:LOCALAPPDATA 'GoW3Recomp'
$StateFile = Join-Path $StateDir 'setup_gui.txt'      # the last disc image chosen
$Log = Join-Path $Root 'build\setup.log'
$Steps = 7

if (-not $Iso -and $Auto) { $Iso = $Auto }
if (-not $Iso -and (Test-Path -LiteralPath $StateFile)) { try { $Iso = (Get-Content -LiteralPath $StateFile -Raw).Trim() } catch {} }

# ---- window ----
$font = New-Object System.Drawing.Font('Segoe UI', 9)
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Gears of War 3 - PC port setup'
$form.Font = $font
$form.ClientSize = New-Object System.Drawing.Size(640, 560)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.BackColor = [System.Drawing.Color]::White
$exe = Join-Path $Root 'build\gow3.exe'
if (Test-Path -LiteralPath $exe) { try { $form.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($exe) } catch {} }

function Label($text, $x, $y, $w, $h, $size = 9, $bold = $false) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = New-Object System.Drawing.Point($x, $y); $l.Size = New-Object System.Drawing.Size($w, $h)
    if ($size -ne 9 -or $bold) { $l.Font = New-Object System.Drawing.Font('Segoe UI', [single]$size, [System.Drawing.FontStyle]$(if ($bold) { 'Bold' } else { 'Regular' })) }
    $form.Controls.Add($l); return $l
}
function Button($text, $x, $y, $w) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text; $b.Location = New-Object System.Drawing.Point($x, $y); $b.Size = New-Object System.Drawing.Size($w, 28)
    $form.Controls.Add($b); return $b
}

Label 'GEARS OF WAR 3' 20 14 600 34 18 $true | Out-Null
Label 'PC port setup: makes the game from your own disc image. No game files come with this project.' 22 50 600 20 | Out-Null

Label 'Your Gears of War 3 disc image (.iso):' 22 84 400 18 | Out-Null
$isoBox = New-Object System.Windows.Forms.TextBox
$isoBox.Location = New-Object System.Drawing.Point(24, 104); $isoBox.Size = New-Object System.Drawing.Size(498, 24)
$isoBox.Text = $Iso
$form.Controls.Add($isoBox)
$browse = Button 'Browse...' 530 102 90

$toolsLabel = Label 'Checking the build tools...' 22 140 600 56
$shortcuts = New-Object System.Windows.Forms.CheckBox
$shortcuts.Text = 'Create desktop shortcuts (game and launcher)'; $shortcuts.Checked = $true
$shortcuts.Location = New-Object System.Drawing.Point(24, 198); $shortcuts.Size = New-Object System.Drawing.Size(400, 22)
$form.Controls.Add($shortcuts)

$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Location = New-Object System.Drawing.Point(24, 230); $progress.Size = New-Object System.Drawing.Size(596, 20)
$progress.Maximum = $Steps * 10
$form.Controls.Add($progress)
$status = Label 'Takes about an hour the first time (most of it compiling). You can leave it running.' 22 254 600 20

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true; $logBox.ReadOnly = $true; $logBox.ScrollBars = 'Vertical'; $logBox.WordWrap = $false
$logBox.Font = New-Object System.Drawing.Font('Consolas', 8.5)
$logBox.BackColor = [System.Drawing.Color]::FromArgb(245, 245, 245)
$logBox.Location = New-Object System.Drawing.Point(24, 280); $logBox.Size = New-Object System.Drawing.Size(596, 220)
$form.Controls.Add($logBox)

$openLog = Button 'Open log' 24 516 90
$cancel = Button 'Cancel' 390 516 90
$convert = Button 'Convert' 490 516 130
$cancel.Enabled = $false
$convert.Enabled = $false
$form.AcceptButton = $convert

function Save-Shot($name) {
    if (-not $Shot) { return }
    $form.Refresh()
    $bmp = New-Object System.Drawing.Bitmap($form.Width, $form.Height)
    $form.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $form.Width, $form.Height)))
    $bmp.Save((Join-Path $Shot "$name.bmp"), [System.Drawing.Imaging.ImageFormat]::Bmp); $bmp.Dispose()
}

# ---- child process: setup.ps1, its output to a file the timer reads ----
$script:proc = $null
$script:out = $null
$script:read = 0
$script:phase = 'check'          # check -> ready -> run -> done / failed
$script:missing = @()
$script:lastLine = ''

function Start-Child([string[]]$arguments, $file) {
    New-Item -ItemType Directory -Force (Split-Path $file) | Out-Null
    Set-Content -LiteralPath $file -Value '' -Encoding UTF8
    $script:out = $file; $script:read = 0
    $all = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$Setup`"") + $arguments
    $script:proc = Start-Process -FilePath 'powershell.exe' -ArgumentList $all -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput $file -RedirectStandardError "$file.err" -PassThru
    $null = $script:proc.Handle   # (keeps the handle open: else ExitCode reads empty after the exit)
}
function Read-NewLines {
    if (-not $script:out -or -not (Test-Path -LiteralPath $script:out)) { return @() }
    try {
        $fs = [System.IO.File]::Open($script:out, 'Open', 'Read', 'ReadWrite')
        try {
            if ($fs.Length -le $script:read) { return @() }
            $fs.Seek($script:read, 'Begin') | Out-Null
            $buf = New-Object byte[] ($fs.Length - $script:read)
            $n = $fs.Read($buf, 0, $buf.Length)
        } finally { $fs.Close() }
        $text = [System.Text.Encoding]::Default.GetString($buf, 0, $n)
        $cut = $text.LastIndexOf("`n")
        if ($cut -lt 0) { return @() }                    # a line still being written
        $script:read += [System.Text.Encoding]::Default.GetByteCount($text.Substring(0, $cut + 1))
        return $text.Substring(0, $cut).Split("`n") | ForEach-Object { $_.TrimEnd("`r") }
    } catch { return @() }
}
function Add-Log($lines) {
    if (-not $lines) { return }
    $logBox.AppendText((($lines | Where-Object { $_ -ne $null }) -join "`r`n") + "`r`n")
}

function Update-Ready {
    $ok = $isoBox.Text -and (Test-Path -LiteralPath $isoBox.Text) -and $script:phase -eq 'ready'
    $convert.Enabled = [bool]$ok
}

$browse.Add_Click({
    $d = New-Object System.Windows.Forms.OpenFileDialog
    $d.Title = 'Your Gears of War 3 disc image'
    $d.Filter = 'Disc images (*.iso)|*.iso|All files (*.*)|*.*'
    if ($isoBox.Text -and (Test-Path -LiteralPath (Split-Path $isoBox.Text -ErrorAction SilentlyContinue))) { $d.InitialDirectory = Split-Path $isoBox.Text }
    if ($d.ShowDialog($form) -eq 'OK') { $isoBox.Text = $d.FileName }
})
$isoBox.Add_TextChanged({ Update-Ready })
$openLog.Add_Click({ if (Test-Path -LiteralPath $Log) { Start-Process notepad.exe -ArgumentList "`"$Log`"" } })

$convert.Add_Click({
    if ($script:phase -eq 'done') {
        Start-Process -FilePath (Join-Path $Root 'build\gow3_launcher.exe') -WorkingDirectory (Join-Path $Root 'build'); $form.Close(); return
    }
    $iso = $isoBox.Text
    if ($script:missing.Count -gt 0 -and -not $Auto) {
        $msg = "These will be installed with winget (Windows may ask for permission):`r`n`r`n  - " + ($script:missing -join "`r`n  - ") +
               "`r`n`r`nThe Visual Studio Build Tools download is a few GB. Continue?"
        if ([System.Windows.Forms.MessageBox]::Show($form, $msg, 'Install build tools', 'OKCancel', 'Question') -ne 'OK') { return }
    }
    try { New-Item -ItemType Directory -Force $StateDir | Out-Null; Set-Content -LiteralPath $StateFile -Value $iso } catch {}
    $arguments = @('-Iso', "`"$iso`"", '-InstallPrerequisites')
    if (-not $shortcuts.Checked) { $arguments += '-NoShortcut' }
    $logBox.Clear()
    $progress.Value = 0
    $status.Text = 'Starting...'
    $script:phase = 'run'
    $convert.Enabled = $false; $browse.Enabled = $false; $isoBox.Enabled = $false; $shortcuts.Enabled = $false
    $cancel.Enabled = $true
    Start-Child $arguments $Log
})

function Stop-Child {
    if ($script:proc -and -not $script:proc.HasExited) {
        & taskkill.exe /T /F /PID $script:proc.Id 2>&1 | Out-Null     # this window's own setup and what it started
    }
}
$cancel.Add_Click({
    if ([System.Windows.Forms.MessageBox]::Show($form, 'Stop the setup? Running it again continues from the last finished step.',
        'Cancel', 'YesNo', 'Question') -ne 'Yes') { return }
    Stop-Child
    $script:phase = 'failed'
    $status.Text = 'Stopped. Convert again to continue.'
})
$form.Add_FormClosing({
    if ($script:phase -eq 'run' -and $script:proc -and -not $script:proc.HasExited) {
        if (-not $Auto -and [System.Windows.Forms.MessageBox]::Show($form, 'The setup is still running. Stop it and close?', 'Close',
            'YesNo', 'Warning') -ne 'Yes') { $_.Cancel = $true; return }
        Stop-Child
    }
})

# ---- the timer: follows the child ----
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({
    $lines = @(Read-NewLines)
    if ($script:phase -eq 'check') {
        if (-not $script:proc.HasExited) { return }
        $lines += @(Read-NewLines)
        $script:missing = @($lines | Where-Object { $_ -match '^\s+- (.+?)\s+\(winget' } | ForEach-Object { $Matches[1] })
        if ($script:proc.ExitCode -eq 0) {
            $toolsLabel.Text = 'Build tools: all found.'
            $toolsLabel.ForeColor = [System.Drawing.Color]::DarkGreen
            $convert.Text = 'Convert'
        } elseif ($script:missing.Count -gt 0) {
            $toolsLabel.Text = "Build tools missing (installed with winget when you convert):`r`n  " + ($script:missing -join ', ')
            $toolsLabel.ForeColor = [System.Drawing.Color]::DarkOrange
            $convert.Text = 'Install + convert'
        } else {
            $toolsLabel.Text = 'Could not check the build tools: ' + (($lines | Where-Object { $_ }) -join ' ')
            $toolsLabel.ForeColor = [System.Drawing.Color]::DarkRed
        }
        $script:phase = 'ready'
        Update-Ready
        Save-Shot 'checked'
        if ($Auto) { $convert.Enabled = $true; $convert.PerformClick() }   # (testing: also with a missing image)
        return
    }
    if ($script:phase -ne 'run') { return }
    Add-Log $lines
    foreach ($l in $lines) {
        if ($l -match '^\[(\d+)/(\d+)\]\s*(.*)$') {
            $n = [int]$Matches[1]
            $progress.Value = [Math]::Min($progress.Maximum, ($n - 1) * 10 + 1)
            $status.Text = "Step $n of ${Steps}: $($Matches[3])"
            if ($n -eq 3) { Save-Shot 'running' }
        } elseif ($l.Trim()) { $script:lastLine = $l.Trim() }
    }
    # creep inside a step so long steps still show life (never past the step's share)
    if ($progress.Value % 10 -ne 0 -and $progress.Value % 10 -lt 9 -and (Get-Random -Maximum 40) -eq 0) { $progress.Value++ }
    if (-not $script:proc.HasExited) { return }
    Add-Log @(Read-NewLines)
    $err = "$($script:out).err"
    if ((Test-Path -LiteralPath $err) -and (Get-Item -LiteralPath $err).Length -gt 0) { Add-Log (Get-Content -LiteralPath $err) }
    $cancel.Enabled = $false
    if ($script:proc.ExitCode -eq 0) {
        $script:phase = 'done'
        $progress.Value = $progress.Maximum
        $status.Text = 'Done. Settings, mods and Play are in the launcher.'
        $status.ForeColor = [System.Drawing.Color]::DarkGreen
        $convert.Text = 'Open launcher'; $convert.Enabled = $true
        Save-Shot 'done'
        if ($Auto) { $timer.Stop(); $form.Close() }
    } else {
        $script:phase = 'failed'
        $why = ($lines + @($script:lastLine) | Where-Object { $_ -match 'setup failed' } | Select-Object -Last 1)
        $status.Text = if ($why) { $why } else { "The setup stopped (exit $($script:proc.ExitCode)). See the log; Convert again to continue." }
        $status.ForeColor = [System.Drawing.Color]::DarkRed
        $script:phase = 'ready'
        $convert.Text = 'Retry'; $browse.Enabled = $true; $isoBox.Enabled = $true; $shortcuts.Enabled = $true
        Update-Ready
        Save-Shot 'failed'
        if ($Auto) { $timer.Stop(); $form.Close() }
    }
})

$form.Add_Shown({
    $isoBox.SelectionStart = $isoBox.Text.Length; $isoBox.SelectionLength = 0
    Start-Child @('-CheckOnly') (Join-Path $env:TEMP "gow3_setup_check_$PID.txt")
    $timer.Start()
})
[void]$form.ShowDialog()
if ($script:phase -eq 'failed' -or ($script:proc -and $script:proc.HasExited -and $script:proc.ExitCode -ne 0 -and $script:phase -ne 'ready')) { exit 1 }
exit 0
