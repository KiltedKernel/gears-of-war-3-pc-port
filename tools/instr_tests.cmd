@echo off
rem Build and run the instruction tests (tests/instr_tests.cpp) against the current recompiler output in ppc/.
call "%~dp0env.cmd" || exit /b 1
cd /d "%GOW3_ROOT%"
python tools\gen_instr_tests.py || exit /b 1
clang-cl /nologo /std:c++20 -march=x86-64-v3 -Wno-everything -fno-strict-aliasing /fp:strict /O2 /EHsc /MT ^
  /I ppc /I tools\XenonRecomp\XenonUtils /I tools\XenonRecomp\thirdparty\simde ^
  tests\instr_tests.cpp /Fe:build\instr_tests\instr_tests.exe /Fo:build\instr_tests\ || exit /b 1
build\instr_tests\instr_tests.exe
