// Session hub: see hub.h and vnet_protocol.h. One thread receives on the hub's UDP port; a second answers LAN
// discovery broadcasts. Players are known by their UDP endpoint; each gets a virtual IP in its room (10.73.0.2 up),
// kept when the same XUID reconnects. A datagram from a player is forwarded with its source rewritten to the sender's
// virtual IP (players cannot impersonate each other), to one player (by virtual IP) or to all others (broadcast).
// The players of a room are told when someone joins or leaves (Notice); anyone may ask who is there (Query / Info).
#include <winsock2.h>
#include <ws2tcpip.h>

#include "hub.h"
#include "vnet_protocol.h"
#include <atomic>
#include <cstdarg>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>
#pragma comment(lib, "ws2_32.lib")

namespace vnet
{
namespace
{
    using Clock = std::chrono::steady_clock;

    struct Player
    {
        sockaddr_in addr{};
        uint32_t vip = 0;
        uint64_t xuid = 0;
        std::string gamertag;
        std::string room;
        Clock::time_point seen;
    };

    std::mutex g_mutex;
    HubOptions g_options;
    SOCKET g_socket = INVALID_SOCKET, g_discovery = INVALID_SOCKET;
    std::atomic<bool> g_running{ false };
    std::thread g_thread, g_discoveryThread;
    std::map<uint64_t, Player> g_players;           // key: endpoint (address << 16 | port)
    std::map<std::string, std::map<uint64_t, uint32_t>> g_vipsByXuid;   // room -> xuid -> vip (kept across reconnects)
    uint64_t g_hubId = 0;
    std::string g_code;
    void (*g_log)(const char*) = nullptr;

    void Log(const char* fmt, ...)
    {
        char line[512];
        va_list a; va_start(a, fmt); vsnprintf(line, sizeof(line), fmt, a); va_end(a);
        if (g_log) g_log(line); else { fputs(line, stdout); fputc('\n', stdout); fflush(stdout); }
    }

    uint64_t Key(const sockaddr_in& a) { return (uint64_t(a.sin_addr.s_addr) << 16) | a.sin_port; }
    std::string Ip(uint32_t vipHost) { char b[32]; snprintf(b, sizeof(b), "%u.%u.%u.%u", vipHost >> 24, (vipHost >> 16) & 255, (vipHost >> 8) & 255, vipHost & 255); return b; }
    std::string RoomName(const std::string& room) { return room.empty() ? "the lobby" : "room '" + room + "'"; }

    void SendTo(const sockaddr_in& to, const void* p, int n) { sendto(g_socket, (const char*)p, n, 0, (const sockaddr*)&to, sizeof(to)); }

    void Reject(const sockaddr_in& to, const std::string& why, uint32_t magic = kMagic)
    {
        vnet::Reject r{ MakeHeader(kReject), {} };
        r.h.magic = magic;
        SetText(r.reason, why);
        SendTo(to, &r, sizeof(r));
    }

    // a line to everyone in the room but one (joins and leaves)
    void NoticeRoom(const std::string& room, uint64_t except, const std::string& text)
    {
        Notice n{ MakeHeader(kNotice), {} };
        SetText(n.text, text);
        for (const auto& [k, p] : g_players)
            if (p.room == room && k != except) SendTo(p.addr, &n, sizeof(n));
    }

    uint32_t AllocateVip(const std::string& room, uint64_t xuid)
    {
        auto& known = g_vipsByXuid[room];
        if (auto it = known.find(xuid); it != known.end()) return it->second;
        for (uint32_t host = 2; host < 0xFFFE; host++)
        {
            if ((host & 0xFF) == 0 || (host & 0xFF) == 0xFF) continue;   // .0 / .255: network and broadcast look-alikes
            const uint32_t vip = kNetwork | host;
            bool used = false;
            for (const auto& [k, v] : known) if (v == vip) { used = true; break; }
            if (!used) { known[xuid] = vip; return vip; }
        }
        return 0;
    }

    void OnHello(const sockaddr_in& from, const Hello& h)
    {
        const std::string room = Text(h.room), gamertag = Text(h.gamertag);
        if (!g_options.password.empty() && Text(h.password) != g_options.password)
        {
            Reject(from, Text(h.password).empty() ? "this server has a password" : "wrong password");
            return;
        }
        const uint64_t key = Key(from);
        // the same profile from another endpoint: a reconnect (the old one went quiet), or a second copy of the game
        for (auto it = g_players.begin(); it != g_players.end(); ++it)
        {
            if (it->first == key || it->second.xuid != h.xuid || it->second.room != room) continue;
            if (Clock::now() - it->second.seen < std::chrono::seconds(4))
            {
                Reject(from, "your profile is already in this session (two copies of the game with one profile?)");
                return;
            }
            g_players.erase(it);
            break;
        }
        int inRoom = 0;
        for (const auto& [k, p] : g_players) if (p.room == room && k != key) inRoom++;
        if (inRoom >= g_options.maxPlayers) { Reject(from, "the session is full"); return; }
        Player& p = g_players[key];
        const bool fresh = p.vip == 0;
        p.addr = from; p.xuid = h.xuid; p.gamertag = gamertag; p.room = room;
        p.seen = Clock::now();
        if (fresh) p.vip = AllocateVip(room, h.xuid);
        if (!p.vip) { g_players.erase(key); Reject(from, "no address left"); return; }
        Welcome w{ MakeHeader(kWelcome), p.vip, kNetwork | 1, uint16_t(inRoom + 1),
                   uint16_t((g_options.server ? kHubServer : 0) | (g_options.password.empty() ? 0 : kHubPassword)), {}, {} };
        SetText(w.hubName, g_options.name);
        SetText(w.motd, g_options.motd);
        SendTo(from, &w, sizeof(w));
        if (fresh)
        {
            Log("hub: %s joined %s as %s (%d players)", p.gamertag.c_str(), RoomName(room).c_str(), Ip(p.vip).c_str(), inRoom + 1);
            NoticeRoom(room, key, p.gamertag + " joined");
        }
    }

