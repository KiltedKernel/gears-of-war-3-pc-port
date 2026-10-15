// Virtual system link network: see vnet.h and vnet_protocol.h. Nobody picks "host" or "join":
//   server ([network] server):  every player connects to the same server (gow3_server.exe); its lobby (or a private
//                               room) is one big system link LAN, so the System Link browser lists everyone's parties.
//   direct (no server):         games find each other. With a friend's code or address ([network] join) the game
//                               joins that friend's game (on the same network it is found there instead, since most
//                               routers cannot loop a public address back in). Without one, it joins a game already
//                               open on this network (LAN broadcast), or opens its own hub (runtime/net/hub.cpp) that
//                               friends join: UPnP forwards the port and gives the join code (net/code.h).
//                               When two games on a network both opened a hub, the one with the higher id moves to
//                               the other while nobody else is in it and no session was made.
// Everything that happens is shown in the game (portgpu::ShowNotice) and the window title, and logged (net: lines).
// One UDP socket to the hub; a thread receives, hands datagrams to x360_net.cpp (SetDeliver) and keeps the session
// alive (a keep-alive every 2 s, answered by the hub; 8 s of silence: reconnect).
#include <winsock2.h>
#include <ws2tcpip.h>

#include <stdafx.h>
#include "vnet.h"
#include "vnet_protocol.h"
#include "hub.h"
#include "code.h"
#include "upnp.h"
#include <user/settings.h>
#include <user/profile.h>
#include <os/logger.h>
#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <thread>
#include <vector>

namespace portgpu
{
    void* GetWindowHandle();                                               // xe_glue (the game window)
    void ShowNotice(const char* title, const char* text, int seconds);     // xe_glue: a toast over the game
}

namespace vnet
{
namespace
{
    using Clock = std::chrono::steady_clock;

    struct Found { sockaddr_in addr{}; uint64_t hubId = 0; std::string name, code; int players = 0; };

    SOCKET g_socket = INVALID_SOCKET;
    std::mutex g_hubMutex;
    sockaddr_in g_hub{};                       // where the session hub is (g_hubMutex)
    std::atomic<uint32_t> g_vip{ 0 };
    std::atomic<bool> g_running{ false }, g_failed{ false }, g_sessionMade{ false }, g_ownHub{ false };
    std::mutex g_readyMutex;
    std::condition_variable g_ready;
    std::thread g_thread;
    DeliverFn g_deliver = nullptr;
    std::mutex g_statusMutex;
    std::string g_status = "offline", g_hubName, g_code;    // g_statusMutex
    std::atomic<uint16_t> g_upnpPort{ 0 };
    std::atomic<bool> g_connected{ false };
    std::mutex g_mergeMutex;
    std::optional<Found> g_mergeTo;            // MergeWatch: a lower hub on this network to move to (g_mergeMutex)
    std::thread g_mergeThread;

    std::string IpString(uint32_t hostOrder)
    {
        return fmt::format("{}.{}.{}.{}", hostOrder >> 24, (hostOrder >> 16) & 255, (hostOrder >> 8) & 255, hostOrder & 255);
    }
    std::string EndpointString(const sockaddr_in& a) { return fmt::format("{}:{}", IpString(ntohl(a.sin_addr.s_addr)), ntohs(a.sin_port)); }

    void SetStatus(const std::string& s)
    {
        { std::lock_guard lock(g_statusMutex); g_status = s; }
        LOGFN("net: {}", s);
    }

    // a toast in the game (and the log)
    void Notify(const std::string& title, const std::string& text, int seconds = 6)
    {
        LOGFN("net: [{}] {}", title, text);
        portgpu::ShowNotice(title.c_str(), text.c_str(), seconds);
    }

    sockaddr_in Hub() { std::lock_guard lock(g_hubMutex); return g_hub; }
    void SetHub(const sockaddr_in& a) { std::lock_guard lock(g_hubMutex); g_hub = a; }

    void SendToHub(const void* p, int n)
    {
        const sockaddr_in hub = Hub();
        sendto(g_socket, (const char*)p, n, 0, (const sockaddr*)&hub, sizeof(hub));
    }

