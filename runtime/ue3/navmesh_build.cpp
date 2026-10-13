// GOW3_NAVMESH_BUILD (experiment): run Gears 3's leftover navmesh generator passes on the level's Pylons, in game.
// The editor's path builder that called them is compiled out; the passes are orphaned APylon member functions (one
// argument: the pylon), found by their status strings ("Exploring" sub_825BDB60, "Exploring seeds" sub_825BDEC8,
// "Simplifying mesh" sub_825BE2C8, "Splitting mesh for imported meshes" sub_825BE398, "Splitting mesh around path
// objects" sub_825BE400; MODLOG). GOW3_NAVMESH_BUILD=<hex,hex,...> picks the functions (default: those five), run in
// order on every Pylon on the game thread, 10 s after the Pylon's world becomes GWorld. Each call logs its return value and
// the pylon's navmesh (NavMeshPtr) object: the int fields of its first 0x200 bytes that changed.
#include <stdafx.h>
#include <ue3/reflection.h>
#include <kernel/function.h>
#include <cpu/ppc_context.h>
#include <kernel/memory.h>
#include <kernel/heap.h>
#include <os/logger.h>
#include <algorithm>
#include <set>
#include <sstream>
#include <unordered_set>
#include <vector>

namespace navmesh_build
{
namespace          // (also used by the traced helpers at the end)
{
    std::vector<uint32_t> g_passes;
    bool g_enabled = false, g_done = false;
    bool g_inPass = false;          // the game thread is inside a pass: traced helpers log
    std::unordered_set<uint32_t> g_inOctree;   // polygons added to their mesh's poly octree (sub_825D3648)
    int g_octreeFixes = 0;
    uint32_t g_probeWorld = 0;      // the world object the generator's line checks run against (r3 of sub_8256F2A0)
    int g_traced = 0;
    uint64_t g_seenAt = 0;

