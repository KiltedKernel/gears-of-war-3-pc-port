// XAM input (Xenia semantics): XamInputGetCapabilities has 3 arguments here (Unleashed's took XamInputGetCapabilitiesEx's 4);
// only connected pads report success; user 0xFF (XUSER_INDEX_ANY) is player 1; a null state pointer is a presence query.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include <hid/hid.h>
#include <os/logger.h>
#include <set>

namespace x360k
{

namespace
{
    constexpr uint32_t kAnyUser = 0xFF;
    uint32_t User(uint32_t userIndex, uint32_t flags) { return (userIndex == kAnyUser || (flags & 0x40000000)) ? 0 : userIndex; }
}

uint32_t XamInputGetCapabilities(uint32_t userIndex, uint32_t flags, XAMINPUT_CAPABILITIES* caps)
{
    XAMINPUT_CAPABILITIES c{};
    const uint32_t r = hid::GetCapabilities(User(userIndex, flags), &c);
    if (r != ERROR_SUCCESS)
        return r;
    if (caps)
    {
        *caps = c;
        ByteSwapInplace(caps->Flags);
        ByteSwapInplace(caps->Gamepad.wButtons);
        ByteSwapInplace(caps->Gamepad.sThumbLX);
        ByteSwapInplace(caps->Gamepad.sThumbLY);
        ByteSwapInplace(caps->Gamepad.sThumbRX);
        ByteSwapInplace(caps->Gamepad.sThumbRY);
        ByteSwapInplace(caps->Vibration.wLeftMotorSpeed);
        ByteSwapInplace(caps->Vibration.wRightMotorSpeed);
    }
    return ERROR_SUCCESS;
}

uint32_t XamInputGetState(uint32_t userIndex, uint32_t flags, XAMINPUT_STATE* state)
{
    static const bool debug = getenv("GOW3_INPUT_DEBUG") != nullptr;
    if (debug)
    {
        static std::mutex m; static std::set<uint64_t> seen;
        std::lock_guard lock(m);
        if (seen.insert((uint64_t(userIndex) << 32) | flags).second)
            LOGFN("XamInputGetState: user {:X} flags {:X} state {} (lr {:08X})", userIndex, flags, (void*)state, GetPPCContext()->lr);
    }
    XAMINPUT_STATE s{};
    const uint32_t r = hid::GetState(User(userIndex, flags), &s);
    if (state)
    {
        memset(state, 0, sizeof(*state));
        if (r == ERROR_SUCCESS)
        {
            *state = s;
            ByteSwapInplace(state->dwPacketNumber);
            ByteSwapInplace(state->Gamepad.wButtons);
            ByteSwapInplace(state->Gamepad.sThumbLX);
            ByteSwapInplace(state->Gamepad.sThumbLY);
            ByteSwapInplace(state->Gamepad.sThumbRX);
            ByteSwapInplace(state->Gamepad.sThumbRY);
        }
    }
    return r;
}

uint32_t XamInputSetState(uint32_t userIndex, uint32_t flags, XAMINPUT_VIBRATION* vibration)
{
    if (!vibration)
        return 0xA0; // ERROR_BAD_ARGUMENTS
    XAMINPUT_VIBRATION v = *vibration;
    ByteSwapInplace(v.wLeftMotorSpeed);
    ByteSwapInplace(v.wRightMotorSpeed);
    return hid::SetState(User(userIndex, flags), &v);
}

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__XamInputGetCapabilities, x360k::XamInputGetCapabilities);
GUEST_FUNCTION_HOOK(__imp__XamInputGetState, x360k::XamInputGetState);
GUEST_FUNCTION_HOOK(__imp__XamInputSetState, x360k::XamInputSetState);
