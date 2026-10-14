@echo off
rem Build environment for the port's scripts: MSVC (any Visual Studio 2022 edition, found with vswhere), clang-cl and
rem ninja (scoop installs are added to PATH if present), and the folders, all relative to this repository:
rem   GOW3_ROOT  this repository
rem   GOW3_XENIA Xenia checkout   (default: next to the repository, ..\xenia)
rem   GOW3_GAME  the player's game files: disc\ (extracted ISO), default.xex, default.bin (default: ..\gow3-decomp)
for %%I in ("%~dp0..") do set "GOW3_ROOT=%%~fI"
rem this install's folders, as tools\setup.ps1 chose them (tools\local.cmd), unless already set
if not defined GOW3_GAME if not defined GOW3_XENIA if exist "%~dp0local.cmd" call "%~dp0local.cmd"
if not defined GOW3_XENIA for %%I in ("%GOW3_ROOT%\..\xenia") do set "GOW3_XENIA=%%~fI"
if not defined GOW3_GAME for %%I in ("%GOW3_ROOT%\..\gow3-decomp") do set "GOW3_GAME=%%~fI"

if defined VCToolsInstallDir goto :tools
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (echo Visual Studio 2022 with "Desktop development with C++" is required & exit /b 1)
for /f "usebackq delims=" %%I in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%I"
if not defined VSDIR (echo Visual Studio 2022 with "Desktop development with C++" is required & exit /b 1)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul 2>nul || exit /b 1

:tools
if exist "%USERPROFILE%\scoop\apps\llvm\current\bin" set "PATH=%USERPROFILE%\scoop\apps\llvm\current\bin;%PATH%"
if exist "%USERPROFILE%\scoop\apps\ninja\current" set "PATH=%USERPROFILE%\scoop\apps\ninja\current;%PATH%"
if exist "%ProgramFiles%\LLVM\bin\clang-cl.exe" set "PATH=%ProgramFiles%\LLVM\bin;%PATH%"
rem Visual Studio's own clang-cl, CMake and Ninja (any installed instance: Build Tools or a full edition), when they are
rem not installed separately (tools\setup.ps1 installs the Build Tools with them)
if not exist "%VSWHERE%" set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
where clang-cl >nul 2>nul || for /f "usebackq delims=" %%I in (`"%VSWHERE%" -products * -requires Microsoft.VisualStudio.Component.VC.Llvm.Clang -property installationPath 2^>nul`) do if exist "%%I\VC\Tools\Llvm\x64\bin\clang-cl.exe" set "PATH=%%I\VC\Tools\Llvm\x64\bin;%PATH%"
where cmake >nul 2>nul || for /f "usebackq delims=" %%I in (`"%VSWHERE%" -products * -property installationPath 2^>nul`) do if exist "%%I\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe" set "PATH=%%I\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin;%PATH%"
where ninja >nul 2>nul || for /f "usebackq delims=" %%I in (`"%VSWHERE%" -products * -property installationPath 2^>nul`) do if exist "%%I\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe" set "PATH=%%I\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
where clang-cl >nul 2>nul || (echo clang-cl not found: install LLVM ^(scoop install llvm, or the VS "C++ Clang tools" component^) & exit /b 1)
where ninja >nul 2>nul || (echo ninja not found: scoop install ninja & exit /b 1)
where cmake >nul 2>nul || (echo cmake not found & exit /b 1)
exit /b 0
