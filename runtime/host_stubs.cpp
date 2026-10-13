// Host pieces not ported yet. Each one logs so the boot log shows when the game reaches it.
//  - audio: XAudio render driver accepts frames and drops them (real output: SDL/XAudio2 later)
//  - input: hid/hid.cpp
#include <stdafx.h>
#include <apu/audio.h>
#include <hid/hid.h>
#include <os/logger.h>

void XAudioInitializeSystem() { LOGN("audio: silent driver (not implemented yet)"); }
void XAudioRegisterClient(PPCFunc* callback, uint32_t param) {}
void XAudioSubmitFrame(void* samples) {}

uint32_t XAudioRegisterRenderDriverClient(be<uint32_t>* callback, be<uint32_t>* driver)
{
    LOGFN("audio: render driver client registered (callback {:08X})", callback ? uint32_t(*callback) : 0);
    if (driver) *driver = 0x41550000;
    return 0;
}
uint32_t XAudioUnregisterRenderDriverClient(uint32_t driver) { return 0; }
uint32_t XAudioSubmitRenderDriverFrame(uint32_t driver, void* samples) { return 0; }

// every kernel/XAM import without an implementation lands here (runtime/import_stubs.cpp, weak)
#include "runtime.h"
void StubImport(const char* name, PPCContext& ctx)
{
    // first few calls of each name at normal level, so a run shows which imports the boot still needs
    static std::mutex m;
    static std::unordered_map<std::string, int> seen;
    int n;
    { std::lock_guard lock(m); n = seen[name]++; }
    if (n < 3)
        LOGFN("unimplemented import {}(r3={:08X} r4={:08X} r5={:08X} r6={:08X})", name, ctx.r3.u32, ctx.r4.u32, ctx.r5.u32, ctx.r6.u32);
    ctx.r3.u64 = 0;
}
