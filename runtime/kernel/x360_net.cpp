// Networking: the Xbox's Winsock/XNet (xam NetDll_*) on the host's Winsock, after Xenia's xam_net.cc / xsocket.cc.
// UE3 opens real sockets even for the single-player campaign (its net driver runs a local listen server), so these
// must work; there is no Xbox Live. Sockets stay on loopback. Every NetDll_ export takes the caller ("XNCALLER_TITLE")
// as its first argument.
// System link (gow3.ini [network], runtime/net/vnet.cpp): the title's UDP sockets live on a virtual LAN. A socket the
// title binds to port P (its virtual port) is a host socket on an ephemeral loopback port; datagrams to another player's
// virtual address (10.73.x.y) or to a broadcast go through the session hub, and datagrams from the session reach the
// bound socket through a loopback "proxy" socket per sender, whose address recvfrom turns back into the sender's
// virtual address. Host sockets keep their native blocking, select and event behaviour.
//  - guest sockets are small handles mapped to host SOCKETs
//  - guest sockaddrs are big-endian in memory: family is a BE u16, port/address are network order (as on the host)
//  - 4-byte socket options and ioctl arguments are byte-swapped (Xenia passes them raw)
//  - errors: Winsock codes equal the Xbox's; they go to the guest thread's last error, which WSAGetLastError reads
//  - VDP (Xbox voice/data protocol, 254) is plain UDP here
#include <winsock2.h>
#include <stdafx.h>
#include <ws2tcpip.h>
#include <cpu/ppc_context.h>
#include <cpu/guest_thread.h>
#include "function.h"
#include "memory.h"
#include "xdm.h"
#include "wait.h"
#include <os/logger.h>
#include <net/vnet.h>
#include <net/vnet_protocol.h>
#include <map>
#include <set>
#pragma comment(lib, "ws2_32.lib")

uint32_t SignalGuestEvent(uint32_t handle);                                                   // imports.cpp
uint32_t WaitForObjects(KernelObject** objs, uint32_t count, bool waitAll, uint32_t timeout); // imports.cpp
uint32_t NtCreateEvent(be<uint32_t>* handle, void* objAttributes, uint32_t eventType, uint32_t initialState);
uint32_t ResetGuestEvent(uint32_t handle);                                                    // imports.cpp

namespace x360k
{

namespace
{
    constexpr uint32_t kInvalid = 0xFFFFFFFF;   // INVALID_SOCKET / SOCKET_ERROR (-1)

    std::mutex g_sockMutex;
    std::map<uint32_t, SOCKET> g_sockets;
    uint32_t g_nextSocket = 0x100;

    SOCKET Host(uint32_t s)
    {
        std::lock_guard lock(g_sockMutex);
        auto it = g_sockets.find(s);
        return it == g_sockets.end() ? INVALID_SOCKET : it->second;
    }

    uint32_t Fail(int wsaError)
    {
        GuestThread::SetLastError(uint32_t(wsaError));
        return kInvalid;
    }
    uint32_t FailHost() { return Fail(WSAGetLastError()); }
    uint32_t NotSock() { return Fail(WSAENOTSOCK); }

    // guest sockaddr <-> host sockaddr (same layout, family big-endian on the guest)
    struct GuestSockaddr { be<uint16_t> family; char data[14]; };
    sockaddr_storage ToHost(const void* guest, int len)
    {
        sockaddr_storage h{};
        if (guest && len >= 2)
        {
            auto* g = (const GuestSockaddr*)guest;
            memcpy(&h, guest, std::min<size_t>(len, sizeof(h)));
            ((sockaddr*)&h)->sa_family = g->family;
        }
        return h;
    }
    void ToGuest(void* guest, const sockaddr_storage& h, int len)
    {
        if (!guest) return;
        memcpy(guest, &h, len);
        ((GuestSockaddr*)guest)->family = ((const sockaddr*)&h)->sa_family;
    }

    bool g_wsaStarted = false;
    void EnsureWinsock()
    {
        if (g_wsaStarted) return;
        WSADATA d;
        g_wsaStarted = WSAStartup(MAKEWORD(2, 2), &d) == 0;
    }

    // ---- system link: virtual UDP sockets (see the top of this file) ----
    struct Virtual
    {
        uint16_t vport = 0;          // the port the title bound (0: not bound yet)
        uint16_t hostPort = 0;       // the host socket's loopback port
        uint32_t boundIp = 0;        // the address the title bound (host order; 0 = any)
        bool hasPeer = false;        // connect()ed: send() goes to peer
        uint32_t peerIp = 0; uint16_t peerPort = 0;
    };
    std::map<uint32_t, Virtual> g_virtual;                   // guest socket -> its virtual side (UDP, system link on)
    std::map<uint64_t, SOCKET> g_proxies;                    // sender (ip << 16 | port) -> loopback proxy socket
    std::map<uint16_t, std::pair<uint32_t, uint16_t>> g_proxyFrom;   // proxy's loopback port -> sender
    uint16_t g_nextEphemeral = 49152;

