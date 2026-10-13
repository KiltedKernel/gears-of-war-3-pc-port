// Hardware watchpoints (debugging), up to four (DR0-DR3), in every guest thread:
//   GOW3_WATCH=<guest address hex>[,<length 1/2/4/8>][,rw]   from the start (rw: reads too, not only writes)
//   watch::Add(address, length, reads) at run time            e.g. from ue3/mem_find.cpp (GOW3_FIND_WATCH=1)
// Each hit (first 60) logs the value there, the guest registers, the guest call chain and the host stack, which
// names the recompiled function (__imp__sub_XXXXXXXX) that touched it; execution then continues.
// GOW3_WATCH_AFTER=<seconds> ignores hits until then (the data being loaded). Guest memory layout is deterministic
// run to run for the same build and inputs, so an address seen in one run can be watched in the next.
#include <stdafx.h>
#include <dbghelp.h>
#include <os/logger.h>
#include <kernel/memory.h>
#include <cpu/ppc_context.h>
#include <algorithm>
#include <mutex>
#include <thread>
#include <vector>

namespace watch
{
namespace
{
    struct Slot { uint64_t host = 0; uint32_t guest = 0, len = 4; bool reads = false; };
    Slot g_slots[4];
    std::mutex g_slotLock;                 // slots and thread list
    std::vector<HANDLE> g_threads;         // guest threads (duplicated handles), armed when a slot is added
    bool g_installed = false;
    const uint64_t g_start = GetTickCount64();    // process start (static initialization)
    uint64_t g_after = 0;
    std::atomic<int> g_hits{ 0 };
    std::mutex g_symLock;

    LONG CALLBACK OnHit(EXCEPTION_POINTERS* ep)
    {
        const uint64_t dr6 = ep->ContextRecord->Dr6;
        if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_SINGLE_STEP || !(dr6 & 0xF))
            return EXCEPTION_CONTINUE_SEARCH;     // not ours (the MMIO trap single-steps too)
        ep->ContextRecord->Dr6 = 0;
        if (GetTickCount64() < g_after || !GetPPCContext()) return EXCEPTION_CONTINUE_EXECUTION;   // too early / not guest code
        const int slot = (dr6 & 1) ? 0 : (dr6 & 2) ? 1 : (dr6 & 4) ? 2 : 3;
        const Slot s = g_slots[slot];
        // re-entrancy: anything here that touches a watched address (or logs while another hit logs) must not recurse
        static thread_local bool inside = false;
        if (inside) return EXCEPTION_CONTINUE_EXECUTION;
        const int n = g_hits++;
        if (n >= 60) return EXCEPTION_CONTINUE_EXECUTION;
        inside = true;
        std::lock_guard lock(g_symLock);
        uint64_t value = 0;
        if (!s.reads)            // (reading a read-watched address here would trigger the watchpoint again)
        {
            memcpy(&value, (void*)s.host, s.len);
            if (s.len == 2) value = ByteSwap(uint16_t(value));
            else if (s.len == 4) value = ByteSwap(uint32_t(value));
            else if (s.len == 8) value = ByteSwap(value);
        }
        auto* g = GetPPCContext();
        const std::string regs = fmt::format(" r3={:08X} r4={:08X} r5={:08X} r11={:08X} r31={:08X} lr={:08X}", g->r3.u32, g->r4.u32,
            g->r5.u32, g->r11.u32, g->r31.u32, uint32_t(g->lr));
        // guest call chain: back chain at 0(r1), each caller's LR saved at -8 from the previous frame
        std::string chain;
        uint32_t sp = g->r1.u32;
        for (int i = 0; i < 14 && sp >= 0x10000; i++)
        {
            const uint32_t next = ByteSwap(*(uint32_t*)g_memory.Translate(sp));
            if (next <= sp || next - sp > 0x100000) break;
            chain += fmt::format(" < {:08X}", ByteSwap(*(uint32_t*)g_memory.Translate(next - 8)));
            sp = next;
        }
        LOGFN("watch #{} slot {}: [{:08X}] = {:X} (thread {}){}", n, slot, s.guest, value, GetCurrentThreadId(), regs);
        LOGFN("    guest:{}", chain);
        HANDLE proc = GetCurrentProcess();
        static bool symInit = false;
        if (!symInit) { SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_LOAD_LINES); SymInitialize(proc, nullptr, TRUE); symInit = true; }
        // the instruction that touched it (no stack walk: recompiled code has no unwind data worth trusting here)
        alignas(SYMBOL_INFO) char buf[sizeof(SYMBOL_INFO) + 256];
        auto* sym = (SYMBOL_INFO*)buf;
        sym->SizeOfStruct = sizeof(SYMBOL_INFO); sym->MaxNameLen = 255;
        DWORD64 disp = 0;
        if (SymFromAddr(proc, ep->ContextRecord->Rip, &disp, sym)) LOGFN("    at {}+{:X}", sym->Name, disp);
        else LOGFN("    at {:X}", ep->ContextRecord->Rip);
        inside = false;
        return EXCEPTION_CONTINUE_EXECUTION;
    }

    // set the debug registers of one thread from the slots (suspends it unless it is the caller)
    void Arm(HANDLE thread, bool self)
    {
        auto apply = [thread] {
            CONTEXT c{}; c.ContextFlags = CONTEXT_DEBUG_REGISTERS;
            GetThreadContext(thread, &c);
            uint64_t dr7 = c.Dr7 & ~0xFFFF00FFull;
            uint64_t* dr[4] = { &c.Dr0, &c.Dr1, &c.Dr2, &c.Dr3 };
            for (int k = 0; k < 4; k++)
            {
                const Slot& s = g_slots[k];
                if (!s.host) continue;
                const uint64_t lenBits = s.len == 1 ? 0 : s.len == 2 ? 1 : s.len == 8 ? 2 : 3;
                *dr[k] = s.host;
                dr7 |= (1ull << (2 * k)) | ((s.reads ? 3ull : 1ull) << (16 + 4 * k)) | (lenBits << (18 + 4 * k));
            }
            c.Dr7 = dr7;
            SetThreadContext(thread, &c);
        };
        if (self) { std::thread([&] { SuspendThread(thread); apply(); ResumeThread(thread); }).join(); return; }
        if (SuspendThread(thread) == DWORD(-1)) return;      // exited
        apply();
        ResumeThread(thread);
    }

    void InstallHandler()
    {
        if (g_installed) return;
        g_installed = true;
        AddVectoredExceptionHandler(1, OnHit);
        if (const char* a = getenv("GOW3_WATCH_AFTER")) g_after = g_start + uint64_t(atoi(a)) * 1000;   // from process start
    }
}

