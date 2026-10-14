#pragma once

#include <kernel/xdm.h>

// Use pthreads directly on macOS to be able to increase default stack size.
#ifdef __APPLE__
#define USE_PTHREAD 1
#endif

#ifdef USE_PTHREAD
#include <pthread.h>
#endif

#define CURRENT_THREAD_HANDLE uint32_t(-2)

struct GuestThreadContext
{
    PPCContext ppcContext{};
    uint8_t* thread = nullptr;

    GuestThreadContext(uint32_t cpuNumber);
    ~GuestThreadContext();
};

struct GuestThreadParams
{
    uint32_t function;
    uint32_t value;
    uint32_t flags;
    uint32_t value2 = 0;   // Port: second argument (r4), used when the kernel starts a thread through XAPI's startup routine
};

struct GuestThreadHandle : KernelObject
{
    GuestThreadParams params;
    std::atomic<bool> suspended;
    std::atomic<bool> finished{ false };   // Port: set when the guest function returns or ExTerminateThread runs
    uint32_t exitCode = 0;
#ifdef USE_PTHREAD
    pthread_t thread;
#else
    std::thread thread;
#endif

    GuestThreadHandle(const GuestThreadParams& params);
    ~GuestThreadHandle() override;

    uint32_t GetThreadId() const;

    uint32_t Wait(uint32_t timeout) override;
};

// thrown by ExTerminateThread; unwinds the recompiled frames back to GuestThreadFunc
struct GuestThreadExit { uint32_t code; };

struct GuestThread
{
    static uint32_t Start(const GuestThreadParams& params);
    static GuestThreadHandle* Start(const GuestThreadParams& params, uint32_t* threadId);

    static uint32_t GetCurrentThreadId();
    static GuestThreadHandle* CurrentHandle();   // null on the main thread (started without a thread object)
    static void SetLastError(uint32_t error);
    static uint32_t GetLastError();   // Port: TEB Win32LastError (shared with WSA errors on the console)

#ifdef _WIN32
    static void SetThreadName(uint32_t threadId, const char* name);
#endif
};