    bool Resolve(const std::string& text, uint16_t defaultPort, sockaddr_in& out)
    {
        std::string host = text;
        uint16_t port = defaultPort;
        if (auto c = host.rfind(':'); c != std::string::npos) { port = uint16_t(atoi(host.c_str() + c + 1)); host.resize(c); }
        addrinfo hints{}; hints.ai_family = AF_INET; hints.ai_socktype = SOCK_DGRAM;
        addrinfo* res = nullptr;
        if (getaddrinfo(host.c_str(), nullptr, &hints, &res) != 0 || !res) return false;
        out = *(sockaddr_in*)res->ai_addr;
        out.sin_port = htons(port ? port : defaultPort);
        freeaddrinfo(res);
        return true;
    }

    // LAN: broadcast "who hosts?" (and ask this PC) for a while; every hub that answers, lowest id first
    std::vector<Found> DiscoverLan(int milliseconds)
    {
        std::vector<Found> found;
        SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        BOOL yes = TRUE;
        setsockopt(s, SOL_SOCKET, SO_BROADCAST, (const char*)&yes, sizeof(yes));
        DWORD timeout = 100;
        setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&timeout, sizeof(timeout));
        sockaddr_in local{}; local.sin_family = AF_INET;
        if (const char* b = getenv("GOW3_NET_BIND")) inet_pton(AF_INET, b, &local.sin_addr);   // tests: this PC only
        bind(s, (sockaddr*)&local, sizeof(local));
        const Header ask = MakeHeader(kDiscover);
        const auto end = Clock::now() + std::chrono::milliseconds(milliseconds);
        auto lastAsk = Clock::now() - std::chrono::seconds(1);
        while (Clock::now() < end)
        {
            if (Clock::now() - lastAsk > std::chrono::milliseconds(300))
            {
                for (uint32_t target : { uint32_t(INADDR_BROADCAST), uint32_t(INADDR_LOOPBACK) })   // the LAN, and this PC
                {
                    sockaddr_in to{}; to.sin_family = AF_INET; to.sin_port = htons(kDiscoveryPort); to.sin_addr.s_addr = htonl(target);
                    sendto(s, (const char*)&ask, sizeof(ask), 0, (sockaddr*)&to, sizeof(to));
                }
                lastAsk = Clock::now();
            }
            char buf[256]; sockaddr_in from{}; int fl = sizeof(from);
            const int n = recvfrom(s, buf, sizeof(buf), 0, (sockaddr*)&from, &fl);
            if (n < int(sizeof(Announce)) || !Valid(buf, n) || ((Header*)buf)->type != kAnnounce) continue;
            const Announce& a = *(Announce*)buf;
            if (g_ownHub && a.hubId == HubId()) continue;
            bool known = false;
            for (const Found& f : found) known |= f.hubId == a.hubId;
            if (known) continue;
            Found f;
            f.addr = from; f.addr.sin_port = htons(a.port);
            f.hubId = a.hubId; f.name = Text(a.name); f.code = Text(a.code); f.players = a.players;
            found.push_back(f);
        }
        closesocket(s);
        std::sort(found.begin(), found.end(), [](const Found& a, const Found& b) { return a.hubId < b.hubId; });
        return found;
    }

    void SendHello()
    {
        const auto& cfg = settings::Get();
        Hello h{ MakeHeader(kHello), {}, profile::Xuid(), {}, {}, {} };
        if (!cfg.netServer.empty()) { SetText(h.room, cfg.netRoom); SetText(h.password, cfg.netPassword); }
        SetText(h.gamertag, profile::Gamertag());
        SendToHub(&h, sizeof(h));
    }

    void UpdateTitle()
    {
        HWND w = (HWND)portgpu::GetWindowHandle();
        if (!w) return;
        std::string t = "Gears of War 3";
        { std::lock_guard lock(g_statusMutex); t += " - " + g_status; }
        SetWindowTextA(w, t.c_str());
    }

    const char* kHowTo = "In the game: Party Options (Back / Backspace on the main menu) > System Link. "
                         "Create a party, or join one from the list.";

