#pragma once
// UPnP port forwarding (runtime/net/upnp.cpp): plain Win32/COM, no game code, so the launcher uses it too ("Get my
// code" on the Multiplayer page).
#include <cstdint>

namespace upnp
{
    bool ForwardUdp(uint16_t port, uint32_t& publicIp);   // publicIp: host byte order, 0 if the router did not say
    void RemoveUdp(uint16_t port);
    void SetLog(void (*log)(const char* line));          // default: no log
}
