// Kernel: mutants, timers, multi-object waits, thread exit, Rtl time/memory helpers (Xenia semantics as reference).
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include <cpu/guest_thread.h>
#include "function.h"
#include "memory.h"
#include "xdm.h"
#include "wait.h"
#include <os/logger.h>
#include <condition_variable>
#include <map>
#include <ntstatus.h>

uint32_t GuestTimeoutToMilliseconds(be<int64_t>* timeout);                                  // imports.cpp
uint32_t WaitForObjects(KernelObject** objs, uint32_t count, bool waitAll, uint32_t timeout); // imports.cpp

namespace x360k
{

// handle -> object of type T, or nullptr for a bad handle or another object type
template <typename T> T* As(uint32_t handle)
{
    return IsKernelObject(handle) ? dynamic_cast<T*>(GetKernelObject(handle)) : nullptr;
}

// ---- mutant: recursive, owned by a thread ----
struct Mutant final : KernelObject
{
    std::atomic<uint32_t> owner{ 0 };   // host thread id, 0 = free
    uint32_t recursion = 0;

    explicit Mutant(bool initialOwner)
    {
        if (initialOwner) { owner = GetCurrentThreadId(); recursion = 1; }
    }

    uint32_t Wait(uint32_t timeout) override
    {
        const uint32_t me = GetCurrentThreadId();
        kwait::Deadline d(timeout);
        for (;;)
        {
            uint32_t o = owner.load();
            if (o == me) { recursion++; return STATUS_SUCCESS; }
            if (o == 0 && owner.compare_exchange_strong(o, me)) { recursion = 1; return STATUS_SUCCESS; }
            if (!kwait::Sleep(owner, o, d)) return STATUS_TIMEOUT;
        }
    }

    uint32_t Release(be<uint32_t>* previousCount)
    {
        if (owner.load() != GetCurrentThreadId())
            return STATUS_MUTANT_NOT_OWNED;
        if (previousCount) *previousCount = recursion;
        if (--recursion == 0)
        {
            owner = 0;
            owner.notify_all();
            kwait::Signaled();
        }
        return STATUS_SUCCESS;
    }
};

uint32_t NtCreateMutant(be<uint32_t>* handle, uint32_t objectAttributes, uint32_t initialOwner)
{
    *handle = GetKernelHandle(CreateKernelObject<Mutant>(initialOwner != 0));
    return STATUS_SUCCESS;
}

uint32_t NtReleaseMutant(uint32_t handle, be<uint32_t>* previousCount)
{
    auto* m = As<Mutant>(handle);
    return m ? m->Release(previousCount) : STATUS_INVALID_HANDLE;
}

// ---- timers: notification (manual reset) or synchronization (auto reset); one host scheduler thread ----
struct Timer;
namespace
{
    std::mutex g_timerMutex;
    std::condition_variable g_timerCv;
    std::multimap<std::chrono::steady_clock::time_point, std::pair<Timer*, uint64_t>> g_timerQueue;   // due -> (timer, generation)
    std::once_flag g_timerThreadOnce;
    void TimerThread();
}

struct Timer final : KernelObject
{
    const bool manualReset;
    std::atomic<bool> signaled{ false };
    uint64_t generation = 0;   // bumped by Set/Cancel: queued firings of an older generation are dropped
    uint32_t periodMs = 0;

    explicit Timer(bool manualReset) : manualReset(manualReset) {}

    uint32_t Wait(uint32_t timeout) override
    {
        kwait::Deadline d(timeout);
        for (;;)
        {
            if (manualReset ? signaled.load() : [&] { bool e = true; return signaled.compare_exchange_strong(e, false); }())
                return STATUS_SUCCESS;
            if (!kwait::Sleep(signaled, false, d)) return STATUS_TIMEOUT;
        }
    }

