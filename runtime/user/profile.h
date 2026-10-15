#pragma once
// The player's profile (user/profile.cpp): gamertag and XUID, kept in %LOCALAPPDATA%\GoW3Recomp\profile.ini (the
// launcher's Multiplayer page edits the gamertag). The XUID is made once, at random, and kept: on a system link game
// it tells the players apart. Saves stay where earlier versions put them (SaveOwnerFolder), whatever the XUID.
#include <cstdint>
#include <string>

namespace profile
{
    uint64_t Xuid();                    // player 1's XUID (made as an offline XUID, 0xE000xxxxxxxxxxxx; any non-zero value in profile.ini is kept)
    const std::string& Gamertag();      // 1-15 printable ASCII characters
    uint64_t SaveOwnerFolder();         // the content folder of player 1's saves (B13EBABEBABEBABE since 0.1.0)
}
