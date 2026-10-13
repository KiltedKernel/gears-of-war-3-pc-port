#include <stdafx.h>
#include "physical.h"
#include <map>

namespace
{
    std::mutex g_mutex;
    std::map<uint32_t, uint32_t> g_free = { { physical::kBegin, physical::kEnd - physical::kBegin } };   // start -> size
    std::map<uint32_t, uint32_t> g_used;                                                                  // start -> size

    void InsertFree(uint32_t start, uint32_t size)
    {
        auto next = g_free.lower_bound(start);
        if (next != g_free.end() && start + size == next->first)       // merge with the following block
        {
            size += next->second;
            next = g_free.erase(next);
        }
        if (next != g_free.begin())
        {
            auto prev = std::prev(next);
            if (prev->first + prev->second == start)                  // merge with the preceding block
            {
                prev->second += size;
                return;
            }
        }
        g_free[start] = size;
    }
}

uint32_t physical::Alloc(uint32_t size, uint32_t alignment, uint32_t minAddress, uint32_t maxAddress, bool topDown)
{
    size = (std::max<uint32_t>(size, 1) + 0xFFF) & ~0xFFFu;
    alignment = std::max<uint32_t>(alignment, 0x1000);
    minAddress = std::max(minAddress & 0x1FFFFFFF, kBegin);
    maxAddress = maxAddress == 0 ? kEnd : std::min<uint64_t>(uint64_t(maxAddress & 0x1FFFFFFF) + 1, kEnd);
    if (maxAddress <= kBegin) maxAddress = kEnd;   // 0x00000000 / small values mean "anywhere"

    std::lock_guard lock(g_mutex);
    auto fits = [&](uint32_t start, uint32_t len, uint32_t& at) {
        uint64_t lo = std::max(start, minAddress), hi = std::min<uint64_t>(uint64_t(start) + len, maxAddress);
        if (topDown)
        {
            if (hi < size) return false;
            uint64_t cand = (hi - size) & ~uint64_t(alignment - 1);
            if (cand < lo) return false;
            at = uint32_t(cand); return true;
        }
        uint64_t cand = (lo + alignment - 1) & ~uint64_t(alignment - 1);
        if (cand + size > hi) return false;
        at = uint32_t(cand); return true;
    };
    uint32_t at = 0;
    bool found = false;
    if (topDown)
    {
        for (auto it = g_free.rbegin(); it != g_free.rend() && !found; ++it) found = fits(it->first, it->second, at);
    }
    else
    {
        for (auto it = g_free.begin(); it != g_free.end() && !found; ++it) found = fits(it->first, it->second, at);
    }
    if (!found)
        return 0xFFFFFFFF;

    // carve [at, at+size) out of its free block
    auto blk = std::prev(g_free.upper_bound(at));
    uint32_t bStart = blk->first, bSize = blk->second;
    g_free.erase(blk);
    if (at > bStart) g_free[bStart] = at - bStart;
    if (at + size < bStart + bSize) g_free[at + size] = bStart + bSize - (at + size);
    g_used[at] = size;
    return at;
}

void physical::Free(uint32_t offset)
{
    std::lock_guard lock(g_mutex);
    auto it = g_used.find(offset & 0x1FFFFFFF);
    if (it == g_used.end())
        return;
    InsertFree(it->first, it->second);
    g_used.erase(it);
}

uint32_t physical::SizeOf(uint32_t offset)
{
    std::lock_guard lock(g_mutex);
    auto it = g_used.find(offset & 0x1FFFFFFF);
    return it == g_used.end() ? 0 : it->second;
}

uint32_t physical::FreeBytes()
{
    std::lock_guard lock(g_mutex);
    uint32_t total = 0;
    for (auto& [s, n] : g_free) total += n;
    return total;
}
