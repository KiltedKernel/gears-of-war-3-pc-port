// Audio, kernel side (Xenia's xboxkrnl_audio*.cc semantics). The XDK's XAudio runs inside the title: it mixes its
// voices and hands the "system" 256-sample, 6-channel frames through a render driver callback; XMA-compressed sounds
// are decoded by the XMA hardware, whose 64-byte contexts the title programs in physical memory and drives through
// MMIO at 0x7FEA0000. Both are Xenia's audio module (XAudio2 output + FFmpeg XMA decoder) behind xe_glue;
// the MMIO page is trapped by gpu/xenos_backend.cpp.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include "memory.h"
#include <os/logger.h>
#include "../../xe_glue/port_gpu.h"

namespace x360k
{

namespace
{
    constexpr uint32_t kDriverTag = 0x41550000;   // 'AU' | client index (Xenia)
}

uint32_t XAudioRegisterRenderDriverClient(be<uint32_t>* callback, be<uint32_t>* driver)
{
    if (!callback || !callback[0])
        return 0x80070057; // X_E_INVALIDARG
    const int index = portgpu::AudioRegisterClient(callback[0], callback[1]);
    if (index < 0)
    {
        LOGN_WARNING("audio: no audio output; render driver client not registered");
        if (driver) *driver = kDriverTag | 0xFFFF;
        return 0;   // keep the title running silently
    }
    if (driver) *driver = kDriverTag | uint32_t(index);
    LOGFN("audio: render driver client {} (callback {:08X} arg {:08X})", index, uint32_t(callback[0]), uint32_t(callback[1]));
    return 0;
}

uint32_t XAudioUnregisterRenderDriverClient(uint32_t driver)
{
    if ((driver & 0xFFFF) != 0xFFFF) portgpu::AudioUnregisterClient(int(driver & 0xFFFF));
    return 0;
}

uint32_t XAudioSubmitRenderDriverFrame(uint32_t driver, uint32_t samples)
{
    // level meter for the log: frames/s and the peak |sample| over 10 s (256 x 6 big-endian floats per frame)
    static uint64_t windowStart = GetTickCount64(), frames = 0;
    static float peak = 0;
    frames++;
    const auto* f = (const uint32_t*)g_memory.Translate(samples);
    for (int i = 0; i < 256 * 6; i += 7)
    {
        uint32_t v = ByteSwap(f[i]);
        float x; memcpy(&x, &v, 4);
        if (x == x) peak = std::max(peak, std::abs(x));
    }
    // GOW3_AUDIO_DUMP=<file> (development): the output, the 6 channels summed, as raw little-endian float32 at
    // 48 kHz (256 samples per frame, channel-major), to check what plays
    static FILE* dump = [] { const char* p = getenv("GOW3_AUDIO_DUMP"); return p ? fopen(p, "wb") : nullptr; }();
    if (dump)
    {
        float mix[256] = {};
        for (int c = 0; c < 6; c++)
            for (int s = 0; s < 256; s++)
            {
                uint32_t v = ByteSwap(f[c * 256 + s]);
                float x; memcpy(&x, &v, 4);
                if (x == x) mix[s] += x;
            }
        fwrite(mix, sizeof(mix), 1, dump);
        if ((frames & 255) == 0) fflush(dump);
    }
    const uint64_t now = GetTickCount64();
    if (now - windowStart >= 10000)
    {
        LOGFN("audio: {:.1f} frames/s, peak {:.3f}", frames * 1000.0 / double(now - windowStart), peak);
        windowStart = now; frames = 0; peak = 0;
    }

    if ((driver & 0xFFFF) != 0xFFFF) portgpu::AudioSubmitFrame(int(driver & 0xFFFF), samples);
    return 0;
}

uint32_t XAudioGetVoiceCategoryVolumeChangeMask(uint32_t driver, be<uint32_t>* mask)
{
    if (mask) *mask = 0;
    return 0;
}

uint32_t XAudioGetVoiceCategoryVolume(uint32_t category, be<float>* volume)
{
    if (volume) *volume = 1.0f;   // Unleashed's stub left this unwritten
    return 0;
}

uint32_t XMACreateContext(be<uint32_t>* context)
{
    const uint32_t c = portgpu::XmaAllocateContext();
    *context = c;
    static int logged = 0;
    if (logged++ < 4) LOGFN("audio: XMACreateContext -> {:08X}", c);
    return c ? 0 : 0xC0000017; // STATUS_NO_MEMORY
}

uint32_t XMAReleaseContext(uint32_t context)
{
    portgpu::XmaReleaseContext(context);
    return 0;
}

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__XAudioRegisterRenderDriverClient, x360k::XAudioRegisterRenderDriverClient);
GUEST_FUNCTION_HOOK(__imp__XAudioUnregisterRenderDriverClient, x360k::XAudioUnregisterRenderDriverClient);
GUEST_FUNCTION_HOOK(__imp__XAudioSubmitRenderDriverFrame, x360k::XAudioSubmitRenderDriverFrame);
GUEST_FUNCTION_HOOK(__imp__XAudioGetVoiceCategoryVolumeChangeMask, x360k::XAudioGetVoiceCategoryVolumeChangeMask);
GUEST_FUNCTION_HOOK(__imp__XAudioGetVoiceCategoryVolume, x360k::XAudioGetVoiceCategoryVolume);
GUEST_FUNCTION_HOOK(__imp__XMACreateContext, x360k::XMACreateContext);
GUEST_FUNCTION_HOOK(__imp__XMAReleaseContext, x360k::XMAReleaseContext);
