# The installation wizard: your Gears of War 3 disc image to the PC port, without a console.
#   Setup.cmd (repository root), or: powershell -ExecutionPolicy Bypass -File tools\setup_gui.ps1
# Pages: welcome, disc image, install location and shortcuts, build tools (tools\setup.ps1 -CheckOnly; missing ones are
# installed with winget when the player agrees), install (runs tools\setup.ps1 and follows it: the step lines "[n/7]"
# drive the progress bar, the rest goes to the log box and build\setup.log), finish. Cancel stops the setup; running
# the wizard again continues from the last finished step.
# Testing: GOW3_SETUP_GUI_CHILD=<script> runs that script instead of setup.ps1 (same arguments);
# GOW3_SETUP_GUI_SHOT=<folder> saves each page as <page>.bmp; GOW3_SETUP_GUI_AUTO=<iso> goes through every page at once
# and closes when done.
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
$Version = if (Test-Path -LiteralPath (Join-Path $Root 'VERSION')) { (Get-Content -LiteralPath (Join-Path $Root 'VERSION') -Raw).Trim() } else { 'dev' }
$Steps = 7
$NeedGB = 25

if (-not $Iso -and $Auto) { $Iso = $Auto }
if (-not $Iso -and -not $Shot -and (Test-Path -LiteralPath $StateFile)) { try { $Iso = (Get-Content -LiteralPath $StateFile -Raw).Trim() } catch {} }

# the folders setup.ps1 would pick (same order: a previous setup's tools\local.cmd, folders next to this one, inside it)
$saved = @{}
$localCmd = Join-Path $Root 'tools\local.cmd'
if (Test-Path -LiteralPath $localCmd) {
    foreach ($l in Get-Content -LiteralPath $localCmd) { if ($l -match '^set "(GOW3_\w+)=(.*)"$') { $saved[$Matches[1]] = $Matches[2] } }
}
function Default-Folder($key, $sibling, $inside) {
    if ($saved[$key]) { return $saved[$key] }
    $s = [IO.Path]::GetFullPath((Join-Path $Root $sibling))
    if (Test-Path -LiteralPath $s) { return $s }
    return [IO.Path]::GetFullPath((Join-Path $Root $inside))
}

# ---- window ----
$form = New-Object System.Windows.Forms.Form
$form.Text = "Gears of War 3 PC port $Version - Setup"
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$form.ClientSize = New-Object System.Drawing.Size(660, 500)
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.StartPosition = 'CenterScreen'
$form.BackColor = [System.Drawing.Color]::White
$exe = Join-Path $Root 'build\gow3.exe'
if (Test-Path -LiteralPath $exe) { try { $form.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($exe) } catch {} }

# header band: the page title
$band = New-Object System.Windows.Forms.Panel
$band.Location = New-Object System.Drawing.Point(0, 0); $band.Size = New-Object System.Drawing.Size(660, 64)
$band.BackColor = [System.Drawing.Color]::FromArgb(24, 24, 24)
$form.Controls.Add($band)
$brand = New-Object System.Windows.Forms.Label
$brand.Text = 'GEARS OF WAR 3'; $brand.ForeColor = [System.Drawing.Color]::White
$brand.Font = New-Object System.Drawing.Font('Segoe UI', [single]15, [System.Drawing.FontStyle]::Bold)
$brand.Location = New-Object System.Drawing.Point(18, 8); $brand.Size = New-Object System.Drawing.Size(400, 30)
$band.Controls.Add($brand)
$pageTitle = New-Object System.Windows.Forms.Label
$pageTitle.ForeColor = [System.Drawing.Color]::FromArgb(220, 50, 40)
$pageTitle.Font = New-Object System.Drawing.Font('Segoe UI', [single]10, [System.Drawing.FontStyle]::Bold)
$pageTitle.Location = New-Object System.Drawing.Point(20, 38); $pageTitle.Size = New-Object System.Drawing.Size(400, 20)
$band.Controls.Add($pageTitle)
$stepLabel = New-Object System.Windows.Forms.Label
$stepLabel.ForeColor = [System.Drawing.Color]::Gray; $stepLabel.TextAlign = 'MiddleRight'
$stepLabel.Location = New-Object System.Drawing.Point(440, 38); $stepLabel.Size = New-Object System.Drawing.Size(200, 20)
$band.Controls.Add($stepLabel)