    bool IsVirtualNet(uint32_t ip) { return (ip & 0xFFFF0000u) == vnet::kNetwork; }
    bool IsBroadcast(uint32_t ip) { return ip == 0xFFFFFFFFu || ip == (vnet::kNetwork | 0xFFFF) || (IsVirtualNet(ip) && (ip & 0xFF) == 0xFF); }
    bool IsLocal(uint32_t ip) { return (ip >> 24) == 127 || ip == vnet::OwnVip() || ip == 0; }

    bool VirtualFor(uint32_t s, Virtual*& v)   // caller holds g_sockMutex
    {
        auto it = g_virtual.find(s);
        if (it == g_virtual.end()) return false;
        v = &it->second;
        return true;
    }

    // a host socket on loopback for the title's (virtual) bind; port 0 picks a free virtual port
    bool BindVirtual(uint32_t s, SOCKET h, uint32_t ip, uint16_t port)   // caller holds g_sockMutex
    {
        Virtual& v = g_virtual[s];
        if (port == 0)
        {
            for (int tries = 0; tries < 16384 && !port; tries++)
            {
                const uint16_t p = g_nextEphemeral++;
                if (g_nextEphemeral < 49152) g_nextEphemeral = 49152;
                bool used = false;
                for (const auto& [k, o] : g_virtual) if (o.vport == p) { used = true; break; }
                if (!used) port = p;
            }
        }
        else
            for (const auto& [k, o] : g_virtual)
                if (k != s && o.vport == port) return false;   // WSAEADDRINUSE
        sockaddr_in a{}; a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        if (bind(h, (sockaddr*)&a, sizeof(a)) != 0) return false;
        int len = sizeof(a);
        getsockname(h, (sockaddr*)&a, &len);
        v.vport = port; v.hostPort = ntohs(a.sin_port); v.boundIp = ip;
        LOGFN("net: system link socket {:X} bound to virtual port {} (host port {})", s, port, v.hostPort);
        return true;
    }

    // a datagram from the session (or from this player to itself) to the title's socket on virtual port dstPort
    void Deliver(uint32_t srcIp, uint16_t srcPort, uint16_t dstPort, const void* data, int length)
    {
        std::lock_guard lock(g_sockMutex);
        uint16_t hostPort = 0;
        for (const auto& [k, v] : g_virtual) if (v.vport == dstPort) { hostPort = v.hostPort; break; }
        if (!hostPort) return;                                   // nobody listens there (as a real LAN would drop it)
        const uint64_t key = (uint64_t(srcIp) << 16) | srcPort;
        SOCKET proxy;
        if (auto it = g_proxies.find(key); it != g_proxies.end()) proxy = it->second;
        else
        {
            proxy = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
            sockaddr_in a{}; a.sin_family = AF_INET; a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            bind(proxy, (sockaddr*)&a, sizeof(a));
            int len = sizeof(a);
            getsockname(proxy, (sockaddr*)&a, &len);
            g_proxies[key] = proxy;
            g_proxyFrom[ntohs(a.sin_port)] = { srcIp, srcPort };
        }
        sockaddr_in to{}; to.sin_family = AF_INET; to.sin_port = htons(hostPort); to.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        sendto(proxy, (const char*)data, length, 0, (sockaddr*)&to, sizeof(to));
    }

    // a received datagram's loopback source -> the sender's virtual address (unchanged if it came from elsewhere)
    void TranslateFrom(sockaddr_storage& a)
    {
        auto* in = (sockaddr_in*)&a;
        if (in->sin_family != AF_INET || ntohl(in->sin_addr.s_addr) != INADDR_LOOPBACK) return;
        std::lock_guard lock(g_sockMutex);
        if (auto it = g_proxyFrom.find(ntohs(in->sin_port)); it != g_proxyFrom.end())
        {
            in->sin_addr.s_addr = htonl(it->second.first);
            in->sin_port = htons(it->second.second);
        }
    }

