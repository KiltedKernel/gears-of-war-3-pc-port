// GOW3_LOG_POS=1 (development): every 2 s, log the location and rotation of every Pawn in the world (the player's
// first), and its CoverLink while in cover, through UE3 reflection. Used to place converted Gears 1/2 level content where the player can see it
// (tools/levelinject.py --offset).
#include <stdafx.h>
#include <ue3/reflection.h>
#include <os/logger.h>
#include <chrono>
#include <thread>

namespace pos_log
{
void Start()
{
    if (!getenv("GOW3_LOG_POS")) return;
    std::thread([] {
        while (!ue3::Init()) std::this_thread::sleep_for(std::chrono::seconds(2));
        uint32_t pawn = 0;
        int loc = -1, rot = -1, link = -1;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::seconds(2));
            if (!pawn)
            {
                pawn = ue3::FindClass("Pawn");
                const uint32_t actor = ue3::FindClass("Actor");
                loc = actor ? ue3::PropertyOffset(actor, "Location") : -1;
                rot = actor ? ue3::PropertyOffset(actor, "Rotation") : -1;
                if (!pawn || loc < 0) { pawn = 0; continue; }
                const uint32_t gearPawn = ue3::FindClass("GearPawn");
                link = gearPawn ? ue3::PropertyOffset(gearPawn, "CurrentLink") : -1;    // the CoverLink while in cover
                LOGFN("pos_log: Pawn {:08X}, Location +{}, Rotation +{}, GearPawn.CurrentLink +{}", pawn, loc, rot, link);
            }
            std::string line;
            for (uint32_t p : ue3::FindInstancesOf(pawn))
            {
                const float x = ue3::ReadFloat(p + loc), y = ue3::ReadFloat(p + loc + 4), z = ue3::ReadFloat(p + loc + 8);
                if (x == 0 && y == 0 && z == 0) continue;
                const int yaw = int(ue3::Read32(p + rot + 4));
                const uint32_t cl = link >= 0 ? ue3::Read32(p + link) : 0;
                line += fmt::format(" {}({:.0f},{:.0f},{:.0f} yaw {}{})", ue3::ObjectName(p), x, y, z, yaw,
                                    cl ? " cover " + ue3::ObjectName(cl) : "");
            }
            if (!line.empty()) LOGFN("pos_log:{}", line);
        }
    }).detach();
}
}
