// Mods' configuration fragments merged into the game's packed configuration (see mods.h, docs/MODDING.md).
//
// Gears 3 reads every .ini from GearGame\CookedXbox360\Coalesced_<LANG>.bin: big-endian int32 file count; per file
// a string name and int32 section count; per section a string name and int32 key count; per key two strings. A
// string is int32 n then n ANSI bytes (n > 0) or -n UTF-16LE units (n < 0), NUL included; the game's own file uses
// UTF-16 throughout, and so does the merged file.
//
// A mod ships fragments as config\*.ini (same rules as tools/coalesced.py merge): '##### <ini file>' picks the
// packed file whose name ends with it ('##### Xbox360-GearGame.ini', '##### GearGame.int'); then [Section] and
//   Key=v   replaces every value of Key (the first such line in the section; later plain lines add values)
//   +Key=v  adds v unless present        -Key=v  removes v
//   *Key=v  replaces the value with the same first field ("(Identifier=X,...)": a menu entry), else adds v
// Sections and files that do not exist are created. Fragments apply in load order (lowest priority first), on top
// of the disc's file or of a whole Coalesced file a mod ships.
#include <stdafx.h>
#include "mods.h"
#include <os/logger.h>
#include <fstream>

namespace mods
{
namespace config
{
namespace
{
    struct Section { std::u16string name; std::vector<std::pair<std::u16string, std::u16string>> keys; };
    struct File { std::u16string name; std::vector<Section> sections; };

    struct Reader
    {
        const std::vector<uint8_t>& d; size_t p = 0; bool ok = true;
        int32_t I32()
        {
            if (p + 4 > d.size()) { ok = false; return 0; }
            const int32_t v = int32_t(uint32_t(d[p]) << 24 | uint32_t(d[p + 1]) << 16 | uint32_t(d[p + 2]) << 8 | d[p + 3]);
            p += 4; return v;
        }
        std::u16string Str()
        {
            const int32_t n = I32();
            std::u16string s;
            if (n > 0)
            {
                if (p + size_t(n) > d.size()) { ok = false; return s; }
                for (int32_t i = 0; i + 1 < n; i++) s += char16_t(d[p + i]);
                p += size_t(n);
            }
            else if (n < 0)
            {
                const size_t units = size_t(-int64_t(n));
                if (p + 2 * units > d.size()) { ok = false; return s; }
                for (size_t i = 0; i + 1 < units; i++) s += char16_t(d[p + 2 * i] | d[p + 2 * i + 1] << 8);
                p += 2 * units;
            }
            return s;
        }
    };

    bool Parse(const std::vector<uint8_t>& d, std::vector<File>& files)
    {
        Reader r{ d };
        const int32_t n = r.I32();
        for (int32_t f = 0; f < n && r.ok; f++)
        {
            File file; file.name = r.Str();
            const int32_t ns = r.I32();
            for (int32_t s = 0; s < ns && r.ok; s++)
            {
                Section sec; sec.name = r.Str();
                const int32_t nk = r.I32();
                for (int32_t k = 0; k < nk && r.ok; k++) { auto key = r.Str(); auto value = r.Str(); sec.keys.emplace_back(std::move(key), std::move(value)); }
                file.sections.push_back(std::move(sec));
            }
            files.push_back(std::move(file));
        }
        return r.ok && r.p == d.size();
    }

    void PutI32(std::vector<uint8_t>& o, int32_t v)
    {
        o.push_back(uint8_t(uint32_t(v) >> 24)); o.push_back(uint8_t(uint32_t(v) >> 16));
        o.push_back(uint8_t(uint32_t(v) >> 8)); o.push_back(uint8_t(v));
    }
    void PutStr(std::vector<uint8_t>& o, const std::u16string& s)
    {
        if (s.empty()) { PutI32(o, 0); return; }
        PutI32(o, -int32_t(s.size() + 1));
        for (char16_t c : s) { o.push_back(uint8_t(c)); o.push_back(uint8_t(c >> 8)); }
        o.push_back(0); o.push_back(0);
    }
    std::vector<uint8_t> Write(const std::vector<File>& files)
    {
        std::vector<uint8_t> o;
        PutI32(o, int32_t(files.size()));
        for (const File& f : files)
        {
            PutStr(o, f.name); PutI32(o, int32_t(f.sections.size()));
            for (const Section& s : f.sections)
            {
                PutStr(o, s.name); PutI32(o, int32_t(s.keys.size()));
                for (const auto& [k, v] : s.keys) { PutStr(o, k); PutStr(o, v); }
            }
        }
        return o;
    }

