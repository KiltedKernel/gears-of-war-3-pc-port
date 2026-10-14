// Mods (see mods.h): discovery, load order, the file index and the merged TOC.
#include <stdafx.h>
#include "mods.h"
#include <os/logger.h>
#include <user/paths.h>
#include <bcrypt.h>
#include <algorithm>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <sstream>
#include <unordered_map>

#pragma comment(lib, "bcrypt.lib")

namespace mods
{
namespace
{
    std::string Lower(std::string s)
    {
        std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return char(tolower(c)); });
        std::replace(s.begin(), s.end(), '/', '\\');
        return s;
    }

    std::string Trim(std::string s)
    {
        const auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

    // build\mods; GOW3_MODS_DIR=<folder> uses another (tools/playtest.ps1: tests bring their own mods)
    std::filesystem::path ModsRoot()
    {
        wchar_t dir[MAX_PATH]{};
        const DWORD n = GetEnvironmentVariableW(L"GOW3_MODS_DIR", dir, MAX_PATH);
        if (n > 0 && n < MAX_PATH) return std::filesystem::path(dir);
        wchar_t exe[MAX_PATH]{};
        GetModuleFileNameW(nullptr, exe, MAX_PATH);
        return std::filesystem::path(exe).parent_path() / "mods";
    }

    Mod ReadManifest(const std::filesystem::path& dir)
    {
        Mod m;
        m.dir = dir;
        m.name = dir.filename().string();
        std::ifstream in(dir / "mod.ini");
        std::string line;
        while (std::getline(in, line))
        {
            line = Trim(line);
            if (line.empty() || line[0] == ';' || line[0] == '#' || line[0] == '[') continue;
            const auto eq = line.find('=');
            if (eq == std::string::npos) continue;
            const std::string key = Lower(Trim(line.substr(0, eq))), value = Trim(line.substr(eq + 1));
            if (key == "name") m.name = value;
            else if (key == "version") m.version = value;
            else if (key == "author") m.author = value;
            else if (key == "description") m.description = value;
            else if (key == "priority") m.priority = atoi(value.c_str());
            else if (key == "enabled") m.enabled = !(Lower(value) == "false" || value == "0" || Lower(value) == "no");
        }
        return m;
    }

    struct State
    {
        std::vector<Mod> mods;                                             // load order: lowest priority first
        std::unordered_map<std::string, std::filesystem::path> files;     // lower-case relative path -> winning file
        std::map<std::string, std::filesystem::path> tocs;                 // merged TOCs built so far
        std::vector<std::pair<const Mod*, std::filesystem::path>> fragments; // config\*.ini of enabled mods, load order
        bool configMerged = false;
    };

    State& Get()
    {
        static State s;
        static std::once_flag once;
        std::call_once(once, [] {
            const auto root = ModsRoot();
            std::error_code ec;
            if (!std::filesystem::is_directory(root, ec)) return;
            for (const auto& entry : std::filesystem::directory_iterator(root, ec))
                if (entry.is_directory() && std::filesystem::is_regular_file(entry.path() / "mod.ini", ec))
                    s.mods.push_back(ReadManifest(entry.path()));
            // files directly under mods\ (the converters' output): one more mod
            bool loose = false;
            for (const auto& entry : std::filesystem::directory_iterator(root, ec))
                if (!(entry.is_directory() && std::filesystem::is_regular_file(entry.path() / "mod.ini", ec))) loose = true;
            if (loose) { Mod m; m.name = "(loose files)"; m.dir = root; s.mods.push_back(m); }
            std::stable_sort(s.mods.begin(), s.mods.end(), [](const Mod& a, const Mod& b) {
                return a.priority != b.priority ? a.priority < b.priority : a.name < b.name; });
            std::map<std::string, std::vector<std::string>> owners;
            for (const Mod& m : s.mods)
            {
                LOGFN("mods: {} {}{}{} (priority {}){}", m.name, m.version, m.author.empty() ? "" : " by ", m.author,
                      m.priority, m.enabled ? "" : " - disabled");
                if (!m.enabled) continue;
                for (auto it = std::filesystem::recursive_directory_iterator(m.dir, ec); it != std::filesystem::recursive_directory_iterator(); it.increment(ec))
                {
                    if (ec) break;
                    // the loose mod does not descend into the folders of the manifest mods
                    if (m.name == "(loose files)" && it.depth() == 0 && it->is_directory() &&
                        std::filesystem::is_regular_file(it->path() / "mod.ini", ec)) { it.disable_recursion_pending(); continue; }
                    if (!it->is_regular_file()) continue;
                    const std::string rel = Lower(std::filesystem::relative(it->path(), m.dir, ec).string());
                    if (rel == "mod.ini" || rel.ends_with("toc.txt") && rel.find("xbox360toc") != std::string::npos) continue;
                    if (rel.starts_with("config\\")) continue;                  // configuration fragments (below)
                    s.files[rel] = it->path();                              // later (higher priority) wins
                    owners[rel].push_back(m.name);
                }
            }
            for (const auto& [rel, who] : owners)
                if (who.size() > 1)
                {
                    std::string list;
                    for (const auto& n : who) list += (list.empty() ? "" : ", ") + n;
                    LOGFN("mods: conflict on {}: {} (the last wins)", rel, list);
                }
            for (const Mod& m : s.mods)
                if (m.enabled && m.name != "(loose files)")
                    for (const auto& f : config::Fragments(m)) s.fragments.emplace_back(&m, f);
            LOGFN("mods: {} mods, {} files, {} configuration fragments", s.mods.size(), s.files.size(), s.fragments.size());
        });
        return s;
    }

    std::string Md5Hex(const std::filesystem::path& file, uint64_t& size)
    {
        size = 0;
        BCRYPT_ALG_HANDLE alg = nullptr; BCRYPT_HASH_HANDLE h = nullptr;
        if (BCryptOpenAlgorithmProvider(&alg, BCRYPT_MD5_ALGORITHM, nullptr, 0) != 0) return {};
        BCryptCreateHash(alg, &h, nullptr, 0, nullptr, 0, 0);
        std::ifstream in(file, std::ios::binary);
        std::vector<char> buf(1 << 20);
        while (in)
        {
            in.read(buf.data(), buf.size());
            const auto n = in.gcount();
            if (n <= 0) break;
            BCryptHashData(h, (PUCHAR)buf.data(), ULONG(n), 0);
            size += uint64_t(n);
        }
        uint8_t digest[16]{};
        BCryptFinishHash(h, digest, sizeof(digest), 0);
        BCryptDestroyHash(h); BCryptCloseAlgorithmProvider(alg, 0);
        return fmt::format("{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
                           digest[0], digest[1], digest[2], digest[3], digest[4], digest[5], digest[6], digest[7], digest[8],
                           digest[9], digest[10], digest[11], digest[12], digest[13], digest[14], digest[15]);
    }

    // the disc's TOC with every mod file under GearGame\CookedXbox360 listed by its size and MD5 (replaced or added)
    std::filesystem::path MergedToc(State& s, const std::string& rel, const std::filesystem::path& gameRoot)
    {
        if (auto it = s.tocs.find(rel); it != s.tocs.end()) return it->second;
        std::vector<std::string> lines;
        {
            std::ifstream in(gameRoot / rel, std::ios::binary);
            std::string line;
            while (std::getline(in, line)) { if (!line.empty() && line.back() == '\r') line.pop_back(); lines.push_back(line); }
        }
        std::map<std::string, std::filesystem::path> modded;   // lower-case file name -> the winning mod's file
        for (const auto& [frel, path] : s.files)
            if (frel.starts_with("geargame\\cookedxbox360\\")) modded[Lower(path.filename().string())] = path;
        std::set<std::string> seen;
        for (auto& line : lines)
        {
            std::istringstream ls(line);
            std::string size, zero, path, md5;
            if (!(ls >> size >> zero >> path >> md5)) continue;
            const std::string name = Lower(path.substr(path.find_last_of('\\') + 1));
            if (auto it = modded.find(name); it != modded.end())
            {
                uint64_t n = 0; const std::string h = Md5Hex(it->second, n);
                line = fmt::format("{} 0 {} {}", n, path, h);
                seen.insert(name);
            }
        }
        // files new to the game are listed in the main TOC only (the language TOCs list localized files)
        std::vector<std::string> added;
        for (const auto& [name, file] : modded)
            if (!seen.count(name) && rel == "geargame\\xbox360toc.txt")
            {
                uint64_t n = 0; const std::string h = Md5Hex(file, n);
                added.push_back(fmt::format("{} 0 ..\\GearGame\\CookedXbox360\\{} {}", n, file.filename().string(), h));
            }
        lines.insert(lines.begin(), added.begin(), added.end());
        const auto out = GetUserPath() / "mods_toc" / rel;
        std::error_code ec;
        std::filesystem::create_directories(out.parent_path(), ec);
        std::ofstream o(out, std::ios::binary);
        for (const auto& line : lines) o << line << "\r\n";
        LOGFN("mods: merged {} ({} entries changed, {} added) -> {}", rel, seen.size(), added.size(), out.string());
        return s.tocs[rel] = out;
    }
}

