@echo off
rem Build the port (build\gow3.exe) from ppc\ (tools/recomp.sh), the runtime and Xenia's GPU (tools/build_xenia.cmd).
call "%~dp0env.cmd" || exit /b 1
cd /d "%GOW3_ROOT%"
if not exist build\build.ninja cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=clang-cl -DCMAKE_CXX_COMPILER=clang-cl "-DGOW3_XENIA=%GOW3_XENIA:\=/%" || exit /b 1
cmake --build build || exit /b 1
rem keyboard/mouse button prompts (build\prompts): made once, drawn (Gears of War: Reloaded's keycaps are an opt-in:
rem tools\make_prompts.py reloaded). Without them the game shows controller prompts only
if not exist build\prompts\kbm_glyphs.rgba (python -P tools\make_prompts.py drawn build\prompts || echo warning: keyboard prompts not made ^(tools\make_prompts.py^): controller prompts only)