    // this game's own hub (direct play, nobody found): friends join it. UPnP and the code run off this thread.
    bool OpenOwnHub(bool otherPorts)
    {
        const auto& cfg = settings::Get();
        HubOptions o;
        o.name = profile::Gamertag();
        o.lanDiscovery = true;
        if (const char* b = getenv("GOW3_NET_BIND")) o.bindAddress = b;   // tests: 127.0.0.1
        for (int i = otherPorts ? 1 : 0; i < (otherPorts ? 5 : 1); i++)
        {
            o.port = uint16_t(cfg.netPort + i);
            if (o.port == kDiscoveryPort) continue;
            if (!StartHub(o)) continue;
            g_ownHub = true;
            sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(o.port); a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            SetHub(a);
            { std::lock_guard lock(g_statusMutex); g_hubName = profile::Gamertag(); }
            // tests (tools/mp_test.ps1 -Connect hairpin): a join code as if the router had given one
            if (const char* fake = getenv("GOW3_NET_FAKE_CODE"))
            {
                { std::lock_guard lock(g_statusMutex); g_code = fake; }
                SetHubCode(fake);
            }
            if (cfg.netUpnp && !getenv("GOW3_NET_BIND"))
            {
                const uint16_t port = o.port;
                std::thread([port] {
                    upnp::SetLog([](const char* line) { LOGFN("net: {}", line); });
                    uint32_t publicIp = 0;
                    const bool ok = upnp::ForwardUdp(port, publicIp);
                    if (!g_ownHub) { if (ok) upnp::RemoveUdp(port); return; }   // moved to another game meanwhile
                    if (ok) g_upnpPort = port;
                    if (ok && publicIp)
                    {
                        const std::string c = code::Encode(publicIp, port);
                        { std::lock_guard lock(g_statusMutex); g_code = c; }
                        SetHubCode(c);
                        SetStatus(fmt::format("online, friends join with code {}", c));
                        Notify("Friends can join you", "Your code: " + c + " (the launcher shows it too). " + kHowTo, 12);
                    }
                    else
                    {
                        SetStatus("online on this network (the router did not open the port)");
                        Notify("Online on your network",
                               "Your router did not open a port, so friends over the internet cannot join your game. Join a "
                               "friend's code instead, or play on a server together.", 14);
                    }
                }).detach();
            }
            return true;
        }
        return false;
    }

    // where to connect: the server, a friend's game (code / address / found on the LAN), or this game's own hub
    bool PickHub(std::string& describe)
    {
        const auto& cfg = settings::Get();
        sockaddr_in target{};
        if (!cfg.netServer.empty())
        {
            uint32_t ip = 0; uint16_t port = 0;
            if (code::Decode(cfg.netServer, ip, port)) { target.sin_family = AF_INET; target.sin_port = htons(port); target.sin_addr.s_addr = htonl(ip); }
            else if (!Resolve(cfg.netServer, kDefaultPort, target)) { describe = "the server " + cfg.netServer + " was not found (check its address)"; return false; }
            SetHub(target);
            { std::lock_guard lock(g_statusMutex); g_hubName = cfg.netServer; }
            describe = "the server " + cfg.netServer;
            return true;
        }
        const std::vector<Found> lan = DiscoverLan(1200);
        if (!cfg.netJoin.empty())
        {
            uint32_t ip = 0; uint16_t port = 0;
            const bool isCode = code::Decode(cfg.netJoin, ip, port);
            if (isCode) { target = {}; target.sin_family = AF_INET; target.sin_port = htons(port); target.sin_addr.s_addr = htonl(ip); }
            else if (!Resolve(cfg.netJoin, kDefaultPort, target)) { describe = "no PC is called " + cfg.netJoin + " (check the code or address)"; return false; }
            // the friend is on this network (or this PC): reach their game here, not through the router
            for (const Found& f : lan)
            {
                uint32_t fip = 0; uint16_t fport = 0;
                const bool sameCode = !f.code.empty() && code::Decode(f.code, fip, fport) && fip == ntohl(target.sin_addr.s_addr) && fport == ntohs(target.sin_port);
                const bool sameAddress = f.addr.sin_addr.s_addr == target.sin_addr.s_addr && f.addr.sin_port == target.sin_port;
                if (sameCode || sameAddress)
                {
                    SetHub(f.addr);
                    { std::lock_guard lock(g_statusMutex); g_hubName = f.name; }
                    describe = f.name + "'s game on your network";
                    return true;
                }
            }
            SetHub(target);
            { std::lock_guard lock(g_statusMutex); g_hubName = isCode ? "code " + cfg.netJoin : cfg.netJoin; }
            describe = isCode ? "code " + cfg.netJoin + " (" + EndpointString(target) + ")" : cfg.netJoin;
            return true;
        }
        if (!lan.empty())
        {
            SetHub(lan.front().addr);
            { std::lock_guard lock(g_statusMutex); g_hubName = lan.front().name; }
            describe = lan.front().name + "'s game on your network";
            return true;
        }
        if (!OpenOwnHub(false))
        {
            // the port is taken: most likely another copy of the game on this PC that has just opened its hub
            const std::vector<Found> again = DiscoverLan(1500);
            if (!again.empty())
            {
                SetHub(again.front().addr);
                { std::lock_guard lock(g_statusMutex); g_hubName = again.front().name; }
                describe = again.front().name + "'s game on your network";
                return true;
            }
            if (!OpenOwnHub(true)) { describe = fmt::format("UDP ports {}-{} are all in use", cfg.netPort, cfg.netPort + 4); return false; }
        }
        describe = "your own game";
        return true;
    }

