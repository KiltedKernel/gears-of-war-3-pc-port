// UPnP port forwarding with Windows' own NAT traversal API (natupnp.h, IUPnPNAT): the host asks the router to forward
// its UDP port to this PC, and reads the router's public address for the join code. Routers without UPnP (or with it
// turned off) answer nothing: the host then plays on the LAN, or players use a relay. Runs on its own thread (COM).
// No game code: the launcher builds it too (upnp.h).
#include <winsock2.h>
#include <ws2tcpip.h>
#include <natupnp.h>
#include "upnp.h"
#include <cstdarg>
#include <cstdio>
#include <string>
#include <thread>
#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "oleaut32.lib")

namespace upnp
{
namespace
{
    void (*g_log)(const char*) = nullptr;
    void Log(const char* fmt, ...)
    {
        if (!g_log) return;
        char line[256];
        va_list a; va_start(a, fmt); vsnprintf(line, sizeof(line), fmt, a); va_end(a);
        g_log(line);
    }

    // this PC's address on the network the default route uses (what the router forwards to)
    std::wstring LocalAddress()
    {
        SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
        sockaddr_in to{}; to.sin_family = AF_INET; to.sin_port = htons(9); to.sin_addr.s_addr = htonl(0x08080808);   // never sent: a connect only picks the route
        connect(s, (sockaddr*)&to, sizeof(to));
        sockaddr_in me{}; int len = sizeof(me);
        getsockname(s, (sockaddr*)&me, &len);
        closesocket(s);
        wchar_t buf[32];
        swprintf(buf, 32, L"%u.%u.%u.%u", me.sin_addr.S_un.S_un_b.s_b1, me.sin_addr.S_un.S_un_b.s_b2, me.sin_addr.S_un.S_un_b.s_b3, me.sin_addr.S_un.S_un_b.s_b4);
        return buf;
    }

    template <class F> void OnComThread(F f)
    {
        std::thread t([&] {
            if (FAILED(CoInitializeEx(nullptr, COINIT_MULTITHREADED))) return;
            f();
            CoUninitialize();
        });
        t.join();
    }
}

bool ForwardUdp(uint16_t port, uint32_t& publicIp)
{
    bool ok = false;
    publicIp = 0;
    OnComThread([&] {
        IUPnPNAT* nat = nullptr;
        if (FAILED(CoCreateInstance(__uuidof(UPnPNAT), nullptr, CLSCTX_ALL, __uuidof(IUPnPNAT), (void**)&nat)) || !nat) return;
        IStaticPortMappingCollection* maps = nullptr;
        if (SUCCEEDED(nat->get_StaticPortMappingCollection(&maps)) && maps)
        {
            BSTR proto = SysAllocString(L"UDP");
            BSTR local = SysAllocString(LocalAddress().c_str());
            BSTR desc = SysAllocString(L"Gears of War 3 PC port");
            IStaticPortMapping* map = nullptr;
            if (SUCCEEDED(maps->Add(port, proto, port, local, VARIANT_TRUE, desc, &map)) && map)
            {
                BSTR ext = nullptr;
                if (SUCCEEDED(map->get_ExternalIPAddress(&ext)) && ext)
                {
                    char a[64]; WideCharToMultiByte(CP_ACP, 0, ext, -1, a, sizeof(a), nullptr, nullptr);
                    in_addr in{};
                    publicIp = inet_pton(AF_INET, a, &in) == 1 ? ntohl(in.s_addr) : 0;
                    SysFreeString(ext);
                }
                ok = true;
                map->Release();
            }
            SysFreeString(proto); SysFreeString(local); SysFreeString(desc);
            maps->Release();
        }
        else Log("UPnP: no router answered (UPnP off or not supported)");
        nat->Release();
    });
    if (ok) Log("UPnP: router forwards UDP %u to this PC (public address %u.%u.%u.%u)", port, publicIp >> 24, (publicIp >> 16) & 255, (publicIp >> 8) & 255, publicIp & 255);
    else Log("UPnP: UDP %u not forwarded", port);
    return ok;
}

void SetLog(void (*log)(const char*)) { g_log = log; }

void RemoveUdp(uint16_t port)
{
    OnComThread([&] {
        IUPnPNAT* nat = nullptr;
        if (FAILED(CoCreateInstance(__uuidof(UPnPNAT), nullptr, CLSCTX_ALL, __uuidof(IUPnPNAT), (void**)&nat)) || !nat) return;
        IStaticPortMappingCollection* maps = nullptr;
        if (SUCCEEDED(nat->get_StaticPortMappingCollection(&maps)) && maps)
        {
            BSTR proto = SysAllocString(L"UDP");
            maps->Remove(port, proto);
            SysFreeString(proto);
            maps->Release();
        }
        nat->Release();
    });
}
}
