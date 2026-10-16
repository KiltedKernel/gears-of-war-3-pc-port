@echo off
rem Build tools\xpack\xpack.exe (Xbox UE3 package decompressor) with clang-cl, XenonRecomp's libmspack headers and a
rem copy of its lzxd (tools\xpack\lzxd_xmem.c: an odd-sized uncompressed block's pad byte optional).
call "%~dp0env.cmd" || exit /b 1
set "MS=%~dp0XenonRecomp\thirdparty\libmspack\libmspack\mspack"
clang-cl /nologo /O2 /EHsc /std:c++17 -Wno-everything /I"%MS%" /Fe"%~dp0xpack\xpack.exe" /Fo"%~dp0xpack\\" "%~dp0xpack\xpack.cpp" "%~dp0xpack\lzxd_xmem.c" || exit /b 1
