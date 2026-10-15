// GOW3_LOG_POS=1 (development): every 2 s, log the location and rotation of every Pawn in the world (the player's
// first), and its CoverLink while in cover, through UE3 reflection. Used to place converted Gears 1/2 level content where the player can see it
// (tools/levelinject.py --offset).
#include <stdafx.h>
#include <ue3/reflection.h>
#include <os/logger.h>
#include <chrono>
#include <sstream>
#include <thread>

namespace pos_log
{
// GOW3_LOG_ACTORS=Class1,Class2 (development): every 10 s, the location of every instance of those classes (where
// to walk in a test: the input script's walkto)
void LogActors(const std::string& list)
{
    std::thread([list] {
        while (!ue3::Init()) std::this_thread::sleep_for(std::chrono::seconds(2));
        const uint32_t actor = ue3::FindClass("Actor");
        const int loc = actor ? ue3::PropertyOffset(actor, "Location") : -1;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::seconds(10));
            std::stringstream names(list);
            for (std::string name; std::getline(names, name, ',');)
            {
                // "Class:Property": also the 32 bits at that property (hex) (e.g. GearFortificationMarker:FortificationType)
                std::string prop;
                if (auto c = name.find(':'); c != std::string::npos) { prop = name.substr(c + 1); name.resize(c); }
                const uint32_t cls = ue3::FindClass(name.c_str());
                if (!cls || loc < 0) continue;
                const int off = prop.empty() ? -1 : ue3::PropertyOffset(cls, prop.c_str());
                std::string line;
                for (uint32_t a : ue3::FindInstancesOf(cls))
                    line += fmt::format(" {}({:.0f},{:.0f},{:.0f}{})", ue3::ObjectName(a), ue3::ReadFloat(a + loc),
                                        ue3::ReadFloat(a + loc + 4), ue3::ReadFloat(a + loc + 8),
                                        off >= 0 ? fmt::format(" {}={:08X}", prop, ue3::Read32(a + off)) : "");
                if (!line.empty()) LOGFN("actors {}:{}", name, line);
            }
        }
    }).detach();
}

void Start()
{
    if (const char* actors = getenv("GOW3_LOG_ACTORS")) LogActors(actors);
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