    std::vector<uint32_t> Snapshot(uint32_t object)
    {
        std::vector<uint32_t> v;
        for (uint32_t k = 0; k < 0x200; k += 4) v.push_back(object ? ue3::Read32(object + k) : 0);
        return v;
    }
}

void Init()
{
    const char* spec = getenv("GOW3_NAVMESH_BUILD");
    if (!spec) return;
    g_enabled = true;
    std::string s = spec;
    if (s == "none") {}                   // no passes (GOW3_NAVMESH_DUMP of the meshes as loaded)
    else if (s.size() > 2)
    {
        std::stringstream ss(s); std::string part;
        while (std::getline(ss, part, ',')) g_passes.push_back(uint32_t(std::stoul(part, nullptr, 16)));
    }
    else g_passes = { 0x825BDB60, 0x825BDEC8, 0x825BE2C8, 0x825BE398, 0x825BE400 };
    LOGFN("navmesh_build: {} passes armed", g_passes.size());
}

void OnGameThread()
{
    if (!g_enabled || g_done || !ue3::Ready()) return;
    static uint32_t pylonClass = 0;
    if (!pylonClass) pylonClass = ue3::FindClass("Pylon");
    if (!pylonClass) return;
    static uint64_t lastLook = 0;
    const uint64_t now = GetTickCount64();
    if (now - lastLook < 1000) return;
    lastLook = now;
    // a pylon's package: Pylon -> its Level (+40) -> TheWorld (+40) -> the map or streamed sublevel package (+40)
    auto packageOf = [](uint32_t pylon) {
        std::string s = ue3::ObjectName(ue3::Read32(ue3::Read32(ue3::Read32(pylon + 40) + 40) + 40));
        std::transform(s.begin(), s.end(), s.begin(), ::tolower);
        return s;
    };
    // GOW3_NAVMESH_LEVEL=<package>: only the pylons of that map or streamed sublevel (e.g. sp_bridge_01_s)
    std::vector<uint32_t> pylons;
    std::string want = getenv("GOW3_NAVMESH_LEVEL") ? getenv("GOW3_NAVMESH_LEVEL") : "";
    std::transform(want.begin(), want.end(), want.begin(), ::tolower);
    std::set<std::string> seenPackages;
    for (uint32_t p : ue3::FindInstancesOf(pylonClass))
    {
        const std::string pkg = packageOf(p);
        seenPackages.insert(pkg);
        if (want.empty() || pkg == want) pylons.push_back(p);
    }
    static std::set<std::string> logged;
    for (const auto& pkg : seenPackages)
        if (logged.insert(pkg).second) LOGFN("navmesh_build: pylons loaded in {}", pkg);
    if (pylons.empty()) { g_seenAt = 0; return; }
    // the game must be past the transition map: a big level loads in the background while GWorld is still
    // GearStartTransition's world, and traces into a world that is not active find nothing. (A map's own pylons
    // live in GWorld; a streamed sublevel's in that sublevel's world, part of GWorld's.)
    const uint32_t gworld = ue3::Read32(0x83429828);
    if (!gworld || ue3::ObjectName(ue3::Read32(gworld + 40)) == "GearStartTransition") { g_seenAt = 0; return; }
    const char* delayEnv = getenv("GOW3_NAVMESH_DELAY");
    const uint64_t delay = 1000ull * (delayEnv ? std::max(1, atoi(delayEnv)) : 10);
    if (!g_seenAt) { g_seenAt = now; LOGFN("navmesh_build: {} pylons, the level active; passes in {} s", pylons.size(), delay / 1000); return; }
    if (now - g_seenAt < delay) return;
    g_done = true;
    const int meshOff = ue3::PropertyOffset(ue3::ClassOf(pylons[0]), "NavMeshPtr");
    // with GIsGame set: the Scout class's default object (sub_825F8E30, cached at 83451BA8) and the scout actor
    // (sub_825F8AB8, cached), both looked up by class name, which fails once GIsGame is cleared
    const uint32_t scoutDefaults = GuestToHostFunction<uint32_t>(0x825F8E30);
    const uint32_t scout = GuestToHostFunction<uint32_t>(0x825F8AB8);
    LOGFN("navmesh_build: scout defaults {:08X} ({}), scout {:08X} ({})", scoutDefaults, ue3::ObjectName(scoutDefaults),
          scout, ue3::ObjectName(scout));
    // GOW3_NAVMESH_PROBE=x,y,z: trace tests down through that point first (line and box, two flag sets), against
    // the world the generator's traces use (r3 of its line checks: the global at 8343982C... read from the scout)
    if (const char* probe = getenv("GOW3_NAVMESH_PROBE"))
    {
        float x = 0, y = 0, z = 0, range = 300;
        sscanf(probe, "%f,%f,%f,%f", &x, &y, &z, &range);
        static uint32_t buf = 0;
        if (!buf) buf = g_memory.MapVirtual(g_userHeap.Alloc(0x400));
        auto put = [](uint32_t a, float f) { uint32_t v; memcpy(&v, &f, 4); *(uint32_t*)g_memory.Translate(a) = ByteSwap(v); };
        const uint32_t hit = buf, end = buf + 0x100, start = buf + 0x110, extent = buf + 0x120;
        put(start, x); put(start + 4, y); put(start + 8, z + range);
        put(end, x); put(end + 4, y); put(end + 8, z - range);
        const auto worlds = ue3::FindInstancesOf(ue3::FindClass("World"));
        const uint32_t gworld = ue3::Read32(0x83429828);    // GWorld (the generator's traces read it: lis -31933, -26584)
        for (uint32_t wo : worlds)
            LOGFN("navmesh_build: world {:08X} {} (outer {}){}", wo, ue3::ObjectName(wo), ue3::ObjectName(ue3::Read32(wo + 40)),
                  wo == gworld ? " = GWorld" : "");
        for (uint32_t worldObj : worlds)
        for (uint32_t flags : { 0x20DEu })
            for (float e : { 0.0f, 30.0f })
            {
                put(extent, e); put(extent + 4, e); put(extent + 8, e);
                memset(g_memory.Translate(hit), 0, 0x60);
                const uint32_t r = GuestToHostFunction<uint32_t>(0x8256F2A0, worldObj, hit, scout, end, start, flags, extent, 0u);
                std::string hits;
                for (uint32_t k = 0; k < 0x60; k += 4)
                {
                    const uint32_t v = ue3::Read32(hit + k);
                    if (v >= 0x40000000 && v < 0x60000000 && ue3::ClassOf(v)) hits += fmt::format(" +{:X}={}", k, ue3::ObjectName(v));
                }
                LOGFN("navmesh_build: probe {:08X} ({:.0f},{:.0f},{:.0f}) flags {:X} extent {} -> {} hit z {:.1f}{}", worldObj, x, y, z, flags, e, r,
                      ue3::ReadFloat(hit + 0x10), hits);
            }
    }
    for (uint32_t pylon : pylons)
    {
        for (uint32_t pass : g_passes)
        {
            const uint32_t mesh = meshOff >= 0 ? ue3::Read32(pylon + meshOff) : 0;
            const auto before = Snapshot(mesh);
            LOGFN("navmesh_build: {} pass {:08X} (mesh {:08X})...", ue3::ObjectName(pylon), pass, mesh);
            const uint64_t t0 = GetTickCount64();
            // 83420554: GIsGame (set at init by sub_8299F7B0, read in 176 places); editor-only behaviour such as the
            // border-polygon test (sub_825D98F8: false whenever it is set) is what the generator expects
            uint32_t* isGame = (uint32_t*)g_memory.Translate(0x83420554);
            const uint32_t savedIsGame = *isGame;
            if (!getenv("GOW3_NAVMESH_KEEP_GISGAME")) *isGame = 0;
            g_inPass = true; g_traced = 0; g_octreeFixes = 0;
            const uint32_t r = GuestToHostFunction<uint32_t>(pass, pylon);
            g_inPass = false;
            *isGame = savedIsGame;
            LOGFN("navmesh_build:   {} polygons added to the octree before their expansion", g_octreeFixes);
            const uint32_t mesh2 = meshOff >= 0 ? ue3::Read32(pylon + meshOff) : 0;
            const auto after = Snapshot(mesh2);
            std::string changes;
            for (size_t k = 0; k < before.size(); k++)
                if (before[k] != after[k]) changes += fmt::format(" +{:X}:{}->{}", 4 * k, int32_t(before[k]), int32_t(after[k]));
            LOGFN("navmesh_build:   returned {} in {} ms; mesh {:08X}{}", r, GetTickCount64() - t0, mesh2,
                  changes.empty() ? " unchanged" : changes.substr(0, 600));
        }
    }
    LOGFN("navmesh_build: done");
    // GOW3_NAVMESH_DUMP=<file>: the generated meshes (path +648, obstacle +652 of each pylon) for tools/navmesh.py:
    // per mesh "NAVM", pylon name, then vertices (UNavigationMeshBase +0x3C: 40-byte FMeshVertex: FVector, padding,
    // +24 TArray<WORD> PolyIndices) and polygons (+0x9C: 136 bytes each: +8 PolyVerts TArray<WORD>, +36 PolyCenter,
    // +60 PolyNormal, +72 BoxBounds (FBox, 28 bytes), +124 PolyHeight), all big-endian as in guest memory
    if (const char* dump = getenv("GOW3_NAVMESH_DUMP"))
    {
        FILE* f = fopen(dump, "wb");
        if (!f) { LOGFN("navmesh_build: cannot write {}", dump); return; }
        auto u32 = [&](uint32_t v) { const uint32_t be = ByteSwap(v); fwrite(&be, 4, 1, f); };
        int bad = 0;
        auto copy = [&](uint32_t guest, uint32_t n) {
            if (guest && ue3::Readable(guest, n)) { fwrite(g_memory.Translate(guest), 1, n, f); return; }
            if (bad++ < 5) LOGFN("navmesh_build: dump: unreadable {:08X} ({} bytes): zeros", guest, n);
            static const uint8_t zeros[256] = {};
            for (uint32_t k = 0; k < n; k += 256) fwrite(zeros, 1, std::min(256u, n - k), f);
        };
        for (uint32_t pylon : pylons)
            for (uint32_t which : { 648u, 652u })
            {
                const uint32_t mesh = ue3::Read32(pylon + which);
                if (!mesh) continue;
                fwrite("NAVM", 1, 4, f);
                const std::string name = ue3::ObjectName(pylon) + (which == 648 ? ".path" : ".obstacle");
                u32(uint32_t(name.size())); fwrite(name.data(), 1, name.size(), f);
                const uint32_t verts = ue3::Read32(mesh + 0x3C), nv = ue3::Read32(mesh + 0x40);
                LOGFN("navmesh_build: dump {}: verts {:08X} x{}, polys {:08X} x{}", name, verts, nv, ue3::Read32(mesh + 0x9C), ue3::Read32(mesh + 0xA0));
                u32(nv);
                for (uint32_t k = 0; k < nv; k++)
                {
                    const uint32_t v = verts + 40 * k;
                    copy(v, 12);
                    const uint32_t idx = ue3::Read32(v + 24), n = ue3::Read32(v + 28);
                    u32(n); if (n) copy(idx, 2 * n);
                }
                const uint32_t polys = ue3::Read32(mesh + 0x9C), np = ue3::Read32(mesh + 0xA0);
                u32(np);
                for (uint32_t k = 0; k < np; k++)
                {
                    const uint32_t q = polys + 136 * k;
                    const uint32_t idx = ue3::Read32(q + 8), n = ue3::Read32(q + 12);
                    u32(n); if (n) copy(idx, 2 * n);
                    copy(q + 36, 12); copy(q + 60, 12); copy(q + 72, 28); copy(q + 124, 4);
                }
                LOGFN("navmesh_build: dumped {}: {} vertices, {} polygons", name, nv, np);
            }
        fclose(f);
    }
}
}

