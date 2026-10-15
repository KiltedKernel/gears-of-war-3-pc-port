// Player profile: profile.ini in the user folder (GetUserPath: %LOCALAPPDATA%\GoW3Recomp, or GOW3_DATA_DIR for tests).
//   [profile]
//   gamertag = Player         ; what other players see (1-15 characters: letters, digits, spaces)
//   xuid = E000123456789ABC   ; made once at random, then kept (system link tells players apart by it)
// A missing file or key is filled in and written back.
#include <stdafx.h>
#include "profile.h"
#include <os/logger.h>
#include <fstream>
#include <mutex>
#include <random>

std::filesystem::path GetUserPath();       // main.cpp
std::filesystem::path GetBaseUserPath();
int GetInstanceNumber();

namespace profile
{
namespace
{
    struct State { uint64_t xuid = 0; std::string gamertag; };

    std::string Clean(std::string s)
    {
        std::string out;
        for (char c : s)
            if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == ' ') out += c;
        while (!out.empty() && out.front() == ' ') out.erase(out.begin());
        while (!out.empty() && out.back() == ' ') out.pop_back();
        if (out.size() > 15) out.resize(15);
        return out;
    }

    State Read(const std::filesystem::path& path)
    {
        State s;
        std::ifstream in(path);
        std::string line;
        while (std::getline(in, line))
        {
            if (auto c = line.find(';'); c != std::string::npos) line.resize(c);
            const auto eq = line.find('=');
            if (eq == std::string::npos) continue;
            auto trim = [](std::string t) {
                const auto a = t.find_first_not_of(" \t\r"), b = t.find_last_not_of(" \t\r");
                return a == std::string::npos ? std::string() : t.substr(a, b - a + 1);
            };
            const std::string k = trim(line.substr(0, eq)), v = trim(line.substr(eq + 1));
            if (k == "gamertag") s.gamertag = Clean(v);
            else if (k == "xuid") s.xuid = std::strtoull(v.c_str(), nullptr, 16);
        }
        return s;
    }

    State Load()
    {
        const auto path = GetUserPath() / "profile.ini";
        State s = Read(path);
        bool write = false;
        if (s.gamertag.empty() && GetInstanceNumber() > 1)
        {
            // a second copy of the game on this PC: the first one's gamertag and the copy's number ("Grug 2"), and a
            // player id of its own (made below)
            std::string gamertag = Read(GetBaseUserPath() / "profile.ini").gamertag;
            if (gamertag.empty()) gamertag = "Player";
            const std::string suffix = " " + std::to_string(GetInstanceNumber());
            s.gamertag = gamertag.substr(0, 15 - suffix.size()) + suffix;
            write = true;
        }
        if (s.gamertag.empty()) { s.gamertag = "Player"; write = true; }
        if (s.xuid == 0)
        {
            std::random_device rd;
            s.xuid = 0xE000000000000000ull | ((uint64_t(rd()) << 32 | rd()) & 0x0000FFFFFFFFFFFFull);
            write = true;
        }
        if (write)
        {
            std::error_code ec;
            std::filesystem::create_directories(path.parent_path(), ec);
            std::ofstream out(path);
            out << "; Gears of War 3 PC port: your profile (the launcher's Multiplayer page edits it)\n"
                << "[profile]\n"
                << "; what other players see: 1-15 letters, digits or spaces\n"
                << "gamertag = " << s.gamertag << "\n"
                << "; your player id, made once at random (keep it: system link tells players apart by it)\n"
                << fmt::format("xuid = {:016X}\n", s.xuid);
        }
        LOGFN("profile: {} ({:016X})", s.gamertag, s.xuid);
        return s;
    }

    const State& Get()
    {
        static State s = Load();
        return s;
    }
}

uint64_t Xuid() { return Get().xuid; }
const std::string& Gamertag() { return Get().gamertag; }
uint64_t SaveOwnerFolder() { return 0xB13EBABEBABEBABEull; }
}
