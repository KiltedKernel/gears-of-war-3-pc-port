@echo off
rem Build Xenia's GPU/audio libraries and the port's glue (xe_glue) with Xenia's own CMake (MSVC).
call "%~dp0env.cmd" || exit /b 1
cd /d "%GOW3_XENIA%" || exit /b 1
if not exist build-gow3\build.ninja cmake -S . -B build-gow3 -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl -DXE_PORT_NO_LTCG=ON "-DXE_PORT_GLUE_DIR=%GOW3_ROOT:\=/%/xe_glue" -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded || exit /b 1
cmake --build build-gow3 --target gow3-xe-glue || exit /b 1