# bottom: a rule and the navigation buttons
$rule = New-Object System.Windows.Forms.Label
$rule.BorderStyle = 'Fixed3D'; $rule.Location = New-Object System.Drawing.Point(0, 446); $rule.Size = New-Object System.Drawing.Size(660, 2)
$form.Controls.Add($rule)
function Button($text, $x, $w, $parent = $form, $y = 458) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text; $b.Location = New-Object System.Drawing.Point($x, $y); $b.Size = New-Object System.Drawing.Size($w, 28)
    $parent.Controls.Add($b); return $b
}
$back = Button '< Back' 336 96
$next = Button 'Next >' 436 110
$cancel = Button 'Cancel' 556 90

# ---- pages: one panel each ----
$pages = @()
function New-Page($title) {
    $p = New-Object System.Windows.Forms.Panel
    $p.Location = New-Object System.Drawing.Point(0, 64); $p.Size = New-Object System.Drawing.Size(660, 380); $p.Visible = $false
    $p.Tag = $title
    $form.Controls.Add($p); $script:pages += $p; return $p
}
function Text($page, $text, $x, $y, $w, $h, $size = 9, $bold = $false) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = New-Object System.Drawing.Point($x, $y); $l.Size = New-Object System.Drawing.Size($w, $h)
    if ($size -ne 9 -or $bold) { $l.Font = New-Object System.Drawing.Font('Segoe UI', [single]$size, [System.Drawing.FontStyle]$(if ($bold) { 'Bold' } else { 'Regular' })) }
    $page.Controls.Add($l); return $l
}
function Check($page, $text, $x, $y, $w, $checked) {
    $c = New-Object System.Windows.Forms.CheckBox
    $c.Text = $text; $c.Checked = $checked
    $c.Location = New-Object System.Drawing.Point($x, $y); $c.Size = New-Object System.Drawing.Size($w, 22)
    $page.Controls.Add($c); return $c
}

# 0. welcome
$pWelcome = New-Page 'Welcome'
Text $pWelcome "This wizard makes a native Windows version of Gears of War 3 from your own copy of the game." 24 20 610 22 10 | Out-Null
Text $pWelcome ("The Xbox 360 game code on your disc is translated to C++ and compiled on this PC, then built together with the " +
    "port's runtime and a launcher. No game files come with this project: you need a disc image (.iso) of your own " +
    "Gears of War 3 disc.") 24 52 610 54 | Out-Null
Text $pWelcome 'You need' 24 118 300 20 9 $true | Out-Null
Text $pWelcome ("-  a Gears of War 3 disc image (.iso) made from your disc`r`n" +
    "-  Windows 10 or 11 (64-bit), a Direct3D 12 graphics card, a Haswell / Zen or newer processor`r`n" +
    "-  about $NeedGB GB of free disk space and an internet connection (build tools and source code are downloaded)`r`n" +
    "-  about an hour the first time; most of it is compiling, and the wizard can be left running") 24 140 610 90 | Out-Null
Text $pWelcome ("Missing build tools (Visual Studio Build Tools, Python, Git) can be installed for you with winget. " +
    "This port is free software under the GPL-3.0 license; Gears of War is a trademark of Microsoft.") 24 244 610 40 | Out-Null
Text $pWelcome "Version $Version" 24 340 300 20 | Out-Null

# 1. disc image
$pDisc = New-Page 'Your disc image'
Text $pDisc 'Choose the disc image (.iso) of your Gears of War 3 game disc:' 24 24 600 20 | Out-Null
$isoBox = New-Object System.Windows.Forms.TextBox
$isoBox.Location = New-Object System.Drawing.Point(26, 50); $isoBox.Size = New-Object System.Drawing.Size(500, 24); $isoBox.Text = $Iso
$pDisc.Controls.Add($isoBox)
$browse = Button 'Browse...' 534 100 $pDisc 48
$isoInfo = Text $pDisc '' 24 86 610 60
Text $pDisc ("The setup checks that the game's executable on the disc is the version this port was made for, and stops with a " +
    "message if it is not.") 24 160 610 40 | Out-Null