    void Welcomed(const Welcome& w, const std::string& describe, bool reconnect)
    {
        const auto& cfg = settings::Get();
        g_vip = w.vip;
        g_failed = false;
        const std::string hubName = Text(w.hubName), motd = Text(w.motd);
        { std::lock_guard lock(g_statusMutex); if (!hubName.empty()) g_hubName = hubName; }
        if (reconnect) { SetStatus("reconnected to " + describe); Notify("Reconnected", "Back in " + describe + ".", 4); }
        else if (w.flags & kHubServer)
        {
            SetStatus(fmt::format("on {} ({} players)", hubName, w.players));
            std::string text = fmt::format("{} player{} here{}. ", w.players, w.players == 1 ? "" : "s",
                                           cfg.netRoom.empty() ? "" : " in room " + cfg.netRoom);
            if (!motd.empty()) text += motd + (strchr(".!?", motd.back()) ? " " : ". ");
            Notify("Connected to " + hubName, text + kHowTo, 12);
        }
        else if (g_ownHub)
        {
            SetStatus(fmt::format("online as {}, players on this network can join", IpString(w.vip)));
            // with UPnP, the notice (with the code, or why there is none) comes when the router has answered
            if (!cfg.netUpnp || getenv("GOW3_NET_BIND"))
                Notify("Online", std::string("Players on your network can join you. ") + kHowTo, 10);
        }
        else
        {
            SetStatus(fmt::format("joined {} ({} players)", describe, w.players));
            Notify("Joined " + (hubName.empty() ? describe : hubName + "'s game"),
                   fmt::format("{} player{} in the session. ", w.players, w.players == 1 ? "" : "s") + kHowTo, 12);
        }
        g_ready.notify_all();
    }

    // direct play with this game's own hub: every 3 s while nobody joined it and no session was made, look for
    // another hub on the network with a lower id (two games opened at once)
    void MergeWatch()
    {
        const auto& cfg = settings::Get();
        while (g_running)
        {
            for (int i = 0; i < 30 && g_running; i++) std::this_thread::sleep_for(std::chrono::milliseconds(100));
            if (!g_connected || !g_ownHub || !cfg.netJoin.empty() || g_sessionMade || HubPlayers() > 1) continue;
            const std::vector<Found> lan = DiscoverLan(400);
            if (!lan.empty() && g_ownHub && lan.front().hubId < HubId())
            {
                std::lock_guard lock(g_mergeMutex);
                g_mergeTo = lan.front();
            }
        }
    }

