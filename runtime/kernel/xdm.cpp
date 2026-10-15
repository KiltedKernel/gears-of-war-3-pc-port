#include <stdafx.h>
#include "xdm.h"
#include "freelist.h"
#include <mutex>
#include <unordered_set>

Mutex g_kernelLock;

namespace
{
    std::mutex g_liveMutex;
    std::unordered_set<uint32_t> g_live;   // handles (guest addresses) of the kernel objects not yet destroyed
}

void TrackKernelObject(void* obj)
{
    std::lock_guard lock(g_liveMutex);
    g_live.insert(g_memory.MapVirtual(obj));
}

bool CloseKernelHandle(uint32_t handle)
{
    {
        std::lock_guard lock(g_liveMutex);
        if (!g_live.erase(handle)) return false;
    }
    KernelObject* obj = GetKernelObject(handle);
    obj->~KernelObject();
    g_userHeap.Free(obj);
    return true;
}

void DestroyKernelObject(KernelObject* obj)
{
    {
        std::lock_guard lock(g_liveMutex);
        g_live.erase(g_memory.MapVirtual(obj));
    }
    obj->~KernelObject();
    g_userHeap.Free(obj);
}

uint32_t GetKernelHandle(KernelObject* obj)
{
    assert(obj != GetInvalidKernelObject());
    return g_memory.MapVirtual(obj);
}

void DestroyKernelObject(uint32_t handle)
{
    DestroyKernelObject(GetKernelObject(handle));
}

bool IsKernelObject(uint32_t handle)
{
    return (handle & 0x80000000) != 0;
}

bool IsKernelObject(void* obj)
{
    return IsKernelObject(g_memory.MapVirtual(obj));
}

bool IsInvalidKernelObject(void* obj)
{
    return obj == GetInvalidKernelObject();
}
