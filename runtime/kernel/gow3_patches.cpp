// Gears of War 3 game patches, as mid-asm hooks placed by the recompiler (config/GOW3_hooks.toml [[midasm_hook]]).
// Addresses from Xenia's game-patches for this XEX ("4D5308AB - Gears of War 3.patch.toml", hash EF4AD5E9DD1982ED;
// illusion, boma, Sowa_95), each checked against the instruction it replaces. Here they follow gow3.ini instead
// of being fixed edits.
//  - frame rate: 8253D9B0 loads the engine's 30 fps tick cap (lfs f1,0x4C8(r3)); skipped, f1 stays 0.0 = no cap.
//    82AF66BC `li r11,2` is the D3D presentation interval (every 2nd vblank); 1 presents every vblank.
//  - 82969614 `lwz r5,0x344(r30)`: MaxAnisotropy -> 16.
//  - the effects: each site loads a GSystemSettings flag into a register; 0 turns the effect off.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include <user/settings.h>

namespace
{
    bool Unlocked() { return settings::Get().frameRate > 30; }
}

bool GoW3MaxTickRateHook(PPCRegister& r3) { return Unlocked(); }          // true: skip the load of the cap
void GoW3PresentIntervalHook(PPCRegister& r11) { if (Unlocked()) r11.u64 = 1; }
void GoW3AnisotropyHook(PPCRegister& r5) { if (settings::Get().anisotropic16x) r5.u64 = 16; }

void GoW3MotionBlurHook(PPCRegister& r) { if (!settings::Get().motionBlur) r.u64 = 0; }
void GoW3DepthOfFieldHook(PPCRegister& r) { if (!settings::Get().depthOfField) r.u64 = 0; }
void GoW3BloomHook(PPCRegister& r) { if (!settings::Get().bloom) r.u64 = 0; }
void GoW3LensFlaresHook(PPCRegister& r) { if (!settings::Get().lensFlares) r.u64 = 0; }
void GoW3AmbientOcclusionHook(PPCRegister& r) { if (!settings::Get().ambientOcclusion) r.u64 = 0; }
