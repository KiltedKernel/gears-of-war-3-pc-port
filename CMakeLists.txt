cmake_minimum_required(VERSION 3.20)
project(GoW3Recomp C CXX)
# the release version (VERSION), shown by the launcher and logged by the game
file(READ ${CMAKE_SOURCE_DIR}/VERSION GOW3_VERSION)
string(STRIP "${GOW3_VERSION}" GOW3_VERSION)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/VERSION)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
cmake_policy(SET CMP0091 NEW)

# ---- recompiled game code: generated from the player's own default.xex (tools/recomp.sh), not in the repo ----
file(GLOB PPC_SOURCES CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/ppc/ppc_recomp.*.cpp ${CMAKE_SOURCE_DIR}/ppc/ppc_func_mapping.cpp)
set(XENON ${CMAKE_SOURCE_DIR}/tools/XenonRecomp)
add_library(gow3_ppc STATIC ${PPC_SOURCES})
target_include_directories(gow3_ppc PUBLIC ${CMAKE_SOURCE_DIR}/ppc ${XENON}/XenonUtils ${XENON}/thirdparty/simde)
# same flags as Unleashed Recompiled's recompiled library
target_compile_options(gow3_ppc PRIVATE -march=x86-64-v3 -Wno-everything -fno-strict-aliasing /fp:strict /O2 /Z7)

# ---- runtime: kernel/XAM/threads/heap lifted from Unleashed Recompiled (GPL-3.0, see runtime/COPYING.unleashed) ----
set(RT ${CMAKE_SOURCE_DIR}/runtime)
file(GLOB_RECURSE RT_SOURCES CONFIGURE_DEPENDS ${RT}/*.cpp)
list(FILTER RT_SOURCES EXCLUDE REGEX ".*/thirdparty/.*")
add_executable(gow3 ${RT_SOURCES} ${RT}/thirdparty/o1heap/o1heap.c)
target_include_directories(gow3 PRIVATE ${RT} ${RT}/thirdparty/o1heap ${RT}/thirdparty/unordered_dense
    ${XENON}/XenonUtils ${XENON}/thirdparty/fmt/include ${XENON}/thirdparty/xxHash)
target_compile_definitions(gow3 PRIVATE FMT_HEADER_ONLY NOMINMAX GOW3_VERSION="${GOW3_VERSION}")
target_compile_options(gow3 PRIVATE -march=x86-64-v3 -Wno-everything -fms-extensions /Z7 $<$<COMPILE_LANGUAGE:CXX>:/EHsc>)
# a windowed program (no console window); the log is build/gow3.log
target_link_options(gow3 PRIVATE /DEBUG /SUBSYSTEM:WINDOWS /ENTRY:wmainCRTStartup)
target_link_libraries(gow3 PRIVATE gow3_ppc ${XENON}/build/XenonUtils/XenonUtils.lib ${XENON}/build/thirdparty/fmt/fmt.lib ${XENON}/build/thirdparty/xxHash/cmake_unofficial/xxhash.lib xinput dbghelp)

# ---- GPU: Xenia's Direct3D 12 GPU (BSD-3) + the port's glue, built by Xenia's own CMake (tools/build_xenia.cmd) ----
get_filename_component(GOW3_XENIA_DEFAULT ${CMAKE_SOURCE_DIR}/../xenia ABSOLUTE)
set(GOW3_XENIA ${GOW3_XENIA_DEFAULT} CACHE PATH "Xenia checkout (tools/setup.ps1 puts it next to this repository)")
set(XENIA_LIBS ${GOW3_XENIA}/build-gow3/obj/Windows)
target_link_libraries(gow3 PRIVATE
    ${XENIA_LIBS}/gow3-xe-glue.lib ${XENIA_LIBS}/xenia-apu-xaudio2.lib ${XENIA_LIBS}/xenia-apu.lib
    ${XENIA_LIBS}/libavcodec.lib ${XENIA_LIBS}/libavformat.lib ${XENIA_LIBS}/libavutil.lib ${XENIA_LIBS}/xenia-gpu-d3d12.lib ${XENIA_LIBS}/xenia-gpu.lib
    ${XENIA_LIBS}/xenia-ui-d3d12.lib ${XENIA_LIBS}/xenia-ui.lib ${XENIA_LIBS}/xenia-base.lib ${XENIA_LIBS}/dxbc.lib
    ${XENIA_LIBS}/snappy.lib ${XENIA_LIBS}/fmt.lib
    dxgi dxguid dwmapi winmm shcore shlwapi comctl32 ntdll ws2_32 bcrypt)

# o1heap self-checks stay on (they catch heap corruption at the faulty operation; the crash reporter names the line)
set_source_files_properties(${RT}/thirdparty/o1heap/o1heap.c PROPERTIES COMPILE_DEFINITIONS "O1HEAP_ASSERT(x)=do{if(!(x))__debugbreak();}while(0)")

# ---- launcher: options window for gow3.ini + Play (launcher/) ----
add_executable(gow3_launcher WIN32 ${CMAKE_SOURCE_DIR}/launcher/launcher.cpp ${CMAKE_SOURCE_DIR}/launcher/launcher.rc)
target_compile_options(gow3_launcher PRIVATE /W3 -Wno-everything $<$<COMPILE_LANGUAGE:CXX>:/EHsc>)
target_compile_definitions(gow3_launcher PRIVATE UNICODE _UNICODE GOW3_VERSION=L"${GOW3_VERSION}")
set_target_properties(gow3_launcher PROPERTIES LINK_FLAGS "/MANIFEST:NO")
target_link_libraries(gow3_launcher PRIVATE comctl32 shcore shell32)
