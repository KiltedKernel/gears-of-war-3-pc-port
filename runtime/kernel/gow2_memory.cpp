// Gears of War 3 kernel: virtual memory and pools.
// Unleashed Recompiled replaced Sonic's XAPI heap with the host heap and left these as stubs; Gears of War 3 runs its
// own (recompiled) XAPI/UE3 allocators on top of them, so they are real here. Semantics follow the Xbox 360 kernel
// as documented by Xenia (xboxkrnl_memory.cc): 4 KB / 64 KB pages, reserve + commit, regions queried by address.
//
// Guest layout (Xbox 360 / Xenia): 0x00010000-0x3FFFFFFF  4 KB-page virtual memory (this file)
//                                  0x40000000-0x7EFFFFFF  64 KB-page virtual memory (this file, MEM_LARGE_PAGES)
//                                  0x90000000-0x9FFFFFFF  host-side heap (kernel objects, stacks; kernel/heap.cpp)
// The title's allocators classify pointers by these ranges (e.g. UE3's free routine at 827F80C8), so they matter.
//                0x82000000-...          the executable image + recompiler function table
//                0xA0000000-0xFFFFFFFF  physical heap (MmAllocatePhysicalMemoryEx, GPU resources)
#include <stdafx.h>
#include "function.h"
#include "heap.h"
#include "memory.h"
#include "physical.h"
#include <os/logger.h>
#include <map>

namespace gow2k
{

namespace
{
    constexpr uint32_t VM4K_BEGIN = 0x00010000, VM4K_END = 0x40000000, VM64K_BEGIN = 0x40000000, VM64K_END = 0x7F000000;
    constexpr uint32_t VM_BEGIN = VM4K_BEGIN, VM_END = VM64K_END;   // whole title range (fixed-address requests)
    constexpr uint32_t MEM_COMMIT_ = 0x1000, MEM_RESERVE_ = 0x2000, MEM_DECOMMIT_ = 0x4000, MEM_RELEASE_ = 0x8000,
                       MEM_FREE_ = 0x10000, MEM_PRIVATE_ = 0x20000, MEM_LARGE_PAGES_ = 0x20000000, MEM_TOP_DOWN_ = 0x100000;

    struct Region { uint32_t size; uint32_t protect; bool committed; };
    std::map<uint32_t, Region> g_regions;   // base -> region (reserved ranges)
    std::mutex g_vmMutex;

    uint32_t AlignUp(uint32_t v, uint32_t a) { return (v + a - 1) & ~(a - 1); }

    // first fit in [VM_BEGIN, VM_END)
    uint32_t FindFree(uint32_t size, uint32_t align, bool topDown, uint32_t VM_BEGIN, uint32_t VM_END)
    {
        std::vector<std::pair<uint32_t, uint32_t>> used(g_regions.size());
        size_t i = 0;
        for (auto& [b, r] : g_regions) used[i++] = { b, b + r.size };
        if (!topDown)
        {
            uint32_t cur = VM_BEGIN;
            for (auto& [b, e] : used)
            {
                if (AlignUp(cur, align) + size <= b) return AlignUp(cur, align);
                cur = std::max(cur, e);
            }
            cur = AlignUp(cur, align);
            return cur + size <= VM_END ? cur : 0;
        }
        uint32_t cur = VM_END;
        for (auto it = used.rbegin(); it != used.rend(); ++it)
        {
            uint32_t cand = (cur - size) & ~(align - 1);
            if (cand >= it->second) return cand;
            cur = std::min(cur, it->first);
        }
        uint32_t cand = (cur - size) & ~(align - 1);
        return cand >= VM_BEGIN ? cand : 0;
    }

