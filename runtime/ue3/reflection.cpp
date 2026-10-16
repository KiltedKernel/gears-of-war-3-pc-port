// UE3 reflection over guest memory (layout found with tools/ue3dump.py on GOW3_MEM_DUMP dumps; MODLOG 2026-10-10):
//  FNameEntry  {u32 Index (= n * 2), u32 HashNext, char Name[]}, packed in a pool; GNames = array of entry pointers
//  UObject     +0 vtable, +32 ObjectInternalInteger (slot in GObjObjects; Gears 2: +4), +40 Outer, +44 Name (index,
//              number), +52 Class. Gears of War 3 (engine 828): +4 HashNext, +8 ObjectFlags (64-bit), +28 LinkerIndex.
//  Gears 3 indexes: "GameViewportClient" 10043, "Default__GameViewportClient" 10044 (Gears 2: 1968, 1969); GNames at
//  02130000 and GObjObjects at 00030000 in the dumps so far. Name entries are packed without alignment.
//  UStruct     +64 super struct, +68 first child (UField), +72 properties size;  UField +60 Next   (Gears 2: 60/76/64)
//  UProperty   +64 ArrayDim, +68 ElementSize, +84 Offset;  UBoolProperty +104 BitMask            (Gears 2: 100/132)
#include <stdafx.h>
#include "reflection.h"
#include <kernel/memory.h>
#include <os/logger.h>
#include <atomic>
#include <cstring>
#include <mutex>
#include <unordered_map>
#include <vector>

extern thread_local bool g_probingGuestMemory;   // crash.cpp

namespace ue3
{
namespace
{
    constexpr uint32_t kSlot = 32;                      // UObject::ObjectInternalInteger
    constexpr uint32_t kViewportName = 10043;           // "GameViewportClient"; the next name is Default__ of it
    std::atomic<bool> g_ready{ false };
    std::mutex g_mutex;
    uint32_t g_names = 0, g_objects = 0, g_objectCount = 0;
    // the engine's globals (TArray {data, count, max} in .data): the arrays are reallocated as they grow, so the data
    // pointers are read again on every lookup (Refresh) instead of being kept from the first search
    constexpr uint32_t kGNamesVar = 0x8343D148, kGObjectsVar = 0x8343D188;
    std::unordered_map<std::string, uint32_t> g_classCache;

    // a guarded copy out of guest memory (SEH: a page that is not committed raises, the copy reports failure)
    bool CopyRaw(void* out, const void* src, size_t size)
    {
        __try { memcpy(out, src, size); return true; }
        __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
    }

    // only plain committed memory: a guard page (guest stacks) would lose its guard, and the GPU register pages
    // fault on purpose for the emulated device; neither may be touched from here
    bool PageReadable(const uint8_t* host)
    {
        MEMORY_BASIC_INFORMATION mbi;
        if (!VirtualQuery(host, &mbi, sizeof(mbi))) return false;
        return mbi.State == MEM_COMMIT && !(mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) && mbi.Protect != 0;
    }

    bool Copy(uint32_t address, void* out, size_t size)
    {
        if (address < 0x10000 || uint64_t(address) + size > 0xC0000000ull) return false;
        if (address >= 0x7FC00000 && address < 0x80000000) return false;   // GPU registers
        const uint8_t* host = (const uint8_t*)g_memory.Translate(address);
        if (!PageReadable(host) || !PageReadable(host + size - 1)) return false;
        g_probingGuestMemory = true;    // the crash reporter lets this fault through to the __except
        const bool ok = CopyRaw(out, g_memory.Translate(address), size);
        g_probingGuestMemory = false;
        return ok;
    }

    // a region scan under SEH: a region can be released between VirtualQuery and the read (the game allocates
    // and frees while this runs on another thread); a fault ends that region's scan instead of the process
    template <class F> bool GuardedScan(F& f, uint32_t a, const uint8_t* host, size_t n)
    {
        bool r = true;
        g_probingGuestMemory = true;
        __try { r = f(a, host, n); }
        __except (GetExceptionCode() == EXCEPTION_ACCESS_VIOLATION ? EXCEPTION_EXECUTE_HANDLER : EXCEPTION_CONTINUE_SEARCH) { r = true; }
        g_probingGuestMemory = false;
        return r;
    }