    void Fire()
    {
        signaled = true;
        signaled.notify_all();
        kwait::Signaled();
    }
};

namespace
{
    void TimerThread()
    {
        std::unique_lock lock(g_timerMutex);
        for (;;)
        {
            if (g_timerQueue.empty()) { g_timerCv.wait(lock); continue; }
            const auto due = g_timerQueue.begin()->first;
            if (std::chrono::steady_clock::now() < due) { g_timerCv.wait_until(lock, due); continue; }   // re-check: the queue may have changed
            auto it = g_timerQueue.begin();
            auto [timer, gen] = it->second;
            g_timerQueue.erase(it);
            if (gen != timer->generation) continue;   // cancelled or re-armed
            timer->Fire();
            if (timer->periodMs)
                g_timerQueue.emplace(std::chrono::steady_clock::now() + std::chrono::milliseconds(timer->periodMs), std::pair{ timer, gen });
        }
    }
}

uint32_t NtCreateTimer(be<uint32_t>* handle, uint32_t objectAttributes, uint32_t timerType)
{
    *handle = GetKernelHandle(CreateKernelObject<Timer>(timerType == 0));   // 0 = NotificationTimer
    return STATUS_SUCCESS;
}

uint32_t NtSetTimerEx(uint32_t handle, be<int64_t>* dueTime, uint32_t apcRoutine, uint32_t apcMode, uint32_t apcContext,
    uint32_t resume, uint32_t periodMs, be<uint32_t>* previousState)
{
    auto* t = As<Timer>(handle);
    if (!t) return STATUS_INVALID_HANDLE;
    if (apcRoutine)
    {
        static int warned = 0;
        if (warned++ < 4) LOGFN_WARNING("NtSetTimerEx: timer APC routine {:08X} not delivered", apcRoutine);
    }
    std::call_once(g_timerThreadOnce, [] { std::thread(TimerThread).detach(); });
    std::lock_guard lock(g_timerMutex);
    if (previousState) *previousState = t->signaled.load();
    t->signaled = false;
    t->periodMs = periodMs;
    const uint64_t gen = ++t->generation;
    const uint32_t ms = GuestTimeoutToMilliseconds(dueTime);
    g_timerQueue.emplace(std::chrono::steady_clock::now() + std::chrono::milliseconds(ms == INFINITE ? 0 : ms), std::pair{ t, gen });
    g_timerCv.notify_all();
    return STATUS_SUCCESS;
}

uint32_t NtCancelTimer(uint32_t handle, be<uint32_t>* currentState)
{
    auto* t = As<Timer>(handle);
    if (!t) return STATUS_INVALID_HANDLE;
    std::lock_guard lock(g_timerMutex);
    if (currentState) *currentState = t->signaled.load();
    ++t->generation;
    return STATUS_SUCCESS;
}

// ---- multi-object wait on handles ----
uint32_t NtWaitForMultipleObjectsEx(uint32_t count, be<uint32_t>* handles, uint32_t waitType, uint32_t waitMode, uint32_t alertable, be<int64_t>* timeout)
{
    thread_local std::vector<KernelObject*> objs;
    objs.resize(count);
    for (uint32_t i = 0; i < count; i++)
    {
        uint32_t h = handles[i];
        objs[i] = IsKernelObject(h) ? GetKernelObject(h) : nullptr;
        if (!objs[i]) return STATUS_INVALID_HANDLE;
    }
    return WaitForObjects(objs.data(), count, waitType == 0, GuestTimeoutToMilliseconds(timeout));   // 0 = WaitAll
}

// ---- threads ----
void ExTerminateThread(uint32_t exitCode)
{
    throw GuestThreadExit{ exitCode };   // caught in GuestThreadFunc (cpu/guest_thread.cpp)
}

// ---- Rtl ----
void RtlFillMemoryUlong(uint32_t destination, uint32_t length, uint32_t pattern)
{
    auto* p = (uint32_t*)g_memory.Translate(destination);
    const uint32_t v = ByteSwap(pattern);   // the guest reads back 'pattern'
    for (uint32_t i = 0; i < length / 4; i++) p[i] = v;
}

void RtlTimeToTimeFields(be<int64_t>* time, XTIME_FIELDS* fields)
{
    const uint64_t t = uint64_t(int64_t(*time));
    FILETIME ft{ DWORD(t), DWORD(t >> 32) };
    SYSTEMTIME st{};
    FileTimeToSystemTime(&ft, &st);
    fields->Year = st.wYear; fields->Month = st.wMonth; fields->Day = st.wDay; fields->Hour = st.wHour;
    fields->Minute = st.wMinute; fields->Second = st.wSecond; fields->Milliseconds = st.wMilliseconds; fields->Weekday = st.wDayOfWeek;
}

uint32_t RtlTimeFieldsToTime(XTIME_FIELDS* fields, be<int64_t>* time)
{
    SYSTEMTIME st{ fields->Year, fields->Month, fields->Weekday, fields->Day, fields->Hour, fields->Minute, fields->Second, fields->Milliseconds };
    FILETIME ft;
    if (!SystemTimeToFileTime(&st, &ft)) return FALSE;
    *time = int64_t((uint64_t(ft.dwHighDateTime) << 32) | ft.dwLowDateTime);
    return TRUE;
}

// ---- lock-free singly linked lists. Guest SLIST_HEADER: next (u32), depth (u16), sequence (u16), all big-endian; the
// title pushes inline with ldarx/stdcx. (a real CAS in the recompiled code), so a 64-bit CAS on the same 8 bytes interoperates.
namespace
{
    struct SListHeader { uint32_t next; uint16_t depth, sequence; };   // decoded (host order)
    uint64_t RawLoad(uint32_t at) { return *(volatile uint64_t*)g_memory.Translate(at); }
    SListHeader Decode(uint64_t raw)
    {
        const uint8_t* b = (const uint8_t*)&raw;
        return { ByteSwap(*(const uint32_t*)b), ByteSwap(*(const uint16_t*)(b + 4)), ByteSwap(*(const uint16_t*)(b + 6)) };
    }
    uint64_t Encode(const SListHeader& h)
    {
        uint64_t raw;
        uint8_t* b = (uint8_t*)&raw;
        *(uint32_t*)b = ByteSwap(h.next); *(uint16_t*)(b + 4) = ByteSwap(h.depth); *(uint16_t*)(b + 6) = ByteSwap(h.sequence);
        return raw;
    }
    bool Cas(uint32_t at, uint64_t expected, uint64_t desired)
    {
        return InterlockedCompareExchange64((volatile LONG64*)g_memory.Translate(at), LONG64(desired), LONG64(expected)) == LONG64(expected);
    }
}

uint32_t InterlockedPopEntrySList(uint32_t list)
{
    for (;;)
    {
        const uint64_t raw = RawLoad(list);
        SListHeader h = Decode(raw);
        if (!h.next)
            return 0;
        const uint32_t entry = h.next;
        h.next = ByteSwap(*(uint32_t*)g_memory.Translate(entry));   // entry->Next
        h.depth--;
        if (Cas(list, raw, Encode(h)))
            return entry;
    }
}

uint32_t InterlockedFlushSList(uint32_t list)
{
    for (;;)
    {
        const uint64_t raw = RawLoad(list);
        SListHeader h = Decode(raw);
        const uint32_t first = h.next;
        h.next = 0; h.depth = 0;
        if (Cas(list, raw, Encode(h)))
            return first;
    }
}

// critical regions only defer kernel APCs, which this runtime doesn't deliver
void KeEnterCriticalRegion() {}
void KeLeaveCriticalRegion() {}

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__NtCreateMutant, x360k::NtCreateMutant);
GUEST_FUNCTION_HOOK(__imp__NtReleaseMutant, x360k::NtReleaseMutant);
GUEST_FUNCTION_HOOK(__imp__NtCreateTimer, x360k::NtCreateTimer);
GUEST_FUNCTION_HOOK(__imp__NtSetTimerEx, x360k::NtSetTimerEx);
GUEST_FUNCTION_HOOK(__imp__NtCancelTimer, x360k::NtCancelTimer);
GUEST_FUNCTION_HOOK(__imp__NtWaitForMultipleObjectsEx, x360k::NtWaitForMultipleObjectsEx);
GUEST_FUNCTION_HOOK(__imp__ExTerminateThread, x360k::ExTerminateThread);
GUEST_FUNCTION_HOOK(__imp__RtlFillMemoryUlong, x360k::RtlFillMemoryUlong);
GUEST_FUNCTION_HOOK(__imp__RtlTimeToTimeFields, x360k::RtlTimeToTimeFields);
GUEST_FUNCTION_HOOK(__imp__RtlTimeFieldsToTime, x360k::RtlTimeFieldsToTime);
GUEST_FUNCTION_HOOK(__imp__InterlockedPopEntrySList, x360k::InterlockedPopEntrySList);
GUEST_FUNCTION_HOOK(__imp__InterlockedFlushSList, x360k::InterlockedFlushSList);
GUEST_FUNCTION_HOOK(__imp__KeEnterCriticalRegion, x360k::KeEnterCriticalRegion);
GUEST_FUNCTION_HOOK(__imp__KeLeaveCriticalRegion, x360k::KeLeaveCriticalRegion);
