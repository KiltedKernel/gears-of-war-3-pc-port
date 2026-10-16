#pragma once
// A mod's manifest and files, as the game (kernel/mods.cpp), the launcher, the server (server/server.cpp) and
// tools/modtool.py all read them. Header-only, standard C++ only. docs/MODDING.md is the creators' description.
//
//   [mod]
//   name, version, author, description     free text
//   priority = 0                           load order: lower first; equal: by name; the last one wins a file
//   enabled = true
//   side = everyone | host | client        who needs the mod in multiplayer (below; default everyone)
//   requires = Other Mod, Another          mods that must be enabled too (by name or folder), else this one is off
//   game = gears3                          the game it is made for (gears3 if not given); others are turned off
//   content = MPCH_COGMarcusG2MP_SF, ...   the game's own content packages the mod's levels use that the campaign
//                                          does not load (multiplayer characters...): loaded once the engine is up
//                                          and kept (runtime/ue3/content_packages.cpp)
//
// side: "client" changes only what this player sees or hears (menus, HUD, text): never compared. "host" changes the
// rules the host's game applies (game modes' settings): it acts in matches you host and is not compared. "everyone"
// (packages, maps, weapon stats, anything both ends simulate): every player of a multiplayer session must have the
// same "everyone" mods; their fingerprint is compared when a player joins (runtime/net/hub.cpp).
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <system_error>
#include <vector>

namespace modinfo
{
    enum class Side { Everyone, Host, Client };

    inline const char* SideName(Side s) { return s == Side::Client ? "client" : s == Side::Host ? "host" : "everyone"; }

    struct Manifest
    {
        std::string folder;                          // the mod's folder name
        std::string name, version, author, description, game = "gears3";
        int priority = 0;
        bool enabled = true;
        Side side = Side::Everyone;
        std::vector<std::string> needs;          // requires = ...
        std::vector<std::string> content;        // content = ... (package names)
        std::string problem;                         // why a mod that is enabled in its mod.ini is not loaded
        std::filesystem::path dir;
        bool loose = false;                          // files placed directly in mods\ (no mod.ini)
        bool Active() const { return enabled && problem.empty(); }
    };