    // the title sends from a virtual socket: to itself, to a player, or to everyone. Returns false for other
    // destinations (the host socket sends those as they are)
    bool SendVirtual(uint32_t s, SOCKET h, const char* data, int length, uint32_t dstIp, uint16_t dstPort)
    {
        uint16_t srcPort;
        {
            std::lock_guard lock(g_sockMutex);
            Virtual* v;
            if (!VirtualFor(s, v)) return false;
            if (!v->vport && !BindVirtual(s, h, 0, 0)) return false;   // sendto before bind: an implicit bind
            srcPort = v->vport;
        }
        if (IsBroadcast(dstIp))
        {
            vnet::Send(vnet::kBroadcast, srcPort, dstPort, data, length);
            Deliver(vnet::OwnVip(), srcPort, dstPort, data, length);       // a broadcast reaches this PC's sockets too
            return true;
        }
        if (IsLocal(dstIp)) { Deliver((dstIp >> 24) == 127 ? dstIp : vnet::OwnVip(), srcPort, dstPort, data, length); return true; }
        if (IsVirtualNet(dstIp)) { vnet::Send(dstIp, srcPort, dstPort, data, length); return true; }
        return false;
    }
}

// ---- startup ----
uint32_t NetDll_WSAStartup(uint32_t caller, uint32_t version, uint8_t* data)
{
    EnsureWinsock();
    if (data)
    {
        memset(data, 0, 0x190);   // X_WSADATA
        *(be<uint16_t>*)(data + 0) = 0x0202;
        *(be<uint16_t>*)(data + 2) = 0x0202;
    }
    return 0;
}
uint32_t NetDll_WSACleanup(uint32_t caller) { return 0; }   // keep Winsock up (the title restarts it)
uint32_t NetDll_XNetStartup(uint32_t caller, uint32_t params)
{
    EnsureWinsock();
    if (vnet::Enabled()) { vnet::SetDeliver(Deliver); vnet::Start(); }
    return 0;
}
uint32_t NetDll_XNetCleanup(uint32_t caller, uint32_t params) { return 0; }

uint32_t NetDll_WSAGetLastError() { return GuestThread::GetLastError(); }
void NetDll_WSASetLastError(uint32_t error) { GuestThread::SetLastError(error); }

// ---- sockets ----
uint32_t NetDll_socket(uint32_t caller, uint32_t af, uint32_t type, uint32_t protocol)
{
    EnsureWinsock();
    if (protocol == 254) protocol = IPPROTO_UDP;   // VDP
    SOCKET h = socket(int(af), int(type), int(protocol));
    if (h == INVALID_SOCKET) return FailHost();
    std::lock_guard lock(g_sockMutex);
    const uint32_t s = g_nextSocket++;
    g_sockets[s] = h;
    if (vnet::Enabled() && af == AF_INET && type == SOCK_DGRAM) g_virtual[s] = {};
    LOGFN("net: socket(af {}, type {}, protocol {}) -> {:X}", af, type, protocol, s);
    return s;
}

uint32_t NetDll_closesocket(uint32_t caller, uint32_t s)
{
    SOCKET h;
    {
        std::lock_guard lock(g_sockMutex);
        auto it = g_sockets.find(s);
        if (it == g_sockets.end()) return NotSock();
        h = it->second;
        g_sockets.erase(it);
        g_virtual.erase(s);
    }
    return closesocket(h) == 0 ? 0 : FailHost();
}

uint32_t NetDll_shutdown(uint32_t caller, uint32_t s, uint32_t how)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    return shutdown(h, int(how)) == 0 ? 0 : FailHost();
}

uint32_t NetDll_setsockopt(uint32_t caller, uint32_t s, uint32_t level, uint32_t name, uint8_t* value, uint32_t length)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    if (level == 0xFFFF && (name == 0x5801 || name == 0x5802)) return 0;   // SO_DECRYPTSOCK / Xbox security: none here
    std::vector<uint8_t> v(value, value + length);
    if (length == 4) *(uint32_t*)v.data() = ByteSwap(*(uint32_t*)v.data());
    else if (level == 0xFFFF && name == SO_LINGER && length == 4) { /* handled above */ }
    if (setsockopt(h, int(level), int(name), (const char*)v.data(), int(length)) != 0)
    {
        LOGFN_WARNING("net: setsockopt({:X}, level {:X}, option {:X}) failed: {}", s, level, name, WSAGetLastError());
        return FailHost();
    }
    return 0;
}

uint32_t NetDll_getsockopt(uint32_t caller, uint32_t s, uint32_t level, uint32_t name, uint8_t* value, be<uint32_t>* length)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    int len = length ? int(uint32_t(*length)) : 0;
    std::vector<uint8_t> v(std::max(len, 4));
    if (getsockopt(h, int(level), int(name), (char*)v.data(), &len) != 0) return FailHost();
    if (len == 4) *(uint32_t*)v.data() = ByteSwap(*(uint32_t*)v.data());
    if (value) memcpy(value, v.data(), len);
    if (length) *length = uint32_t(len);
    return 0;
}

uint32_t NetDll_ioctlsocket(uint32_t caller, uint32_t s, uint32_t cmd, be<uint32_t>* arg)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    u_long v = arg ? uint32_t(*arg) : 0;
    if (ioctlsocket(h, long(cmd), &v) != 0) return FailHost();
    if (arg) *arg = uint32_t(v);
    return 0;
}