    std::map<uint32_t, Region>::iterator FindRegion(uint32_t addr)
    {
        auto it = g_regions.upper_bound(addr);
        if (it == g_regions.begin()) return g_regions.end();
        --it;
        return addr < it->first + it->second.size ? it : g_regions.end();
    }
}

// ---- physical backing: 64 KB frames for committed virtual memory, from the shared physical allocator ----
namespace
{
    constexpr uint32_t FRAME = 0x10000;
    std::map<uint32_t, uint32_t> g_chunks;       // committed 64 KB virtual chunk -> physical offset

    uint32_t TakeFrame()
    {
        uint32_t f = physical::Alloc(FRAME, FRAME);
        return f == 0xFFFFFFFF ? 0 : f;
    }

    // map frames under every 64 KB chunk of [base, base+size) that isn't mapped yet; new memory reads as zeros
    bool CommitRange(uint32_t base, uint32_t size)
    {
        for (uint32_t c = base & ~(FRAME - 1); c < base + size; c += FRAME)
        {
            if (g_chunks.count(c)) continue;
            uint32_t f = TakeFrame();
            if (!f || !g_memory.MapPhysical(c, f, FRAME))
            {
                LOGFN_ERROR("NtAllocateVirtualMemory: cannot back {:08X} with physical memory (frame {:X})", c, f);
                return false;
            }
            memset(g_memory.Translate(c), 0, FRAME);
            g_chunks[c] = f;
        }
        return true;
    }

