@echo off
call "%~dp0env.cmd" || exit /b 1
set "X=%GOW3_ROOT%\tools\XenonRecomp"
set "D=%GOW3_ROOT%\tools\xexdump"
clang-cl /nologo /std:c++20 /O2 /EHsc "/I%X%\XenonUtils" "/I%X%\thirdparty\fmt\include" "%D%\main.cpp" "/Fe:%D%\xexdump.exe" "/Fo:%D%\\" /link "%X%\build\XenonUtils\XenonUtils.lib" "%X%\build\thirdparty\fmt\fmt.lib"