# 2. location and shortcuts
$pWhere = New-Page 'Install location'
Text $pWhere ("The port is built in this folder:`r`n  $Root") 24 20 610 40 | Out-Null
Text $pWhere 'The game files from your disc (about 8 GB) go to:' 24 70 600 20 | Out-Null
$gameBox = New-Object System.Windows.Forms.TextBox
$gameBox.Location = New-Object System.Drawing.Point(26, 92); $gameBox.Size = New-Object System.Drawing.Size(500, 24)
$gameBox.Text = Default-Folder 'GOW3_GAME' '..\gow3-decomp' 'game'
$pWhere.Controls.Add($gameBox)
$gameBrowse = Button 'Browse...' 534 100 $pWhere 90
Text $pWhere 'The graphics emulation source (Xenia, built here) goes to:' 24 128 600 20 | Out-Null
$xeniaBox = New-Object System.Windows.Forms.TextBox
$xeniaBox.Location = New-Object System.Drawing.Point(26, 150); $xeniaBox.Size = New-Object System.Drawing.Size(500, 24)
$xeniaBox.Text = Default-Folder 'GOW3_XENIA' '..\xenia' 'xenia'
$pWhere.Controls.Add($xeniaBox)
$xeniaBrowse = Button 'Browse...' 534 100 $pWhere 148
$spaceInfo = Text $pWhere '' 24 186 610 20
$desktop = Check $pWhere 'Shortcuts on the desktop (game and launcher)' 26 222 500 $true
$startMenu = Check $pWhere 'Shortcuts in the Start menu' 26 248 500 $true
Text $pWhere 'Saves and settings caches live in %LOCALAPPDATA%\GoW3Recomp.' 24 290 610 20 | Out-Null

# 3. build tools
$pTools = New-Page 'Build tools'
Text $pTools ("The game code is compiled on this PC, which needs Visual Studio's C++ build tools (with clang-cl, CMake and " +
    "Ninja), Python 3 and Git for Windows.") 24 20 610 40 | Out-Null
$toolsInfo = Text $pTools 'Checking...' 24 70 610 110
$installTools = Check $pTools 'Install the missing tools now with winget (Windows may ask for permission; the Visual Studio Build Tools are a few GB)' 26 190 610 $true
$installTools.Size = New-Object System.Drawing.Size(610, 40)
$installTools.Visible = $false

# 4. install
$pRun = New-Page 'Installing'
$status = Text $pRun 'Starting...' 24 16 610 20 9 $true
$progress = New-Object System.Windows.Forms.ProgressBar
$progress.Location = New-Object System.Drawing.Point(26, 42); $progress.Size = New-Object System.Drawing.Size(606, 20); $progress.Maximum = $Steps * 10
$pRun.Controls.Add($progress)
$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true; $logBox.ReadOnly = $true; $logBox.ScrollBars = 'Vertical'; $logBox.WordWrap = $false
$logBox.Font = New-Object System.Drawing.Font('Consolas', [single]8.5)
$logBox.BackColor = [System.Drawing.Color]::FromArgb(245, 245, 245)
$logBox.Location = New-Object System.Drawing.Point(26, 72); $logBox.Size = New-Object System.Drawing.Size(606, 260)
$pRun.Controls.Add($logBox)
$openLog = Button 'Open log' 26 100 $pRun 340
Text $pRun 'The first run takes about an hour. Closing the wizard stops it; running it again continues.' 136 345 500 20 | Out-Null

# 5. finish
$pDone = New-Page 'Finished'
$doneText = Text $pDone '' 24 24 610 130 10
$launchNow = Check $pDone 'Start the launcher now (settings, mods and Play)' 26 170 500 $true