// a watchpoint at run time, in the first free slot, armed in every guest thread
bool Add(uint32_t guest, uint32_t len, bool reads)
{
    std::lock_guard lock(g_slotLock);
    if (len != 1 && len != 2 && len != 4 && len != 8) len = 4;
    guest &= ~(len - 1);   // DR alignment
    for (Slot& s : g_slots)
        if (!s.host)
        {
            InstallHandler();
            s = { uint64_t(g_memory.Translate(guest)), guest, len, reads };
            for (HANDLE t : g_threads) Arm(t, false);
            LOGFN("watch: {} guest {:08X} ({} bytes)", reads ? "reads and writes of" : "writes to", guest, len);
            return true;
        }
    return false;
}

void Install()
{
    const char* e = getenv("GOW3_WATCH");
    if (!e) return;
    const char* c = strchr(e, ',');
    Add(uint32_t(strtoul(e, nullptr, 16)), c ? uint32_t(atoi(c + 1)) : 4, strstr(e, ",rw") != nullptr);
}

// the guest threads registered so far (duplicated handles; used by ue3/profiler.cpp)
std::vector<HANDLE> GuestThreads()
{
    std::lock_guard lock(g_slotLock);
    return g_threads;
}

// called by every guest thread when it starts: registered, and armed with the current slots
void ArmCurrentThread()
{
    HANDLE self = nullptr;
    DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &self, THREAD_ALL_ACCESS, FALSE, 0);
    std::lock_guard lock(g_slotLock);
    g_threads.push_back(self);
    if (std::any_of(std::begin(g_slots), std::end(g_slots), [](const Slot& s) { return s.host != 0; })) Arm(self, true);
}
}