    std::u16string Widen(const std::string& utf8)
    {
        if (utf8.empty()) return {};
        const int n = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), int(utf8.size()), nullptr, 0);
        std::wstring w(size_t(n), L'\0');
        MultiByteToWideChar(CP_UTF8, 0, utf8.data(), int(utf8.size()), w.data(), n);
        return std::u16string(w.begin(), w.end());
    }
    std::u16string LowerW(std::u16string s)
    {
        for (char16_t& c : s) { if (c >= u'A' && c <= u'Z') c = char16_t(c + 32); if (c == u'/') c = u'\\'; }
        return s;
    }
    std::string Trim(std::string s)
    {
        const auto a = s.find_first_not_of(" \t\r\n"), b = s.find_last_not_of(" \t\r\n");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }
    std::u16string FirstField(const std::u16string& v) { const auto c = v.find(u','); return c == std::u16string::npos ? v : v.substr(0, c); }

    // one fragment onto the parsed files; returns the number of key lines applied (problems are logged)
    int Apply(std::vector<File>& files, const std::filesystem::path& fragment, const std::string& modName)
    {
        std::ifstream in(fragment, std::ios::binary);
        std::string line;
        File* file = nullptr; Section* sec = nullptr;
        std::vector<std::u16string> replaced;
        int applied = 0, lineNo = 0;
        while (std::getline(in, line))
        {
            lineNo++;
            if (lineNo == 1 && line.size() >= 3 && (uint8_t)line[0] == 0xEF && (uint8_t)line[1] == 0xBB && (uint8_t)line[2] == 0xBF) line.erase(0, 3);
            line = Trim(line);
            if (line.empty() || line[0] == ';' || (line[0] == '#' && line.rfind("#####", 0) != 0)) continue;
            auto bad = [&](const char* why) { LOGFN_WARNING("mods: {} {}:{}: {}", modName, fragment.filename().string(), lineNo, why); };
            if (line.rfind("#####", 0) == 0)
            {
                const std::u16string want = LowerW(Widen(Trim(line.substr(5))));
                file = nullptr;
                for (File& f : files)
                {
                    const std::u16string n = LowerW(f.name);
                    if (n.size() >= want.size() && n.compare(n.size() - want.size(), want.size(), want) == 0) { file = &f; break; }
                }
                if (!file) { files.push_back({ u"..\\..\\GearGame\\Config\\" + Widen(Trim(line.substr(5))), {} }); file = &files.back(); }
                sec = nullptr; continue;
            }
            if (!file) { bad("no '##### <ini file>' line before this"); continue; }
            if (line.front() == '[' && line.back() == ']')
            {
                const std::u16string name = Widen(line.substr(1, line.size() - 2));
                sec = nullptr;
                for (Section& s : file->sections) if (s.name == name) { sec = &s; break; }
                if (!sec) { file->sections.push_back({ name, {} }); sec = &file->sections.back(); }
                replaced.clear(); continue;
            }
            const auto eq = line.find('=');
            if (!sec || eq == std::string::npos) { bad("expected [section] or key=value"); continue; }
            const char op = (line[0] == '+' || line[0] == '-' || line[0] == '*') ? line[0] : 0;
            const std::u16string key = Widen(Trim(line.substr(op ? 1 : 0, eq - (op ? 1 : 0)))), value = Widen(line.substr(eq + 1));
            auto& keys = sec->keys;
            if (op == '+')
            {
                if (std::find(keys.begin(), keys.end(), std::make_pair(key, value)) == keys.end()) keys.emplace_back(key, value);
            }
            else if (op == '-')
                keys.erase(std::remove(keys.begin(), keys.end(), std::make_pair(key, value)), keys.end());
            else if (op == '*')
            {
                auto it = std::find_if(keys.begin(), keys.end(), [&](const auto& kv) { return kv.first == key && FirstField(kv.second) == FirstField(value); });
                if (it == keys.end()) keys.emplace_back(key, value); else it->second = value;
            }
            else
            {
                if (std::find(replaced.begin(), replaced.end(), key) == replaced.end())
                {
                    keys.erase(std::remove_if(keys.begin(), keys.end(), [&](const auto& kv) { return kv.first == key; }), keys.end());
                    replaced.push_back(key);
                }
                keys.emplace_back(key, value);
            }
            applied++;
        }
        return applied;
    }
}

bool Merge(const std::filesystem::path& base, const std::vector<std::pair<const Mod*, std::filesystem::path>>& fragments,
           const std::filesystem::path& out)
{
    std::ifstream in(base, std::ios::binary);
    std::vector<uint8_t> d((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    std::vector<File> files;
    if (d.empty() || !Parse(d, files)) { LOGFN_WARNING("mods: {} is not a packed configuration file: fragments not applied", base.string()); return false; }
    int keys = 0;
    for (const auto& [mod, frag] : fragments)
    {
        const int n = Apply(files, frag, mod->name);
        LOGFN("mods: config {} from {}: {} lines", frag.filename().string(), mod->name, n);
        keys += n;
    }
    std::error_code ec;
    std::filesystem::create_directories(out.parent_path(), ec);
    const auto bytes = Write(files);
    std::ofstream o(out, std::ios::binary);
    o.write((const char*)bytes.data(), std::streamsize(bytes.size()));
    LOGFN("mods: merged configuration ({} fragments, {} lines) -> {}", fragments.size(), keys, out.string());
    return bool(o);
}
}
}
