@echo off
rem Build environment for the port's scripts: MSVC (any Visual Studio 2022 edition, found with vswhere), clang-cl and
rem ninja (scoop installs are added to PATH if present), and the folders, all relative to this repository:
rem   GOW3_ROOT  this repository
rem   GOW3_XENIA Xenia checkout   (default: next to the repository, ..\xenia)
rem   GOW3_GAME  the player's game files: disc\ (extracted ISO), default.xex, default.bin (default: ..\gow3-decomp)
for %%I in ("%~dp0..") do set "GOW3_ROOT=%%~fI"
if not defined GOW3_XENIA for %%I in ("%GOW3_ROOT%\..\xenia") do set "GOW3_XENIA=%%~fI"
if not defined GOW3_GAME for %%I in ("%GOW3_ROOT%\..\gow3-decomp") do set "GOW3_GAME=%%~fI"

if defined VCToolsInstallDir goto :tools
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (echo Visual Studio 2022 with "Desktop development with C++" is required & exit /b 1)
for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%I"
if not defined VSDIR (echo Visual Studio 2022 with "Desktop development with C++" is required & exit /b 1)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1

:tools
if exist "%USERPROFILE%\scoop\apps\llvm\current\bin" set "PATH=%USERPROFILE%\scoop\apps\llvm\current\bin;%PATH%"
if exist "%USERPROFILE%\scoop\apps\ninja\current" set "PATH=%USERPROFILE%\scoop\apps\ninja\current;%PATH%"
where clang-cl >nul 2>nul || (echo clang-cl not found: install LLVM ^(scoop install llvm, or the VS "C++ Clang tools" component^) & exit /b 1)
where ninja >nul 2>nul || (echo ninja not found: scoop install ninja & exit /b 1)
where cmake >nul 2>nul || (echo cmake not found & exit /b 1)
exit /b 0
