#pragma once
// Mods: folders under mods\ next to the executable that add or replace game files (docs/MODDING.md).
//   mods\<ModName>\mod.ini        [mod] name, version, author, description, priority (default 0), enabled
//   mods\<ModName>\GearGame\...   files at the disc's relative paths (packages, texture caches, movies...)
// Files directly under mods\ (no mod.ini) form one more mod, "(loose files)", at priority 0. When several mods
// have the same file, the highest priority wins (then the later name). The game finds packages through
// GearGame\Xbox360TOC*.txt: the runtime serves a merged TOC (the disc's, with every mod file's size and MD5), so
// mods never ship or edit a TOC.
#include <filesystem>
#include <string>
#include <vector>

namespace mods
{
    struct Mod
    {
        std::string name, version, author, description;
        std::filesystem::path dir;
        int priority = 0;
        bool enabled = true;
    };

    const std::vector<Mod>& List();                        // every mod found, enabled or not, in load order
    // the file a mod provides for a game-relative path ("GearGame\CookedXbox360\X.xxx"), or empty;
    // TOC files get the merged TOC (built once, from disc + mods)
    std::filesystem::path Resolve(const std::string& relative, const std::filesystem::path& gameRoot);
}
