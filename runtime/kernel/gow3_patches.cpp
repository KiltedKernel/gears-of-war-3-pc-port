// Gears of War 3 game patches, as mid-asm hooks placed by the recompiler (config/GOW3_hooks.toml [[midasm_hook]]).
// Addresses from Xenia's game-patches for this XEX ("4D5308AB - Gears of War 3.patch.toml", hash EF4AD5E9DD1982ED;
// illusion, boma, Sowa_95), each checked against the instruction it replaces. Here they follow gow3.ini instead
// of being fixed edits.
//  - frame rate: 8253D9B0 loads the engine's 30 fps tick cap (lfs f1,0x4C8(r3)); the player's frame rate is returned
//    instead. (The game patch's 0.0, "no cap", let the engine tick as fast as the CPU allows whenever it presents no
//    frame, as while loading; in the Gears 2 port that ran the net driver at ~1000 Hz and broke campaign co-op.)
//    82AF66BC `li r11,2` is the D3D presentation interval (every 2nd vblank); 1 presents every vblank.
//  - 82969614 `lwz r5,0x344(r30)`: MaxAnisotropy -> 16.
//  - 8260BC48 / 8260C0A4: stepUp's retry threshold (144) times (30 / frame rate)^2.
//  - the effects: each site loads a GSystemSettings flag into a register; 0 turns the effect off.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include <user/settings.h>
#include <os/logger.h>
#include <ue3/reflection.h>
#include <cstdlib>

namespace
{
    bool Unlocked() { return settings::Get().frameRate > 30; }
}

bool GoW3MaxTickRateHook(PPCRegister& r3, PPCRegister& f1)
{
    if (!Unlocked()) return false;                  // the game's own 30 fps cap
    f1.f64 = double(settings::Get().frameRate);     // the cap is the player's frame rate
    return true;                                    // skip the load of the console's cap
}
void GoW3PresentIntervalHook(PPCRegister& r11) { if (Unlocked()) r11.u64 = 1; }
// (debugging) GOW3_TRACE_STEP=1: what a player pawn's walking ran into, at most 5 times a second: stepUp's FCheckResult
// (r7: Actor +4, Location +8, Normal +20, Time +32, Component +40) and the pawn (r3). Found what stopped the player
// at the converted prison's cell doorway at high frame rates
void GoW3StepUpTraceHook(PPCRegister& r3, PPCRegister& r7)
{
    static const bool on = std::getenv("GOW3_TRACE_STEP") != nullptr;
    if (!on || !ue3::Ready()) return;
    static uint64_t last = 0;
    const uint64_t now = GetTickCount64();
    if (now - last < 200) return;
    const uint32_t pawn = r3.u32, hit = r7.u32;
    const std::string pc = ue3::ObjectName(ue3::ClassOf(pawn));
    if (pc.find("Marcus") == std::string::npos) return;
    last = now;
    const uint32_t actor = ue3::Read32(hit + 4), comp = ue3::Read32(hit + 40);
    LOGFN("stepUp: {} hit {} ({}) comp {} ({}) at {:.1f},{:.1f},{:.1f} normal {:.2f},{:.2f},{:.2f} time {:.3f}",
          ue3::ObjectName(pawn), actor ? ue3::ObjectName(actor) : "-", actor ? ue3::ObjectName(ue3::ClassOf(actor)) : "-",
          comp ? ue3::ObjectName(comp) : "-", comp ? ue3::ObjectName(ue3::ClassOf(comp)) : "-",
          ue3::ReadFloat(hit + 8), ue3::ReadFloat(hit + 12), ue3::ReadFloat(hit + 16),
          ue3::ReadFloat(hit + 20), ue3::ReadFloat(hit + 24), ue3::ReadFloat(hit + 28), ue3::ReadFloat(hit + 32));
}
void GoW3AnisotropyHook(PPCRegister& r5) { if (settings::Get().anisotropic16x) r5.u64 = 16; }

void GoW3MotionBlurHook(PPCRegister& r) { if (!settings::Get().motionBlur) r.u64 = 0; }
void GoW3DepthOfFieldHook(PPCRegister& r) { if (!settings::Get().depthOfField) r.u64 = 0; }
// bloom: no setting. The only read of GSystemSettings.Bloom (82593DC0) is dead in the shipped code (its compare is
// overwritten by the next one), so zeroing it changed nothing. Kept empty for code generated before 0.2.
void GoW3BloomHook(PPCRegister& r) {}
void GoW3LensFlaresHook(PPCRegister& r) { if (!settings::Get().lensFlares) r.u64 = 0; }
void GoW3AmbientOcclusionHook(PPCRegister& r) { if (!settings::Get().ambientOcclusion) r.u64 = 0; }
