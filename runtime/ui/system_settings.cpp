// Engine effects from gow3.ini ([graphics] motion_blur, depth_of_field, bloom, lens_flares, distortion, fog_volumes,
// self_shadowing): written into the live GSystemSettings (0x8313751C, the copy 142 instructions address) once the
// engine has read its own config, and again every second (a level load can reapply the config). Field offsets come
// from FSystemSettings' name table (sub_82351488: "MotionBlur" -> +0x2C ...). The renderer reads these per frame.
#include <stdafx.h>
#include <kernel/memory.h>
#include <os/logger.h>
#include <user/settings.h>
#include <thread>

namespace system_settings
{
namespace
{
    constexpr uint32_t kSettings = 0x8313751C;
    struct Field { uint32_t offset; const char* name; bool settings::Settings::*value; };
    constexpr Field kFields[] = {
        { 0x2C, "MotionBlur", &settings::Settings::motionBlur },
        { 0x30, "DepthOfField", &settings::Settings::depthOfField },
        { 0x38, "Bloom", &settings::Settings::bloom },
        { 0x40, "Distortion", &settings::Settings::distortion },
        { 0x48, "LensFlares", &settings::Settings::lensFlares },
        { 0x4C, "FogVolumes", &settings::Settings::fogVolumes },
        { 0x1E8, "bEnableForegroundSelfShadowing", &settings::Settings::selfShadowing },
    };

    uint32_t Get(uint32_t a) { return ByteSwap(*(uint32_t*)g_memory.Translate(a)); }
    void Put(uint32_t a, uint32_t v) { *(uint32_t*)g_memory.Translate(a) = ByteSwap(v); }

    // the engine has loaded SystemSettings once ResX/ResY (+0x1B0/+0x1B4) hold the config's 1280 x 720
    bool Loaded() { return Get(kSettings + 0x1B0) == 1280 && Get(kSettings + 0x1B4) == 720; }

    void Worker()
    {
        const auto& s = settings::Get();
        bool all = true;
        for (const Field& f : kFields) all &= s.*(f.value) == (f.offset == 0x1E8 ? false : true);
        if (all) return;   // every effect as the game ships: nothing to do
        bool logged = false;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            if (!Loaded()) continue;
            for (const Field& f : kFields)
            {
                const uint32_t want = s.*(f.value) ? 1 : 0;
                if (Get(kSettings + f.offset) != want) Put(kSettings + f.offset, want);
            }
            if (!logged)
            {
                logged = true;
                std::string line;
                for (const Field& f : kFields) line += fmt::format(" {}={}", f.name, s.*(f.value) ? 1 : 0);
                LOGFN("system settings:{}", line);
            }
        }
    }
}

void Start()
{
    static std::once_flag once;
    std::call_once(once, [] { std::thread(Worker).detach(); });
}
}
