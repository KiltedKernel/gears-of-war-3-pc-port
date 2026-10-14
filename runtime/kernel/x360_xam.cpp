// XAM: one local, offline profile in slot 0 (Xenia semantics as reference), launch data, notifications, empty
// achievement/stat enumerators, inet_addr.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include "memory.h"
#include "xdm.h"
#include "heap.h"
#include <os/logger.h>

uint32_t XamCreateEmptyEnumerator();   // xam.cpp
uint32_t CompleteOverlapped(XXOVERLAPPED* ov, uint32_t result, uint32_t length);   // xam.cpp

namespace x360k
{

namespace
{
    constexpr uint64_t kXuid = 0xB13EBABEBABEBABEull;   // the local profile (also Xenia's default)
    constexpr const char* kGamertag = "Player";
    constexpr uint32_t ERROR_NO_SUCH_USER_ = 0x525, X_E_NO_SUCH_USER = 0x80070525, X_ERROR_NOT_FOUND = 0x490;
}

uint32_t XamUserGetSigninState(uint32_t userIndex)
{
    return userIndex == 0 ? 1 : 0;   // 1 = eXUserSigninState_SignedInLocally
}

uint32_t XamUserGetSigninInfo(uint32_t userIndex, uint32_t flags, XUSER_SIGNIN_INFO* info)
{
    if (userIndex != 0)
        return ERROR_NO_SUCH_USER_;
    memset(info, 0, sizeof(*info));
    info->xuid = kXuid;
    info->SigninState = 1;
    strncpy(info->Name, kGamertag, sizeof(info->Name) - 1);
    return 0;
}

uint32_t XamUserGetXUID(uint32_t userIndex, uint32_t typeMask, be<uint64_t>* xuid)
{
    if (userIndex != 0)
        return X_E_NO_SUCH_USER;
    *xuid = kXuid;
    return 0;
}

uint32_t XamUserGetName(uint32_t userIndex, char* buffer, uint32_t bufferSize)
{
    if (userIndex != 0)
        return ERROR_NO_SUCH_USER_;
    if (bufferSize)
    {
        strncpy(buffer, kGamertag, bufferSize - 1);
        buffer[std::min<size_t>(strlen(kGamertag), bufferSize - 1)] = 0;
    }
    return 0;
}

// the title was not started by another title: no launch data
uint32_t XamLoaderGetLaunchDataSize(be<uint32_t>* size)
{
    if (size) *size = 0;
    return X_ERROR_NOT_FOUND;
}

uint32_t XamLoaderGetLaunchData(void* buffer, uint32_t size)
{
    return X_ERROR_NOT_FOUND;
}

uint32_t XMsgCancelIORequest(uint32_t overlapped, uint32_t wait)
{
    return 0;   // every request here completes immediately
}

void XNotifyPositionUI(uint32_t position) {}

// offline: no achievements or stats to list
uint32_t XamUserCreateAchievementEnumerator(uint32_t titleId, uint32_t userIndex, uint64_t xuid, uint32_t flags,
    uint32_t offset, uint32_t count, be<uint32_t>* bufferSize, be<uint32_t>* handle)
{
    if (bufferSize) *bufferSize = count * 0x224;   // X_ACHIEVEMENT_DETAILS plus its strings, generously
    *handle = XamCreateEmptyEnumerator();
    return 0;
}

// profile settings writes (options, sensitivity) are accepted; not persisted yet
uint32_t XamUserWriteProfileSettings(uint32_t titleId, uint32_t userIndex, uint32_t settingCount, uint32_t settings, XXOVERLAPPED* overlapped)
{
    if (overlapped)
        return CompleteOverlapped(overlapped, 0, 0);
    return 0;
}

// XAM's own allocations (zeroed), from the host-side heap
uint32_t XamAlloc(uint32_t flags, uint32_t size, be<uint32_t>* out)
{
    void* p = g_userHeap.Alloc(std::max<uint32_t>(size, 1));
    if (!p)
    {
        *out = 0;
        return 0x8007000E; // E_OUTOFMEMORY
    }
    memset(p, 0, size);
    *out = g_memory.MapVirtual(p);
    return 0;
}

uint32_t XamFree(uint32_t ptr)
{
    if (ptr)
        g_userHeap.Free(g_memory.Translate(ptr));
    return 0;
}

// inet_addr on a big-endian console: the network-order value is the plain number a.b.c.d
uint32_t NetDll_inet_addr(uint32_t a1, uint32_t a2)
{
    // XNet exports take a 'caller' first; accept either convention
    const uint32_t ptr = a1 >= 0x10000 ? a1 : a2;
    if (ptr < 0x10000)
        return 0xFFFFFFFF;
    const char* s = (const char*)g_memory.Translate(ptr);
    unsigned b[4];
    char tail;
    if (sscanf(s, "%u.%u.%u.%u%c", &b[0], &b[1], &b[2], &b[3], &tail) != 4 || b[0] > 255 || b[1] > 255 || b[2] > 255 || b[3] > 255)
        return 0xFFFFFFFF;   // INADDR_NONE
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3];
}

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__XamUserGetSigninState, x360k::XamUserGetSigninState);
GUEST_FUNCTION_HOOK(__imp__XamUserGetSigninInfo, x360k::XamUserGetSigninInfo);
GUEST_FUNCTION_HOOK(__imp__XamUserGetXUID, x360k::XamUserGetXUID);
GUEST_FUNCTION_HOOK(__imp__XamUserGetName, x360k::XamUserGetName);
GUEST_FUNCTION_HOOK(__imp__XamLoaderGetLaunchDataSize, x360k::XamLoaderGetLaunchDataSize);
GUEST_FUNCTION_HOOK(__imp__XamLoaderGetLaunchData, x360k::XamLoaderGetLaunchData);
GUEST_FUNCTION_HOOK(__imp__XMsgCancelIORequest, x360k::XMsgCancelIORequest);
GUEST_FUNCTION_HOOK(__imp__XNotifyPositionUI, x360k::XNotifyPositionUI);
GUEST_FUNCTION_HOOK(__imp__XamUserCreateAchievementEnumerator, x360k::XamUserCreateAchievementEnumerator);
// XamUserWriteProfileSettings: x360_profile.cpp
GUEST_FUNCTION_HOOK(__imp__XamAlloc, x360k::XamAlloc);
GUEST_FUNCTION_HOOK(__imp__XamFree, x360k::XamFree);
GUEST_FUNCTION_HOOK(__imp__NetDll_inet_addr, x360k::NetDll_inet_addr);
