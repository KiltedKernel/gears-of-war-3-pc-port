#pragma once
// Mods: folders under mods\ next to the executable that add or replace game files (docs/MODDING.md).
//   mods\<ModName>\mod.ini        [mod] name, version, author, description, priority, enabled, side, requires, game
//                                 (mod_manifest.h: the rules shared with the launcher, the server and modtool.py)
//   mods\<ModName>\GearGame\...   files at the disc's relative paths (packages, texture caches, movies...); bare
//                                 packages and Coalesced files (as Xbox 360 mods are shared) go to CookedXbox360
// Files directly under mods\ (no mod.ini) form one more mod, "(loose files)", at priority 0. When several mods
// have the same file, the highest priority wins (then the later name). The game finds packages through
// GearGame\Xbox360TOC*.txt: the runtime serves a merged TOC (the disc's, with every mod file's size and MD5), so
// mods never ship or edit a TOC. A mod's config\*.ini fragments are merged into the packed configuration
// (Coalesced_<LANG>.bin) the same way, so several mods can change the game's .ini settings.
#include "mod_manifest.h"
#include <filesystem>
#include <string>
#include <vector>

namespace mods
{
    using Mod = modinfo::Manifest;

    const std::vector<Mod>& List();                        // every mod found, enabled or not, in load order
    // the file a mod provides for a game-relative path ("GearGame\CookedXbox360\X.xxx"), or empty;
    // TOC files get the merged TOC (built once, from disc + mods)
    std::filesystem::path Resolve(const std::string& relative, const std::filesystem::path& gameRoot);
    // the "everyone" mods a multiplayer session compares (mod_manifest.h)
    const modinfo::NetSet& Multiplayer();

    // configuration fragments (kernel/mod_config.cpp): a mod's config\*.ini (modinfo::Fragments), merged into the
    // packed configuration GearGame\CookedXbox360\Coalesced_<LANG>.bin (the disc's, or a mod's whole file) when the
    // game first asks for a file
    namespace config
    {
        bool Merge(const std::filesystem::path& base, const std::vector<std::pair<const Mod*, std::filesystem::path>>& fragments,
                   const std::filesystem::path& out);
    }
}
