# Built inside Xenia's CMake build (D:/xenia, -DXE_PORT_GLUE_DIR=<this dir>): Xenia's GPU libraries + the glue,
# linked into gow3.exe by the port's own build.
add_library(gow3-xe-glue STATIC glue.cc)
target_include_directories(gow3-xe-glue PRIVATE
  ${PROJECT_SOURCE_DIR} ${PROJECT_SOURCE_DIR}/src ${PROJECT_SOURCE_DIR}/third_party ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(gow3-xe-glue PUBLIC xenia-apu-xaudio2 xenia-apu xenia-gpu-d3d12 xenia-gpu xenia-ui-d3d12 xenia-ui xenia-base)

# link check: an executable with only the glue, to list what Xenia's GPU still needs from the emulator
add_executable(gow3-xe-linkcheck linkcheck.cc)
target_include_directories(gow3-xe-linkcheck PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(gow3-xe-linkcheck PRIVATE gow3-xe-glue)