    // committed guest regions, for the one-time table search
    template <class F> void ForEachRegion(uint64_t from, uint64_t to, F&& f)
    {
        for (uint64_t a = from; a < to;)
        {
            MEMORY_BASIC_INFORMATION mbi;
            uint8_t* host = (uint8_t*)g_memory.Translate(uint32_t(a));
            if (!VirtualQuery(host, &mbi, sizeof(mbi))) break;
            const uint64_t end = std::min<uint64_t>(to, a + uint64_t((uint8_t*)mbi.BaseAddress + mbi.RegionSize - host));
            if (mbi.State == MEM_COMMIT && !(mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) && end > a)
                if (!GuardedScan(f, uint32_t(a), host, size_t(end - a))) return;
            a = end;
        }
    }

    std::string EntryName(uint32_t entry)
    {
        char buf[96] = {};
        if (!entry || !Copy(entry + 8, buf, sizeof(buf) - 1)) return {};
        buf[sizeof(buf) - 1] = 0;
        return buf;
    }

    // the engine's hardcoded names come first (None, then the property types: ByteProperty, IntProperty...). Not a
    // later name at a fixed index: names made while the configuration loads come before GameViewportClient, so a
    // mod's configuration fragment moved it off index 10043 and the tables were never found
    bool CheckNames(uint32_t table)
    {
        if (EntryName(Read32(table)) != "None") return false;
        for (uint32_t i = 1; i <= 3; i++)
        {
            const std::string n = EntryName(Read32(table + 4 * i));
            if (n.size() < 9 || n.compare(n.size() - 8, 8, "Property") != 0) return false;
        }
        return true;
    }

    bool CheckObjects(uint32_t table)
    {
        // every listed object's ObjectInternalInteger is its own slot
        int good = 0;
        for (uint32_t i : { 1u, 2u, 3u, 4u, 10u, 100u, 1000u, 5000u, 20000u })
        {
            const uint32_t x = Read32(table + 4 * i);
            if (x && Read32(x + kSlot) != i) return false;
            good += x != 0;
        }
        return good >= 6;
    }

