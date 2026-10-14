// Guest address space (based on Unleashed Recompiled's memory.cpp, GPL-3.0), rebuilt for Gears of War 3.
//
// The Xbox 360 has 512 MB of physical memory; every virtual allocation is backed by physical pages and the GPU only
// sees physical addresses. The XDK's D3D (running in-game) hands physical addresses of its virtual allocations (fences,
// write-backs) to the GPU and reads them back through the virtual address, so here virtual pages are real views of the
// same physical memory, like Xenia does:
//   0x00000000-0x7EFFFFFF  title virtual memory: address-space placeholders; NtAllocateVirtualMemory maps 64 KB views
//                          of the physical section on commit (kernel/x360_memory.cpp)
//   0x7F000000-0x9FFFFFFF  ordinary memory: GPU MMIO registers (0x7FC80000), the executable image, the recompiler
//                          function table, the host-side heap (0x90000000)
//   0xA0000000/0xC0000000/0xE0000000  three views of the 512 MB physical section (the E0 one shifted by 4 KB)
#include <stdafx.h>
#include "memory.h"
#pragma comment(lib, "onecore.lib")

namespace
{
    constexpr size_t PHYS_SIZE = 0x20000000ull;
    constexpr uint64_t E0_VIEW = 0xE0000000ull - 0x1000;
    HANDLE g_section = nullptr;

    void Fail(const char* what, uint64_t a, uint64_t b = 0)
    {
        fprintf(stderr, "memory: %s %llx %llx failed (%lu)\n", what, (unsigned long long)a, (unsigned long long)b, GetLastError());
    }

    uint8_t* TryMap()
    {
        uint8_t* base = (uint8_t*)VirtualAlloc2(nullptr, nullptr, PPC_MEMORY_SIZE, MEM_RESERVE | MEM_RESERVE_PLACEHOLDER, PAGE_NOACCESS, nullptr, 0);
        if (!base)
        {
            Fail("4 GB placeholder", 0);
            return nullptr;
        }
        // carve the placeholder into [0,7F000000) | [7F000000,A0000000) | A0 window | C0 window (minus its last 64 KB)
        // | gap | E0 window. The console's 0xE0000000 window is offset by 4 KB (virtual E0000000+x = physical x+0x1000;
        // the D3D computes physical addresses that way), so that view starts 4 KB below 0xE0000000. The last 4 KB of
        // physical memory is not reachable through it, as on the console.
        auto split = [&](uint64_t off, uint64_t size) {
            if (VirtualFree(base + off, size, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER)) return true;
            Fail("placeholder split", off, size);
            return false;
        };
        if (!(split(0, 0x7F000000) && split(0x7F000000, 0x21000000) && split(0xA0000000, PHYS_SIZE) &&
              split(0xC0000000, PHYS_SIZE - 0x10000) && split(0xDFFF0000, 0xF000) && split(E0_VIEW, PHYS_SIZE)))
            return nullptr;
        if (!VirtualAlloc2(nullptr, base + 0x7F000000, 0x21000000, MEM_RESERVE | MEM_COMMIT | MEM_REPLACE_PLACEHOLDER, PAGE_READWRITE, nullptr, 0))
        {
            Fail("commit", 0x7F000000, 0x21000000);
            return nullptr;
        }
        const struct { uint64_t at; size_t size; } views[] = { { 0xA0000000, PHYS_SIZE }, { 0xC0000000, PHYS_SIZE - 0x10000 }, { E0_VIEW, PHYS_SIZE } };
        for (auto& v : views)
            if (!MapViewOfFile3(g_section, nullptr, base + v.at, 0, v.size, MEM_REPLACE_PLACEHOLDER, PAGE_READWRITE, nullptr, 0))
            {
                Fail("physical view", v.at, v.size);
                return nullptr;
            }
        return base;
    }
}

Memory::Memory()
{
    g_section = CreateFileMapping2(INVALID_HANDLE_VALUE, nullptr, FILE_MAP_ALL_ACCESS, PAGE_READWRITE, SEC_COMMIT, PHYS_SIZE, nullptr, nullptr, 0);
    if (!g_section)
    {
        Fail("physical section", PHYS_SIZE);
        return;
    }
    base = TryMap();
    if (base == nullptr)
        return;

    for (size_t i = 0; PPCFuncMappings[i].guest != 0; i++)
    {
        if (PPCFuncMappings[i].host != nullptr)
            InsertFunction(PPCFuncMappings[i].guest, PPCFuncMappings[i].host);
    }
}

// Map 'size' bytes of physical memory (64 KB aligned offset) at a title virtual address inside a placeholder.
bool Memory::MapPhysical(uint32_t virtualAddress, uint32_t physicalOffset, uint32_t size)
{
    uint8_t* at = base + virtualAddress;
    // the placeholder is usually larger than this view: split off exactly this range (fails harmlessly if it already is)
    VirtualFree(at, size, MEM_RELEASE | MEM_PRESERVE_PLACEHOLDER);
    if (MapViewOfFile3(g_section, nullptr, at, physicalOffset, size, MEM_REPLACE_PLACEHOLDER, PAGE_READWRITE, nullptr, 0))
        return true;
    Fail("map physical", virtualAddress, physicalOffset);
    return false;
}

void Memory::UnmapPhysical(uint32_t virtualAddress)
{
    UnmapViewOfFile2(GetCurrentProcess(), base + virtualAddress, MEM_PRESERVE_PLACEHOLDER);
}

void* MmGetHostAddress(uint32_t ptr)
{
    return g_memory.Translate(ptr);
}
