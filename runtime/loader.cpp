// Loads the player's default.xex into guest memory (decrypted + decompressed by XenonUtils) and gives the kernel
// variable imports real storage. Variable imports found in Gears of War 3 (tools/list_var_imports.py):
//   ExThreadObjectType KeDebugMonitorData XexExecutableModuleHandle XboxKrnlVersion ExLoadedCommandLine
//   XboxHardwareInfo VdGlobalDevice VdGlobalXamDevice KeCertMonitorData VdGpuClockInMHz VdHSIOCalibrationLock
//   KeTimeStampBundle
#include <stdafx.h>
#include <image.h>
#include <xex.h>
#include <file.h>
#include <kernel/memory.h>
#include <kernel/heap.h>
#include <os/logger.h>
#include "loader.h"
#include <thread>
#include <unordered_map>

namespace
{
    // XE_EXPORT tables shipped with XenonUtils (from Xenia): ordinal -> name, for variables only
    struct ExportEntry { uint32_t ordinal; const char* name; bool variable; };
#define XE_EXPORT(lib, ord, name, kind) { ord, #name, kind == 1 }
    constexpr int kFunction = 0, kVariable = 1;
    const ExportEntry kKernelExports[] = {
#include <xbox/xboxkrnl_table.inc>
    };
    const ExportEntry kXamExports[] = {
#include <xbox/xam_table.inc>
    };
#undef XE_EXPORT

    uint32_t g_timeStampBundle = 0;
    std::vector<uint8_t> g_xexFile;

    uint32_t AllocZeroed(uint32_t size)
    {
        void* p = g_userHeap.AllocPhysical(size, 16);
        memset(p, 0, size);
        return g_memory.MapVirtual(p);
    }

    void W32(uint32_t a, uint32_t v) { *(be<uint32_t>*)g_memory.Translate(a) = v; }
    void W16(uint32_t a, uint16_t v) { *(be<uint16_t>*)g_memory.Translate(a) = v; }
    void W64(uint32_t a, uint64_t v) { *(be<uint64_t>*)g_memory.Translate(a) = v; }

    uint32_t MakeVariable(const char* name, uint32_t moduleHandle)
    {
        const std::string_view n(name);
        if (n == "XboxHardwareInfo")
        {
            uint32_t a = AllocZeroed(16);
            W32(a + 0, 0x00000020);          // flags: HDD present
            *(uint8_t*)g_memory.Translate(a + 4) = 6;   // number of hardware threads
            return a;
        }
        if (n == "XboxKrnlVersion")
        {
            uint32_t a = AllocZeroed(8);
            W16(a + 0, 2); W16(a + 2, 0); W16(a + 4, 17559); W16(a + 6, 0);   // 2.0.17559.0
            return a;
        }
        if (n == "KeTimeStampBundle")
        {
            g_timeStampBundle = AllocZeroed(24);   // InterruptTime (u64), SystemTime (u64), TickCount (u32), padding
            return g_timeStampBundle;
        }
        if (n == "ExLoadedCommandLine")
        {
            // the game's command line (UE3 appCmdLine): GOW3_COMMAND_LINE adds to it, e.g. a map URL to start on
            // (tools/chapter_test.py: "GearGame_P?game=geargamecontent.GearGameSP?chapter=7?listen")
            std::string line = "\"default.xex\"";
            if (const char* extra = getenv("GOW3_COMMAND_LINE"); extra && *extra) line += std::string(" ") + extra;
            line.resize(std::min<size_t>(line.size(), 1023));
            uint32_t a = AllocZeroed(1024);
            strcpy((char*)g_memory.Translate(a), line.c_str());
            if (line.size() > 13) LOGFN("loader: command line {}", line);
            return a;
        }
        if (n == "XexExecutableModuleHandle")
        {
            // the variable holds a pointer to the module handle
            uint32_t a = AllocZeroed(4);
            W32(a, moduleHandle);
            return a;
        }
        if (n == "VdGpuClockInMHz")
        {
            uint32_t a = AllocZeroed(4);
            W32(a, 500);
            return a;
        }
        // ExThreadObjectType, KeDebugMonitorData, KeCertMonitorData, VdGlobalDevice, VdGlobalXamDevice,
        // VdHSIOCalibrationLock: zeroed storage is what an idle retail kernel exposes
        return AllocZeroed(0x100);
    }

