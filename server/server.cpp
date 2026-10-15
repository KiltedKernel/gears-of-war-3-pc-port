// gow3_server: a Gears of War 3 PC port multiplayer server. Everyone who connects is on one big system link network
// (its lobby; or a private room), so the game's System Link browser lists everyone's parties, and players see who
// joins and leaves. Run it on any PC or server with a reachable UDP port; players enter its address (or the code it
// prints) in the launcher (Multiplayer > On a server). It forwards datagrams between the players of a room and keeps
// no data: no accounts, no logs on disk.
//   gow3_server [--port 3074] [--name NAME] [--motd TEXT] [--password TEXT] [--max 16] [--bind ADDRESS] [--no-upnp]
#include <winsock2.h>
#include <windows.h>
#include "../runtime/net/hub.h"
#include "../runtime/net/code.h"
#include "../runtime/net/upnp.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <thread>

namespace
{
    uint16_t g_upnpPort = 0;

    void Log(const char* line)
    {
        const std::time_t t = std::time(nullptr);
        char stamp[16];
        std::strftime(stamp, sizeof(stamp), "%H:%M:%S", std::localtime(&t));
        const char* text = strncmp(line, "hub: ", 5) == 0 ? line + 5 : line;
        printf("[%s] %s\n", stamp, text);
        fflush(stdout);
    }

    BOOL WINAPI OnCtrl(DWORD)
    {
        if (g_upnpPort) upnp::RemoveUdp(g_upnpPort);
        vnet::StopHub();
        return FALSE;   // then the default handler ends the process
    }
}

int main(int argc, char** argv)
{
    vnet::HubOptions o;
    o.server = true;
    o.name = "Gears 3 server";
    o.lanDiscovery = false;     // reached by its address, not found on a LAN (that is for players' own games)
    bool useUpnp = true;
    for (int i = 1; i < argc; i++)
    {
        const std::string a = argv[i];
        if (a == "--port" && i + 1 < argc) o.port = uint16_t(atoi(argv[++i]));
        else if (a == "--max" && i + 1 < argc) o.maxPlayers = atoi(argv[++i]);
        else if (a == "--name" && i + 1 < argc) o.name = argv[++i];
        else if (a == "--motd" && i + 1 < argc) o.motd = argv[++i];
        else if (a == "--password" && i + 1 < argc) o.password = argv[++i];
        else if (a == "--bind" && i + 1 < argc) o.bindAddress = argv[++i];
        else if (a == "--no-upnp") useUpnp = false;
        else
        {
            printf("usage: gow3_server [--port 3074] [--name NAME] [--motd TEXT] [--password TEXT] [--max 16]\n"
                   "                   [--bind ADDRESS] [--no-upnp]\n");
            return a == "--help" || a == "-h" || a == "/?" ? 0 : 2;
        }
    }
    if (o.name.size() > 31) o.name.resize(31);
    SetConsoleTitleA(("Gears of War 3 server - " + o.name).c_str());
    vnet::SetHubLog(Log);
    if (!vnet::StartHub(o))
    {
        printf("UDP port %u is in use: is another server (or a game hosting) running? Try --port %u.\n", o.port, o.port + 1);
        return 1;
    }
    SetConsoleCtrlHandler(OnCtrl, TRUE);
    printf("\n  %s is running on UDP port %u%s.\n", o.name.c_str(), o.port, o.password.empty() ? "" : " (with a password)");
    printf("  Players on this network: enter this PC's address in the launcher (Multiplayer > On a server).\n");
    if (useUpnp && o.bindAddress.empty())
    {
        printf("  Asking the router to open the port (UPnP)...\n");
        uint32_t publicIp = 0;
        if (upnp::ForwardUdp(o.port, publicIp))
        {
            g_upnpPort = o.port;
            if (publicIp)
                printf("  Over the internet: players enter the server code %s (or %u.%u.%u.%u:%u).\n",
                       code::Encode(publicIp, o.port).c_str(), publicIp >> 24, (publicIp >> 16) & 255, (publicIp >> 8) & 255,
                       publicIp & 255, o.port);
        }
        else
            printf("  The router did not open the port (UPnP is off, or not supported). For players over the internet,\n"
                   "  forward UDP port %u to this PC on the router, or run the server on a PC or host that has a public address.\n", o.port);
    }
    printf("  Windows Firewall: allow gow3_server.exe if it asks. Ctrl+C or closing this window stops the server.\n\n");
    fflush(stdout);
    for (;;) std::this_thread::sleep_for(std::chrono::hours(1));
}
