// Mods' configuration fragments merged into the game's packed configuration (see mods.h, docs/MODDING.md): the
// format and the fragment rules are modcore's (modcore/modcore.h, MergeConfig).
#include <stdafx.h>
#include "mods.h"
#include <os/logger.h>
#include <fstream>

namespace mods
{
namespace config
{
bool Merge(const std::filesystem::path& base, const std::vector<std::pair<const Mod*, std::filesystem::path>>& fragments,
           const std::filesystem::path& out)
{
    std::ifstream in(base, std::ios::binary);
    const std::vector<uint8_t> d((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    std::vector<std::pair<std::string, std::filesystem::path>> frags;
    for (const auto& [mod, f] : fragments) frags.emplace_back(mod->name, f);
    const auto bytes = modcore::MergeConfig(d, frags, [](const std::string& line) { LOGFN("mods: {}", line); });
    if (bytes.empty()) { LOGFN_WARNING("mods: {} is not a packed configuration file: fragments not applied", base.string()); return false; }
    std::error_code ec;
    std::filesystem::create_directories(out.parent_path(), ec);
    std::ofstream o(out, std::ios::binary);
    o.write((const char*)bytes.data(), std::streamsize(bytes.size()));
    LOGFN("mods: merged configuration ({} fragments) -> {}", fragments.size(), out.string());
    return bool(o);
}
}
}