    void Run()
    {
        const auto& cfg = settings::Get();
        std::vector<char> buf(2048);
        std::string describe;
        bool connected = false, everConnected = false, warned = false;
        auto pickAgain = Clock::now();
        auto started = Clock::now(), lastHello = started - std::chrono::seconds(10), lastKeep = started, lastHeard = started;
        auto lastTitle = started - std::chrono::seconds(10);
        bool picked = false;
        while (g_running)
        {
            const auto now = Clock::now();
            if (!picked)
            {
                if (now < pickAgain) { std::this_thread::sleep_for(std::chrono::milliseconds(100)); continue; }
                if (!PickHub(describe))
                {
                    SetStatus("cannot connect: " + describe);
                    if (!warned) Notify("Multiplayer", "Cannot connect: " + describe + ". Trying again...", 10);
                    warned = true;
                    g_failed = true;
                    g_ready.notify_all();
                    pickAgain = now + std::chrono::seconds(5);
                    continue;
                }
                picked = true;
                started = Clock::now();
                lastHello = started - std::chrono::seconds(10);
                SetStatus("connecting to " + describe);
            }

            // until welcomed: Hello every 0.5 s (1 s after the first 10 s); after 6 s, say so once
            if (!connected && now - lastHello > std::chrono::milliseconds(now - started < std::chrono::seconds(10) ? 500 : 1000))
            {
                SendHello();
                lastHello = now;
                if (now - started > std::chrono::seconds(6) && !warned)
                {
                    warned = true;
                    g_failed = true;
                    g_ready.notify_all();
                    if (!cfg.netServer.empty())
                        Notify("Cannot reach the server", cfg.netServer + " does not answer. Is it running, and is the address right? "
                               "Still trying...", 12);
                    else
                    {
                        // a friend's code that does not answer: maybe they are on this network after all
                        const std::vector<Found> lan = DiscoverLan(800);
                        if (!lan.empty())
                        {
                            SetHub(lan.front().addr);
                            describe = lan.front().name + "'s game on your network";
                            Notify("Joining your network instead", "Nobody answered at " + cfg.netJoin + ", but " + describe +
                                   " is open: joining it.", 10);
                            started = now; warned = false;
                            continue;
                        }
                        Notify("Cannot reach your friend", "Nobody answers at " + cfg.netJoin + ". Is their game running and online? "
                               "If their router cannot open a port, they can join your code instead, or you can both use a server. "
                               "Still trying...", 15);
                    }
                    SetStatus("cannot reach " + describe + " (still trying)");
                }
            }
            if (connected && now - lastKeep > std::chrono::seconds(2))
            {
                const Header k = MakeHeader(kKeepAlive);
                SendToHub(&k, sizeof(k));
                lastKeep = now;
            }
            if (connected && now - lastHeard > std::chrono::seconds(8))
            {
                connected = g_connected = false;
                warned = false;
                started = now;
                Notify("Connection lost", "Lost " + describe + ". Reconnecting...", 8);
                SetStatus("reconnecting to " + describe);
                // a friend's game that closed: in direct play without a code, find another game or open one
                if (cfg.netServer.empty() && cfg.netJoin.empty() && !g_ownHub) { picked = false; g_vip = 0; }
                continue;
            }
            // two hubs on one network: the higher id moves to the lower while it is alone and no session was made
            // (MergeWatch finds the other one, off this thread)
            if (connected && g_ownHub && !g_sessionMade && HubPlayers() <= 1)
            {
                std::optional<Found> to;
                { std::lock_guard lock(g_mergeMutex); to.swap(g_mergeTo); }
                if (to)
                {
                    const Header bye = MakeHeader(kBye);
                    SendToHub(&bye, sizeof(bye));
                    std::this_thread::sleep_for(std::chrono::milliseconds(50));   // the hub forgets us before it closes
                    if (const uint16_t port = g_upnpPort.exchange(0)) std::thread([port] { upnp::RemoveUdp(port); }).detach();
                    StopHub();
                    g_ownHub = false;
                    { std::lock_guard lock(g_statusMutex); g_code.clear(); g_hubName = to->name; }
                    SetHub(to->addr);
                    describe = to->name + "'s game on your network";
                    connected = g_connected = everConnected = false; warned = false; started = now; g_vip = 0;
                    SetStatus("moving to " + describe);
                    continue;
                }
            }
            if (now - lastTitle > std::chrono::seconds(2)) { UpdateTitle(); lastTitle = now; }

            sockaddr_in from{}; int fl = sizeof(from);
            const int n = recvfrom(g_socket, buf.data(), int(buf.size()), 0, (sockaddr*)&from, &fl);
            if (n <= 0 || !Valid(buf.data(), n)) continue;
            lastHeard = Clock::now();
            switch (((Header*)buf.data())->type)
            {
            case kWelcome:
                if (n >= int(sizeof(Welcome)) && !connected)
                {
                    connected = g_connected = true;
                    lastKeep = lastHeard;
                    Welcomed(*(Welcome*)buf.data(), describe, everConnected);
                    everConnected = true;
                }
                break;
            case kReject:
                if (n >= int(sizeof(vnet::Reject)))
                {
                    const std::string why = Text(((vnet::Reject*)buf.data())->reason);
                    SetStatus("not let in: " + why);
                    Notify("Could not join", "Not let in: " + why + ".", 15);
                    g_failed = true;
                    g_ready.notify_all();
                    // no point asking again with the same answer: wait for a restart with other settings
                    while (g_running) std::this_thread::sleep_for(std::chrono::milliseconds(200));
                }
                break;
            case kNotice:
                if (n >= int(sizeof(Notice)))
                {
                    std::string title;
                    { std::lock_guard lock(g_statusMutex); title = g_hubName.empty() ? "Multiplayer" : g_hubName; }
                    Notify(title, Text(((Notice*)buf.data())->text), 5);
                }
                break;
            case kData:
                if (n >= int(sizeof(Data)) && g_deliver)
                {
                    const Data& d = *(Data*)buf.data();
                    g_deliver(d.srcVip, d.srcPort, d.dstPort, buf.data() + sizeof(Data), n - int(sizeof(Data)));
                }
                break;
            }
        }
    }
}

