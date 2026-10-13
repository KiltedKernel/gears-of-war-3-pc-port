// GOW3_NO_PLAY_AREA_KILL=1 (development, converted levels): Gears 3 kills a player who stays outside the play area (its
// navmesh) longer than GearPawn.MaxOutOfPlayAreaTime (GearPawn.CheckInPlayArea -> GearGame.HandlePlayerOutOfPlayArea
// -> FellOutOfWorld). A converted level's navmesh does not yet cover everything a pawn can stand on (props, ledges),
// so this raises MaxOutOfPlayAreaTime on every GearPawn, once a second, to free-roam a converted level.
#include <stdafx.h>
#include <ue3/reflection.h>
#include <kernel/memory.h>
#include <os/logger.h>
#include <chrono>
#include <thread>

namespace play_area
{
void Start()
{
    if (!getenv("GOW3_NO_PLAY_AREA_KILL")) return;
    std::thread([] {
        while (!ue3::Init()) std::this_thread::sleep_for(std::chrono::seconds(2));
        int offset = -1;
        uint32_t pawnClass = 0;
        int patched = 0;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            if (!pawnClass)
            {
                pawnClass = ue3::FindClass("GearPawn");
                if (!pawnClass) continue;
                offset = ue3::PropertyOffset(pawnClass, "MaxOutOfPlayAreaTime");
                LOGFN("play_area: GearPawn.MaxOutOfPlayAreaTime at +{}", offset);
                if (offset < 0) return;
            }
            for (uint32_t pawn : ue3::FindInstancesOf(pawnClass))
            {
                if (!ue3::Readable(pawn + offset, 4)) continue;
                const float big = 1.0e9f;
                uint32_t v; memcpy(&v, &big, 4);
                uint32_t* p = (uint32_t*)g_memory.Translate(pawn + offset);
                if (*p != ByteSwap(v)) { *p = ByteSwap(v); if (patched++ < 5) LOGFN("play_area: {} no longer killed outside the play area", ue3::ObjectName(pawn)); }
            }
        }
    }).detach();
}
}
