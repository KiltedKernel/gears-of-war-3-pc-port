// (debugging) GOW3_COVER_TRACE=1: the player's cover search, at most 4 times a second. A Gears 3 player finds cover
// only through the navigation mesh: GearPawn.FindCoverFromLocAndDir (native: sub_82A9BAD8) gathers the polygons
// within reach (sub_825A2228: a centre at r3, the found polygons as an array at r5) and reads each polygon's PolyCover
// (+112 data, +116 count: 24-byte references, the CoverLink pointer first, the slot index at +20). Logged: the
// query, the polygons found with their cover, the search's result (found or not).
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include <kernel/memory.h>
#include <os/logger.h>
#include <ue3/reflection.h>
#include <cstdlib>
#include <atomic>

namespace
{
    bool On() { static const bool on = std::getenv("GOW3_COVER_TRACE") != nullptr; return on; }
    thread_local bool t_inFind = false;
    thread_local bool t_log = false;
}

extern "C" PPC_FUNC(__imp__sub_825A2228);
PPC_FUNC(sub_825A2228)
{
    if (!t_inFind || !t_log) { __imp__sub_825A2228(ctx, base); return; }
    const uint32_t centre = ctx.r3.u32, arr = ctx.r5.u32;
    const float cx = ue3::ReadFloat(centre), cy = ue3::ReadFloat(centre + 4), cz = ue3::ReadFloat(centre + 8);
    const double radius = ctx.f1.f64;
    __imp__sub_825A2228(ctx, base);
    const uint32_t data = ue3::Read32(arr), n = ue3::Read32(arr + 4);
    std::string polys;
    for (uint32_t k = 0; k < n && k < 24; k++)
    {
        const uint32_t poly = ue3::Read32(data + 4 * k);
        if (!poly) continue;
        const uint32_t cov = ue3::Read32(poly + 112), nc = ue3::Read32(poly + 116);
        polys += fmt::format(" [{:X}: {}", poly, nc);
        for (uint32_t j = 0; j < nc && j < 4; j++)
        {
            const uint32_t link = ue3::Read32(cov + 24 * j);
            polys += fmt::format(" {}#{}", link ? ue3::ObjectName(link) : "null", int32_t(ue3::Read32(cov + 24 * j + 20)));
        }
        polys += "]";
    }
    LOGFN("cover: polygons near ({:.0f},{:.0f},{:.0f}) r {:.0f}: {} ->{}", cx, cy, cz, radius, n, polys.substr(0, 600));
}

extern "C" PPC_FUNC(__imp__sub_82A9BAD8);
PPC_FUNC(sub_82A9BAD8)
{
    if (!On()) { __imp__sub_82A9BAD8(ctx, base); return; }
    const uint32_t pawn = ctx.r3.u32;
    static uint64_t last = 0;
    const uint64_t now = GetTickCount64();
    t_log = now - last >= 250 && ue3::ObjectName(ue3::ClassOf(pawn)).find("Marcus") != std::string::npos;
    if (t_log) last = now;
    t_inFind = true;
    __imp__sub_82A9BAD8(ctx, base);
    t_inFind = false;
    if (t_log) LOGFN("cover: FindCoverFromLocAndDir for {} -> {}", ue3::ObjectName(pawn), ctx.r3.u32);
    t_log = false;
}

// (debugging) GOW3_NAV_TRACE=1: the path search's edge tests (FNavMeshEdgeBase::Supports, sub_825C25B0, virtual slot 6
// of every edge class: this = the edge, r3 = supported), counted per edge class (vtable at +0) with how many passed,
// logged every 2 s while any ran: whether a search reaches a class at all (FNavMeshCrossPylonEdge: vtable 8215EAC8)
#include <map>
#include <mutex>
extern "C" PPC_FUNC(__imp__sub_825C25B0);
PPC_FUNC(sub_825C25B0)
{
    static const bool on = std::getenv("GOW3_NAV_TRACE") != nullptr;
    if (!on) { __imp__sub_825C25B0(ctx, base); return; }
    const uint32_t vt = ue3::Read32(ctx.r3.u32);
    __imp__sub_825C25B0(ctx, base);
    static std::mutex m; static std::map<uint32_t, std::pair<uint64_t, uint64_t>> counts; static uint64_t last = 0;
    std::lock_guard<std::mutex> lock(m);
    auto& c = counts[vt]; c.first++; if (ctx.r3.u32) c.second++;
    if (GetTickCount64() - last > 2000)
    {
        last = GetTickCount64();
        std::string s;
        for (auto& [k, v] : counts) s += fmt::format(" {:08X}: {}/{}", k, v.second, v.first);
        LOGFN("navtrace: edge tests (vtable: passed/tested){}", s);
        counts.clear();
    }
}

