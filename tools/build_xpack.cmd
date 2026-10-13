@echo off
rem Build tools\xpack\xpack.exe (Xbox UE3 package decompressor) with clang-cl and XenonRecomp's libmspack lzxd.
call "%~dp0env.cmd" || exit /b 1
set "MS=%~dp0XenonRecomp\thirdparty\libmspack\libmspack\mspack"
clang-cl /nologo /O2 /EHsc /std:c++17 -Wno-everything /I"%MS%" /Fe"%~dp0xpack\xpack.exe" /Fo"%~dp0xpack\\" "%~dp0xpack\xpack.cpp" "%MS%\lzxd.c" || exit /b 1
