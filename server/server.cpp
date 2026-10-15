// gow3_server: a Gears of War 3 PC port multiplayer server. Everyone who connects is on one big system link network
// (its lobby; or a private room), so the game's System Link browser lists everyone's parties, and players see who
// joins and leaves. Run it on any PC or server with a reachable UDP port; players enter its address (or the code it
// prints) in the launcher (Multiplayer > On a server). It forwards datagrams between the players of a room and keeps
// no data: no accounts, no logs on disk.
//   gow3_server [--config server.ini] [--port 3074] [--name NAME] [--motd TEXT] [--password TEXT] [--max 16]
//               [--mods FOLDER] [--bind ADDRESS] [--no-upnp]
// server.ini (next to the server, or --config): [server] port, name, motd, password, max, mods, upnp; the command line
// overrides it. mods: a mods folder holding the mod folders players install (the server reads each mod.ini and the
// names and sizes of the mod's files): the server's sessions then need exactly its "everyone" mods, and a player with
// other mods is turned away with the list. Without it, each room plays with the mods of its first player.
#include <winsock2.h>
#include <windows.h>
#include "../runtime/net/hub.h"
#include "../runtime/net/code.h"
#include "../runtime/net/upnp.h"
#include "../runtime/kernel/mod_manifest.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fstream>
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

    // the key = value lines of server.ini; false: no such file
    bool ReadConfig(const std::filesystem::path& path, vnet::HubOptions& o, std::string& mods, bool& useUpnp)
    {
        std::ifstream in(path);
        if (!in) return false;
        std::string line;
        while (std::getline(in, line))
        {
            line = modinfo::Trim(line);
            if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
            const auto eq = line.find('=');
            if (eq == std::string::npos) continue;
            const std::string key = modinfo::Lower(modinfo::Trim(line.substr(0, eq))), value = modinfo::Trim(line.substr(eq + 1));
            const std::string low = modinfo::Lower(value);
            if (key == "port") o.port = uint16_t(atoi(value.c_str()));
            else if (key == "name") o.name = value;
            else if (key == "motd") o.motd = value;
            else if (key == "password") o.password = value;
            else if (key == "max") o.maxPlayers = atoi(value.c_str());
            else if (key == "mods") mods = value;
            else if (key == "upnp") useUpnp = !(low == "false" || low == "0" || low == "no");
        }
        return true;
    }

    std::filesystem::path ExeDir()
    {
        wchar_t exe[MAX_PATH]{};
        GetModuleFileNameW(nullptr, exe, MAX_PATH);
        return std::filesystem::path(exe).parent_path();
    }
}

int main(int argc, char** argv)
{
    vnet::HubOptions o;
    o.server = true;
    o.name = "Gears 3 server";
    o.lanDiscovery = false;     // reached by its address, not found on a LAN (that is for players' own games)
    bool useUpnp = true;
    std::string mods;
    std::filesystem::path config = ExeDir() / "server.ini";
    for (int i = 1; i + 1 < argc; i++)
        if (std::string(argv[i]) == "--config") config = argv[i + 1];
    const bool haveConfig = ReadConfig(config, o, mods, useUpnp);
    for (int i = 1; i < argc; i++)
    {
        const std::string a = argv[i];
        if (a == "--config" && i + 1 < argc) i++;
        else if (a == "--mods" && i + 1 < argc) mods = argv[++i];
        else if (a == "--port" && i + 1 < argc) o.port = uint16_t(atoi(argv[++i]));
        else if (a == "--max" && i + 1 < argc) o.maxPlayers = atoi(argv[++i]);
        else if (a == "--name" && i + 1 < argc) o.name = argv[++i];
        else if (a == "--motd" && i + 1 < argc) o.motd = argv[++i];
        else if (a == "--password" && i + 1 < argc) o.password = argv[++i];
        else if (a == "--bind" && i + 1 < argc) o.bindAddress = argv[++i];
        else if (a == "--no-upnp") useUpnp = false;
        else
        {
            printf("usage: gow3_server [--config server.ini] [--port 3074] [--name NAME] [--motd TEXT] [--password TEXT]\n"
                   "                   [--max 16] [--mods FOLDER] [--bind ADDRESS] [--no-upnp]\n");
            return a == "--help" || a == "-h" || a == "/?" ? 0 : 2;
        }
    }
    if (o.name.size() > 31) o.name.resize(31);
    if (haveConfig) printf("  Settings from %s\n", config.string().c_str());
    if (!mods.empty())
    {
        // a relative folder is relative to server.ini's (from the command line: to the current folder)
        std::filesystem::path dir = mods;
        if (dir.is_relative() && haveConfig) dir = config.parent_path() / dir;
        const auto list = modinfo::Scan(dir);
        if (list.empty())
        {
            printf("No mods in %s: --mods (or mods = in server.ini) names a mods folder, which holds the mods' folders.\n",
                   dir.string().c_str());
            return 2;
        }
        const auto net = modinfo::ForMultiplayer(list);
        o.fixedMods = true;
        o.mods = net.fingerprint;
        o.modList = net.summary;
        for (const auto& m : list)
            printf("  mod: %s %s (%s)%s\n", m.name.c_str(), m.version.c_str(), modinfo::SideName(m.side),
                   !m.enabled ? " - off" : !m.problem.empty() ? (" - off: " + m.problem).c_str() : "");
    }
    SetConsoleTitleA(("Gears of War 3 server - " + o.name).c_str());
    vnet::SetHubLog(Log);
    if (!vnet::StartHub(o))
    {
        printf("UDP port %u is in use: is another server (or a game hosting) running? Try --port %u.\n", o.port, o.port + 1);
        return 1;
    }
    SetConsoleCtrlHandler(OnCtrl, TRUE);
    printf("\n  %s is running on UDP port %u%s.\n", o.name.c_str(), o.port, o.password.empty() ? "" : " (with a password)");
    if (o.fixedMods)
    {
        if (o.mods) printf("  Players need these mods: %s (fingerprint %016llx).\n", o.modList.c_str(), (unsigned long long)o.mods);
        else printf("  Players need no mods (none of its mods is for everyone).\n");
    }
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
