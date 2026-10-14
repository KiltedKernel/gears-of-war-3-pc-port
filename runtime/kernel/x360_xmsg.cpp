// XMsg: requests to the console's system apps (Xenia's xam_msg.cc / AppManager). Each app answers synchronously;
// an overlapped request is completed at once with the app's result. Apps: 0xFA XMP (music player), 0xFB XGI
// (achievements/contexts), 0xFC XLiveBase (Live, offline here), 0xFE XAM. Every request is logged (first few per
// message) so new ones show up in the boot log.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include "memory.h"
#include <os/logger.h>
#include <map>

uint32_t CompleteOverlapped(XXOVERLAPPED* ov, uint32_t result, uint32_t length);   // xam.cpp

namespace x360k
{

namespace
{
    constexpr uint32_t X_E_INVALIDARG = 0x80070057, X_E_FAIL = 0x80004005, X_E_NOTFOUND = 0x80070490;

    void Log(const char* what, uint32_t app, uint32_t message, uint32_t buffer, uint32_t length, uint32_t result)
    {
        static std::mutex m;
        static std::map<uint64_t, int> seen;
        std::lock_guard lock(m);
        if (seen[(uint64_t(app) << 32) | message]++ < 3)
            LOGFN("{}: app {:X} message {:08X} buffer {:08X} ({:X} bytes) -> {:08X}", what, app, message, buffer, length, result);
    }

    // one app's answer to a message; X_E_NOTFOUND = unknown app
    uint32_t Dispatch(uint32_t app, uint32_t message, uint32_t buffer, uint32_t length)
    {
        switch (app)
        {
        case 0xFA:   // XMP: no custom soundtrack playing
        case 0xFB:   // XGI: achievements, rich presence, stats: accepted, nothing to report offline
        case 0xFE:   // XAM
            return 0;
        case 0xFC:   // XLiveBase (Xenia xlivebase_app.cc): not signed in to Live
            switch (message)
            {
            case 0x00058004:   // XLiveBaseGetLogonId: a bool in the buffer
                if (buffer) *(be<uint32_t>*)g_memory.Translate(buffer) = 1;
                return 0;
            case 0x00058006:   // XLiveBaseGetNatType
            case 0x00058007:   // XOnlineGetServiceInfo
                return 0x80151802;   // X_ONLINE_E_LOGON_NOT_LOGGED_ON
            case 0x0005800E:   // XPresenceInitialize
            case 0x00058046:   // titles need this to succeed to see the signed-in profile
                return 0;
            default:           // friends, messaging, ...: unavailable
                return X_E_FAIL;
            }
        default:
            return X_E_NOTFOUND;
        }
    }
}

uint32_t XMsgStartIORequestEx(uint32_t app, uint32_t message, XXOVERLAPPED* overlapped, uint32_t buffer, uint32_t length, uint32_t taskArgs)
{
    uint32_t result = Dispatch(app, message, buffer, length);
    if (result == X_E_NOTFOUND)
        result = X_E_INVALIDARG;
    Log("XMsgStartIORequestEx", app, message, buffer, length, result);
    if (overlapped)
        return CompleteOverlapped(overlapped, result, 0);
    return result;
}

uint32_t XMsgStartIORequest(uint32_t app, uint32_t message, XXOVERLAPPED* overlapped, uint32_t buffer, uint32_t length)
{
    return XMsgStartIORequestEx(app, message, overlapped, buffer, length, 0);
}

// synchronous message: (app, message, param1, param2); param1 is usually the argument block, param2 its size
uint32_t XMsgInProcessCall(uint32_t app, uint32_t message, uint32_t param1, uint32_t param2)
{
    uint32_t result;
    if (message == 0x7001B && param1)
    {
        // XMP "get status" (Unleashed): no title music playing
        auto* p = (be<uint32_t>*)g_memory.Translate(param1);
        uint32_t* out = (uint32_t*)g_memory.Translate(p[1]);
        out[0] = 0; out[1] = 0;
        result = 0;
    }
    else
        result = Dispatch(app, message, param1, param2);
    if (result == X_E_NOTFOUND) result = X_E_INVALIDARG;
    Log("XMsgInProcessCall", app, message, param1, param2, result);
    return result;
}

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__XMsgInProcessCall, x360k::XMsgInProcessCall);
GUEST_FUNCTION_HOOK(__imp__XMsgStartIORequestEx, x360k::XMsgStartIORequestEx);
GUEST_FUNCTION_HOOK(__imp__XMsgStartIORequest, x360k::XMsgStartIORequest);
