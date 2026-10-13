// GoW2 profile settings (XamUserRead/WriteProfileSettings), Xenia semantics: the game's options (title-specific
// binaries), sensitivity, invert and the dashboard settings it asks for. Values the game writes are kept per
// (title, setting id) in %LOCALAPPDATA%\GoW3Recomp\profile_settings.bin; anything never written reads as the default.
//
// Guest layout: X_USER_READ_PROFILE_SETTINGS { u32 count; u32 settings_ptr } then count X_USER_PROFILE_SETTING (40 bytes:
// +0 source, +8 xuid (or user index at +8), +16 setting id, +24 X_USER_DATA: type byte, +32 8-byte value or
// {u32 size, u32 ptr} for strings/binaries), then the string/binary data, max size each. A setting id is
// (type << 28) | (max size << 16) | id.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include "memory.h"
#include "xdm.h"
#include <os/logger.h>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <vector>

std::filesystem::path GetUserPath();   // main.cpp
uint32_t CompleteOverlapped(XXOVERLAPPED* ov, uint32_t result, uint32_t length);   // xam.cpp

namespace
{
    constexpr uint32_t kSettingSize = 40;
    constexpr uint32_t ERROR_INSUFFICIENT_BUFFER_ = 0x7A, ERROR_INVALID_PARAMETER_ = 0x57;
    constexpr uint8_t TYPE_CONTEXT = 0, TYPE_WSTRING = 4, TYPE_BINARY = 6;
    constexpr uint32_t SOURCE_DEFAULT = 1;

    struct Setting
    {
        uint32_t source = SOURCE_DEFAULT;
        uint8_t type = 0;
        uint8_t value[8] = {};          // guest (big-endian) bytes of the value union
        std::vector<uint8_t> extended;  // string/binary payload
    };

    std::mutex g_mutex;
    std::map<std::pair<uint32_t, uint32_t>, Setting> g_settings;   // (title, setting id)
    bool g_loaded = false;

    uint8_t TypeOf(uint32_t id) { return uint8_t(id >> 28); }
    uint32_t MaxSizeOf(uint32_t id) { return (id >> 16) & 0xFFF; }
    bool HasExtended(uint32_t id) { return TypeOf(id) == TYPE_WSTRING || TypeOf(id) == TYPE_BINARY; }

    std::filesystem::path FilePath() { return GetUserPath() / "profile_settings.bin"; }

    // file: "GW2P", u32 count, then per setting: title, id, source, type(1), value(8), size(4), payload
    void Load()
    {
        if (g_loaded) return;
        g_loaded = true;
        std::ifstream f(FilePath(), std::ios::binary);
        char magic[4];
        uint32_t count = 0;
        if (!f.read(magic, 4) || memcmp(magic, "GW2P", 4) || !f.read((char*)&count, 4)) return;
        for (uint32_t i = 0; i < count && f; i++)
        {
            uint32_t title, id, size;
            Setting s;
            f.read((char*)&title, 4).read((char*)&id, 4).read((char*)&s.source, 4).read((char*)&s.type, 1)
             .read((char*)s.value, 8).read((char*)&size, 4);
            if (!f || size > 0x10000) break;
            s.extended.resize(size);
            f.read((char*)s.extended.data(), size);
            if (f) g_settings[{ title, id }] = std::move(s);
        }
        LOGFN("profile: loaded {} settings from {}", g_settings.size(), FilePath().string());
    }

    void Save()
    {
        std::error_code ec;
        std::filesystem::create_directories(FilePath().parent_path(), ec);
        const auto tmp = FilePath().string() + ".tmp";
        {
            std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
            const uint32_t count = uint32_t(g_settings.size());
            f.write("GW2P", 4).write((const char*)&count, 4);
            for (const auto& [key, s] : g_settings)
            {
                const uint32_t size = uint32_t(s.extended.size());
                f.write((const char*)&key.first, 4).write((const char*)&key.second, 4).write((const char*)&s.source, 4)
                 .write((const char*)&s.type, 1).write((const char*)s.value, 8).write((const char*)&size, 4)
                 .write((const char*)s.extended.data(), size);
            }
            if (!f) { LOGFN_WARNING("profile: could not write {}", tmp); return; }
        }
        std::filesystem::rename(tmp, FilePath(), ec);   // replace atomically, so a crash never leaves half a file
        if (ec) LOGFN_WARNING("profile: could not replace {}: {}", FilePath().string(), ec.message());
    }

