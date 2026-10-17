// Gears of War 3 kernel: video driver interface (Vd*). The XDK's D3D (linked into the game) drives the Xenos GPU
// through these calls plus a PM4 ring buffer; semantics follow Xenia's xboxkrnl_video.cc. The GPU side (consuming the
// ring buffer) is gpu/command_processor.cpp.
#include <stdafx.h>
#include "function.h"
#include "memory.h"
#include <os/logger.h>
#include <gpu/gpu.h>
#include "physical.h"

namespace x360k
{

struct X_VIDEO_MODE
{
    be<uint32_t> display_width, display_height, is_interlaced, is_widescreen, is_hi_def;
    be<float> refresh_rate;
    be<uint32_t> video_standard, unknown_0x8a, unknown_0x01, reserved[3];
};
static_assert(sizeof(X_VIDEO_MODE) == 48);

void VdQueryVideoMode(X_VIDEO_MODE* mode)
{
    memset(mode, 0, sizeof(*mode));
    mode->display_width = gpu::kDisplayWidth;
    mode->display_height = gpu::kDisplayHeight;
    mode->is_interlaced = 0;
    mode->is_widescreen = 1;
    mode->is_hi_def = 1;
    mode->refresh_rate = 60.0f;
    mode->video_standard = 1;   // NTSC
    mode->unknown_0x8a = 0x4A;
    mode->unknown_0x01 = 0x01;
}

uint32_t VdQueryVideoFlags() { return 0x7; }   // widescreen | 1280x720 | 1920x1080 capable

// X_DISPLAY_INFO (0x58 bytes): front buffer size/format, scaler parameters, overscan, display size and refresh
void VdGetCurrentDisplayInformation(uint8_t* info)
{
    memset(info, 0, 0x58);
    *(be<uint16_t>*)(info + 0x00) = gpu::kDisplayWidth;     // front buffer width
    *(be<uint16_t>*)(info + 0x02) = gpu::kDisplayHeight;    // front buffer height
    *(be<uint16_t>*)(info + 0x48) = gpu::kDisplayWidth;     // display width
    *(be<uint16_t>*)(info + 0x4A) = gpu::kDisplayHeight;    // display height
    *(be<float>*)(info + 0x4C) = 60.0f;                     // refresh rate
    *(be<uint16_t>*)(info + 0x56) = gpu::kDisplayWidth;     // actual display width
}

void VdGetCurrentDisplayGamma(be<uint32_t>* type, be<float>* power)
{
    *type = 2;                // power-law gamma
    *power = 2.22222233f;
}

uint32_t VdSetDisplayMode(uint32_t flags) { return 0; }
uint32_t VdInitializeEngines(uint32_t unk0, uint32_t callback, uint32_t arg, uint32_t pfpPtr, uint32_t mePtr) { return 1; }
void VdShutdownEngines() {}
uint32_t VdIsHSIOTrainingSucceeded() { return 1; }
uint32_t VdRetrainEDRAM(uint32_t unk0, uint32_t unk1, uint32_t unk2) { return 0; }
void VdRetrainEDRAMWorker(uint32_t unk0) {}
uint32_t VdPersistDisplay(uint32_t unk0, be<uint32_t>* unk1) { if (unk1) *unk1 = 0; return 1; }
void VdEnableDisableClockGating(uint32_t enabled) {}
uint32_t VdCallGraphicsNotificationRoutines(uint32_t unk0, uint32_t argsPtr) { return 0; }

void VdGetSystemCommandBuffer(uint8_t* p0, be<uint32_t>* p1)
{
    memset(p0, 0, 0x94);
    *(be<uint32_t>*)p0 = 0xBEEF0000;
    *p1 = 0xBEEF0001;
}

void VdSetSystemCommandBufferGpuIdentifierAddress(uint32_t address) { gpu::SetSystemCommandBufferIdentifier(address); }

void VdInitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2) { gpu::InitializeRingBuffer(ptr, sizeLog2); }
void VdEnableRingBufferRPtrWriteBack(uint32_t ptr, uint32_t blockLog2) { gpu::EnableReadPointerWriteBack(ptr, blockLog2); }
void VdSetGraphicsInterruptCallback(uint32_t callback, uint32_t userData) { gpu::SetInterruptCallback(callback, userData); }

// Fills the caller's buffer with the scaler setup packet stream and returns its length in dwords. The console writes
// real scaler registers; type-2 PM4 NOPs are accepted by the command processor (Xenia does the same).
uint32_t VdInitializeScalerCommandBuffer(uint32_t sourceXY, uint32_t sourceWH, uint32_t scaleX, uint32_t scaleY,
    uint32_t unk4, uint32_t unk5, uint32_t unk6, uint32_t unk7, uint32_t unk8, uint32_t unk9, be<uint32_t>* dest, uint32_t destCount)
{
    if (!dest || destCount > 0x1000)
    {
        LOGFN_WARNING("VdInitializeScalerCommandBuffer: unexpected destination {:p} count {}", (void*)dest, destCount);
        return 0;
    }
    for (uint32_t i = 0; i < destCount; i++)
        dest[i] = 0x80000000;
    return destCount;
}

