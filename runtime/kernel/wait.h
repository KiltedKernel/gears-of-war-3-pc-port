#pragma once
// Timed waits for kernel objects (GoW2 runtime). std::atomic::wait has no timeout; on Windows it is built on
// WaitOnAddress, so waiting here with WaitOnAddress is woken by the same notify_one/notify_all calls.
#include <atomic>
#include <cstdint>
#include <windows.h>
#pragma comment(lib, "synchronization.lib")

namespace kwait
{
    struct Deadline
    {
        uint32_t timeout;   // ms, INFINITE = forever
        uint64_t end;
        explicit Deadline(uint32_t timeout) : timeout(timeout), end(GetTickCount64() + (timeout == INFINITE ? 0 : timeout)) {}
        // ms left to sleep, 0 = expired
        uint32_t Remaining() const
        {
            if (timeout == INFINITE) return INFINITE;
            uint64_t now = GetTickCount64();
            return now >= end ? 0 : uint32_t(end - now);
        }
    };

    // sleep until 'a' may no longer equal 'observed' (or the deadline passes); false once the deadline has passed
    template <typename T>
    bool Sleep(const std::atomic<T>& a, T observed, const Deadline& d)
    {
        uint32_t ms = d.Remaining();
        if (ms == 0) return false;
        WaitOnAddress((volatile void*)&a, &observed, sizeof(T), ms);
        return true;
    }
}

namespace kwait
{
    // bumped on every signal of any kernel object: multi-object waits sleep on it
    inline std::atomic<uint32_t> g_generation{ 0 };
    inline void Signaled() { g_generation.fetch_add(1); g_generation.notify_all(); }
}