    bool FindTables()
    {
        // fast path: the addresses seen in every dump so far
        if (const uint32_t n = Read32(kGNamesVar); n && CheckNames(n)) g_names = n;
        else if (static bool logged = false; n && !logged)
        {
            logged = true;
            LOGFN("ue3: names table at {:08X} not recognised (0: {}, 1: {}, 10043: {})", n, EntryName(Read32(n)),
                  EntryName(Read32(n + 4)), EntryName(Read32(n + 4 * kViewportName)));
        }
        if (const uint32_t o = Read32(kGObjectsVar); g_names && o && CheckObjects(o)) g_objects = o;
        if (!g_names)
        {
            // the name entry {kViewportName * 2, hash next, "GameViewportClient"} (any alignment), then the pointer table
            static const char name[] = "GameViewportClient";
            const uint8_t idx[4] = { 0, 0, uint8_t((kViewportName * 2) >> 8), uint8_t(kViewportName * 2) };
            uint32_t entry = 0;
            ForEachRegion(0x10000, 0xC0000000, [&](uint32_t base, const uint8_t* host, size_t size) {
                for (size_t i = 0; i + 8 + sizeof(name) <= size; i++)
                    if (host[i + 3] == idx[3] && memcmp(host + i, idx, 4) == 0 && memcmp(host + i + 8, name, sizeof(name)) == 0) { entry = base + uint32_t(i); return false; }
                return true;
            });
            if (!entry) return false;
            const uint32_t be = ByteSwap(entry);
            ForEachRegion(0x10000, 0xC0000000, [&](uint32_t base, const uint8_t* host, size_t size) {
                const uint32_t* w = (const uint32_t*)host;
                for (size_t i = 0; i < size / 4; i++)
                    if (w[i] == be && base + 4 * i >= 4 * kViewportName && CheckNames(uint32_t(base + 4 * i - 4 * kViewportName))) { g_names = uint32_t(base + 4 * i - 4 * kViewportName); return false; }
                return true;
            });
            if (!g_names) return false;
        }
        if (!g_objects)
        {
            // objects named Default__GameViewportClient (number 0, at +44) -> their slot (+kSlot) -> the table
            // entry pointing back at them; raw word compares, only candidates are validated
            std::vector<std::pair<uint32_t, uint32_t>> named;   // (object, slot)
            const uint32_t nDefault = ByteSwap(kViewportName + 1);
            ForEachRegion(0x10000, 0xA0000000, [&](uint32_t base, const uint8_t* host, size_t size) {
                const uint32_t* w = (const uint32_t*)host;
                for (size_t i = 11; i + 1 < size / 4; i++)
                    if (w[i] == nDefault && w[i + 1] == 0)
                    {
                        const uint32_t obj = uint32_t(base + 4 * (i - 11)), slot = ByteSwap(w[i - 11 + kSlot / 4]);
                        if (slot < 2000000) named.push_back({ obj, slot });
                    }
                return named.size() < 64;
            });
            for (auto [obj, slot] : named)
            {
                const uint32_t be = ByteSwap(obj);
                ForEachRegion(0x10000, 0xA0000000, [&](uint32_t base, const uint8_t* host, size_t size) {
                    const uint32_t* w = (const uint32_t*)host;
                    for (size_t i = 0; i < size / 4; i++)
                        if (w[i] == be && base + 4 * i >= 4ull * slot && CheckObjects(uint32_t(base + 4 * i - 4 * slot)))
                        {
                            g_objects = uint32_t(base + 4 * i - 4 * slot);
                            return false;
                        }
                    return true;
                });
                if (g_objects) break;
            }
            if (!g_objects) return false;
        }
        // the table's length: up to the last slot whose object agrees with its index (holes are allowed)
        uint32_t n = 0, last = 0;
        for (uint32_t bad = 0; n < 3000000 && bad < 4096; n++)
        {
            if (!Readable(g_objects + 4 * n, 4)) break;
            const uint32_t x = Read32(g_objects + 4 * n);
            if (x && Read32(x + kSlot) == n) { last = n; bad = 0; } else bad++;
        }
        g_objectCount = last + 1;
        return true;
    }
}

namespace
{
    // current table addresses and object count from the engine's globals
    void Refresh()
    {
        const uint32_t n = Read32(kGNamesVar), o = Read32(kGObjectsVar), count = Read32(kGObjectsVar + 4);
        if (n && n != g_names && CheckNames(n)) g_names = n;
        if (o && o != g_objects && CheckObjects(o)) g_objects = o;
        if (o == g_objects && count > g_objectCount && count < 4000000) g_objectCount = count;
    }
}

uint32_t Read32(uint32_t address)
{
    uint32_t v;
    return Copy(address, &v, 4) ? ByteSwap(v) : 0;
}

float ReadFloat(uint32_t address)
{
    const uint32_t u = Read32(address);
    float f;
    memcpy(&f, &u, 4);
    return f;
}

bool Readable(uint32_t address, uint32_t size)
{
    uint8_t probe;
    return Copy(address, &probe, 1) && Copy(address + size - 1, &probe, 1);
}

bool Ready() { return g_ready; }

bool Init()
{
    if (g_ready) return true;
    std::lock_guard lock(g_mutex);
    if (g_ready) return true;
    if (!FindTables()) return false;
    g_ready = true;
    LOGFN("ue3: names at {:08X}, objects at {:08X} ({} slots)", g_names, g_objects, g_objectCount);
    return true;
}

std::string Name(uint32_t index)
{
    static thread_local uint64_t lastRefresh = 0;
    if (GetTickCount64() - lastRefresh > 500) { Refresh(); lastRefresh = GetTickCount64(); }
    if (!g_names || index > 1000000) return {};
    return EntryName(Read32(g_names + 4 * index));
}

std::string ObjectName(uint32_t object)
{
    if (!object) return {};
    const uint32_t index = Read32(object + 44), number = Read32(object + 48);
    std::string n = Name(index);
    return number ? n + "_" + std::to_string(number - 1) : n;
}

uint32_t ClassOf(uint32_t object) { return object ? Read32(object + 52) : 0; }

uint32_t FindClass(const char* name)
{
    {
        std::lock_guard lock(g_mutex);
        auto it = g_classCache.find(name);
        if (it != g_classCache.end()) return it->second;
    }
    uint32_t found = 0;
    Refresh();                      // (the tables are found early in the boot: the count grows after)
    for (uint32_t i = 0; i < g_objectCount && !found; i++)
    {
        const uint32_t o = Read32(g_objects + 4 * i);
        if (o && ObjectName(ClassOf(o)) == "Class" && ObjectName(o) == name) found = o;
    }
    if (found) { std::lock_guard lock(g_mutex); g_classCache[name] = found; }
    return found;
}

bool IsA(uint32_t object, uint32_t cls)
{
    for (uint32_t c = ClassOf(object), guard = 0; c && guard < 64; c = Read32(c + 64), guard++)
        if (c == cls) return true;
    return false;
}

uint32_t FindInstance(const char* className)
{
    const uint32_t cls = FindClass(className);
    if (!cls) return 0;
    for (uint32_t i = 0; i < g_objectCount; i++)
    {
        const uint32_t o = Read32(g_objects + 4 * i);
        if (o && ClassOf(o) == cls && ObjectName(o).rfind("Default__", 0) != 0) return o;
    }
    return 0;
}

uint32_t FindObject(const char* name)
{
    Refresh();
    for (uint32_t i = 0; i < g_objectCount; i++)
    {
        const uint32_t o = Read32(g_objects + 4 * i);
        if (o && ObjectName(o) == name) return o;
    }
    return 0;
}

std::vector<uint32_t> FindInstancesOf(uint32_t cls)
{
    std::vector<uint32_t> out;
    Refresh();
    // the table grows as objects are created: extend the known length first
    for (uint32_t n = g_objectCount, bad = 0; n < 3000000 && bad < 4096; n++)
    {
        const uint32_t x = Read32(g_objects + 4 * n);
        if (x && Read32(x + kSlot) == n) { g_objectCount = n + 1; bad = 0; } else bad++;
    }
    std::vector<uint32_t> table(g_objectCount);
    if (!g_objectCount || !Copy(g_objects, table.data(), table.size() * 4)) return out;
    std::unordered_map<uint32_t, bool> isSub;   // class -> derives from cls
    for (uint32_t raw : table)
    {
        const uint32_t o = ByteSwap(raw);
        if (!o) continue;
        const uint32_t c = ClassOf(o);
        if (!c) continue;
        auto it = isSub.find(c);
        if (it == isSub.end())
        {
            bool sub = false;
            for (uint32_t k = c, guard = 0; k && guard < 64; k = Read32(k + 64), guard++)
                if (k == cls) { sub = true; break; }
            it = isSub.emplace(c, sub).first;
        }
        if (it->second && ObjectName(o).rfind("Default__", 0) != 0) out.push_back(o);
    }
    return out;
}

std::vector<uint32_t> AllObjects()
{
    std::vector<uint32_t> out;
    Refresh();
    for (uint32_t n = g_objectCount, bad = 0; n < 3000000 && bad < 4096; n++)
    {
        const uint32_t x = Read32(g_objects + 4 * n);
        if (x && Read32(x + kSlot) == n) { g_objectCount = n + 1; bad = 0; } else bad++;
    }
    std::vector<uint32_t> table(g_objectCount);
    if (!g_objectCount || !Copy(g_objects, table.data(), table.size() * 4)) return out;
    out.reserve(table.size());
    for (uint32_t raw : table) if (raw) out.push_back(ByteSwap(raw));
    return out;
}

namespace
{
    uint32_t FindProperty(uint32_t cls, const char* property)
    {
        for (uint32_t c = cls, guard = 0; c && guard < 64; c = Read32(c + 64), guard++)
            for (uint32_t f = Read32(c + 68), n = 0; f && n < 10000; f = Read32(f + 60), n++)
                if (ObjectName(f) == property) return f;
        return 0;
    }
}

int PropertyOffset(uint32_t cls, const char* property)
{
    const uint32_t p = FindProperty(cls, property);
    return p ? int(Read32(p + 84)) : -1;
}

uint32_t BoolMask(uint32_t cls, const char* property)
{
    const uint32_t p = FindProperty(cls, property);
    static const uint32_t boolProperty = FindClass("BoolProperty");
    return p && ClassOf(p) == boolProperty ? Read32(p + 104) : 0;   // 0 for any other property type
}
}
