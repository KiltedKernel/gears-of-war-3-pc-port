// Kernel/XAM imports Gears of War 3 uses that Gears of War 2 did not (behaviour after Xenia's implementations).
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include "memory.h"
#include <os/logger.h>

namespace gow3k
{
// MmMapIoSpace(unknown, physical address, size, protect): the XMA contexts' physical addresses mapped for the CPU.
// Physical memory is already visible at 0xA0000000 (cached view), so the mapping is that view.
uint32_t MmMapIoSpace(uint32_t unknown, uint32_t physical, uint32_t size, uint32_t protect)
{
    return 0xA0000000u | (physical & 0x1FFFFFFFu);
}

// KeInitializeDpc(XDPC*, routine, context): type 19, unqueued; the title queues none we have seen (no KeInsertQueueDpc
// import), so only the fields are set
struct XDPC
{
    be<uint16_t> type;
    uint8_t selectedCpu, desiredCpu;
    be<uint32_t> listFlink, listBlink;
    be<uint32_t> routine, context, arg1, arg2;
};

void KeInitializeDpc(XDPC* dpc, uint32_t routine, uint32_t context)
{
    if (!dpc) return;
    dpc->type = 19;
    dpc->selectedCpu = 0; dpc->desiredCpu = 0;
    dpc->listFlink = 0; dpc->listBlink = 0;
    dpc->routine = routine; dpc->context = context;
    dpc->arg1 = 0; dpc->arg2 = 0;
}

// XamUserGetDeviceContext(user, unknown, out): titles treat failure as context 0, so 0 is written either way
uint32_t XamUserGetDeviceContext(uint32_t userIndex, uint32_t unknown, be<uint32_t>* out)
{
    if (out) *out = 0;
    return (userIndex == 0 || (userIndex & 0xFF) == 0xFF) ? 0 : 0x534;   // ERROR_NO_SUCH_USER: only player 1 is signed in
}

uint32_t XamVoiceIsActiveProcess() { return 0; }   // no voice chat

// XamContentGetLicenseMask(mask, overlapped): a disc title has no marketplace licenses
uint32_t XamContentGetLicenseMask(be<uint32_t>* mask, uint32_t overlapped)
{
    if (mask) *mask = 0;
    return 0;
}

// XAudioGetSpeakerConfig(config): stereo (Xenia's value)
uint32_t XAudioGetSpeakerConfig(be<uint32_t>* config)
{
    if (config) *config = 0x00010001;
    return 0;
}
}

GUEST_FUNCTION_HOOK(__imp__MmMapIoSpace, gow3k::MmMapIoSpace);
GUEST_FUNCTION_HOOK(__imp__KeInitializeDpc, gow3k::KeInitializeDpc);
GUEST_FUNCTION_HOOK(__imp__XamUserGetDeviceContext, gow3k::XamUserGetDeviceContext);
GUEST_FUNCTION_HOOK(__imp__XamVoiceIsActiveProcess, gow3k::XamVoiceIsActiveProcess);
GUEST_FUNCTION_HOOK(__imp__XamContentGetLicenseMask, gow3k::XamContentGetLicenseMask);
GUEST_FUNCTION_HOOK(__imp__XAudioGetSpeakerConfig, gow3k::XAudioGetSpeakerConfig);