    void PutU32(uint8_t* p, uint32_t v) { p[0] = uint8_t(v >> 24); p[1] = uint8_t(v >> 16); p[2] = uint8_t(v >> 8); p[3] = uint8_t(v); }
    uint32_t GetU32(const uint8_t* p) { return (uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | (uint32_t(p[2]) << 8) | p[3]; }

    Setting Default(uint32_t id)
    {
        Setting s;
        s.type = TypeOf(id);
        if (id == 0x10040003) PutU32(s.value, 3);          // XPROFILE_OPTION_CONTROLLER_VIBRATION: on
        else if (id == 0x1004003A) PutU32(s.value, 2);     // XPROFILE_GAMER_TIER: gold (Xenia's default)
        return s;
    }
}

namespace gow2k
{

uint32_t XamUserReadProfileSettingsImpl(uint32_t titleId, uint32_t userIndex, uint32_t xuidCount, be<uint64_t>* xuids,
    uint32_t settingCount, be<uint32_t>* settingIds, be<uint32_t>* bufferSize, uint8_t* buffer, XXOVERLAPPED* overlapped)
{
    if (settingCount < 1 || settingCount > 32 || !bufferSize)
        return ERROR_INVALID_PARAMETER_;
    const uint32_t size = *bufferSize;
    if (size && !buffer)
        return ERROR_INVALID_PARAMETER_;

    const uint32_t copies = xuids ? std::max<uint32_t>(xuidCount, 1) : 1;
    uint32_t needed = 0, extendedSize = 0;
    for (uint32_t i = 0; i < settingCount; i++)
    {
        needed += kSettingSize;
        if (HasExtended(settingIds[i])) extendedSize += MaxSizeOf(settingIds[i]);
    }
    needed = needed * copies + extendedSize * copies + 8;
    if (!buffer || size < needed)
    {
        if (!size) *bufferSize = needed;
        return ERROR_INSUFFICIENT_BUFFER_;
    }

    std::lock_guard lock(g_mutex);
    Load();
    const uint32_t title = titleId ? titleId : 0x4D5308AB;
    memset(buffer, 0, needed);
    const uint32_t bufferGuest = g_memory.MapVirtual(buffer);
    uint8_t* out = buffer + 8;
    uint32_t extendedAt = bufferGuest + 8 + settingCount * kSettingSize;
    uint32_t written = 0;
    for (uint32_t i = 0; i < settingCount; i++)
    {
        const uint32_t id = settingIds[i];
        if (!id) continue;
        const auto it = g_settings.find({ title, id });
        const Setting s = it != g_settings.end() ? it->second : Default(id);
        PutU32(out + 0, s.source);
        if (xuids) { const uint64_t x = xuids[0]; PutU32(out + 8, uint32_t(x >> 32)); PutU32(out + 12, uint32_t(x)); }
        else { PutU32(out + 8, userIndex); PutU32(out + 12, 0xFFFFFFFF); }   // Xenia: xuid = -1, then the index over its high word
        PutU32(out + 16, id);
        out[24] = s.type;
        memcpy(out + 32, s.value, 8);
        if (HasExtended(id))
        {
            const uint32_t n = std::min<uint32_t>(uint32_t(s.extended.size()), MaxSizeOf(id));
            PutU32(out + 32, n);
            PutU32(out + 36, n ? extendedAt : 0);
            if (n) memcpy(g_memory.Translate(extendedAt), s.extended.data(), n);
            extendedAt += MaxSizeOf(id);
        }
        out += kSettingSize;
        written++;
    }
    PutU32(buffer, written);
    PutU32(buffer + 4, bufferGuest + 8);
    if (overlapped)
        return CompleteOverlapped(overlapped, 0, 0);
    return 0;
}

uint32_t XamUserWriteProfileSettingsImpl(uint32_t titleId, uint32_t userIndex, uint32_t settingCount, uint32_t settings,
    XXOVERLAPPED* overlapped)
{
    if (!settingCount || !settings)
        return ERROR_INVALID_PARAMETER_;
    {
        std::lock_guard lock(g_mutex);
        Load();
        const uint32_t title = titleId ? titleId : 0x4D5308AB;
        for (uint32_t i = 0; i < settingCount; i++)
        {
            const uint8_t* in = (const uint8_t*)g_memory.Translate(settings + i * kSettingSize);
            const uint32_t id = GetU32(in + 16);
            Setting s;
            s.source = GetU32(in + 0) ? GetU32(in + 0) : 2;   // TITLE
            s.type = in[24];
            memcpy(s.value, in + 32, 8);
            if (s.type == TYPE_CONTEXT && TypeOf(id) != TYPE_CONTEXT) s.type = TypeOf(id);
            if (HasExtended(id))
            {
                const uint32_t n = std::min<uint32_t>(GetU32(in + 32), MaxSizeOf(id)), ptr = GetU32(in + 36);
                if (n && ptr)
                {
                    const uint8_t* src = (const uint8_t*)g_memory.Translate(ptr);
                    s.extended.assign(src, src + n);
                }
                memset(s.value + 4, 0, 4);   // the guest pointer means nothing next run
            }
            g_settings[{ title, id }] = std::move(s);
        }
        Save();
    }
    if (overlapped)
        return CompleteOverlapped(overlapped, 0, 0);
    return 0;
}

} // namespace gow2k

GUEST_FUNCTION_HOOK(__imp__XamUserReadProfileSettings, gow2k::XamUserReadProfileSettingsImpl);
GUEST_FUNCTION_HOOK(__imp__XamUserWriteProfileSettings, gow2k::XamUserWriteProfileSettingsImpl);