# ---- navigation ----
$script:page = 0
$script:phase = 'idle'        # tools check: idle -> check -> checked; install: run -> done / failed
$script:missing = @()
$script:toolsOk = $false
function Save-Shot($name) {
    if (-not $Shot) { return }
    $form.Refresh()
    $bmp = New-Object System.Drawing.Bitmap($form.Width, $form.Height)
    $form.DrawToBitmap($bmp, (New-Object System.Drawing.Rectangle(0, 0, $form.Width, $form.Height)))
    $bmp.Save((Join-Path $Shot "$name.bmp"), [System.Drawing.Imaging.ImageFormat]::Bmp); $bmp.Dispose()
}
function Update-Disc {
    $p = $isoBox.Text.Trim('"', ' ')
    if (-not $p) { $isoInfo.Text = ''; return $false }
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { $isoInfo.ForeColor = [System.Drawing.Color]::DarkRed; $isoInfo.Text = 'File not found.'; return $false }
    $gb = (Get-Item -LiteralPath $p).Length / 1GB
    $isoInfo.ForeColor = [System.Drawing.Color]::DarkGreen
    $isoInfo.Text = ('{0} - {1:N1} GB' -f (Split-Path $p -Leaf), $gb)
    if ($gb -lt 6) { $isoInfo.ForeColor = [System.Drawing.Color]::DarkOrange; $isoInfo.Text += "`r`nThat is small for a Gears of War 3 game disc (about 7-8 GB): is it the right image?" }
    return $true
}
function Update-Space {
    try {
        $drive = New-Object System.IO.DriveInfo ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($gameBox.Text)))
        $free = $drive.AvailableFreeSpace / 1GB
        $spaceInfo.Text = ('Free on {0} {1:N0} GB (about {2} GB needed in all)' -f $drive.Name, $free, $NeedGB)
        $spaceInfo.ForeColor = $(if ($free -lt $NeedGB) { [System.Drawing.Color]::DarkOrange } else { [System.Drawing.Color]::DarkGreen })
    } catch { $spaceInfo.Text = '' }
}
function Show-Page($n) {
    $script:page = $n
    for ($i = 0; $i -lt $pages.Count; $i++) { $pages[$i].Visible = ($i -eq $n) }
    $pageTitle.Text = $pages[$n].Tag
    $stepLabel.Text = "Step $($n + 1) of $($pages.Count)"
    $back.Enabled = $n -gt 0 -and $n -lt 4
    $next.Text = $(if ($n -eq 3) { 'Install' } elseif ($n -eq 5) { 'Finish' } else { 'Next >' })
    $cancel.Text = $(if ($n -eq 5) { 'Close' } else { 'Cancel' })
    switch ($n) {
        1 { $next.Enabled = Update-Disc }
        2 { Update-Space; $next.Enabled = $true }
        3 { $next.Enabled = $script:phase -eq 'checked' -and ($script:toolsOk -or $installTools.Checked); if ($script:phase -eq 'idle') { Start-Check } }
        4 { $next.Enabled = $false }
        default { $next.Enabled = $true }
    }
}