    void OnData(const sockaddr_in& from, char* buf, int n)
    {
        auto it = g_players.find(Key(from));
        if (it == g_players.end()) return;                     // not joined (or dropped): ignore
        Player& sender = it->second;
        sender.seen = Clock::now();
        Data* d = (Data*)buf;
        d->srcVip = sender.vip;                                 // no impersonation
        if (d->dstVip == kBroadcast || (d->dstVip & 0xFF) == 0xFF)
        {
            for (auto& [k, p] : g_players)
                if (&p != &sender && p.room == sender.room) SendTo(p.addr, buf, n);
            return;
        }
        for (auto& [k, p] : g_players)
            if (p.room == sender.room && p.vip == d->dstVip) { SendTo(p.addr, buf, n); return; }
    }

    void OnQuery(const sockaddr_in& from, const Query& q)
    {
        Info info{};
        info.h = MakeHeader(kInfo);
        info.token = q.token;
        info.flags = uint16_t((g_options.server ? kHubServer : 0) | (g_options.password.empty() ? 0 : kHubPassword));
        info.players = uint16_t(g_players.size());
        info.maxPlayers = uint16_t(g_options.maxPlayers);
        SetText(info.name, g_options.name);
        SetText(info.motd, g_options.motd);
        for (const auto& [k, p] : g_players)
        {
            if (info.listed == kMaxListed) break;
            SetText(info.gamertags[info.listed++], p.gamertag);
        }
        SendTo(from, &info, sizeof(info));
    }

    void Leave(std::map<uint64_t, Player>::iterator it, const char* why)
    {
        Log("hub: %s %s %s", it->second.gamertag.c_str(), why, RoomName(it->second.room).c_str());
        const std::string room = it->second.room, gamertag = it->second.gamertag;
        const uint64_t key = it->first;
        g_players.erase(it);
        NoticeRoom(room, key, gamertag + " left");
    }

    void Receive()
    {
        std::vector<char> buf(2048);
        auto lastSweep = Clock::now();
        while (g_running)
        {
            sockaddr_in from{}; int fl = sizeof(from);
            const int n = recvfrom(g_socket, buf.data(), int(buf.size()), 0, (sockaddr*)&from, &fl);
            std::lock_guard lock(g_mutex);
            if (n > 0 && n >= int(sizeof(Header)) && ((Header*)buf.data())->magic == kMagic && ((Header*)buf.data())->version != kVersion)
            {
                if (((Header*)buf.data())->type == kHello) Reject(from, "a different version of the PC port: both need the same one");
            }
            else if (n >= int(sizeof(Header)) && OtherGame(((Header*)buf.data())->magic))
            {
                // the other game's port: its Hello (or its launcher's Query) gets a Reject it can read (its magic; version 2
                // layout in both)
                const uint8_t type = ((Header*)buf.data())->type;
                if (type == kHello || (type == kQuery && n >= int(sizeof(Query))))
                    Reject(from, std::string("this is for ") + kGameName + ", not " + OtherGame(((Header*)buf.data())->magic), ((Header*)buf.data())->magic);
            }
            else if (n > 0 && Valid(buf.data(), n))
            {
                switch (((Header*)buf.data())->type)
                {
                case kHello: if (n >= int(sizeof(Hello))) OnHello(from, *(Hello*)buf.data()); break;
                case kData: if (n >= int(sizeof(Data))) OnData(from, buf.data(), n); break;
                case kQuery: if (n >= int(sizeof(Query))) OnQuery(from, *(Query*)buf.data()); break;
                case kKeepAlive:
                    if (auto it = g_players.find(Key(from)); it != g_players.end())
                    {
                        it->second.seen = Clock::now();
                        const Header k = MakeHeader(kKeepAlive);   // the game knows the hub is still there
                        SendTo(from, &k, sizeof(k));
                    }
                    break;
                case kBye:
                    if (auto it = g_players.find(Key(from)); it != g_players.end()) Leave(it, "left");
                    break;
                }
            }
            // players not heard from for 10 s are dropped
            const auto now = Clock::now();
            if (now - lastSweep < std::chrono::seconds(1)) continue;
            lastSweep = now;
            for (auto i = g_players.begin(); i != g_players.end();)
            {
                auto next = std::next(i);
                if (now - i->second.seen > std::chrono::seconds(10)) Leave(i, "timed out of");
                i = next;
            }
        }
    }