uint32_t NetDll_bind(uint32_t caller, uint32_t s, uint8_t* name, uint32_t length)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    auto a = ToHost(name, int(length));
    auto* in = (sockaddr_in*)&a;
    {
        std::lock_guard lock(g_sockMutex);
        Virtual* v;
        if (in->sin_family == AF_INET && VirtualFor(s, v))
        {
            if (BindVirtual(s, h, ntohl(in->sin_addr.s_addr), ntohs(in->sin_port))) return 0;
            LOGFN_WARNING("net: bind({:X}) to virtual port {} failed", s, ntohs(in->sin_port));
            return Fail(WSAEADDRINUSE);
        }
    }
    // the title binds its net driver to INADDR_ANY: keep it on loopback, so nothing is exposed to the network (and
    // Windows Firewall doesn't ask); system link goes through the virtual sockets above
    if (in->sin_family == AF_INET && in->sin_addr.s_addr == htonl(INADDR_ANY))
        in->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (bind(h, (sockaddr*)&a, int(length)) != 0)
    {
        LOGFN_WARNING("net: bind({:X}) failed: {}", s, WSAGetLastError());
        return FailHost();
    }
    return 0;
}

uint32_t NetDll_connect(uint32_t caller, uint32_t s, uint8_t* name, uint32_t length)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    auto a = ToHost(name, int(length));
    {
        std::lock_guard lock(g_sockMutex);
        Virtual* v;
        if (VirtualFor(s, v) && ((sockaddr*)&a)->sa_family == AF_INET)
        {
            v->hasPeer = true;
            v->peerIp = ntohl(((sockaddr_in*)&a)->sin_addr.s_addr);
            v->peerPort = ntohs(((sockaddr_in*)&a)->sin_port);
            return 0;
        }
    }
    return connect(h, (sockaddr*)&a, int(length)) == 0 ? 0 : FailHost();
}

uint32_t NetDll_listen(uint32_t caller, uint32_t s, uint32_t backlog)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    return listen(h, int(backlog)) == 0 ? 0 : FailHost();
}

uint32_t NetDll_accept(uint32_t caller, uint32_t s, uint8_t* addr, be<uint32_t>* addrLength)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    sockaddr_storage a{};
    int len = sizeof(a);
    SOCKET n = accept(h, (sockaddr*)&a, &len);
    if (n == INVALID_SOCKET) return FailHost();
    if (addr && addrLength) { int l = std::min<int>(len, int(uint32_t(*addrLength))); ToGuest(addr, a, l); *addrLength = uint32_t(len); }
    std::lock_guard lock(g_sockMutex);
    const uint32_t ns = g_nextSocket++;
    g_sockets[ns] = n;
    return ns;
}

uint32_t NetDll_getsockname(uint32_t caller, uint32_t s, uint8_t* name, be<uint32_t>* length)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    sockaddr_storage a{};
    int len = sizeof(a);
    if (getsockname(h, (sockaddr*)&a, &len) != 0) return FailHost();
    {
        std::lock_guard lock(g_sockMutex);
        Virtual* v;
        if (VirtualFor(s, v) && v->vport)
        {
            auto* in = (sockaddr_in*)&a;
            in->sin_addr.s_addr = htonl(v->boundIp);
            in->sin_port = htons(v->vport);
        }
    }
    if (name && length) ToGuest(name, a, std::min<int>(len, int(uint32_t(*length))));
    if (length) *length = uint32_t(len);
    return 0;
}

uint32_t NetDll_getpeername(uint32_t caller, uint32_t s, uint8_t* name, be<uint32_t>* length)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    sockaddr_storage a{};
    int len = sizeof(a);
    if (getpeername(h, (sockaddr*)&a, &len) != 0) return FailHost();
    if (name && length) ToGuest(name, a, std::min<int>(len, int(uint32_t(*length))));
    if (length) *length = uint32_t(len);
    return 0;
}

uint32_t NetDll_recv(uint32_t caller, uint32_t s, char* buffer, uint32_t length, uint32_t flags)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    int r = recv(h, buffer, int(length), int(flags));
    return r < 0 ? FailHost() : uint32_t(r);
}

uint32_t NetDll_recvfrom(uint32_t caller, uint32_t s, char* buffer, uint32_t length, uint32_t flags, uint8_t* from, be<uint32_t>* fromLength)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    sockaddr_storage a{};
    int len = sizeof(a);
    int r = recvfrom(h, buffer, int(length), int(flags), (sockaddr*)&a, &len);
    if (r < 0) return FailHost();
    TranslateFrom(a);
    if (from && fromLength) { ToGuest(from, a, std::min<int>(len, int(uint32_t(*fromLength)))); *fromLength = uint32_t(len); }
    return uint32_t(r);
}

uint32_t NetDll_send(uint32_t caller, uint32_t s, const char* buffer, uint32_t length, uint32_t flags)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    {
        uint32_t ip = 0; uint16_t port = 0; bool peer = false;
        {
            std::lock_guard lock(g_sockMutex);
            Virtual* v;
            if (VirtualFor(s, v) && v->hasPeer) { peer = true; ip = v->peerIp; port = v->peerPort; }
        }
        if (peer && SendVirtual(s, h, buffer, int(length), ip, port)) return length;
    }
    int r = send(h, buffer, int(length), int(flags));
    return r < 0 ? FailHost() : uint32_t(r);
}