// traced generator helpers (logged only inside a pass, first 120 calls)
extern "C" PPC_FUNC(__imp__sub_825ABC70);
extern "C" PPC_FUNC(__imp__sub_826410D0);
extern "C" PPC_FUNC(__imp__sub_825D3648);
extern "C" PPC_FUNC(__imp__sub_825D45C0);
extern "C" PPC_FUNC(__imp__sub_825D98F8);
extern "C" PPC_FUNC(__imp__sub_825D37B0);
extern "C" PPC_FUNC(__imp__sub_825E0BC8);
extern "C" PPC_FUNC(__imp__sub_825AA7C8);
extern "C" PPC_FUNC(__imp__sub_825A8428);
extern "C" PPC_FUNC(__imp__sub_8256F2A0);
extern "C" PPC_FUNC(__imp__sub_825A82C0);
extern "C" PPC_FUNC(__imp__sub_825A9930);
extern "C" PPC_FUNC(__imp__sub_825D96C0);
extern "C" PPC_FUNC(__imp__sub_825AA178);
extern "C" PPC_FUNC(__imp__sub_825A84A0);
extern "C" PPC_FUNC(__imp__sub_826408B8);
extern "C" PPC_FUNC(__imp__sub_825AB488);
#define NAVMESH_TRACE(fn, label)                                                                                        PPC_FUNC(fn)                                                                                                            {                                                                                                                           const bool log = navmesh_build::g_inPass && navmesh_build::g_traced < 120;                                               const uint32_t r3 = ctx.r3.u32, r4 = ctx.r4.u32, r5 = ctx.r5.u32, r6 = ctx.r6.u32, r7 = ctx.r7.u32, r8 = ctx.r8.u32;      const double f1 = ctx.f1.f64;                                                                                           __imp__##fn(ctx, base);                                                                                                 if (log)                                                                                                                {                                                                                                                           navmesh_build::g_traced++;                                                                                              LOGFN("navmesh_build:     {} r3={:08X} r4={:08X} r5={:08X} r6={:08X} r7={:08X} r8={:08X} f1={:.2f} -> {:08X} [{} {} {}]",                label, r3, r4, r5, r6, r7, r8, f1, ctx.r3.u32, ue3::ObjectName(r3), ue3::ObjectName(r4), ue3::ObjectName(ue3::Read32(r4 + 40))); /* r4's outer */                                                              }                                                                                                                   }
NAVMESH_TRACE(sub_825ABC70, "AddPolyFromPoint")
NAVMESH_TRACE(sub_826410D0, "Expand")
NAVMESH_TRACE(sub_825D45C0, "825D45C0")
NAVMESH_TRACE(sub_825D98F8, "825D98F8")
NAVMESH_TRACE(sub_825D37B0, "825D37B0")
NAVMESH_TRACE(sub_825E0BC8, "825E0BC8")
NAVMESH_TRACE(sub_825AA7C8, "825AA7C8")
NAVMESH_TRACE(sub_825A8428, "825A8428")
NAVMESH_TRACE(sub_825A82C0, "825A82C0")
NAVMESH_TRACE(sub_825A9930, "825A9930")
NAVMESH_TRACE(sub_825D96C0, "825D96C0")
NAVMESH_TRACE(sub_825AA178, "825AA178")
NAVMESH_TRACE(sub_825A84A0, "825A84A0")
NAVMESH_TRACE(sub_826408B8, "826408B8")

