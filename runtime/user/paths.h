#pragma once
#include <filesystem>
// Save data lives in %LOCALAPPDATA%\GoW3Recomp\save (Unleashed: user folder)
std::filesystem::path GetUserPath();
std::filesystem::path GetSavePath(bool checkForMods = false);
std::filesystem::path GetGamePath();   // the player's game files ("game:")
