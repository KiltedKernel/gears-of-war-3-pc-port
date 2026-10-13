// GOW3_PROFILE=1 (development): a sampling profiler. Every millisecond each guest thread is suspended for a moment
// and its instruction pointer recorded; every 10 s the busiest functions of each busy thread are logged, resolved
// with gow3.pdb (recompiled guest code shows as __imp__sub_XXXXXXXX; waits show as ntdll/kernel functions).
// GOW3_PROFILE=all samples every thread of the process (the GPU command processor and driver threads too).
#include <stdafx.h>
#include <dbghelp.h>
#include <tlhelp32.h>
#include <os/logger.h>
#include <algorithm>
#include <chrono>
#include <map>
#include <thread>
#include <unordered_map>
#include <vector>

namespace watch { std::vector<HANDLE> GuestThreads(); }   // watch.cpp

namespace profiler
{
void Start()
{
    if (!getenv("GOW3_PROFILE")) return;
    std::thread([] {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
        HANDLE proc = GetCurrentProcess();
        SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS);
        SymInitialize(proc, nullptr, TRUE);
        std::map<HANDLE, std::unordered_map<uint64_t, int>> samples;   // thread -> rip -> count
        uint64_t next = GetTickCount64() + 10000;
        const bool all = strcmp(getenv("GOW3_PROFILE"), "all") == 0;
        std::vector<HANDLE> threads;
        uint64_t refresh = 0;
        for (;;)
        {
            Sleep(1);
            if (!all) threads = watch::GuestThreads();
            else if (GetTickCount64() >= refresh)
            {
                refresh = GetTickCount64() + 2000;
                std::map<DWORD, HANDLE> keep;
                for (HANDLE h : threads) keep[GetThreadId(h)] = h;
                HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
                THREADENTRY32 te{ sizeof(te) };
                for (BOOL ok = Thread32First(snap, &te); ok; ok = Thread32Next(snap, &te))
                    if (te.th32OwnerProcessID == GetCurrentProcessId() && te.th32ThreadID != GetCurrentThreadId() && !keep.count(te.th32ThreadID))
                        if (HANDLE h = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, te.th32ThreadID))
                            keep[te.th32ThreadID] = h;
                CloseHandle(snap);
                threads.clear();
                for (auto& [id, h] : keep) threads.push_back(h);
            }
            for (HANDLE t : threads)
            {
                if (SuspendThread(t) == DWORD(-1)) continue;
                CONTEXT c{}; c.ContextFlags = CONTEXT_CONTROL;
                const bool got = GetThreadContext(t, &c);
                ResumeThread(t);                               // before anything that allocates (the thread may hold a heap lock)
                if (got) samples[t][c.Rip]++;
            }
            if (GetTickCount64() < next) continue;
            next = GetTickCount64() + 10000;
            int busiest = 0;
            // busiest first: threads by samples not in waits
            std::vector<std::pair<int, HANDLE>> order;
            for (auto& [t, m] : samples) { int n = 0; for (auto& [r, c] : m) n += c; order.push_back({ n, t }); }
            for (auto& [n0, t] : order)
            {
                auto& m = samples[t];
                // by function: resolve each sampled address to its symbol
                std::unordered_map<std::string, int> byFunc;
                int total = 0;
                for (auto& [rip, n] : m)
                {
                    alignas(SYMBOL_INFO) char buf[sizeof(SYMBOL_INFO) + 256];
                    auto* sym = (SYMBOL_INFO*)buf;
                    sym->SizeOfStruct = sizeof(SYMBOL_INFO); sym->MaxNameLen = 255;
                    DWORD64 disp = 0;
                    std::string name = SymFromAddr(proc, rip, &disp, sym) ? sym->Name : fmt::format("{:X}", rip);
                    byFunc[name] += n; total += n;
                }
                std::vector<std::pair<int, std::string>> top;
                int waiting = 0;
                for (auto& [f, n] : byFunc)
                {
                    top.push_back({ n, f });
                    if (f.find("Wait") != std::string::npos || f.find("Delay") != std::string::npos || f == "NtRemoveIoCompletion") waiting += n;
                }
                std::sort(top.rbegin(), top.rend());
                if (all && waiting > total * 0.95) continue;    // idle threads
                std::string line;
                for (size_t k = 0; k < std::min<size_t>(top.size(), 8); k++)
                    line += fmt::format(" {:.0f}% {}", 100.0 * top[k].first / total, top[k].second);
                LOGFN("profile: thread {} ({} samples):{}", GetThreadId(t), total, line);
                if (++busiest >= 12) break;
            }
            samples.clear();
        }
    }).detach();
}
}