// Navigation mesh joins between levels (tools/navcross.py writes FNavMeshCrossPylonEdge with both polygon references
// by GUID, as Gears 3's own streaming levels store them): a reference's pylon pointer (+0 of the 28-byte reference;
// GUID +4, polygon +20, cached polygon +24) stays null unless something resolves it, and FPolyReference's resolver
// (sub_825BC810) gives no polygon for a null pylon: the path search never crossed (GOW3_NAV_TRACE showed no
// cross-pylon edge tested; Dom stood in the doorway between two sections). Every 2 s, for every live pylon's path mesh
// (Pylon +648), the edges (the mesh's edge pointer array, found once by its elements' vtables) whose references at
// +116 and +144 have no pylon get the live pylon with that NavGuid (Pylon +608); a reference to a pylon no longer live
// is cleared. GOW3_NAV_NO_FIXCROSS=1 turns it off.
#include <kernel/memory.h>
#include <thread>
#include <unordered_map>
namespace
{
    void Write32(uint32_t a, uint32_t v) { *(uint32_t*)g_memory.Translate(a) = ByteSwap(v); }
    bool IsEdgeVtable(uint32_t vt) { return vt >= 0x8215E900 && vt <= 0x8215EC40; }
    int g_edgeArrayOff = -1;
    int FindEdgeArray(uint32_t mesh)
    {
        for (int off = 0x40; off <= 0x1C0; off += 4)
        {
            const uint32_t data = ue3::Read32(mesh + off), n = ue3::Read32(mesh + off + 4);
            if (data < 0x40000000 || n == 0 || n > 100000) continue;
            int ok = 0;
            for (uint32_t k = 0; k < n && k < 8; k++) { const uint32_t e = ue3::Read32(data + 4 * k); if (e && IsEdgeVtable(ue3::Read32(e))) ok++; }
            if (ok >= int(std::min<uint32_t>(n, 8))) return off;
        }
        return -1;
    }
    void FixCrossThread()
    {
        while (!ue3::Ready()) Sleep(2000);
        static const uint32_t pylonClass = ue3::FindClass("Pylon");
        uint64_t fixed = 0, lastLogged = 0;
        for (;;)
        {
            Sleep(2000);
            if (!pylonClass) continue;
            const std::vector<uint32_t> pylons = ue3::FindInstancesOf(pylonClass);
            std::unordered_map<std::string, uint32_t> byGuid;
            for (uint32_t p : pylons)
            {
                std::string g(16, '\0');
                for (int k = 0; k < 4; k++) { const uint32_t w = ue3::Read32(p + 608 + 4 * k); memcpy(&g[4 * k], &w, 4); }
                byGuid[g] = p;
            }
            for (uint32_t p : pylons)
            {
                const uint32_t mesh = ue3::Read32(p + 648);
                if (!mesh) continue;
                if (g_edgeArrayOff < 0) { g_edgeArrayOff = FindEdgeArray(mesh); if (g_edgeArrayOff >= 0) LOGFN("navcross: edge pointer array at mesh +0x{:X}", g_edgeArrayOff); }
                if (g_edgeArrayOff < 0) continue;
                const uint32_t data = ue3::Read32(mesh + g_edgeArrayOff), n = ue3::Read32(mesh + g_edgeArrayOff + 4);
                if (data < 0x40000000 || n > 100000) continue;
                for (uint32_t k = 0; k < n; k++)
                {
                    const uint32_t e = ue3::Read32(data + 4 * k);
                    if (!e || ue3::Read32(e) != 0x8215EAC8) continue;          // FNavMeshCrossPylonEdge
                    static int dumped = 0;
                    if (dumped < 6 && std::getenv("GOW3_NAV_TRACE"))
                    {
                        dumped++;
                        std::string raw;
                        for (int j = 0; j < 44; j++) raw += fmt::format("{:08X}{}", ue3::Read32(e + 4 * j), j % 8 == 7 ? " | " : " ");
                        LOGFN("navcross: edge {:08X} of {} (mesh {:08X} #{}): {}", e, ue3::ObjectName(p), mesh, k, raw);
                    }
                    for (uint32_t refOff : { 116u, 144u })
                    {
                        const uint32_t ref = e + refOff;
                        std::string g(16, '\0');
                        for (int j = 0; j < 4; j++) { const uint32_t w = ue3::Read32(ref + 4 + 4 * j); memcpy(&g[4 * j], &w, 4); }
                        if (g == std::string(16, '\0')) continue;
                        const auto it = byGuid.find(g);
                        const uint32_t want = it == byGuid.end() ? 0 : it->second, cur = ue3::Read32(ref);
                        if (cur != want) { Write32(ref, want); Write32(ref + 24, 0); fixed++; }
                    }
                }
            }
            if (fixed != lastLogged) { LOGFN("navcross: {} cross-pylon references resolved by GUID so far", fixed); lastLogged = fixed; }
        }
    }
    struct FixCrossStart { FixCrossStart() { if (!std::getenv("GOW3_NAV_NO_FIXCROSS")) std::thread(FixCrossThread).detach(); } } g_fixCrossStart;
}

