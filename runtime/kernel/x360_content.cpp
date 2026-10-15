// XContent (saves, profile-bound data), after Xenia's xam_content.cc / content_manager.cc. One device: the hard
// drive (id 1). Each package is a folder:
//   %LOCALAPPDATA%\GoW3Recomp\content\<owner>\4D5308AB\<content type, 8 hex>\<file name>\
// owner = the profile's XUID for saved games (type 1), 0000000000000000 otherwise; <folder>.header keeps the
// package's XCONTENT_DATA (display name) for enumeration. XamContentCreateEx mounts the package under the title's root
// name (e.g. "save:"), so the title's file I/O lands in the folder (kernel/x360_files.cpp).
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include "memory.h"
#include "xam.h"
#include "xdm.h"
#include <os/logger.h>
#include <user/profile.h>
#include <user/paths.h>
#include <fstream>

uint32_t CompleteOverlapped(XXOVERLAPPED* ov, uint32_t result, uint32_t length);   // xam.cpp

namespace x360k
{

namespace
{
    constexpr uint32_t kDeviceHdd = 1;
    constexpr uint32_t kTitle = 0x4D5308AB;
    constexpr uint32_t X_ERROR_FILE_NOT_FOUND = 2, X_ERROR_PATH_NOT_FOUND = 3, X_ERROR_ALREADY_EXISTS = 183,
                       X_ERROR_DEVICE_NOT_CONNECTED = 0x48F, X_ERROR_NO_SUCH_USER = 0x525;
    constexpr uint32_t kCreated = 1, kOpened = 2;   // XCONTENT_NEW / XCONTENT_EXISTING
    std::mutex g_contentMutex;

    std::filesystem::path TypeFolder(uint32_t type)
    {
        const uint64_t owner = type == XCONTENTTYPE_SAVEDATA ? profile::SaveOwnerFolder() : 0;   // (fixed: saves stay put)
        return GetUserPath() / "content" / fmt::format("{:016X}", owner) / fmt::format("{:08X}", kTitle) / fmt::format("{:08X}", type);
    }

    std::string FileName(const XCONTENT_DATA& d)
    {
        return std::string(d.szFileName, strnlen(d.szFileName, sizeof(d.szFileName)));
    }

    std::filesystem::path PackagePath(const XCONTENT_DATA& d) { return TypeFolder(d.dwContentType) / FileName(d); }
    std::filesystem::path HeaderPath(const XCONTENT_DATA& d) { auto p = PackagePath(d); p += ".header"; return p; }

    void WriteHeader(const XCONTENT_DATA& d)
    {
        std::ofstream f(HeaderPath(d), std::ios::binary);
        f.write((const char*)&d, sizeof(d));
    }