# ---- child process (setup.ps1), its output to a file the timer reads ----
$script:proc = $null; $script:out = $null; $script:read = 0; $script:lastLine = ''
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
        if ($cut -lt 0) { return @() }
        $script:read += [System.Text.Encoding]::Default.GetByteCount($text.Substring(0, $cut + 1))
        return $text.Substring(0, $cut).Split("`n") | ForEach-Object { $_.TrimEnd("`r") }
    } catch { return @() }
}
function Stop-Child {
    if ($script:proc -and -not $script:proc.HasExited) { & taskkill.exe /T /F /PID $script:proc.Id 2>&1 | Out-Null }   # this wizard's own setup and what it started
}
function Start-Check {
    $script:phase = 'check'
    $toolsInfo.Text = 'Checking the build tools...'
    Start-Child @('-CheckOnly') (Join-Path $env:TEMP "gow3_setup_check_$PID.txt")
}
function Start-Install {
    $iso = $isoBox.Text.Trim('"', ' ')
    try { New-Item -ItemType Directory -Force $StateDir | Out-Null; Set-Content -LiteralPath $StateFile -Value $iso } catch {}
    $arguments = @('-Iso', "`"$iso`"", '-Game', "`"$($gameBox.Text.TrimEnd('\'))`"", '-Xenia', "`"$($xeniaBox.Text.TrimEnd('\'))`"")
    if ($installTools.Checked) { $arguments += '-InstallPrerequisites' }
    if (-not $desktop.Checked) { $arguments += '-NoShortcut' }
    if ($startMenu.Checked) { $arguments += '-StartMenu' }
    $logBox.Clear(); $progress.Value = 0
    $status.Text = 'Starting...'
    $script:phase = 'run'
    Show-Page 4
    Start-Child $arguments $Log
}
function Finish-Page($ok, $text) {
    $doneText.Text = $text
    $doneText.ForeColor = $(if ($ok) { [System.Drawing.Color]::DarkGreen } else { [System.Drawing.Color]::DarkRed })
    $launchNow.Visible = $ok
    Show-Page 5
    $back.Enabled = -not $ok           # after a failure: back to the build tools page, to install again
    Save-Shot $(if ($ok) { 'page5_done' } else { 'page5_failed' })
}