uint32_t NetDll_sendto(uint32_t caller, uint32_t s, const char* buffer, uint32_t length, uint32_t flags, uint8_t* to, uint32_t toLength)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    auto a = ToHost(to, int(toLength));
    if (to && ((sockaddr*)&a)->sa_family == AF_INET &&
        SendVirtual(s, h, buffer, int(length), ntohl(((sockaddr_in*)&a)->sin_addr.s_addr), ntohs(((sockaddr_in*)&a)->sin_port)))
        return length;
    int r = sendto(h, buffer, int(length), int(flags), to ? (sockaddr*)&a : nullptr, int(toLength));
    return r < 0 ? FailHost() : uint32_t(r);
}

// ---- select: guest fd_set = BE count + 64 BE handles ----
namespace
{
    struct GuestFdSet { be<uint32_t> count; be<uint32_t> fds[64]; };
    bool ToHostSet(GuestFdSet* g, fd_set& h, std::map<SOCKET, uint32_t>& back)
    {
        FD_ZERO(&h);
        if (!g) return true;
        for (uint32_t i = 0; i < std::min<uint32_t>(g->count, 64); i++)
        {
            SOCKET hs = Host(g->fds[i]);
            if (hs == INVALID_SOCKET) return false;
            FD_SET(hs, &h);
            back[hs] = g->fds[i];
        }
        return true;
    }
    void ToGuestSet(GuestFdSet* g, const fd_set& h, const std::map<SOCKET, uint32_t>& back)
    {
        if (!g) return;
        uint32_t n = 0;
        for (u_int i = 0; i < h.fd_count && n < 64; i++)
        {
            auto it = back.find(h.fd_array[i]);
            if (it != back.end()) g->fds[n++] = it->second;
        }
        g->count = n;
    }
}

uint32_t NetDll_select(uint32_t caller, uint32_t nfds, GuestFdSet* readfds, GuestFdSet* writefds, GuestFdSet* exceptfds, be<uint32_t>* timeout)
{
    fd_set r, w, e;
    std::map<SOCKET, uint32_t> back;
    if (!ToHostSet(readfds, r, back) || !ToHostSet(writefds, w, back) || !ToHostSet(exceptfds, e, back))
        return NotSock();
    timeval tv{};
    if (timeout) { tv.tv_sec = long(uint32_t(timeout[0])); tv.tv_usec = long(uint32_t(timeout[1])); }
    if (r.fd_count + w.fd_count + e.fd_count == 0)
    {
        // Winsock rejects empty sets; the console just waits
        if (timeout) Sleep(DWORD(tv.tv_sec * 1000 + tv.tv_usec / 1000));
        return 0;
    }
    int n = select(0, readfds ? &r : nullptr, writefds ? &w : nullptr, exceptfds ? &e : nullptr, timeout ? &tv : nullptr);
    if (n < 0) return FailHost();
    ToGuestSet(readfds, r, back);
    ToGuestSet(writefds, w, back);
    ToGuestSet(exceptfds, e, back);
    return uint32_t(n);
}

uint32_t NetDll___WSAFDIsSet(uint32_t s, GuestFdSet* set)
{
    if (!set) return 0;
    for (uint32_t i = 0; i < std::min<uint32_t>(set->count, 64); i++)
        if (set->fds[i] == s) return 1;
    return 0;
}

// ---- WSA events (plain guest events) and overlapped I/O (performed synchronously) ----
uint32_t NetDll_WSACreateEvent()
{
    be<uint32_t> h = 0;
    NtCreateEvent(&h, nullptr, 0, 0);   // manual reset, not signaled
    return h;
}
uint32_t NetDll_WSACloseEvent(uint32_t event) { return 1; }   // TRUE (kept alive: handles are cheap)
uint32_t NetDll_WSASetEvent(uint32_t event) { return SignalGuestEvent(event) == 0; }
uint32_t NetDll_WSAResetEvent(uint32_t event) { return ResetGuestEvent(event) == 0; }

uint32_t NetDll_WSAWaitForMultipleEvents(uint32_t count, be<uint32_t>* events, uint32_t waitAll, uint32_t timeout, uint32_t alertable)
{
    std::vector<KernelObject*> objs(count);
    for (uint32_t i = 0; i < count; i++)
        objs[i] = IsKernelObject(events[i]) ? GetKernelObject(events[i]) : nullptr;
    uint32_t r = WaitForObjects(objs.data(), count, waitAll != 0, timeout);
    return r == STATUS_TIMEOUT ? WSA_WAIT_TIMEOUT : r;   // WSA_WAIT_EVENT_0 + i == STATUS_WAIT_0 + i
}

uint32_t NetDll_WSAEventSelect(uint32_t caller, uint32_t s, uint32_t event, uint32_t networkEvents)
{
    // Asynchronous readiness notification would need a host watcher thread; signal at once so pollers re-check.
    static int warned = 0;
    if (warned++ < 3) LOGFN_WARNING("net: WSAEventSelect({:X}, events {:X}) signals immediately", s, networkEvents);
    if (event) SignalGuestEvent(event);
    return 0;
}

