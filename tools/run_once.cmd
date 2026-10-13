@echo off
rem Run the game once from build\, its output in build\run_once.txt.
call "%~dp0env.cmd" || exit /b 1
cd /d "%GOW3_ROOT%\build"
gow3.exe "%GOW3_GAME%\disc" > run_once.txt 2>&1
echo exitcode %errorlevel%