    void DecommitRange(uint32_t base, uint32_t size)
    {
        for (uint32_t c = base & ~(FRAME - 1); c < base + size; c += FRAME)
        {
            auto it = g_chunks.find(c);
            if (it == g_chunks.end()) continue;
            g_memory.UnmapPhysical(c);
            physical::Free(it->second);
            g_chunks.erase(it);
        }
    }
}

uint32_t NtAllocateVirtualMemory(be<uint32_t>* baseAddress, be<uint32_t>* regionSize, uint32_t allocationType, uint32_t protect, uint32_t debugMemory)
{
    const uint32_t page = (allocationType & MEM_LARGE_PAGES_) ? 0x10000 : 0x1000;
    uint32_t size = AlignUp(uint32_t(*regionSize), page);
    uint32_t base = *baseAddress;
    if (size == 0)
        return 0xC000000D; // STATUS_INVALID_PARAMETER

    std::lock_guard lock(g_vmMutex);
    if (base == 0)
    {
        // a fresh reservation: 64 KB allocation granularity like the real kernel
        const bool large = page == 0x10000;
        base = large ? FindFree(AlignUp(size, 0x10000), 0x10000, allocationType & MEM_TOP_DOWN_, VM64K_BEGIN, VM64K_END)
                     : FindFree(AlignUp(size, 0x10000), 0x10000, allocationType & MEM_TOP_DOWN_, VM4K_BEGIN, VM4K_END);
        if (!base)
        {
            LOGFN_ERROR("NtAllocateVirtualMemory: out of guest virtual space ({:X} bytes, lr {:08X})", size, GetPPCContext()->lr);
            // guest call chain: back chain at 0(r1), the caller's LR saved at -8 from the previous frame
            uint32_t sp = GetPPCContext()->r1.u32;
            std::string chain;
            for (int i = 0; i < 12 && sp >= 0x10000; i++)
            {
                const uint32_t next = ByteSwap(*(uint32_t*)g_memory.Translate(sp));
                if (next <= sp || next - sp > 0x100000) break;
                chain += fmt::format(" < {:08X}", ByteSwap(*(uint32_t*)g_memory.Translate(next - 8)));
                sp = next;
            }
            LOGFN_ERROR("  guest stack:{}", chain);
            return 0xC0000017; // STATUS_NO_MEMORY
        }
        g_regions[base] = { AlignUp(size, 0x10000), protect, false };
    }
    else
    {
        base &= ~(page - 1);
        auto it = FindRegion(base);
        if (it == g_regions.end())
        {
            if (base < VM_BEGIN || base + size > VM_END)
            {
                LOGFN_ERROR("NtAllocateVirtualMemory: fixed address {:08X} outside the VM range", base);
                return 0xC0000018; // STATUS_CONFLICTING_ADDRESSES
            }
            g_regions[base & ~0xFFFFu] = { AlignUp(size + (base & 0xFFFF), 0x10000), protect, false };
        }
    }
    if (allocationType & MEM_COMMIT_)
    {
        if (!CommitRange(base, size))
            return 0xC0000017;
        if (auto it = FindRegion(base); it != g_regions.end()) it->second.committed = true;
    }
    *baseAddress = base;
    *regionSize = size;
    return 0;
}

uint32_t NtFreeVirtualMemory(be<uint32_t>* baseAddress, be<uint32_t>* regionSize, uint32_t freeType, uint32_t debugMemory)
{
    std::lock_guard lock(g_vmMutex);
    uint32_t base = *baseAddress;
    auto it = FindRegion(base);
    if (it == g_regions.end())
        return 0xC000009F; // STATUS_MEMORY_NOT_ALLOCATED
    if (freeType & MEM_RELEASE_)
    {
        DecommitRange(it->first, it->second.size);
        *baseAddress = it->first;
        *regionSize = it->second.size;
        g_regions.erase(it);
    }
    else
    {
        uint32_t size = *regionSize ? AlignUp(uint32_t(*regionSize), 0x1000) : it->second.size - (base - it->first);
        // decommit: whole 64 KB chunks go back to the pool, partial ones are just cleared
        uint32_t first = AlignUp(base, FRAME), last = (base + size) & ~(FRAME - 1);
        if (last > first) DecommitRange(first, last - first);
        if (first > base) memset(g_memory.Translate(base), 0, std::min(size, first - base));
        *regionSize = size;
    }
    return 0;
}

// physical address of a guest virtual address, as the GPU sees it
uint32_t MmGetPhysicalAddress(uint32_t address)
{
    if (address >= 0xA0000000u)
        return physical::ToPhysical(address);
    std::lock_guard lock(g_vmMutex);
    auto it = g_chunks.find(address & ~(FRAME - 1));
    if (it != g_chunks.end())
    {
        static int logged = 0;
        if (logged++ < 12)
            LOGFN("MmGetPhysicalAddress: virtual {:08X} -> physical {:08X} (lr {:08X})", address,
                it->second + (address & (FRAME - 1)), GetPPCContext()->lr);
        return it->second + (address & (FRAME - 1));
    }
    static int warned = 0;
    if (warned++ < 8)
        LOGFN_WARNING("MmGetPhysicalAddress: {:08X} has no physical backing", address);
    return address & 0x1FFFFFFF;
}

struct XMEMORY_BASIC_INFORMATION
{
    be<uint32_t> BaseAddress, AllocationBase, AllocationProtect, RegionSize, State, Protect, Type;
};

uint32_t NtQueryVirtualMemory(uint32_t address, XMEMORY_BASIC_INFORMATION* info, uint32_t regionType)
{
    std::lock_guard lock(g_vmMutex);
    auto it = FindRegion(address);
    if (it == g_regions.end())
    {
        info->BaseAddress = address & ~0xFFF;
        info->AllocationBase = 0;
        info->AllocationProtect = 0;
        info->RegionSize = 0x1000;
        info->State = MEM_FREE_;
        info->Protect = 1; // PAGE_NOACCESS
        info->Type = 0;
    }
    else
    {
        info->BaseAddress = address & ~0xFFF;
        info->AllocationBase = it->first;
        info->AllocationProtect = it->second.protect;
        info->RegionSize = it->first + it->second.size - (address & ~0xFFF);
        info->State = it->second.committed ? MEM_COMMIT_ : MEM_RESERVE_;
        info->Protect = it->second.protect;
        info->Type = MEM_PRIVATE_;
    }

    return 0;
}

// executive pool: kernel-side small allocations (guest memory from the host-side heap)
uint32_t ExAllocatePoolTypeWithTag(uint32_t size, uint32_t tag, uint32_t poolType)
{
    void* p = g_userHeap.Alloc(std::max<uint32_t>(size, 16));
    return p ? g_memory.MapVirtual(p) : 0;
}

uint32_t ExAllocatePool(uint32_t size) { return ExAllocatePoolTypeWithTag(size, 0, 0); }

void ExFreePool(uint32_t ptr)
{
    if (ptr) g_userHeap.Free(g_memory.Translate(ptr));
}

struct XMM_STATISTICS
{
    be<uint32_t> Length, TotalPhysicalPages, KernelPages, TitleAvailablePages, TitleTotalVirtualMemoryBytes,
        TitleReservedVirtualMemoryBytes, TitlePhysicalPages, TitlePoolPages, TitleStackPages, TitleImagePages,
        TitleHeapPages, TitleVirtualPages, TitlePageTablePages, TitleCachePages, SystemAvailablePages,
        SystemTotalVirtualMemoryBytes, SystemReservedVirtualMemoryBytes, SystemPhysicalPages, SystemPoolPages,
        SystemStackPages, SystemImagePages, SystemHeapPages, SystemVirtualPages, SystemPageTablePages, SystemCachePages,
        HighestPhysicalPage;
};

uint32_t MmQueryStatistics(XMM_STATISTICS* stats)
{
    if (stats->Length != sizeof(XMM_STATISTICS))
        return 0xC0000004; // STATUS_INFO_LENGTH_MISMATCH
    const uint32_t len = stats->Length;
    memset(stats, 0, sizeof(*stats));
    stats->Length = len;
    stats->TotalPhysicalPages = 0x20000;         // 512 MB of 4 KB pages, as on the console
    stats->KernelPages = 0x100;
    stats->TitleAvailablePages = 0x1C000;
    stats->TitleTotalVirtualMemoryBytes = VM64K_END - VM4K_BEGIN;
    stats->HighestPhysicalPage = 0x1FFFF;
    return 0;
}

namespace { std::map<uint32_t, uint32_t> g_protect; std::mutex g_protectMutex; }

void MmSetAddressProtect(uint32_t address, uint32_t size, uint32_t protect)
{
    std::lock_guard lock(g_protectMutex);
    g_protect[address] = protect;   // host memory stays read/write; the value is only reported back
}

uint32_t MmQueryAddressProtect(uint32_t address)
{
    std::lock_guard lock(g_protectMutex);
    auto it = g_protect.find(address);
    return it != g_protect.end() ? it->second : 0x04; // PAGE_READWRITE
}
}

GUEST_FUNCTION_HOOK(__imp__NtAllocateVirtualMemory, gow2k::NtAllocateVirtualMemory);
GUEST_FUNCTION_HOOK(__imp__NtFreeVirtualMemory, gow2k::NtFreeVirtualMemory);
GUEST_FUNCTION_HOOK(__imp__NtQueryVirtualMemory, gow2k::NtQueryVirtualMemory);
GUEST_FUNCTION_HOOK(__imp__ExAllocatePoolTypeWithTag, gow2k::ExAllocatePoolTypeWithTag);
GUEST_FUNCTION_HOOK(__imp__ExAllocatePool, gow2k::ExAllocatePool);
GUEST_FUNCTION_HOOK(__imp__ExFreePool, gow2k::ExFreePool);
GUEST_FUNCTION_HOOK(__imp__MmQueryStatistics, gow2k::MmQueryStatistics);
GUEST_FUNCTION_HOOK(__imp__MmGetPhysicalAddress, gow2k::MmGetPhysicalAddress);
GUEST_FUNCTION_HOOK(__imp__MmSetAddressProtect, gow2k::MmSetAddressProtect);
GUEST_FUNCTION_HOOK(__imp__MmQueryAddressProtect, gow2k::MmQueryAddressProtect);