    // LAN: answer "who hosts?" with the hub's port, name, id and code
    void Discovery()
    {
        std::vector<char> buf(512);
        while (g_running)
        {
            sockaddr_in from{}; int fl = sizeof(from);
            const int n = recvfrom(g_discovery, buf.data(), int(buf.size()), 0, (sockaddr*)&from, &fl);
            if (n <= 0) { if (!g_running) break; continue; }
            if (!Valid(buf.data(), n) || ((Header*)buf.data())->type != kDiscover) continue;
            Announce a{ MakeHeader(kAnnounce), g_options.port, uint16_t(HubPlayers()), g_hubId, {}, {} };
            SetText(a.name, g_options.name);
            { std::lock_guard lock(g_mutex); SetText(a.code, g_code); }
            sendto(g_discovery, (const char*)&a, sizeof(a), 0, (const sockaddr*)&from, sizeof(from));
        }
    }
}

void SetHubLog(void (*log)(const char*)) { g_log = log; }
uint64_t HubId() { return g_hubId; }
void SetHubCode(const std::string& code) { std::lock_guard lock(g_mutex); g_code = code; }

int HubPlayers()
{
    std::lock_guard lock(g_mutex);
    return int(g_players.size());
}

bool StartHub(const HubOptions& options)
{
    if (g_running) return true;
    WSADATA wsa; WSAStartup(MAKEWORD(2, 2), &wsa);
    g_options = options;
    std::random_device rd;
    g_hubId = (uint64_t(rd()) << 32) | rd();
    g_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    BOOL exclusive = TRUE;   // two games on one PC must not share the port
    setsockopt(g_socket, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, (const char*)&exclusive, sizeof(exclusive));
    uint32_t bindIp = INADDR_ANY;
    if (!options.bindAddress.empty()) inet_pton(AF_INET, options.bindAddress.c_str(), &bindIp);
    sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(options.port); a.sin_addr.s_addr = bindIp;
    if (bind(g_socket, (sockaddr*)&a, sizeof(a)) != 0)
    {
        Log("hub: UDP port %u is in use (error %d)", options.port, WSAGetLastError());
        closesocket(g_socket); g_socket = INVALID_SOCKET;
        return false;
    }
    // a datagram to a player who has gone makes Windows report WSAECONNRESET on the next receive: not an error here
    BOOL no = FALSE; DWORD ret = 0;
    WSAIoctl(g_socket, SIO_UDP_CONNRESET, &no, sizeof(no), nullptr, 0, &ret, nullptr, nullptr);
    int buffer = 1 << 20;    // every player's traffic passes here
    setsockopt(g_socket, SOL_SOCKET, SO_RCVBUF, (const char*)&buffer, sizeof(buffer));
    setsockopt(g_socket, SOL_SOCKET, SO_SNDBUF, (const char*)&buffer, sizeof(buffer));
    DWORD timeout = 1000;    // the receive loop also drops silent players
    setsockopt(g_socket, SOL_SOCKET, SO_RCVTIMEO, (const char*)&timeout, sizeof(timeout));
    g_running = true;
    g_thread = std::thread(Receive);
    if (options.lanDiscovery)
    {
        g_discovery = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        BOOL yes = TRUE;
        setsockopt(g_discovery, SOL_SOCKET, SO_REUSEADDR, (const char*)&yes, sizeof(yes));   // several hubs on one PC
        sockaddr_in d{}; d.sin_family = AF_INET; d.sin_port = htons(kDiscoveryPort); d.sin_addr.s_addr = bindIp;
        if (bind(g_discovery, (sockaddr*)&d, sizeof(d)) == 0) g_discoveryThread = std::thread(Discovery);
        else { Log("hub: LAN discovery port %u unavailable", kDiscoveryPort); closesocket(g_discovery); g_discovery = INVALID_SOCKET; }
    }
    Log("hub: listening on UDP %u", options.port);
    return true;
}

void StopHub()
{
    if (!g_running) return;
    {
        // the players learn at once rather than after a time-out
        std::lock_guard lock(g_mutex);
        Notice n{ MakeHeader(kNotice), {} };
        SetText(n.text, g_options.server ? g_options.name + " is shutting down" : g_options.name + " closed the session");
        for (const auto& [k, p] : g_players) SendTo(p.addr, &n, sizeof(n));
    }
    g_running = false;
    closesocket(g_socket); g_socket = INVALID_SOCKET;
    if (g_discovery != INVALID_SOCKET) { closesocket(g_discovery); g_discovery = INVALID_SOCKET; }
    if (g_thread.joinable()) g_thread.join();
    if (g_discoveryThread.joinable()) g_discoveryThread.join();
    std::lock_guard lock(g_mutex);
    g_players.clear();
    g_vipsByXuid.clear();
    g_code.clear();
}
}