$browse.Add_Click({
    $d = New-Object System.Windows.Forms.OpenFileDialog
    $d.Title = 'Your Gears of War 3 disc image'; $d.Filter = 'Disc images (*.iso)|*.iso|All files (*.*)|*.*'
    if ($d.ShowDialog($form) -eq 'OK') { $isoBox.Text = $d.FileName }
})
foreach ($pair in @(@($gameBrowse, $gameBox), @($xeniaBrowse, $xeniaBox))) {
    $box = $pair[1]
    $pair[0].Add_Click({
        $d = New-Object System.Windows.Forms.FolderBrowserDialog
        $d.Description = 'Choose a folder'; $d.ShowNewFolderButton = $true
        if ($d.ShowDialog() -eq 'OK') { $box.Text = $d.SelectedPath }
    }.GetNewClosure())
}
$isoBox.Add_TextChanged({ if ($script:page -eq 1) { $next.Enabled = Update-Disc } })
$gameBox.Add_TextChanged({ Update-Space })
$installTools.Add_CheckedChanged({ if ($script:page -eq 3) { $next.Enabled = $script:phase -eq 'checked' -and ($script:toolsOk -or $installTools.Checked) } })
$openLog.Add_Click({ if (Test-Path -LiteralPath $Log) { Start-Process notepad.exe -ArgumentList "`"$Log`"" } })
$back.Add_Click({
    if ($script:page -eq 5) { $script:phase = 'checked'; Show-Page 3 }
    elseif ($script:page -gt 0) { Show-Page ($script:page - 1) }
})
$next.Add_Click({
    switch ($script:page) {
        3 { Start-Install }
        5 {
            if ($launchNow.Checked -and $script:phase -eq 'done' -and -not $Auto) {
                Start-Process -FilePath (Join-Path $Root 'build\gow3_launcher.exe') -WorkingDirectory (Join-Path $Root 'build')
            }
            $form.Close()
        }
        default { Show-Page ($script:page + 1) }
    }
})
$cancel.Add_Click({
    if ($script:page -eq 5) { $form.Close(); return }
    if ($script:phase -eq 'run' -and $script:proc -and -not $script:proc.HasExited) {
        if ([System.Windows.Forms.MessageBox]::Show($form, 'Stop the setup? Running the wizard again continues from the last finished step.',
            'Cancel', 'YesNo', 'Question') -ne 'Yes') { return }
        Stop-Child
        $script:phase = 'failed'
        Finish-Page $false 'Stopped. Run Setup again to continue from the last finished step.'
        return
    }
    $form.Close()
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
    if ($script:phase -eq 'check') {
        if (-not $script:proc.HasExited) { return }
        $lines = @(Read-NewLines)
        $script:missing = @($lines | Where-Object { $_ -match '^\s+- (.+?)\s+\(winget' } | ForEach-Object { $Matches[1] })
        $script:toolsOk = $script:proc.ExitCode -eq 0
        if ($script:toolsOk) {
            $toolsInfo.Text = 'All build tools were found.'; $toolsInfo.ForeColor = [System.Drawing.Color]::DarkGreen
            $installTools.Visible = $false
        } elseif ($script:missing.Count -gt 0) {
            $toolsInfo.Text = "Missing:`r`n  - " + ($script:missing -join "`r`n  - ")
            $toolsInfo.ForeColor = [System.Drawing.Color]::DarkOrange
            $installTools.Visible = $true
        } else {
            $toolsInfo.Text = 'Could not check the build tools: ' + (($lines | Where-Object { $_ }) -join ' ')
            $toolsInfo.ForeColor = [System.Drawing.Color]::DarkRed
        }
        $script:phase = 'checked'
        if ($script:page -eq 3) { Show-Page 3 }
        Save-Shot 'page3_tools'
        if ($Auto) { $installTools.Checked = $true; Start-Install }
        return
    }
    if ($script:phase -ne 'run') { return }
    $lines = @(Read-NewLines)
    if ($lines) { $logBox.AppendText((($lines | Where-Object { $_ -ne $null }) -join "`r`n") + "`r`n") }
    foreach ($l in $lines) {
        if ($l -match '^\[(\d+)/(\d+)\]\s*(.*)$') {
            $n = [int]$Matches[1]
            $progress.Value = [Math]::Min($progress.Maximum, ($n - 1) * 10 + 1)
            $status.Text = "Step $n of ${Steps}: $($Matches[3])"
            if ($n -eq 3) { Save-Shot 'page4_install' }
        } elseif ($l.Trim()) { $script:lastLine = $l.Trim() }
    }
    if ($progress.Value % 10 -ne 0 -and $progress.Value % 10 -lt 9 -and (Get-Random -Maximum 40) -eq 0) { $progress.Value++ }
    if (-not $script:proc.HasExited) { return }
    $rest = @(Read-NewLines)
    if ($rest) { $logBox.AppendText(($rest -join "`r`n") + "`r`n") }
    $err = "$($script:out).err"
    if ((Test-Path -LiteralPath $err) -and (Get-Item -LiteralPath $err).Length -gt 0) { $logBox.AppendText((Get-Content -LiteralPath $err -Raw)) }
    if ($script:proc.ExitCode -eq 0) {
        $script:phase = 'done'
        $progress.Value = $progress.Maximum
        $where = @()
        if ($desktop.Checked) { $where += 'the desktop' }
        if ($startMenu.Checked) { $where += 'the Start menu' }
        $short = $(if ($where) { "Shortcuts are on $($where -join ' and '): Gears of War 3, and Gears of War 3 Launcher (settings, mods and Play)." } else { 'Run build\gow3_launcher.exe (settings, mods and Play) or build\gow3.exe.' })
        Finish-Page $true ("Gears of War 3 is ready.`r`n`r`n$short`r`n`r`nThe log of this setup is build\setup.log.")
    } else {
        $script:phase = 'failed'
        $why = (@($rest + $lines + @($script:lastLine)) | Where-Object { $_ -match 'setup failed' } | Select-Object -Last 1)
        if (-not $why) { $why = "The setup stopped (exit $($script:proc.ExitCode))." }
        Finish-Page $false ("$why`r`n`r`nThe full log is build\setup.log. Fix the cause and run Setup again: finished steps are skipped. " +
            "Back returns to the build tools page to try again now.")
    }
    if ($Auto) { $timer.Stop(); $form.Close() }
})

$form.Add_Shown({
    Show-Page 0
    $timer.Start()
    if ($Shot -or $Auto) {
        Save-Shot 'page0_welcome'
        Show-Page 1; Save-Shot 'page1_disc'
        Show-Page 2; Save-Shot 'page2_where'
        Show-Page 3      # starts the tools check; the timer saves page3_tools and, with _AUTO, installs
    }
})
[void]$form.ShowDialog()
if ($script:phase -eq 'failed') { exit 1 }
exit 0
