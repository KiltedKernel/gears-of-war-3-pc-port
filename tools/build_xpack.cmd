@echo off
rem Build tools\xpack\xpack.exe (Xbox UE3 package decompressor) with clang-cl, libmspack's headers and a copy of its
rem lzxd (tools\xpack\lzxd_xmem.c: an odd-sized uncompressed block's pad byte optional). libmspack is fetched into
rem tools\third_party\libmspack the first time (an older install's XenonRecomp copy is used when it is there).
call "%~dp0env.cmd" || exit /b 1
set "MS=%~dp0XenonRecomp\thirdparty\libmspack\libmspack\mspack"
if exist "%MS%\lzx.h" goto :build
set "MS=%~dp0third_party\libmspack\libmspack\mspack"
if not exist "%MS%\lzx.h" (
  git clone --quiet https://github.com/kyz/libmspack "%~dp0third_party\libmspack" || exit /b 1
  git -C "%~dp0third_party\libmspack" checkout --quiet v1.11 || exit /b 1
)
:build
clang-cl /nologo /O2 /EHsc /std:c++17 -Wno-everything /I"%MS%" /Fe"%~dp0xpack\xpack.exe" /Fo"%~dp0xpack\\" "%~dp0xpack\xpack.cpp" "%~dp0xpack\lzxd_xmem.c" || exit /b 1
