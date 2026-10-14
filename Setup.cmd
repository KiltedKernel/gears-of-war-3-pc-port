@echo off
rem Gears of War 3 PC port: the converter window (your disc image to the PC port). Same as tools\setup.ps1, with a window.
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0tools\setup_gui.ps1" %*