const std::vector<Mod>& List() { return Get().mods; }

std::filesystem::path Resolve(const std::string& relative, const std::filesystem::path& gameRoot)
{
    State& s = Get();
    if (s.files.empty() && s.fragments.empty()) return {};
    if (!s.fragments.empty())
    {
        // once, before anything is served (the merged TOC lists the merged files): every language's packed
        // configuration on the disc, with the fragments applied
        static std::mutex cm;
        std::lock_guard lock(cm);
        if (!s.configMerged)
        {
            s.configMerged = true;
            std::error_code ec;
            for (const auto& e : std::filesystem::directory_iterator(gameRoot / "GearGame" / "CookedXbox360", ec))
            {
                const std::string name = Lower(e.path().filename().string());
                if (!(name.starts_with("coalesced_") && name.ends_with(".bin"))) continue;
                const std::string crel = "geargame\\cookedxbox360\\" + name;
                const auto it = s.files.find(crel);
                const auto base = it != s.files.end() ? it->second : e.path();
                const auto out = GetUserPath() / "mods_config" / crel;
                if (config::Merge(base, s.fragments, out)) s.files[crel] = out;
            }
        }
    }
    std::string rel = Lower(relative);
    while (!rel.empty() && rel[0] == '\\') rel.erase(0, 1);
    if (rel.starts_with("geargame\\xbox360toc") && rel.ends_with(".txt"))
    {
        static std::mutex m;
        std::lock_guard lock(m);
        return MergedToc(s, rel, gameRoot);
    }
    if (auto it = s.files.find(rel); it != s.files.end()) return it->second;
    return {};
}
}