    inline std::string Lower(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(tolower(c)); });
        std::replace(s.begin(), s.end(), '/', '\\');
        return s;
    }

    inline std::string Trim(const std::string& s)
    {
        const auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

    inline Manifest Read(const std::filesystem::path& dir)
    {
        Manifest m;
        m.dir = dir;
        m.folder = m.name = dir.filename().string();
        std::ifstream in(dir / "mod.ini");
        std::string line;
        bool first = true;
        while (std::getline(in, line))
        {
            if (first && line.size() >= 3 && (unsigned char)line[0] == 0xEF && (unsigned char)line[1] == 0xBB) line.erase(0, 3);   // UTF-8 BOM
            first = false;
            line = Trim(line);
            if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
            const auto eq = line.find('=');
            if (eq == std::string::npos) continue;
            const std::string key = Lower(Trim(line.substr(0, eq))), value = Trim(line.substr(eq + 1)), low = Lower(value);
            if (key == "name") m.name = value;
            else if (key == "version") m.version = value;
            else if (key == "author") m.author = value;
            else if (key == "description") m.description = value;
            else if (key == "priority") m.priority = atoi(value.c_str());
            else if (key == "enabled") m.enabled = !(low == "false" || low == "0" || low == "no");
            else if (key == "side") m.side = low == "client" ? Side::Client : low == "host" || low == "server" ? Side::Host : Side::Everyone;
            else if (key == "game") m.game = low.empty() ? "gears3" : low;
            else if (key == "requires" || key == "content")
            {
                auto& list = key == "content" ? m.content : m.needs;
                size_t a = 0;
                while (a <= value.size())
                {
                    const size_t b = (std::min)(value.find(',', a), value.size());
                    const std::string r = Trim(value.substr(a, b - a));
                    if (!r.empty()) list.push_back(r);
                    a = b + 1;
                }
            }
        }
        return m;
    }

    // every mod under a mods folder, in load order (priority, then name; the loose files last among equals as
    // "(loose files)" sorts by that name), with each one's problem set: made for another game, or a required mod
    // that is missing or off
    inline std::vector<Manifest> Scan(const std::filesystem::path& root)
    {
        std::vector<Manifest> mods;
        std::error_code ec;
        if (!std::filesystem::is_directory(root, ec)) return mods;
        bool loose = false;
        for (const auto& e : std::filesystem::directory_iterator(root, ec))
        {
            if (e.is_directory(ec) && std::filesystem::is_regular_file(e.path() / "mod.ini", ec)) mods.push_back(Read(e.path()));
            else loose = true;
        }
        if (loose) { Manifest m; m.name = m.folder = "(loose files)"; m.dir = root; m.loose = true; mods.push_back(m); }
        std::stable_sort(mods.begin(), mods.end(), [](const Manifest& a, const Manifest& b) {
            return a.priority != b.priority ? a.priority < b.priority : a.name < b.name; });
        for (auto& m : mods)
            if (m.game != "gears3")
                m.problem = "made for " + std::string(m.game == "gears2" ? "Gears of War 2" : m.game == "gears1" ? "Gears of War" : m.game);
        // requirements, repeated until nothing changes (a mod turned off can turn off the mods that need it)
        for (bool changed = true; changed;)
        {
            changed = false;
            for (auto& m : mods)
            {
                if (!m.Active()) continue;
                for (const auto& r : m.needs)
                {
                    const auto it = std::find_if(mods.begin(), mods.end(), [&](const Manifest& o) {
                        return Lower(o.name) == Lower(r) || Lower(o.folder) == Lower(r); });
                    if (it == mods.end()) m.problem = "needs the mod " + r + ", which is not installed";
                    else if (!it->Active()) m.problem = "needs the mod " + r + ", which is off";
                    if (!m.problem.empty()) { changed = true; break; }
                }
            }
        }
        return mods;
    }

    // where a mod's file goes in the game, from its path in the mod (lower case, backslashes). Mods made for the Xbox
    // 360 are often shared as the bare files, or inside the folders of the game's installation: a path holding a
    // GearGame folder is taken from there; a bare package, configuration or texture cache goes to
    // GearGame\CookedXbox360, a bare movie to GearGame\Movies. Empty: not a game file (mod.ini, config fragments, TOCs,
    // read-me files).
    inline std::string GamePath(std::string rel)
    {
        rel = Lower(rel);
        if (rel == "mod.ini" || rel.rfind("config\\", 0) == 0) return {};
        if (const auto g = rel.rfind("geargame\\", 0) == 0 ? 0 : rel.find("\\geargame\\"); g != std::string::npos)
            rel = rel.substr(g == 0 ? 0 : g + 1);
        else
        {
            const auto dot = rel.find_last_of('.');
            const std::string ext = dot == std::string::npos ? "" : rel.substr(dot + 1);
            const std::string name = rel.substr(rel.find_last_of('\\') + 1);
            if (ext == "xxx" || ext == "upk" || ext == "tfc" || (ext == "bin" && name.rfind("coalesced_", 0) == 0)) rel = "geargame\\cookedxbox360\\" + name;
            else if (ext == "bik") rel = "geargame\\movies\\" + name;
            else return {};
        }
        if (rel.rfind("geargame\\xbox360toc", 0) == 0 && rel.size() > 4 && rel.substr(rel.size() - 4) == ".txt") return {};   // the runtime builds the TOC
        return rel;
    }

    // a mod's files: (game path, file on disk). The loose files do not descend into the manifest mods' folders.
    inline std::vector<std::pair<std::string, std::filesystem::path>> Files(const Manifest& m)
    {
        std::vector<std::pair<std::string, std::filesystem::path>> out;
        std::error_code ec;
        for (auto it = std::filesystem::recursive_directory_iterator(m.dir, ec); it != std::filesystem::recursive_directory_iterator(); it.increment(ec))
        {
            if (ec) break;
            if (m.loose && it.depth() == 0 && it->is_directory(ec) && std::filesystem::is_regular_file(it->path() / "mod.ini", ec))
            {
                it.disable_recursion_pending();
                continue;
            }
            if (!it->is_regular_file(ec)) continue;
            const std::string game = GamePath(std::filesystem::relative(it->path(), m.dir, ec).string());
            if (!game.empty()) out.emplace_back(game, it->path());
        }
        std::sort(out.begin(), out.end());
        return out;
    }

    // a mod's configuration fragments (config\*.ini), by name
    inline std::vector<std::filesystem::path> Fragments(const Manifest& m)
    {
        std::vector<std::filesystem::path> out;
        std::error_code ec;
        if (m.loose) return out;
        for (const auto& e : std::filesystem::directory_iterator(m.dir / "config", ec))
            if (e.is_regular_file(ec) && Lower(e.path().extension().string()) == ".ini") out.push_back(e.path());
        std::sort(out.begin(), out.end());
        return out;
    }

    // what a multiplayer session compares: the "everyone" mods that are on, in load order. The fingerprint is FNV-1a
    // (64-bit) over "name|version|path:size;..." per mod (game files and fragments), so two players with the same mods
    // match whatever their folders are called; 0 when there are none (as games before 0.3 send). summary: "Name 1.0,
    // Other 2" for messages.
    struct NetSet { uint64_t fingerprint = 0; std::string summary; };

    inline NetSet ForMultiplayer(const std::vector<Manifest>& mods)
    {
        NetSet s;
        std::string text;
        for (const auto& m : mods)
        {
            if (!m.Active() || m.side != Side::Everyone) continue;
            std::error_code ec;
            text += m.name + "|" + m.version + "|";
            for (const auto& [game, file] : Files(m)) text += game + ":" + std::to_string(std::filesystem::file_size(file, ec)) + ";";
            for (const auto& f : Fragments(m)) text += "config\\" + Lower(f.filename().string()) + ":" + std::to_string(std::filesystem::file_size(f, ec)) + ";";
            text += "\n";
            s.summary += (s.summary.empty() ? "" : ", ") + m.name + (m.version.empty() ? "" : " " + m.version);
        }
        if (text.empty()) return s;
        uint64_t h = 0xCBF29CE484222325ull;
        for (unsigned char c : text) { h ^= c; h *= 0x100000001B3ull; }
        s.fingerprint = h ? h : 1;
        return s;
    }
}
