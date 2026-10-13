// Debug tracing of kernel imports. GOW3_TRACE_IMPORTS=1 logs every call; GOW3_CHECK_HEAP=1 checks the host-side heap'
// invariants after every import and reports the first call after which they no longer hold.
#include <stdafx.h>
#include <kernel/heap.h>
#include <os/logger.h>

static const bool g_trace = getenv("GOW3_TRACE_IMPORTS") != nullptr;
static const bool g_check = getenv("GOW3_CHECK_HEAP") != nullptr;
static std::atomic<bool> g_broken{ false };

void TraceImport(const char* name, bool after)
{
    if (g_trace && !after)
        LOGFN("> {} (thread {})", name + 7, GetCurrentThreadId());
    if (g_check && !g_broken)
    {
        bool ok;
        { std::lock_guard l(g_userHeap.mutex); ok = o1heapDoInvariantsHold(g_userHeap.heap); }
        if (!ok && !g_broken.exchange(true))
            LOGFN_ERROR("heap invariants broken {} {}", after ? "after" : "before", name + 7);
    }
}
