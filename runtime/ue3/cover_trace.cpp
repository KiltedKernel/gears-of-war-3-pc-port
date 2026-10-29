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