// The poly octree (UNavigationMeshBase +500) is created by the first sub_825D3648 (add a polygon to it); the editor
// path finalises every new polygon through sub_825D9F10, which adds it, but the in-game exploration's seed polygon
// skips that, and the expansion step (sub_825AB488) gives up at once when the polygon's centre is in no polygon of
// the octree. So during a pass each polygon goes into the octree before its expansion, unless it is there already.
PPC_FUNC(sub_825D3648)
{
    if (navmesh_build::g_inPass) navmesh_build::g_inOctree.insert(ctx.r4.u32);
    __imp__sub_825D3648(ctx, base);
}
PPC_FUNC(sub_825AB488)
{
    static uint64_t calls = 0, t0 = 0;
    if (navmesh_build::g_inPass && !t0) t0 = GetTickCount64();
    if (navmesh_build::g_inPass && ++calls % 200 == 0)
    {
        const uint32_t mesh = ue3::Read32(ctx.r3.u32 + 648);
        LOGFN("navmesh_build:   progress: {} expansions, {} vertices, {} polygons, {} s", calls, int32_t(ue3::Read32(mesh + 0x40)),
              int32_t(ue3::Read32(mesh + 0x8C)), (GetTickCount64() - t0) / 1000);
    }
    if (navmesh_build::g_inPass && !navmesh_build::g_inOctree.count(ctx.r4.u32))
    {
        const uint32_t pylon = ctx.r3.u32, poly = ctx.r4.u32, mesh = ue3::Read32(pylon + 648);
        const PPCContext saved = ctx;
        GuestToHostFunction<void>(0x825D3648, mesh, poly);
        ctx = saved;
        navmesh_build::g_octreeFixes++;
    }
    __imp__sub_825AB488(ctx, base);
}

