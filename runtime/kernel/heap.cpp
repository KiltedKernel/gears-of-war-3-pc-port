#include <stdafx.h>
#include "heap.h"
#include "memory.h"
#include "function.h"
#include "physical.h"
#include <os/logger.h>

constexpr size_t RESERVED_BEGIN = 0x7FEA0000;
constexpr size_t RESERVED_END = 0xA0000000;

void Heap::Init()
{
    // GoW2: 0x00000000-0x7EFFFFFF is the title's NtAllocateVirtualMemory space (kernel/gow2_memory.cpp); the host-side
    // heap (kernel objects, thread stacks) uses the otherwise unused 0x90000000-0x9FFFFFFF range. Physical memory has
    // its own allocator (kernel/physical.cpp), shared with the frames that back virtual memory.
    heap = o1heapInit(g_memory.Translate(0x90000000), 0xA0000000 - 0x90000000);
}

void* Heap::Alloc(size_t size)
{
    std::lock_guard lock(mutex);

    return o1heapAllocate(heap, std::max<size_t>(1, size));
}

// physical memory in the 0xA0000000 (64 KB page) window
void* Heap::AllocPhysical(size_t size, size_t alignment)
{
    uint32_t off = physical::Alloc(uint32_t(size), uint32_t(alignment));
    if (off == 0xFFFFFFFF)
    {
        LOGFN_ERROR("AllocPhysical: out of physical memory ({:X} bytes, {:X} free)", size, physical::FreeBytes());
        return nullptr;
    }
    return g_memory.Translate(0xA0000000 | off);
}

void Heap::Free(void* ptr)
{
    if (ptr >= g_memory.Translate(0xA0000000))
        physical::Free(physical::ToPhysical(g_memory.MapVirtual(ptr)));
    else
    {
        std::lock_guard lock(mutex);
        o1heapFree(heap, ptr);
    }
}

size_t Heap::Size(void* ptr)
{
    if (!ptr)
        return 0;
    if (ptr >= g_memory.Translate(0xA0000000))
        return physical::SizeOf(physical::ToPhysical(g_memory.MapVirtual(ptr)));
    return *((size_t*)ptr - 2) - O1HEAP_ALIGNMENT; // relies on fragment header in o1heap.c
}

uint32_t RtlAllocateHeap(uint32_t heapHandle, uint32_t flags, uint32_t size)
{
    void* ptr = g_userHeap.Alloc(size);
    if ((flags & 0x8) != 0)
        memset(ptr, 0, size);

    assert(ptr);
    return g_memory.MapVirtual(ptr);
}

uint32_t RtlReAllocateHeap(uint32_t heapHandle, uint32_t flags, uint32_t memoryPointer, uint32_t size)
{
    void* ptr = g_userHeap.Alloc(size);
    if ((flags & 0x8) != 0)
        memset(ptr, 0, size);

    if (memoryPointer != 0)
    {
        void* oldPtr = g_memory.Translate(memoryPointer);
        memcpy(ptr, oldPtr, std::min<size_t>(size, g_userHeap.Size(oldPtr)));
        g_userHeap.Free(oldPtr);
    }

    assert(ptr);
    return g_memory.MapVirtual(ptr);
}

uint32_t RtlFreeHeap(uint32_t heapHandle, uint32_t flags, uint32_t memoryPointer)
{
    if (memoryPointer != NULL)
        g_userHeap.Free(g_memory.Translate(memoryPointer));

    return true;
}

uint32_t RtlSizeHeap(uint32_t heapHandle, uint32_t flags, uint32_t memoryPointer)
{
    if (memoryPointer != NULL)
        return (uint32_t)g_userHeap.Size(g_memory.Translate(memoryPointer));

    return 0;
}

uint32_t XAllocMem(uint32_t size, uint32_t flags)
{
    void* ptr = (flags & 0x80000000) != 0 ?
        g_userHeap.AllocPhysical(size, (1ull << ((flags >> 24) & 0xF))) :
        g_userHeap.Alloc(size);

    if ((flags & 0x40000000) != 0)
        memset(ptr, 0, size);

    assert(ptr);
    return g_memory.MapVirtual(ptr);
}

void XFreeMem(uint32_t baseAddress, uint32_t flags)
{
    if (baseAddress != NULL)
        g_userHeap.Free(g_memory.Translate(baseAddress));
}

// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_STUB(sub_82BD7788); // HeapCreate
// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_STUB(sub_82BD9250); // HeapDestroy

// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_HOOK(sub_82BD7D30, RtlAllocateHeap);
// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_HOOK(sub_82BD8600, RtlFreeHeap);
// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_HOOK(sub_82BD88F0, RtlReAllocateHeap);
// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_HOOK(sub_82BD6FD0, RtlSizeHeap);

// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_HOOK(sub_831CC9C8, XAllocMem);
// SWA address (Sonic Unleashed), not GoW2: re-target before enabling
// GUEST_FUNCTION_HOOK(sub_831CCA60, XFreeMem);