namespace
{
    struct GuestWsaBuf { be<uint32_t> len; be<uint32_t> buf; };
    struct GuestWsaOverlapped { be<uint32_t> internal, internalHigh, offset, offsetHigh, event; };

    uint32_t Complete(GuestWsaOverlapped* ov, int result, uint32_t bytes)
    {
        if (!ov) return result < 0 ? kInvalid : 0;
        ov->internal = result < 0 ? uint32_t(WSAGetLastError()) : 0;
        ov->internalHigh = bytes;
        if (ov->event) SignalGuestEvent(ov->event);
        return result < 0 ? kInvalid : 0;
    }
}

uint32_t NetDll_WSARecvFrom(uint32_t caller, uint32_t s, GuestWsaBuf* buffers, uint32_t count, be<uint32_t>* received,
    be<uint32_t>* flags, uint8_t* from, be<uint32_t>* fromLength, GuestWsaOverlapped* overlapped, uint32_t completion)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    if (!count) return Fail(WSAEINVAL);
    sockaddr_storage a{};
    int len = sizeof(a);
    int r = recvfrom(h, (char*)g_memory.Translate(buffers[0].buf), int(uint32_t(buffers[0].len)), flags ? int(uint32_t(*flags)) : 0, (sockaddr*)&a, &len);
    if (r >= 0)
    {
        TranslateFrom(a);
        if (received) *received = uint32_t(r);
        if (from && fromLength) { ToGuest(from, a, std::min<int>(len, int(uint32_t(*fromLength)))); *fromLength = uint32_t(len); }
    }
    else if (WSAGetLastError() != WSAEWOULDBLOCK || !overlapped)
        return FailHost();
    return Complete(overlapped, r, r < 0 ? 0 : uint32_t(r)) == kInvalid ? FailHost() : 0;
}

uint32_t NetDll_WSARecv(uint32_t caller, uint32_t s, GuestWsaBuf* buffers, uint32_t count, be<uint32_t>* received,
    be<uint32_t>* flags, GuestWsaOverlapped* overlapped, uint32_t completion)
{
    return NetDll_WSARecvFrom(caller, s, buffers, count, received, flags, nullptr, nullptr, overlapped, completion);
}

uint32_t NetDll_WSASendTo(uint32_t caller, uint32_t s, GuestWsaBuf* buffers, uint32_t count, be<uint32_t>* sent,
    uint32_t flags, uint8_t* to, uint32_t toLength, GuestWsaOverlapped* overlapped, uint32_t completion)
{
    SOCKET h = Host(s);
    if (h == INVALID_SOCKET) return NotSock();
    // the buffers make one datagram (for a datagram socket; a stream gets the same bytes in order)
    std::vector<char> packet;
    for (uint32_t i = 0; i < count; i++)
    {
        const char* b = (const char*)g_memory.Translate(buffers[i].buf);
        packet.insert(packet.end(), b, b + uint32_t(buffers[i].len));
    }
    auto a = ToHost(to, int(toLength));
    const uint32_t total = uint32_t(packet.size());
    bool done = false;
    if (to && ((sockaddr*)&a)->sa_family == AF_INET)
        done = SendVirtual(s, h, packet.data(), int(total), ntohl(((sockaddr_in*)&a)->sin_addr.s_addr), ntohs(((sockaddr_in*)&a)->sin_port));
    if (!done)
    {
        int r = to ? sendto(h, packet.data(), int(total), int(flags), (sockaddr*)&a, int(toLength))
                   : send(h, packet.data(), int(total), int(flags));
        if (r < 0) { Complete(overlapped, r, 0); return FailHost(); }
    }
    if (sent) *sent = total;
    Complete(overlapped, 0, total);
    return 0;
}

uint32_t NetDll_WSASend(uint32_t caller, uint32_t s, GuestWsaBuf* buffers, uint32_t count, be<uint32_t>* sent,
    uint32_t flags, GuestWsaOverlapped* overlapped, uint32_t completion)
{
    return NetDll_WSASendTo(caller, s, buffers, count, sent, flags, nullptr, 0, overlapped, completion);
}

uint32_t NetDll_WSAGetOverlappedResult(uint32_t caller, uint32_t s, GuestWsaOverlapped* overlapped, be<uint32_t>* bytes,
    uint32_t wait, be<uint32_t>* flags)
{
    if (!overlapped) return 0;
    if (bytes) *bytes = overlapped->internalHigh;
    if (flags) *flags = 0;
    if (overlapped->internal) { GuestThread::SetLastError(overlapped->internal); return 0; }
    return 1;   // TRUE
}

uint32_t NetDll_WSACancelOverlappedIO(uint32_t caller, uint32_t s, uint32_t overlapped) { return 0; }

// ---- XNet: a local, offline console ----
struct XNADDR { be<uint32_t> ina, inaOnline; be<uint16_t> wPortOnline; uint8_t abEnet[6]; uint8_t abOnline[20]; };

