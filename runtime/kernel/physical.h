#pragma once
// Physical memory allocator for the Xbox 360's 512 MB (GoW2 runtime). Address-ordered first fit with 4 KB granularity:
// it serves MmAllocatePhysicalMemoryEx (any size; UE3 takes one 140 MB pool) and the 64 KB frames that back committed
// virtual memory (kernel/gow2_memory.cpp). Offsets are physical (0x00000000-0x1FFFFFFF); guest virtual = 0xA0000000 | offset.
#include <cstdint>

namespace physical
{
    constexpr uint32_t kBegin = 0x00100000, kEnd = 0x20000000;   // the first 1 MB stays reserved, like the console kernel
    // returns the physical offset, or 0xFFFFFFFF
    uint32_t Alloc(uint32_t size, uint32_t alignment, uint32_t minAddress = 0, uint32_t maxAddress = 0xFFFFFFFF, bool topDown = false);
    void Free(uint32_t offset);
    uint32_t SizeOf(uint32_t offset);
    uint32_t FreeBytes();

    // guest address in a physical window (0xA0000000/0xC0000000/0xE0000000) <-> physical offset. The 0xE0000000
    // window is shifted by 4 KB on the console: virtual E0000000+x is physical x+0x1000 (kernel/memory.cpp).
    inline uint32_t ToPhysical(uint32_t guest) { return (guest & 0x1FFFFFFF) + (guest >= 0xE0000000u ? 0x1000 : 0); }
    inline uint32_t ToWindow(uint32_t window, uint32_t offset) { return window + offset - (window == 0xE0000000u ? 0x1000 : 0); }
}
