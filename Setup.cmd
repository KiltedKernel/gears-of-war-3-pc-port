@echo off
rem Gears of War 3 PC port: the installation wizard (your disc image to the PC port; tools\setup_gui.ps1, around tools\setup.ps1).
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\setup_gui.ps1" %*