uint32_t NetDll_XNetGetTitleXnAddr(uint32_t caller, XNADDR* addr)
{
    memset(addr, 0, sizeof(*addr));
    if (vnet::Enabled())
    {
        // system link: the virtual address the session hub gave this player (pending until it answered)
        vnet::Start();
        if (!vnet::WaitReady(3000)) return 0;     // XNET_GET_XNADDR_PENDING: the title asks again
        const uint32_t vip = vnet::OwnVip();
        addr->ina = vip;
        addr->inaOnline = vip;
        const uint8_t mac[6] = { 0x00, 0x22, 0x48, uint8_t(vip >> 16), uint8_t(vip >> 8), uint8_t(vip) };
        memcpy(addr->abEnet, mac, 6);
        memcpy(addr->abOnline, mac, 6);
        return 0x2 | 0x4;                         // XNET_GET_XNADDR_ETHERNET | STATIC
    }
    addr->ina = 0x7F000001;                 // loopback (Xenia)
    memset(addr->abEnet, 0xCC, 6);          // non-zero MAC: some titles seed random ids from it
    return 0x4;                             // XNET_GET_XNADDR_STATIC
}
// cable connected (XNET_ETHERNET_LINK_ACTIVE | 100MBPS | FULL_DUPLEX): Gears of War 3 shows "Connection Lost" over
// its title screen for a down link; there is still no Xbox Live (logon state stays disconnected)
uint32_t NetDll_XNetGetEthernetLinkStatus(uint32_t caller) { return 0x01 | 0x02 | 0x08; }
uint32_t NetDll_XNetXnAddrToInAddr(uint32_t caller, XNADDR* addr, uint32_t xnkid, be<uint32_t>* ina) { *ina = addr->ina; return 0; }
// an address back to the XNADDR XNetGetTitleXnAddr would give that player (system link: the MAC follows the address)
uint32_t NetDll_XNetInAddrToXnAddr(uint32_t caller, uint32_t ina, XNADDR* addr, uint8_t* xnkid)
{
    if (addr)
    {
        memset(addr, 0, sizeof(*addr));
        addr->ina = ina;
        addr->inaOnline = ina;
        const uint32_t ip = ina;   // the guest loads IN_ADDR (network-order bytes) big-endian: the plain numeric address
        const uint8_t mac[6] = { 0x00, 0x22, 0x48, uint8_t(ip >> 16), uint8_t(ip >> 8), uint8_t(ip) };
        memcpy(addr->abEnet, mac, 6);
    }
    if (xnkid) memset(xnkid, 0, 8);
    return 0;
}
uint32_t NetDll_XNetServerToInAddr(uint32_t caller, uint32_t ina, uint32_t serviceId, be<uint32_t>* out) { *out = ina; return 0; }
uint32_t NetDll_XNetUnregisterInAddr(uint32_t caller, uint32_t ina) { return 0; }
uint32_t NetDll_XNetRegisterKey(uint32_t caller, uint32_t xnkid, uint32_t xnkey) { return 0; }
// the title names the port its system link traffic uses (Gears 3: 14000); the virtual LAN carries every port alike
uint32_t NetDll_XNetSetSystemLinkPort(uint32_t caller, uint32_t port) { LOGFN("net: system link port {}", port & 0xFFFF); return 0; }
uint32_t NetDll_XNetUnregisterKey(uint32_t caller, uint32_t xnkid) { return 0; }
uint32_t NetDll_XNetRandom(uint32_t caller, uint8_t* buffer, uint32_t length)
{
    for (uint32_t i = 0; i < length; i++) buffer[i] = uint8_t(rand());
    return 0;
}
uint32_t NetDll_XNetInAddrToString(uint32_t caller, uint32_t ina, char* out, uint32_t size)
{
    if (out && size) snprintf(out, size, "%u.%u.%u.%u", ina >> 24, (ina >> 16) & 0xFF, (ina >> 8) & 0xFF, ina & 0xFF);
    return 0;
}