    uint32_t Finish(XXOVERLAPPED* overlapped, uint32_t result, uint32_t length = 0)
    {
        return overlapped ? CompleteOverlapped(overlapped, result, length) : result;
    }
}

uint32_t XamContentCreateEx(uint32_t userIndex, const char* rootName, const XCONTENT_DATA* data, uint32_t flags,
    be<uint32_t>* disposition, be<uint32_t>* licenseMask, uint32_t cacheSize, uint64_t contentSize, XXOVERLAPPED* overlapped)
{
    std::lock_guard lock(g_contentMutex);
    const auto path = PackagePath(*data);
    std::error_code ec;
    const bool exists = std::filesystem::is_directory(path, ec);
    uint32_t result = 0, disp = kOpened;

    switch (flags & 0xF)
    {
    case 1:   // CREATE_NEW
        if (exists) result = X_ERROR_ALREADY_EXISTS; else disp = kCreated;
        break;
    case 2:   // CREATE_ALWAYS: an existing package is emptied
        if (exists) std::filesystem::remove_all(path, ec);
        disp = exists ? kOpened : kCreated;
        break;
    case 3:   // OPEN_EXISTING
        if (!exists) result = X_ERROR_FILE_NOT_FOUND;
        break;
    case 4:   // OPEN_ALWAYS
        if (!exists) disp = kCreated;
        break;
    case 5:   // TRUNCATE_EXISTING
        if (!exists) result = X_ERROR_FILE_NOT_FOUND; else std::filesystem::remove_all(path, ec);
        break;
    default:
        result = X_ERROR_PATH_NOT_FOUND;
    }
    if (result == 0)
    {
        std::filesystem::create_directories(path, ec);
        if (disp == kCreated || !std::filesystem::exists(HeaderPath(*data), ec)) WriteHeader(*data);
        XamRootCreate(rootName, (const char*)path.u8string().c_str());
        if (disposition) *disposition = disp;
        if (licenseMask) *licenseMask = 0xFFFFFFFF;
    }
    LOGFN("XamContentCreateEx: '{}' type {:X} mode {} as {}: -> {} ({})", FileName(*data), uint32_t(data->dwContentType),
        flags & 0xF, rootName, result, disp == kCreated ? "created" : "opened");
    return Finish(overlapped, result, result == 0 ? disp : 0);
}

uint32_t XamContentClose(const char* rootName, XXOVERLAPPED* overlapped)
{
    XamRootDelete(rootName);
    return Finish(overlapped, 0);
}

uint32_t XamContentFlush(const char* rootName, XXOVERLAPPED* overlapped) { return Finish(overlapped, 0); }

uint32_t XamContentDelete(uint32_t userIndex, const XCONTENT_DATA* data, XXOVERLAPPED* overlapped)
{
    std::lock_guard lock(g_contentMutex);
    std::error_code ec;
    const bool existed = std::filesystem::remove_all(PackagePath(*data), ec) > 0;
    std::filesystem::remove(HeaderPath(*data), ec);
    return Finish(overlapped, existed ? 0 : X_ERROR_FILE_NOT_FOUND);
}

uint32_t XamContentGetCreator(uint32_t userIndex, const XCONTENT_DATA* data, be<uint32_t>* isCreator, be<uint64_t>* creator, XXOVERLAPPED* overlapped)
{
    if (isCreator) *isCreator = 1;
    if (creator) *creator = data->dwContentType == XCONTENTTYPE_SAVEDATA ? profile::Xuid() : 0;
    return Finish(overlapped, 0);
}

uint32_t XamContentSetThumbnail(const char* rootName, const uint8_t* image, uint32_t size, XXOVERLAPPED* overlapped)
{
    std::string_view root = XamGetRootPath(rootName);
    if (!root.empty() && image && size)
    {
        auto p = std::filesystem::path(std::u8string_view((const char8_t*)root.data(), root.size()));
        std::ofstream f(p.string() + ".thumbnail.png", std::ios::binary);
        f.write((const char*)image, size);
    }
    return Finish(overlapped, 0);
}

// packages of one type on the hard drive, items of XCONTENT_DATA
uint32_t XamContentCreateEnumerator(uint32_t userIndex, uint32_t deviceId, uint32_t contentType, uint32_t flags,
    uint32_t itemsPerEnumerate, be<uint32_t>* bufferSize, be<uint32_t>* handle)
{
    if (deviceId && deviceId != kDeviceHdd)
        return X_ERROR_DEVICE_NOT_CONNECTED;
    std::vector<uint8_t> items;
    std::error_code ec;
    for (auto& entry : std::filesystem::directory_iterator(TypeFolder(contentType), ec))
    {
        if (!entry.is_directory(ec)) continue;
        XCONTENT_DATA d{};
        std::ifstream f(entry.path().string() + ".header", std::ios::binary);
        if (!f.read((char*)&d, sizeof(d)))
        {
            // no header: rebuild it from the folder name
            d = {};
            d.dwContentType = contentType;
            const std::string name = entry.path().filename().string();
            strncpy(d.szFileName, name.c_str(), sizeof(d.szFileName));
            for (size_t i = 0; i < name.size() && i < 127; i++) d.szDisplayName[i] = uint16_t(name[i]);
        }
        d.DeviceID = kDeviceHdd;
        const auto* bytes = (const uint8_t*)&d;
        items.insert(items.end(), bytes, bytes + sizeof(d));
    }
    if (bufferSize) *bufferSize = uint32_t(sizeof(XCONTENT_DATA) * std::max<uint32_t>(itemsPerEnumerate, 1));
    *handle = XamCreateItemEnumerator(std::move(items), sizeof(XCONTENT_DATA), itemsPerEnumerate);
    return 0;
}

uint32_t XamContentGetDeviceState(uint32_t deviceId, XXOVERLAPPED* overlapped)
{
    return Finish(overlapped, deviceId == kDeviceHdd || deviceId == 0 ? 0 : X_ERROR_DEVICE_NOT_CONNECTED);
}

uint32_t XamContentGetDeviceData(uint32_t deviceId, XDEVICE_DATA* data)
{
    if (deviceId != kDeviceHdd && deviceId != 0)
        return X_ERROR_DEVICE_NOT_CONNECTED;
    memset(data, 0, sizeof(*data));
    data->DeviceID = kDeviceHdd;
    data->DeviceType = XCONTENTDEVICETYPE_HDD;
    data->ulDeviceBytes = 0x2000000000ull;       // 128 GB
    data->ulDeviceFreeBytes = 0x1000000000ull;   // 64 GB free
    const char* name = "Hard Drive";
    for (int i = 0; name[i]; i++) data->wszName[i] = uint16_t(name[i]);
    return 0;
}

// the storage device selector: the hard drive, chosen at once
uint32_t XamShowDeviceSelectorUI(uint32_t userIndex, uint32_t contentType, uint32_t flags, uint64_t totalRequested,
    be<uint32_t>* deviceId, XXOVERLAPPED* overlapped)
{
    if (deviceId) *deviceId = kDeviceHdd;
    return Finish(overlapped, 0);
}

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__XamContentCreateEx, x360k::XamContentCreateEx);
GUEST_FUNCTION_HOOK(__imp__XamContentClose, x360k::XamContentClose);
GUEST_FUNCTION_HOOK(__imp__XamContentFlush, x360k::XamContentFlush);
GUEST_FUNCTION_HOOK(__imp__XamContentDelete, x360k::XamContentDelete);
GUEST_FUNCTION_HOOK(__imp__XamContentGetCreator, x360k::XamContentGetCreator);
GUEST_FUNCTION_HOOK(__imp__XamContentSetThumbnail, x360k::XamContentSetThumbnail);
GUEST_FUNCTION_HOOK(__imp__XamContentCreateEnumerator, x360k::XamContentCreateEnumerator);
GUEST_FUNCTION_HOOK(__imp__XamContentGetDeviceState, x360k::XamContentGetDeviceState);
GUEST_FUNCTION_HOOK(__imp__XamContentGetDeviceData, x360k::XamContentGetDeviceData);
GUEST_FUNCTION_HOOK(__imp__XamShowDeviceSelectorUI, x360k::XamShowDeviceSelectorUI);
