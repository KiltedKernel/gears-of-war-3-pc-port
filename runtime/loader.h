#pragma once
#include <filesystem>
uint32_t LdrLoadModule(const std::filesystem::path& path);
void UpdateTimeStampBundle();
extern uint32_t g_moduleHandle;   // guest address of the executable's loader entry (XexExecutableModuleHandle)
extern uint32_t g_xexHeader;      // guest copy of the XEX header (optional headers live here)