// (debugging, with GOW3_NAV_TRACE) FNavMeshCrossPylonEdge's polygon accessors (vtable slots 12, 13: Poly0 at +116,
// Poly1 at +144): calls and null results, every 2 s
namespace { std::atomic<uint64_t> g_crossGet[4]; }
extern "C" PPC_FUNC(__imp__sub_825BEBA0);
extern "C" PPC_FUNC(__imp__sub_825BEBC8);
static void CrossGetLog()
{
    static uint64_t last = 0;
    if (GetTickCount64() - last < 2000) return;
    last = GetTickCount64();
    LOGFN("navtrace: cross edge Poly0 {} calls ({} null), Poly1 {} calls ({} null)", g_crossGet[0].load(), g_crossGet[1].load(), g_crossGet[2].load(), g_crossGet[3].load());
}
PPC_FUNC(sub_825BEBA0)
{
    static const bool on = std::getenv("GOW3_NAV_TRACE") != nullptr;
    __imp__sub_825BEBA0(ctx, base);
    if (!on) return;
    g_crossGet[0]++; if (!ctx.r3.u32) g_crossGet[1]++; CrossGetLog();
}
PPC_FUNC(sub_825BEBC8)
{
    static const bool on = std::getenv("GOW3_NAV_TRACE") != nullptr;
    __imp__sub_825BEBC8(ctx, base);
    if (!on) return;
    g_crossGet[2]++; if (!ctx.r3.u32) g_crossGet[3]++; CrossGetLog();
}

// (debugging, GOW3_NAV_TRACE) FNavMeshCrossPylonEdge::GetOtherPoly (slot 4, sub_825BEAB8): its callers (return
// addresses) and null results, every 2 s: which code walks the cross-pylon edges
extern "C" PPC_FUNC(__imp__sub_825BEAB8);
PPC_FUNC(sub_825BEAB8)
{
    static const bool on = std::getenv("GOW3_NAV_TRACE") != nullptr;
    const uint32_t caller = uint32_t(ctx.lr);
    __imp__sub_825BEAB8(ctx, base);
    if (!on) return;
    static std::mutex m; static std::map<uint32_t, std::pair<uint64_t, uint64_t>> by; static uint64_t last = 0;
    std::lock_guard<std::mutex> lock(m);
    auto& c = by[caller]; c.first++; if (!ctx.r3.u32) c.second++;
    if (GetTickCount64() - last > 2000)
    {
        last = GetTickCount64();
        std::string s;
        for (auto& [k, v] : by) s += fmt::format(" {:08X}: {} ({} null)", k, v.first, v.second);
        LOGFN("navtrace: cross GetOtherPoly callers{}", s);
    }
}
