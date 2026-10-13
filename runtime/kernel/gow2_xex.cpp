// Gears of War 3 kernel: executable-module queries (Xenia: xboxkrnl_modules.cc / xboxkrnl_rtl.cc semantics).
#include <stdafx.h>
#include "function.h"
#include "memory.h"
#include <xex.h>
#include <loader.h>
#include <os/logger.h>

namespace gow2k
{

namespace
{
    // optional header (key) in the guest copy of the XEX header: small keys (low byte 0 or 1) hold the value itself,
    // larger ones point at data inside the header
    uint32_t FindOptHeader(uint32_t headerBase, uint32_t key, bool& found)
    {
        auto* h = (const Xex2Header*)g_memory.Translate(headerBase);
        auto* opt = (const Xex2OptHeader*)(h + 1);
        for (uint32_t i = 0; i < h->headerCount; i++)
        {
            if (opt[i].key == key)
            {
                found = true;
                if ((key & 0xFF) == 0 || (key & 0xFF) == 1)
                    return opt[i].value;
                return headerBase + opt[i].offset;
            }
        }
        found = false;
        return 0;
    }
}

uint32_t RtlImageXexHeaderField(uint32_t xexHeaderBase, uint32_t imageField)
{
    bool found;
    uint32_t v = FindOptHeader(xexHeaderBase ? xexHeaderBase : g_xexHeader, imageField, found);
    if (!found)
        LOGFN_WARNING("RtlImageXexHeaderField: no field {:08X}", imageField);
    return v;
}

// XamGetExecutionId(XEX_EXECUTION_ID** out): the title's execution info (media id, version, title id, disc number ...)
// is the XEX's 0x40006 optional header, already in guest memory with the rest of the headers. Gears of War 3 reads it
// when it opens the player's storage after "press start".
uint32_t XamGetExecutionId(be<uint32_t>* out)
{
    bool found;
    const uint32_t info = FindOptHeader(g_xexHeader, 0x40006, found);
    if (!found) { LOGN_WARNING("XamGetExecutionId: no execution info header"); if (out) *out = 0; return 0xC0000225; }
    if (out) *out = info;
    return 0;
}

uint32_t XexGetModuleHandle(const char* moduleName, be<uint32_t>* hModule)
{
    // only the title itself is loaded (no extra modules in Gears of War 3)
    std::string_view n = moduleName ? moduleName : "";
    if (n.empty() || n == "default.xex" || n == "GearGame.xex")
    {
        *hModule = g_moduleHandle;
        return 0;
    }
    LOGFN_WARNING("XexGetModuleHandle: {} not loaded", n);
    *hModule = 0;
    return 0xC0000225; // STATUS_NOT_FOUND
}

uint32_t XexGetModuleSection(uint32_t hModule, const char* name, be<uint32_t>* data, be<uint32_t>* size)
{
    // resource sections listed in the XEX resource info header (e.g. the title's XDBF)
    bool found;
    uint32_t info = FindOptHeader(g_xexHeader, XEX_HEADER_RESOURCE_INFO, found);
    if (found)
    {
        uint32_t total = *(be<uint32_t>*)g_memory.Translate(info);
        auto* res = (const Xex2ResourceInfo*)g_memory.Translate(info);
        for (uint32_t off = 4; off + 16 <= total; off += 16)
        {
            const uint8_t* id = (const uint8_t*)g_memory.Translate(info + off);
            if (strncmp((const char*)id, name, 8) == 0)
            {
                *data = *(be<uint32_t>*)(id + 8);
                *size = *(be<uint32_t>*)(id + 12);
                return 0;
            }
        }
    }
    LOGFN_WARNING("XexGetModuleSection: section {} not found", name);
    return 0xC0000225;
}

uint32_t XexGetProcedureAddress(uint32_t hModule, uint32_t ordinal, be<uint32_t>* procAddress)
{
    LOGFN_WARNING("XexGetProcedureAddress(module {:08X}, ordinal {}) not supported", hModule, ordinal);
    *procAddress = 0;
    return 0xC0000225;
}
}

GUEST_FUNCTION_HOOK(__imp__RtlImageXexHeaderField, gow2k::RtlImageXexHeaderField);
GUEST_FUNCTION_HOOK(__imp__XamGetExecutionId, gow2k::XamGetExecutionId);
GUEST_FUNCTION_HOOK(__imp__XexGetModuleHandle, gow2k::XexGetModuleHandle);
GUEST_FUNCTION_HOOK(__imp__XexGetModuleSection, gow2k::XexGetModuleSection);
GUEST_FUNCTION_HOOK(__imp__XexGetProcedureAddress, gow2k::XexGetProcedureAddress);
