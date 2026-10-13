@echo off
rem Build XenonRecomp (tools\XenonRecomp: the pinned commit in xenonrecomp-commit.txt + xenonrecomp-gow3.patch).
call "%~dp0env.cmd" || exit /b 1
cd /d "%GOW3_ROOT%\tools\XenonRecomp"
if not exist build\build.ninja cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl || exit /b 1
cmake --build build --target XenonRecomp || exit /b 1
