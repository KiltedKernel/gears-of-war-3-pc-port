// Crash reporter: on an access violation / illegal instruction, log the exception, the host call stack resolved to
// recompiled guest functions (sub_XXXXXXXX via the PDB), and the guest registers of the faulting thread.
#include <stdafx.h>
#include <mutex>
#include <string>
#include <dbghelp.h>
#include <os/logger.h>
#include <kernel/memory.h>
#include <cpu/ppc_context.h>
#include <ue3/reflection.h>
#pragma comment(lib, "dbghelp.lib")

static LONG WINAPI OnCrash(EXCEPTION_POINTERS* ep)
{
    static std::atomic<bool> once{ false };
    if (once.exchange(true))
        return EXCEPTION_CONTINUE_SEARCH;

    auto* er = ep->ExceptionRecord;
    HANDLE proc = GetCurrentProcess();
    SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_LOAD_LINES);
    SymInitialize(proc, nullptr, TRUE);

    uint64_t fault = er->NumberParameters >= 2 ? er->ExceptionInformation[1] : 0;
    std::string where = "";
    if (g_memory.IsInMemoryRange((void*)fault))
        where = fmt::format(" (guest address {:08X})", uint32_t(fault - (uint64_t)g_memory.base));
    LOGFN_ERROR("CRASH: exception {:08X} at {:p}, {} {:X}{}", er->ExceptionCode, er->ExceptionAddress,
        er->NumberParameters ? (er->ExceptionInformation[0] ? "writing" : "reading") : "", fault, where);

    if (auto* g = GetPPCContext())
        LOGFN_ERROR("guest: r1={:08X} r3={:08X} r4={:08X} r5={:08X} r6={:08X} r11={:08X} r31={:08X} lr={:08X}",
            g->r1.u32, g->r3.u32, g->r4.u32, g->r5.u32, g->r6.u32, g->r11.u32, g->r31.u32, uint32_t(g->lr));
    // registers that hold UE3 objects, by name (which object the crashing code was working on)
    if (auto* g = GetPPCContext(); g && ue3::Ready())
    {
        const PPCRegister* regs[] = { &g->r3, &g->r4, &g->r5, &g->r6, &g->r7, &g->r8, &g->r9, &g->r10, &g->r24,
                                      &g->r25, &g->r26, &g->r27, &g->r28, &g->r29, &g->r30, &g->r31 };
        const int numbers[] = { 3, 4, 5, 6, 7, 8, 9, 10, 24, 25, 26, 27, 28, 29, 30, 31 };
        for (int k = 0; k < 16; k++)
        {
            const uint32_t o = regs[k]->u32;
            if (o < 0x40000000 || !ue3::Readable(o, 64)) continue;
            const uint32_t cls = ue3::ClassOf(o);
            if (!cls || !ue3::Readable(cls, 64)) continue;
            const std::string name = ue3::ObjectName(o), cname = ue3::ObjectName(cls);
            if (name.empty() || cname.empty()) continue;
            LOGFN_ERROR("guest: r{} = {} ({}) in {}", numbers[k], name, cname, ue3::ObjectName(ue3::Read32(o + 40)));
        }
    }

    // walk the host stack; recompiled functions are named sub_<guest address>
    CONTEXT ctx = *ep->ContextRecord;
    STACKFRAME64 frame{};
    frame.AddrPC.Offset = ctx.Rip; frame.AddrPC.Mode = AddrModeFlat;
    frame.AddrFrame.Offset = ctx.Rbp; frame.AddrFrame.Mode = AddrModeFlat;
    frame.AddrStack.Offset = ctx.Rsp; frame.AddrStack.Mode = AddrModeFlat;
    alignas(SYMBOL_INFO) char buf[sizeof(SYMBOL_INFO) + 256];
    auto* sym = (SYMBOL_INFO*)buf;
    for (int i = 0; i < 32 && StackWalk64(IMAGE_FILE_MACHINE_AMD64, proc, GetCurrentThread(), &frame, &ctx, nullptr,
        SymFunctionTableAccess64, SymGetModuleBase64, nullptr); i++)
    {
        sym->SizeOfStruct = sizeof(SYMBOL_INFO); sym->MaxNameLen = 255;
        DWORD64 disp = 0;
        IMAGEHLP_LINE64 line{ sizeof(IMAGEHLP_LINE64) };
        DWORD ldisp = 0;
        bool hasLine = SymGetLineFromAddr64(proc, frame.AddrPC.Offset, &ldisp, &line);
        if (SymFromAddr(proc, frame.AddrPC.Offset, &disp, sym))
            LOGFN_ERROR("  #{:2} {}+{:X}{}", i, sym->Name, disp, hasLine ? fmt::format("  ({}:{})", std::filesystem::path(line.FileName).filename().string(), line.LineNumber) : "");
        else
            LOGFN_ERROR("  #{:2} {:X}", i, frame.AddrPC.Offset);
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

// set by guarded guest reads (ue3/reflection.cpp): a fault there is caught by its __try, not reported as a crash
thread_local bool g_probingGuestMemory = false;

void InstallCrashHandler()
{
    AddVectoredExceptionHandler(1, [](EXCEPTION_POINTERS* ep) -> LONG {
        if (g_probingGuestMemory) return EXCEPTION_CONTINUE_SEARCH;
        auto code = ep->ExceptionRecord->ExceptionCode;
        if (code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_ILLEGAL_INSTRUCTION || code == EXCEPTION_BREAKPOINT ||
            code == EXCEPTION_INT_DIVIDE_BY_ZERO || code == EXCEPTION_SINGLE_STEP || code == EXCEPTION_STACK_OVERFLOW || code == 0xC0000409)
            return OnCrash(ep);
        return EXCEPTION_CONTINUE_SEARCH;
    });
}

// Hang dump (GOW3_HANG_DUMP=<seconds>): after that time, log every thread's host stack resolved to guest functions.
#include <tlhelp32.h>
#include <thread>
// the calling thread's stack, recompiled guest functions by name (GOW3_FILE_STACK: who opens a package)
void LogCurrentStack(const std::string& why)
{
    static std::mutex m;
    std::lock_guard lock(m);
    HANDLE proc = GetCurrentProcess();
    static bool init = false;
    if (!init) { SymInitialize(proc, nullptr, TRUE); init = true; }
    void* frames[48];
    const USHORT n = RtlCaptureStackBackTrace(1, 48, frames, nullptr);
    alignas(SYMBOL_INFO) char buf[sizeof(SYMBOL_INFO) + 256];
    auto* sym = (SYMBOL_INFO*)buf;
    std::string line = why + ":";
    for (USHORT i = 0; i < n; i++)
    {
        sym->SizeOfStruct = sizeof(SYMBOL_INFO); sym->MaxNameLen = 255; DWORD64 d = 0;
        if (SymFromAddr(proc, DWORD64(frames[i]), &d, sym))
        {
            const std::string name = sym->Name;
            if (name.rfind("__imp__sub_", 0) == 0 || name.rfind("sub_", 0) == 0) line += " < " + name;
        }
    }
    LOGFN("{}", line);
}

static void DumpAllThreads()
{
    HANDLE proc = GetCurrentProcess();
    SymSetOptions(SYMOPT_UNDNAME | SYMOPT_DEFERRED_LOADS | SYMOPT_LOAD_LINES);
    SymInitialize(proc, nullptr, TRUE);
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te{ sizeof(te) };
    const DWORD self = GetCurrentThreadId(), pid = GetCurrentProcessId();
    for (BOOL ok = Thread32First(snap, &te); ok; ok = Thread32Next(snap, &te))
    {
        if (te.th32OwnerProcessID != pid || te.th32ThreadID == self) continue;
        HANDLE t = OpenThread(THREAD_ALL_ACCESS, FALSE, te.th32ThreadID);
        if (!t) continue;
        SuspendThread(t);
        CONTEXT ctx{}; ctx.ContextFlags = CONTEXT_FULL;
        GetThreadContext(t, &ctx);
        STACKFRAME64 frame{};
        frame.AddrPC.Offset = ctx.Rip; frame.AddrPC.Mode = AddrModeFlat;
        frame.AddrFrame.Offset = ctx.Rbp; frame.AddrFrame.Mode = AddrModeFlat;
        frame.AddrStack.Offset = ctx.Rsp; frame.AddrStack.Mode = AddrModeFlat;
        std::string line = fmt::format("thread {}:", te.th32ThreadID);
        alignas(SYMBOL_INFO) char buf[sizeof(SYMBOL_INFO) + 256];
        auto* sym = (SYMBOL_INFO*)buf;
        for (int i = 0; i < 14 && StackWalk64(IMAGE_FILE_MACHINE_AMD64, proc, t, &frame, &ctx, nullptr, SymFunctionTableAccess64, SymGetModuleBase64, nullptr); i++)
        {
            sym->SizeOfStruct = sizeof(SYMBOL_INFO); sym->MaxNameLen = 255; DWORD64 d = 0;
            line += SymFromAddr(proc, frame.AddrPC.Offset, &d, sym) ? fmt::format(" < {}", sym->Name) : fmt::format(" < {:X}", frame.AddrPC.Offset);
        }
        ResumeThread(t);
        CloseHandle(t);
        LOGFN("{}", line);
    }
    CloseHandle(snap);
}

// GOW3_MEM_DUMP=<seconds>: write the committed guest heap and one physical window (0x40000000-0xBFFFFFFF) to memdump\<address>.bin, for
// finding engine data offline (e.g. a font's character table)
static void DumpGuestMemory()
{
    CreateDirectoryW(L"memdump", nullptr);
    size_t total = 0;
    for (uint64_t a = 0x00010000; a < 0xC0000000;)
    {
        MEMORY_BASIC_INFORMATION mbi;
        uint8_t* host = (uint8_t*)g_memory.Translate(uint32_t(a));
        if (!VirtualQuery(host, &mbi, sizeof(mbi))) break;
        const uint64_t end = std::min<uint64_t>(0xC0000000, a + uint64_t((uint8_t*)mbi.BaseAddress + mbi.RegionSize - host));
        if (mbi.State == MEM_COMMIT && !(mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
        {
            char name[64];
            snprintf(name, sizeof(name), "memdump\\%08X.bin", uint32_t(a));
            if (FILE* f = fopen(name, "wb")) { fwrite(host, 1, size_t(end - a), f); fclose(f); }
            total += size_t(end - a);
        }
        a = end;
    }
    LOGFN("memory dump: {} MB written to memdump\\", total >> 20);
}

void InstallHangDump()
{
    if (const char* s = getenv("GOW3_MEM_DUMP"))
    {
        int secs = atoi(s);
        std::thread([secs] { Sleep(secs * 1000); DumpGuestMemory(); }).detach();
    }
    if (const char* s = getenv("GOW3_HANG_DUMP"))
    {
        int secs = atoi(s);
        std::thread([secs] { Sleep(secs * 1000); LOGN("---- hang dump ----"); DumpAllThreads(); }).detach();
    }
}