    void PatchVariableImports(const uint8_t* xex)
    {
        auto* header = (const Xex2Header*)xex;
        auto* imports = (const Xex2ImportHeader*)getOptHeaderPtr(xex, XEX_HEADER_IMPORT_LIBRARIES);
        if (!imports)
            return;

        // a fake loader entry for XexExecutableModuleHandle: the XDK reads the image base and the XEX header from it
        uint32_t module = AllocZeroed(0x100);
        uint32_t headerCopy = AllocZeroed(header->headerSize);
        g_moduleHandle = module;
        g_xexHeader = headerCopy;
        memcpy(g_memory.Translate(headerCopy), xex, header->headerSize);
        W32(module + 0x18, 0x82000000);      // DllBase
        W32(module + 0x58, headerCopy);      // XexHeaderBase

        std::vector<std::string_view> names;
        auto* str = (const char*)(imports + 1);
        size_t off = 0;
        for (size_t i = 0; i < imports->numImports; i++)
        {
            names.emplace_back(str + off);
            off += ((names.back().length() + 1) + 3) & ~3;
        }

        auto* library = (Xex2ImportLibrary*)((char*)imports + sizeof(Xex2ImportHeader) + imports->sizeOfStringTable);
        for (size_t i = 0; i < names.size(); i++)
        {
            const ExportEntry* table = names[i] == "xboxkrnl.exe" ? kKernelExports : names[i] == "xam.xex" ? kXamExports : nullptr;
            const size_t tableSize = names[i] == "xboxkrnl.exe" ? std::size(kKernelExports) : std::size(kXamExports);
            auto* desc = (Xex2ImportDescriptor*)(library + 1);
            for (size_t im = 0; table && im < library->numberOfImports; im++)
            {
                uint32_t slot = desc[im].firstThunk;
                // Xex2LoadImage byte-swapped every record in place: the slot now holds the record in host order
                uint32_t record = *(uint32_t*)g_memory.Translate(slot);
                uint32_t type = record >> 24, ordinal = record & 0xFFFF;
                if (type != 0)
                    continue;
                for (size_t t = 0; t < tableSize; t++)
                {
                    if (table[t].ordinal == ordinal && table[t].variable)
                    {
                        uint32_t v = MakeVariable(table[t].name, module);
                        W32(slot, v);
                        LOGF_IMPL(None, "Loader", "variable import {} -> {:08X}", table[t].name, v);
                    }
                }
            }
            library = (Xex2ImportLibrary*)((char*)(library + 1) + library->numberOfImports * sizeof(Xex2ImportDescriptor));
        }
    }
}

uint32_t g_moduleHandle = 0, g_xexHeader = 0;

void UpdateTimeStampBundle()
{
    if (!g_timeStampBundle)
        return;
    FILETIME ft; GetSystemTimeAsFileTime(&ft);
    uint64_t sys = (uint64_t(ft.dwHighDateTime) << 32) | ft.dwLowDateTime;
    uint64_t ms = GetTickCount64();
    W64(g_timeStampBundle + 0, ms * 10000);   // interrupt time, 100 ns units
    W64(g_timeStampBundle + 8, sys);
    W32(g_timeStampBundle + 16, uint32_t(ms));
}

uint32_t LdrLoadModule(const std::filesystem::path& path)
{
    std::vector<uint8_t> file = LoadFile(path);
    if (file.empty())
    {
        LOGF_IMPL(Error, "Loader", "cannot read {}", path.string());
        return 0;
    }
    Image image = Image::ParseImage(file.data(), file.size());
    if (!image.data)
    {
        LOGF_IMPL(Error, "Loader", "{} is not a XEX2", path.string());
        return 0;
    }
    // XenonUtils allocated max(blocks, imageSize) (our xex.cpp fix), so image.size bytes are valid
    memcpy(g_memory.Translate(image.base), image.data.get(), image.size);

    PatchVariableImports(file.data());
    UpdateTimeStampBundle();
    LOGF_IMPL(None, "Loader", "{}: base {:08X} size {:X} entry {:08X}", path.filename().string(), image.base, image.size, image.entry_point);
    return uint32_t(image.entry_point);
}
