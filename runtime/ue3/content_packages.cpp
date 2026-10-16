// Content packages a mod needs (mod.ini "content = A_SF, B_SF"; GOW3_CONTENT_PACKAGES=A_SF,B_SF for testing): the
// game's own seekfree packages whose objects a mod's levels import but which the game would not load there. The case
// it was made for: a multiplayer character in the campaign (the trilogy's squad as Gears 3's classic Marcus and Dom:
// GearPawn_COGMarcusG2MP, cooked only into MPCH_COGMarcusG2MP_SF, which the multiplayer menus load on demand).
//
// Loaded as the game loads a multiplayer character (GearMPCharacterStore's native LoadPawnClass, sub_82A29D98: the
// name "MPCH_" + class + "_SF", then) UObject::LoadPackageAsync, sub_822B7328: const FString& PackageName r3,
// completion callback r4, callback user data r5, const FGuid* r6, FName PackageType r7 (64-bit; 0 = None). Called on
// the game thread once the engine is up (Default__GearTypes exists). An FString is a TArray<TCHAR> {data, count incl.
// the NUL, max}, UTF-16BE.
// Nothing references a loaded package: the next garbage collection (every map change) would drop it. So the objects
// directly in the package (its ObjectReferencers: a seekfree package lists what it carries in them) join the root set
// (ObjectFlags at +8, 64-bit big-endian: RF_RootSet 0x4000 in the low word at +12; checked against
// RF_ClassDefaultObject 0x200 on a Default__ object first). Objects are rooted as they appear, for a minute.
#include <stdafx.h>
#include <ue3/reflection.h>
#include <kernel/function.h>
#include <kernel/heap.h>
#include <kernel/memory.h>
#include <kernel/mods.h>
#include <os/logger.h>
#include <chrono>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

extern DWORD g_mainThreadId;   // main.cpp

namespace content_packages
{
namespace
{
    constexpr uint32_t kLoadPackageAsync = 0x822B7328;
    constexpr uint32_t kOuterOffset = 40, kFlagsLow = 12, kRootSet = 0x4000, kClassDefault = 0x200;

    struct Request
    {
        std::string name;
        bool sent = false;
        uint32_t package = 0;                                  // the UPackage, once it exists
        std::chrono::steady_clock::time_point seen{};
        std::set<uint32_t> rooted;
        bool done = false;
    };
    std::vector<Request> g_requests;
    bool g_init = false, g_disabled = false, g_engineUp = false;
    int g_frame = 0;

    uint32_t Alloc(uint32_t size) { return g_memory.MapVirtual(g_userHeap.Alloc(size)); }
    void Put32(uint32_t a, uint32_t v) { *(uint32_t*)g_memory.Translate(a) = ByteSwap(v); }

    void Init()
    {
        g_init = true;
        std::vector<std::string> names;
        auto add = [&](const std::string& csv)
        {
            size_t a = 0;
            while (a <= csv.size())
            {
                const size_t b = (std::min)(csv.find(',', a), csv.size());
                const std::string n = modinfo::Trim(csv.substr(a, b - a));
                if (!n.empty() && std::find(names.begin(), names.end(), n) == names.end()) names.push_back(n);
                a = b + 1;
            }
        };
        for (const auto& m : mods::List())
            if (m.Active()) for (const auto& c : m.content) add(c);
        if (const char* env = getenv("GOW3_CONTENT_PACKAGES")) add(env);
        for (auto& n : names) g_requests.push_back({ n });
        if (!g_requests.empty()) LOGFN("content packages: {} requested", g_requests.size());
    }

    // the root-set flag at +12 is where this build keeps the low word of ObjectFlags: Default__ objects have
    // RF_ClassDefaultObject there
    bool FlagsLayoutOk()
    {
        static int ok = -1;
        if (ok < 0)
        {
            const uint32_t cdo = ue3::FindObject("Default__GearTypes");
            ok = cdo && (ue3::Read32(cdo + kFlagsLow) & kClassDefault) ? 1 : 0;
            if (!ok) LOGN_WARNING("content packages: object flags not where expected; packages are loaded but not kept");
        }
        return ok == 1;
    }

    void Send(Request& r)
    {
        r.sent = true;
        const uint32_t units = uint32_t(r.name.size()) + 1;
        const uint32_t str = Alloc(12), data = Alloc(2 * units);
        for (uint32_t i = 0; i < units; i++)
            *(uint16_t*)g_memory.Translate(data + 2 * i) = ByteSwap(uint16_t(i + 1 < units ? uint8_t(r.name[i]) : 0));
        Put32(str, data); Put32(str + 4, units); Put32(str + 8, units);
        GuestToHostFunction<void>(kLoadPackageAsync, str, 0u, 0u, 0u, uint64_t(0));
        LOGFN("content packages: loading {}", r.name);
    }

    // the package's own objects (Outer +40 = the package: its ObjectReferencers, which reference everything the
    // package carries, so rooting them keeps it all) into the root set. (Not by linker: a cooked build detaches a
    // seekfree package's linker once it has loaded, every object's _Linker is null then.)
    void Root(Request& r)
    {
        const size_t before = r.rooted.size();
        for (uint32_t o : ue3::AllObjects())
        {
            if (ue3::Read32(o + kOuterOffset) != r.package || r.rooted.count(o)) continue;
            uint32_t* flags = (uint32_t*)g_memory.Translate(o + kFlagsLow);
            *flags = ByteSwap(ByteSwap(*flags) | kRootSet);
            r.rooted.insert(o);
            LOGFN("content packages: {}: keeping {} ({})", r.name, ue3::ObjectName(o), ue3::ObjectName(ue3::ClassOf(o)));
        }
        if (r.rooted.size() != before) LOGFN("content packages: {}: {} objects kept loaded", r.name, r.rooted.size());
    }
}

// from the game thread's controller poll (hid::GetState), once a frame
void OnGameThread()
{
    if (g_disabled || GetCurrentThreadId() != g_mainThreadId) return;
    if (!g_init) { Init(); if (g_requests.empty()) { g_disabled = true; return; } }
    if (++g_frame % 30 || !ue3::Ready()) return;
    if (!g_engineUp) { g_engineUp = ue3::FindObject("Default__GearTypes") != 0; if (!g_engineUp) return; }
    bool pending = false;
    for (auto& r : g_requests)
    {
        if (r.done) continue;
        pending = true;
        if (!r.sent) { Send(r); continue; }
        if (!r.package)
        {
            const uint32_t p = ue3::FindObject(r.name.c_str());
            if (p && ue3::ClassOf(p) == ue3::FindClass("Package"))
            {
                r.package = p; r.seen = std::chrono::steady_clock::now();
                LOGFN("content packages: {} is package {:08X}", r.name, p);
            }
            else if (p) LOGFN("content packages: {}: an object of that name, class {}", r.name, ue3::ObjectName(ue3::ClassOf(p)));
            continue;
        }
        if (g_frame % 120 == 0 && FlagsLayoutOk()) Root(r);
        if (std::chrono::steady_clock::now() - r.seen > std::chrono::seconds(60)) r.done = true;
    }
    if (!pending) g_disabled = true;
}
}
