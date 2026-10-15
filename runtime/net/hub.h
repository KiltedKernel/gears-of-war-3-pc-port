#pragma once
// The session hub (runtime/net/hub.cpp): forwards the players' datagrams within a room. In direct play the first game
// on a network runs one and the others join it; server/server.cpp (gow3_server.exe) is the same hub on a server that
// everyone connects to. Plain Winsock, no game code: it builds into the game and into the server alike.
#include <cstdint>
#include <string>

namespace vnet
{
    struct HubOptions
    {
        uint16_t port = 3074;          // UDP port players connect to
        bool lanDiscovery = true;      // answer LAN "who hosts?" broadcasts (kDiscoveryPort)
        bool server = false;           // a dedicated server (shown as such; its rooms are lobbies anyone may join)
        std::string name;              // shown to others: the game's gamertag, or the server's name
        std::string motd;              // server: a line shown to players when they connect
        std::string password;          // non-empty: players must send it
        int maxPlayers = 16;           // per room
        std::string bindAddress;       // empty: every interface; "127.0.0.1": this PC only (tests: no firewall prompt)
    };

    bool StartHub(const HubOptions& options);   // false: the port is in use (the reason is logged)
    void StopHub();
    int HubPlayers();                           // players in all rooms
    uint64_t HubId();                           // random, made at start: LAN games pick the lowest when two run hubs
    void SetHubCode(const std::string& code);   // the join code (UPnP), sent with LAN answers
    void SetHubLog(void (*log)(const char* line));
}
