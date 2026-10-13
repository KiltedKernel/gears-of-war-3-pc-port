// GOW3_FIND=<hex>[;<hex>...] (development): every 10 s, search committed guest memory for each byte pattern and log
// the guest addresses where it is (first 8 each, once per new address). Used to find where loaded data lives (e.g. a
// mesh's collision nodes); with GOW3_FIND_WATCH=1 a read watchpoint is armed on each pattern's first match
// (watch.cpp, up to four), which names the code that reads it.
#include <stdafx.h>
#include <kernel/memory.h>
#include <os/logger.h>
#include <algorithm>
#include <chrono>
#include <set>
#include <thread>
#include <vector>

namespace watch { bool Add(uint32_t guest, uint32_t len, bool reads); }   // watch.cpp

namespace mem_find
{
// one region, guarded: Xenia changes guest page protection at any time (GPU write tracking), so a read can fault
static int Scan(const uint8_t* lo, const uint8_t* hi, const uint8_t* pat, size_t n, const uint8_t** found, int max)
{
    int k = 0;
    __try
    {
        for (const uint8_t* p = lo; p + n <= hi && k < max; p++)
            if (p[0] == pat[0] && memcmp(p, pat, n) == 0) found[k++] = p;
    }
    __except (EXCEPTION_EXECUTE_HANDLER) {}
    return k;
}

void Start()
{
    const char* e = getenv("GOW3_FIND");
    if (!e) return;
    std::vector<std::vector<uint8_t>> patterns;
    for (std::string s = e; !s.empty();)
    {
        const size_t cut = s.find(';');
        const std::string hex = s.substr(0, cut);
        std::vector<uint8_t> b;
        for (size_t i = 0; i + 1 < hex.size(); i += 2) b.push_back(uint8_t(strtoul(hex.substr(i, 2).c_str(), nullptr, 16)));
        if (!b.empty()) patterns.push_back(b);
        s = cut == std::string::npos ? "" : s.substr(cut + 1);
    }
    const bool arm = getenv("GOW3_FIND_WATCH") != nullptr;   // a read watchpoint on each pattern's first match
    LOGFN("mem_find: {} pattern(s){}", patterns.size(), arm ? ", watched when found" : "");
    std::thread([patterns, arm] {
        std::vector<std::set<uint32_t>> seen(patterns.size());
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::seconds(10));
            uint8_t* base = (uint8_t*)g_memory.base;
            for (uint64_t off = 0x40000000; off < 0x80000000ull;)   // the 64 KB-page heap (loaded data); the physical views mirror it
            {
                MEMORY_BASIC_INFORMATION mbi{};
                if (!VirtualQuery(base + off, &mbi, sizeof(mbi))) break;
                const uint64_t start = uint64_t((uint8_t*)mbi.BaseAddress - base), size = mbi.RegionSize;
                if (mbi.State == MEM_COMMIT && (mbi.Protect & (PAGE_READWRITE | PAGE_READONLY | PAGE_EXECUTE_READWRITE)) &&
                    !(mbi.Protect & PAGE_GUARD))
                {
                    const uint8_t* lo = base + start;
                    const uint8_t* hi = lo + size;
                    for (size_t k = 0; k < patterns.size(); k++)
                    {
                        const uint8_t* found[8];
                        const int n = Scan(lo, hi, patterns[k].data(), patterns[k].size(), found, 8);
                        for (int j = 0; j < n; j++)
                        {
                            const uint32_t guest = uint32_t(found[j] - base);
                            if (seen[k].size() < 8 && seen[k].insert(guest).second)
                            {
                                LOGFN("mem_find: pattern {} at {:08X}", k, guest);
                                if (arm && seen[k].size() == 1) watch::Add(guest, 4, true);
                            }
                        }
                    }
                }
                off = start + size;
            }
        }
    }).detach();
}
}