bool Enabled() { return settings::Get().netOnline; }
uint32_t OwnVip() { return g_vip; }
std::string Status() { std::lock_guard lock(g_statusMutex); return g_status; }
void SetDeliver(DeliverFn fn) { g_deliver = fn; }
void NoteSession() { g_sessionMade = true; }

bool WaitReady(int milliseconds)
{
    std::unique_lock lock(g_readyMutex);
    g_ready.wait_for(lock, std::chrono::milliseconds(milliseconds), [] { return g_vip != 0 || g_failed.load(); });
    return g_vip != 0;
}

void Send(uint32_t dstVip, uint16_t srcPort, uint16_t dstPort, const void* data, int length)
{
    const uint32_t vip = g_vip;
    if (g_socket == INVALID_SOCKET || !vip || length < 0 || length > kMaxPayload) return;
    char buf[sizeof(Data) + kMaxPayload];
    Data d{ MakeHeader(kData), vip, dstVip, srcPort, dstPort };
    memcpy(buf, &d, sizeof(d));
    memcpy(buf + sizeof(d), data, size_t(length));
    SendToHub(buf, int(sizeof(d) + length));
}

void Start()
{
    static std::once_flag once;
    std::call_once(once, [] {
        if (!Enabled()) return;
        WSADATA wsa; WSAStartup(MAKEWORD(2, 2), &wsa);
        SetHubLog([](const char* line) { LOGFN("net: {}", line); });
        g_socket = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        DWORD timeout = 200;
        setsockopt(g_socket, SOL_SOCKET, SO_RCVTIMEO, (const char*)&timeout, sizeof(timeout));
        int buffer = 1 << 20;   // the game's traffic comes in bursts (a match start): no drops while this thread is busy
        setsockopt(g_socket, SOL_SOCKET, SO_RCVBUF, (const char*)&buffer, sizeof(buffer));
        setsockopt(g_socket, SOL_SOCKET, SO_SNDBUF, (const char*)&buffer, sizeof(buffer));
        BOOL no = FALSE; DWORD ret = 0;   // an unreachable hub must not end the receive loop (WSAECONNRESET)
        WSAIoctl(g_socket, SIO_UDP_CONNRESET, &no, sizeof(no), nullptr, 0, &ret, nullptr, nullptr);
        sockaddr_in local{}; local.sin_family = AF_INET;
        if (const char* b = getenv("GOW3_NET_BIND")) inet_pton(AF_INET, b, &local.sin_addr);   // tests: this PC only
        bind(g_socket, (sockaddr*)&local, sizeof(local));
        LOGFN("net: online as {} ({:016X}); {}", profile::Gamertag(), profile::Xuid(),
              settings::Get().netServer.empty() ? "direct play" : "server " + settings::Get().netServer);
        g_running = true;
        g_thread = std::thread(Run);
        if (settings::Get().netServer.empty()) g_mergeThread = std::thread(MergeWatch);
    });
}

void Stop()
{
    if (!g_running) return;
    const Header bye = MakeHeader(kBye);
    SendToHub(&bye, sizeof(bye));
    g_running = false;
    if (g_thread.joinable()) g_thread.join();
    if (g_mergeThread.joinable()) g_mergeThread.join();
    closesocket(g_socket); g_socket = INVALID_SOCKET;
    if (g_upnpPort) upnp::RemoveUdp(g_upnpPort);
    StopHub();
}
}