// D3D asks the kernel to build the swap in the 64 dwords it reserved in the ring (Xenia's VdSwap): the front buffer's
// texture fetch constant (from the D3D texture header, with its CPU address made physical) into fetch constant 0, then
// the XE_SWAP packet (signature, physical front buffer, width, height), then NOPs. 10 arguments; 9-10 on the stack.
void VdSwap(be<uint32_t>* bufferPtr, be<uint32_t>* fetchPtr, uint32_t unk2, uint32_t unk3, uint32_t unk4,
    be<uint32_t>* frontBuffer, be<uint32_t>* textureFormat, be<uint32_t>* colorSpace, be<uint32_t>* width, be<uint32_t>* height)
{
    uint32_t fetch[6];
    for (int i = 0; i < 6; i++) fetch[i] = fetchPtr[i];
    const uint32_t physical = physical::ToPhysical(fetch[1] & 0xFFFFF000u);   // base_address: dword_1 bits 12-31
    fetch[1] = (fetch[1] & 0xFFFu) | (physical & 0xFFFFF000u);

    uint32_t n = 0;
    bufferPtr[n++] = (0u << 30) | ((6u - 1) << 16) | 0x4800u;               // type 0: SHADER_CONSTANT_FETCH_00_0, 6 regs
    for (int i = 0; i < 6; i++) bufferPtr[n++] = fetch[i];
    bufferPtr[n++] = (3u << 30) | ((4u - 1) << 16) | (gpu::kPm4XeSwap << 8);   // type 3: XE_SWAP, 4 dwords
    bufferPtr[n++] = 'SWAP';
    bufferPtr[n++] = physical;
    bufferPtr[n++] = *width;
    bufferPtr[n++] = *height;
    while (n < 64) bufferPtr[n++] = 0x80000000u;                              // type 2 NOPs

    // frame rate, every 10 s, with the hitches (the longest frame, frames over 50 ms): a stutter averages away
    static uint64_t frames = 0, windowStart = GetTickCount64(), windowFrames = 0, hitches = 0;
    static double longest = 0;
    static LARGE_INTEGER freq{}, last{};
    LARGE_INTEGER qpc;
    QueryPerformanceCounter(&qpc);
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    if (last.QuadPart)
    {
        const double ms = double(qpc.QuadPart - last.QuadPart) * 1000.0 / double(freq.QuadPart);
        longest = (std::max)(longest, ms);
        hitches += ms > 50.0;
    }
    last = qpc;
    frames++; windowFrames++;
    const uint64_t now = GetTickCount64();
    if (now - windowStart >= 10000)
    {
        LOGFN("video: {:.1f} fps ({} frames; longest {:.0f} ms, {} over 50 ms)", windowFrames * 1000.0 / double(now - windowStart),
              frames, longest, hitches);
        windowStart = now; windowFrames = 0; hitches = 0; longest = 0;
    }

    static int logged = 0;
    if (logged++ < 3)
        LOGFN("VdSwap: front buffer {:08X} (physical {:08X}) {}x{} format {} color space {}", uint32_t(*frontBuffer), physical,
            uint32_t(*width), uint32_t(*height), uint32_t(*textureFormat), uint32_t(*colorSpace));
}

}

GUEST_FUNCTION_HOOK(__imp__VdQueryVideoMode, x360k::VdQueryVideoMode);
GUEST_FUNCTION_HOOK(__imp__VdQueryVideoFlags, x360k::VdQueryVideoFlags);
GUEST_FUNCTION_HOOK(__imp__VdGetCurrentDisplayInformation, x360k::VdGetCurrentDisplayInformation);
GUEST_FUNCTION_HOOK(__imp__VdGetCurrentDisplayGamma, x360k::VdGetCurrentDisplayGamma);
GUEST_FUNCTION_HOOK(__imp__VdSetDisplayMode, x360k::VdSetDisplayMode);
GUEST_FUNCTION_HOOK(__imp__VdInitializeEngines, x360k::VdInitializeEngines);
GUEST_FUNCTION_HOOK(__imp__VdShutdownEngines, x360k::VdShutdownEngines);
GUEST_FUNCTION_HOOK(__imp__VdIsHSIOTrainingSucceeded, x360k::VdIsHSIOTrainingSucceeded);
GUEST_FUNCTION_HOOK(__imp__VdRetrainEDRAM, x360k::VdRetrainEDRAM);
GUEST_FUNCTION_HOOK(__imp__VdRetrainEDRAMWorker, x360k::VdRetrainEDRAMWorker);
GUEST_FUNCTION_HOOK(__imp__VdPersistDisplay, x360k::VdPersistDisplay);
GUEST_FUNCTION_HOOK(__imp__VdEnableDisableClockGating, x360k::VdEnableDisableClockGating);
GUEST_FUNCTION_HOOK(__imp__VdCallGraphicsNotificationRoutines, x360k::VdCallGraphicsNotificationRoutines);
GUEST_FUNCTION_HOOK(__imp__VdGetSystemCommandBuffer, x360k::VdGetSystemCommandBuffer);
GUEST_FUNCTION_HOOK(__imp__VdSetSystemCommandBufferGpuIdentifierAddress, x360k::VdSetSystemCommandBufferGpuIdentifierAddress);
GUEST_FUNCTION_HOOK(__imp__VdInitializeRingBuffer, x360k::VdInitializeRingBuffer);
GUEST_FUNCTION_HOOK(__imp__VdEnableRingBufferRPtrWriteBack, x360k::VdEnableRingBufferRPtrWriteBack);
GUEST_FUNCTION_HOOK(__imp__VdSetGraphicsInterruptCallback, x360k::VdSetGraphicsInterruptCallback);
GUEST_FUNCTION_HOOK(__imp__VdInitializeScalerCommandBuffer, x360k::VdInitializeScalerCommandBuffer);
GUEST_FUNCTION_HOOK(__imp__VdSwap, x360k::VdSwap);
