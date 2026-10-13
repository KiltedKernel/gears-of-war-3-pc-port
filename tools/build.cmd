@echo off
rem Build the port (build\gow3.exe) from ppc\ (tools/recomp.sh), the runtime and Xenia's GPU (tools/build_xenia.cmd).
call "%~dp0env.cmd" || exit /b 1
cd /d "%GOW3_ROOT%"
if not exist build\build.ninja cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl "-DGOW3_XENIA=%GOW3_XENIA:\=/%" || exit /b 1
cmake --build build || exit /b 1