// DNS and QoS need Xbox Live: answer "not available" in the result structures
uint32_t NetDll_XNetDnsLookup(uint32_t caller, const char* host, uint32_t event, be<uint32_t>* dns)
{
    if (dns)
    {
        void* p = g_userHeap.Alloc(0x40);
        memset(p, 0, 0x40);
        *(be<uint32_t>*)p = 1;   // XNDNS.iStatus: non-zero = failed
        *dns = g_memory.MapVirtual(p);
    }
    if (event) SignalGuestEvent(event);
    return 0;
}
uint32_t NetDll_XNetDnsRelease(uint32_t caller, uint32_t dns) { if (dns) g_userHeap.Free(g_memory.Translate(dns)); return 0; }
uint32_t NetDll_XNetQosListen(uint32_t caller, uint32_t xnkid, uint32_t data, uint32_t length, uint32_t bitsPerSec, uint32_t flags) { return 0; }
uint32_t NetDll_XNetQosLookup() { return WSAENETDOWN; }
uint32_t NetDll_XNetQosRelease(uint32_t caller, uint32_t qos) { return 0; }
uint32_t XNetLogonGetTitleID() { return 0x4D5308AB; }

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__NetDll_WSAStartup, x360k::NetDll_WSAStartup);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSACleanup, x360k::NetDll_WSACleanup);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetStartup, x360k::NetDll_XNetStartup);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetCleanup, x360k::NetDll_XNetCleanup);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSAGetLastError, x360k::NetDll_WSAGetLastError);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSASetLastError, x360k::NetDll_WSASetLastError);
GUEST_FUNCTION_HOOK(__imp__NetDll_socket, x360k::NetDll_socket);
GUEST_FUNCTION_HOOK(__imp__NetDll_closesocket, x360k::NetDll_closesocket);
GUEST_FUNCTION_HOOK(__imp__NetDll_shutdown, x360k::NetDll_shutdown);
GUEST_FUNCTION_HOOK(__imp__NetDll_setsockopt, x360k::NetDll_setsockopt);
GUEST_FUNCTION_HOOK(__imp__NetDll_getsockopt, x360k::NetDll_getsockopt);
GUEST_FUNCTION_HOOK(__imp__NetDll_ioctlsocket, x360k::NetDll_ioctlsocket);
GUEST_FUNCTION_HOOK(__imp__NetDll_bind, x360k::NetDll_bind);
GUEST_FUNCTION_HOOK(__imp__NetDll_connect, x360k::NetDll_connect);
GUEST_FUNCTION_HOOK(__imp__NetDll_listen, x360k::NetDll_listen);
GUEST_FUNCTION_HOOK(__imp__NetDll_accept, x360k::NetDll_accept);
GUEST_FUNCTION_HOOK(__imp__NetDll_getsockname, x360k::NetDll_getsockname);
GUEST_FUNCTION_HOOK(__imp__NetDll_getpeername, x360k::NetDll_getpeername);
GUEST_FUNCTION_HOOK(__imp__NetDll_recv, x360k::NetDll_recv);
GUEST_FUNCTION_HOOK(__imp__NetDll_recvfrom, x360k::NetDll_recvfrom);
GUEST_FUNCTION_HOOK(__imp__NetDll_send, x360k::NetDll_send);
GUEST_FUNCTION_HOOK(__imp__NetDll_sendto, x360k::NetDll_sendto);
GUEST_FUNCTION_HOOK(__imp__NetDll_select, x360k::NetDll_select);
GUEST_FUNCTION_HOOK(__imp__NetDll___WSAFDIsSet, x360k::NetDll___WSAFDIsSet);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSACreateEvent, x360k::NetDll_WSACreateEvent);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSACloseEvent, x360k::NetDll_WSACloseEvent);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSASetEvent, x360k::NetDll_WSASetEvent);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSAResetEvent, x360k::NetDll_WSAResetEvent);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSAWaitForMultipleEvents, x360k::NetDll_WSAWaitForMultipleEvents);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSAEventSelect, x360k::NetDll_WSAEventSelect);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSARecvFrom, x360k::NetDll_WSARecvFrom);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSARecv, x360k::NetDll_WSARecv);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSASendTo, x360k::NetDll_WSASendTo);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSASend, x360k::NetDll_WSASend);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSAGetOverlappedResult, x360k::NetDll_WSAGetOverlappedResult);
GUEST_FUNCTION_HOOK(__imp__NetDll_WSACancelOverlappedIO, x360k::NetDll_WSACancelOverlappedIO);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetGetTitleXnAddr, x360k::NetDll_XNetGetTitleXnAddr);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetGetEthernetLinkStatus, x360k::NetDll_XNetGetEthernetLinkStatus);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetXnAddrToInAddr, x360k::NetDll_XNetXnAddrToInAddr);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetInAddrToXnAddr, x360k::NetDll_XNetInAddrToXnAddr);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetServerToInAddr, x360k::NetDll_XNetServerToInAddr);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetUnregisterInAddr, x360k::NetDll_XNetUnregisterInAddr);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetRegisterKey, x360k::NetDll_XNetRegisterKey);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetSetSystemLinkPort, x360k::NetDll_XNetSetSystemLinkPort);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetUnregisterKey, x360k::NetDll_XNetUnregisterKey);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetRandom, x360k::NetDll_XNetRandom);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetInAddrToString, x360k::NetDll_XNetInAddrToString);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetDnsLookup, x360k::NetDll_XNetDnsLookup);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetDnsRelease, x360k::NetDll_XNetDnsRelease);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetQosListen, x360k::NetDll_XNetQosListen);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetQosLookup, x360k::NetDll_XNetQosLookup);
GUEST_FUNCTION_HOOK(__imp__NetDll_XNetQosRelease, x360k::NetDll_XNetQosRelease);
GUEST_FUNCTION_HOOK(__imp__XNetLogonGetTitleID, x360k::XNetLogonGetTitleID);
