#pragma once
// Virtual system link network (runtime/net/vnet.cpp): the game's LAN traffic carried to other players through a session
// hub (runtime/net/hub.cpp): a friend's game, this game's own hub, or a server (gow3_server.exe). The game sees a plain
// LAN: its own address 10.73.x.y, the others' 10.73.x.z, broadcasts reaching everyone in the session.
// Settings: gow3.ini [network] (user/settings.h), edited by the launcher's Multiplayer page; docs/MULTIPLAYER.md.
#include <cstdint>
#include <string>

namespace vnet
{
    bool Enabled();                    // [network] online
    bool WaitReady(int milliseconds);  // connected to the hub (false: still connecting, or failed)
    uint32_t OwnVip();                 // this player's virtual IPv4 (host byte order), 0 before the hub answered
    std::string Status();              // one line for logs / the window title
    void NoteSession();                // the title made a system link session: this game's hub stays where it is

    // sends one datagram from virtual port srcPort to dstVip:dstPort (kBroadcast: everyone in the session)
    void Send(uint32_t dstVip, uint16_t srcPort, uint16_t dstPort, const void* data, int length);

    // datagrams from the session are handed to this (x360_net.cpp delivers them to the bound guest socket)
    using DeliverFn = void (*)(uint32_t srcVip, uint16_t srcPort, uint16_t dstPort, const void* data, int length);
    void SetDeliver(DeliverFn fn);

    void Start();                      // at the title's XNetStartup; reads the settings (idempotent)
    void Stop();                       // says goodbye to the hub (process exit)
}