// the world point check (UWorld at r3, a check result at r4): on a hit, the named objects in the result
PPC_FUNC(sub_8256F2A0)
{
    const bool log = navmesh_build::g_inPass && navmesh_build::g_traced < 120;
    if (!log) { __imp__sub_8256F2A0(ctx, base); return; }     // (guarded reads are slow: only when logging)
    navmesh_build::g_probeWorld = ctx.r3.u32;
    const uint32_t result = ctx.r4.u32, list = ctx.r7.u32;
    const float px = ue3::ReadFloat(ctx.r6.u32), py = ue3::ReadFloat(ctx.r6.u32 + 4), pz = ue3::ReadFloat(ctx.r6.u32 + 8);
    const float sx = ue3::ReadFloat(ctx.r7.u32), sy = ue3::ReadFloat(ctx.r7.u32 + 4), sz = ue3::ReadFloat(ctx.r7.u32 + 8);
    const uint32_t flags = ctx.r8.u32;
    __imp__sub_8256F2A0(ctx, base);
    navmesh_build::g_traced++;
    std::string hits;
    for (uint32_t k = 0; k < 0x98 && ctx.r3.u32; k += 4)
    {
        const uint32_t v = ue3::Read32((k < 0x4C ? result : list - 0x4C) + k);
        if (v < 0x40000000 || v >= 0x60000000) continue;
        const std::string n = ue3::ObjectName(v);
        if (!n.empty() && n != "None") hits += fmt::format(" +{:X}={} ({})", k, n, ue3::ObjectName(ue3::ClassOf(v)));
    }
    LOGFN("navmesh_build:     line check ({:.0f},{:.0f},{:.0f}) -> ({:.0f},{:.0f},{:.0f}) flags {:X} -> {}{}", sx, sy, sz, px, py, pz,
          flags, ctx.r3.u32, hits.substr(0, 120));
}
