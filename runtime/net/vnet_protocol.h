#pragma once
// The session hub protocol (runtime/net/vnet.cpp, hub.cpp, server/server.cpp): one UDP socket per game or server.
// Every message starts with Header; integers are little-endian. A game connects to one hub: a friend's game (direct
// play: the first game on a network opens a hub, the others join it), or a server (gow3_server.exe). The hub gives it
// a virtual IP on 10.73.0.0/16 and forwards the game's datagrams between the players of its room (unicast by virtual
// IP, or to everyone for broadcast). Header-only: the launcher includes it too (server list, connection check).
#include <cstdint>
#include <cstring>
#include <string>
#ifdef _WINSOCK2API_
#ifndef SIO_UDP_CONNRESET   // mstcpip.h: a datagram to a closed port must not fail the next receive (WSAECONNRESET)
#define SIO_UDP_CONNRESET _WSAIOW(IOC_VENDOR, 12)
#endif
#endif

namespace vnet
{
    // each game's port has its own magic, so a Gears 3 game never mixes with a Gears 2 one: a hub turns the other
    // game away with a Reject in that game's own magic, which its player can read
    constexpr uint32_t kMagicGears2 = 0x4E563247;    // "G2VN"
    constexpr uint32_t kMagicGears3 = 0x4E563347;    // "G3VN"
    constexpr uint32_t kMagic = kMagicGears3;
    constexpr const char* kGameName = "Gears of War 3";
    inline const char* OtherGame(uint32_t magic) { return magic == kMagicGears2 ? "Gears of War 2" : nullptr; }
    constexpr uint8_t kVersion = 2;
    constexpr uint16_t kDefaultPort = 3074;          // the Xbox's own port; any UDP port works
    constexpr uint16_t kDiscoveryPort = 3075;        // LAN: "who hosts?" broadcasts and the hubs' answers
    constexpr uint32_t kNetwork = 0x0A490000;        // 10.73.0.0
    constexpr uint32_t kBroadcast = 0xFFFFFFFF;
    constexpr int kMaxPayload = 1400;
    constexpr int kMaxListed = 24;                   // gamertags in an Info answer

    enum Type : uint8_t
    {
        kHello = 1,      // game -> hub: Hello (repeated until Welcome or Reject)
        kWelcome = 2,    // hub -> game: Welcome
        kData = 3,       // either way: Data + payload
        kKeepAlive = 4,  // game -> hub every 2 s, and the hub's answer (a hub drops a player silent for 10 s)
        kBye = 5,        // game -> hub: leaving
        kDiscover = 6,   // LAN broadcast to kDiscoveryPort: who hosts?
        kAnnounce = 7,   // hub -> asker: Announce
        kReject = 8,     // hub -> game: Reject (full, wrong password, same profile twice...)
        kQuery = 9,      // anyone -> hub: Query (the launcher's server list and connection check; needs no Hello)
        kInfo = 10,      // hub -> asker: Info
        kNotice = 11,    // hub -> players of a room: Notice (someone joined or left)
    };

    enum HubFlags : uint16_t { kHubServer = 1, kHubPassword = 2 };

#pragma pack(push, 1)
    struct Header { uint32_t magic; uint8_t version; uint8_t type; uint16_t reserved; };
    struct Hello { Header h; char room[32]; uint64_t xuid; char gamertag[16]; char password[32]; uint64_t mods; char modList[44]; };
    // mods: the fingerprint of the player's "everyone" mods (kernel/mod_manifest.h; 0: none, as games before 0.3 send in
    // what was padding), modList their names for messages. Hello is the size of a Welcome, and Query of an Info:
    // a hub never answers a spoofed sender with more bytes than it got
    struct Welcome { Header h; uint32_t vip; uint32_t hubVip; uint16_t players; uint16_t flags; char hubName[32]; char motd[96]; };
    struct Data { Header h; uint32_t srcVip; uint32_t dstVip; uint16_t srcPort; uint16_t dstPort; };   // + payload
    struct Announce { Header h; uint16_t port; uint16_t players; uint64_t hubId; char name[32]; char code[16]; };
    struct Reject { Header h; char reason[96]; };
    struct Info
    {
        Header h; uint32_t token; uint16_t flags; uint16_t players; uint16_t maxPlayers; uint8_t listed; uint8_t reserved;
        char name[32]; char motd[96]; char gamertags[kMaxListed][16];
    };
    struct Query { Header h; uint32_t token; char pad[sizeof(Info) - sizeof(Header) - 4]; };
    struct Notice { Header h; char text[96]; };
#pragma pack(pop)
    static_assert(sizeof(Hello) == sizeof(Welcome) && sizeof(Query) == sizeof(Info), "requests are as large as their answers");

    inline Header MakeHeader(Type t) { return Header{ kMagic, kVersion, uint8_t(t), 0 }; }
    inline bool Valid(const void* p, int n)
    {
        if (n < int(sizeof(Header))) return false;
        const Header* h = (const Header*)p;
        return h->magic == kMagic && h->version == kVersion;
    }
    // a fixed-size text field from a message (may lack the terminator)
    template <size_t N> inline std::string Text(const char (&s)[N]) { return std::string(s, strnlen(s, N)); }
    template <size_t N> inline void SetText(char (&d)[N], const std::string& s) { memset(d, 0, N); memcpy(d, s.data(), s.size() < N - 1 ? s.size() : N - 1); }
}
