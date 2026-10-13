#include <stdafx.h>
#include "xam.h"
#include "xdm.h"
#include "function.h"
#include <os/logger.h>
#include <hid/hid.h>
#include <ui/game_window.h>
#include <cpu/guest_thread.h>
#include <ranges>
#include <unordered_set>
#include "xxHashMap.h"
#include <user/paths.h>
#include <user/config.h>

struct XamListener : KernelObject
{
    uint32_t id{};
    uint64_t areas{};
    std::vector<std::tuple<uint32_t, uint32_t>> notifications;

    XamListener(const XamListener&) = delete;
    XamListener& operator=(const XamListener&) = delete;

    XamListener();
    ~XamListener();
};

struct XamEnumeratorBase : KernelObject
{
    virtual uint32_t Next(void* buffer)
    {
        return -1;
    }
};

template<typename TIterator = std::vector<XHOSTCONTENT_DATA>::iterator>
struct XamEnumerator : XamEnumeratorBase
{
    uint32_t fetch;
    size_t size;
    TIterator position;
    TIterator begin;
    TIterator end;

    XamEnumerator() = default;
    XamEnumerator(uint32_t fetch, size_t size, TIterator begin, TIterator end) : fetch(fetch), size(size), position(begin), begin(begin), end(end)
    {

    }

    uint32_t Next(void* buffer) override
    {
        if (position == end)
        {
            return -1;
        }

        if (buffer == nullptr)
        {
            for (size_t i = 0; i < fetch; i++)
            {
                if (position == end)
                {
                    return i == 0 ? -1 : i;
                }

                ++position;
            }
        }

        for (size_t i = 0; i < fetch; i++)
        {
            if (position == end)
            {
                return i == 0 ? -1 : i;
            }

            memcpy(buffer, &*position, size);

            ++position;
            buffer = (void*)((size_t)buffer + size);
        }

        return fetch;
    }
};

std::array<xxHashMap<XHOSTCONTENT_DATA>, 3> gContentRegistry{};
std::unordered_set<XamListener*> gListeners{};
xxHashMap<std::string> gRootMap;

// GoW2: root names are case-insensitive, a root can be remounted elsewhere and unmounted (XamContentClose), and
// lookups come from many threads
static std::mutex gRootMutex;
static uint64_t RootKey(std::string_view root)
{
    std::string lower(root);
    for (auto& c : lower) c = char(tolower((unsigned char)c));
    return StringHash(lower);
}

std::string_view XamGetRootPath(const std::string_view& root)
{
    std::lock_guard lock(gRootMutex);
    const auto result = gRootMap.find(RootKey(root));

    if (result == gRootMap.end())
        return "";

    return result->second;   // entries are never erased while a path may be in use (see XamRootDelete)
}

void XamRootCreate(const std::string_view& root, const std::string_view& path)
{
    std::lock_guard lock(gRootMutex);
    gRootMap[RootKey(root)] = std::string(path);
}

void XamRootDelete(const std::string_view& root)
{
    // keep the string alive for callers holding a view: point the root at nothing instead of erasing it
    std::lock_guard lock(gRootMutex);
    auto it = gRootMap.find(RootKey(root));
    if (it != gRootMap.end()) it->second.clear();
}

XamListener::XamListener()
{
    gListeners.insert(this);
}

XamListener::~XamListener()
{
    gListeners.erase(this);
}

XCONTENT_DATA XamMakeContent(uint32_t type, const std::string_view& name)
{
    XCONTENT_DATA data{ 1, type };

    strncpy(data.szFileName, name.data(), sizeof(data.szFileName));

    return data;
}

void XamRegisterContent(const XCONTENT_DATA& data, const std::string_view& root)
{
    const auto idx = data.dwContentType - 1;

    gContentRegistry[idx].emplace(StringHash(data.szFileName), XHOSTCONTENT_DATA{ data }).first->second.szRoot = root;
}

void XamRegisterContent(uint32_t type, const std::string_view name, const std::string_view& root)
{
    XCONTENT_DATA data{ 1, type, {}, "" };

    strncpy(data.szFileName, name.data(), sizeof(data.szFileName));

    XamRegisterContent(data, root);
}

// GoW2: notifications follow Xenia (kernel_state.cc / xnotifylistener.cc). An id's listener-mask index is bits 25-30
// and its version bits 16-24; the first system listener gets the startup notifications titles wait for (UI not
// active, user 0 signed in) and the first Live listener is told Live is disconnected.
namespace
{
    std::mutex g_notifyMutex;
    bool g_notifiedSystem = false, g_notifiedLive = false;
    uint32_t NotifyMaskIndex(uint32_t id) { return (id >> 25) & 0x3F; }
    uint32_t NotifyVersion(uint32_t id) { return (id >> 16) & 0x1FF; }

    void Enqueue(XamListener* listener, uint32_t id, uint32_t param)
    {
        if ((listener->areas & (1ull << NotifyMaskIndex(id))) == 0) return;
        if (NotifyVersion(id) > listener->id) return;   // id holds the listener's max version
        listener->notifications.emplace_back(id, param);
    }
}

uint32_t XamNotifyCreateListener(uint64_t qwAreas, uint32_t maxVersion)
{
    auto* listener = CreateKernelObject<XamListener>();
    listener->areas = qwAreas;
    listener->id = std::min<uint32_t>(maxVersion, 10);
    LOGFN("XamNotifyCreateListener: areas {:X} max version {}", qwAreas, maxVersion);

    std::lock_guard lock(g_notifyMutex);
    if (!g_notifiedSystem && (qwAreas & 1))
    {
        g_notifiedSystem = true;
        Enqueue(listener, 0x00000009, 0);   // XN_SYS_UI: not active
        Enqueue(listener, 0x0000000A, 1);   // XN_SYS_SIGNINCHANGED: user 0
    }
    if (!g_notifiedLive && (qwAreas & 2))
    {
        g_notifiedLive = true;
        Enqueue(listener, 0x02000001, 0x001510F1);   // XN_LIVE_CONNECTIONCHANGED: X_ONLINE_S_LOGON_DISCONNECTED
        Enqueue(listener, 0x02000003, 1);            // XN_LIVE_LINK_STATE_CHANGED: link up (cable connected)
    }
    return GetKernelHandle(listener);
}

void XamNotifyEnqueueEvent(uint32_t dwId, uint32_t dwParam)
{
    std::lock_guard lock(g_notifyMutex);
    for (const auto& listener : gListeners)
        Enqueue(listener, dwId, dwParam);
}

bool XNotifyGetNext(uint32_t hNotification, uint32_t dwMsgFilter, be<uint32_t>* pdwId, be<uint32_t>* pParam)
{
    if (pParam) *pParam = 0;
    if (!pdwId) return false;
    *pdwId = 0;
    auto* listener = IsKernelObject(hNotification) ? dynamic_cast<XamListener*>(GetKernelObject(hNotification)) : nullptr;
    if (!listener) return false;

    std::lock_guard lock(g_notifyMutex);
    auto& queue = listener->notifications;
    for (size_t i = 0; i < queue.size(); i++)
    {
        if (dwMsgFilter && std::get<0>(queue[i]) != dwMsgFilter) continue;
        *pdwId = std::get<0>(queue[i]);
        if (pParam) *pParam = std::get<1>(queue[i]);
        LOGFN("XNotifyGetNext: {:08X} ({:X})", std::get<0>(queue[i]), std::get<1>(queue[i]));
        queue.erase(queue.begin() + i);
        return true;
    }
    return false;
}

uint32_t XamShowMessageBoxUI(uint32_t dwUserIndex, be<uint16_t>* wszTitle, be<uint16_t>* wszText, uint32_t cButtons,
    xpointer<be<uint16_t>>* pwszButtons, uint32_t dwFocusButton, uint32_t dwFlags, be<uint32_t>* pResult, XXOVERLAPPED* pOverlapped)
{
    *pResult = cButtons ? cButtons - 1 : 0;

#if _DEBUG
    assert("XamShowMessageBoxUI encountered!" && false);
#elif _WIN32
    // This code is Win32-only as it'll most likely crash, misbehave or
    // cause corruption due to using a different type of memory than what
    // wchar_t is on Linux. Windows uses 2 bytes while Linux uses 4 bytes.
    std::vector<std::wstring> texts{};

    texts.emplace_back(reinterpret_cast<wchar_t*>(wszTitle));
    texts.emplace_back(reinterpret_cast<wchar_t*>(wszText));

    for (size_t i = 0; i < cButtons; i++)
        texts.emplace_back(reinterpret_cast<wchar_t*>(pwszButtons[i].get()));

    for (auto& text : texts)
    {
        for (size_t i = 0; i < text.size(); i++)
            ByteSwapInplace(text[i]);
    }

    wprintf(L"[XamShowMessageBoxUI] !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n");
    wprintf(L"[XamShowMessageBoxUI] If you are encountering this message and the game has ceased functioning,\n");
    wprintf(L"[XamShowMessageBoxUI] please create an issue at https://github.com/hedge-dev/UnleashedRecomp/issues.\n");
    wprintf(L"[XamShowMessageBoxUI] !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n");
    wprintf(L"[XamShowMessageBoxUI] %ls\n", texts[0].c_str());
    wprintf(L"[XamShowMessageBoxUI] %ls\n", texts[1].c_str());
    wprintf(L"[XamShowMessageBoxUI] ");

    for (size_t i = 0; i < cButtons; i++)
    {
        wprintf(L"%ls", texts[2 + i].c_str());

        if (i != cButtons - 1)
            wprintf(L" | ");
    }

    wprintf(L"\n");
    wprintf(L"[XamShowMessageBoxUI] Defaulted to button: %d\n", pResult->get());
    wprintf(L"[XamShowMessageBoxUI] !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n");
#endif

    if (pOverlapped)
    {
        pOverlapped->dwCompletionContext = GuestThread::GetCurrentThreadId();
        pOverlapped->Error = 0;
        pOverlapped->Length = -1;
    }

    XamNotifyEnqueueEvent(9, 0);

    return 0;
}

uint32_t XamContentCreateEnumerator(uint32_t dwUserIndex, uint32_t DeviceID, uint32_t dwContentType,
    uint32_t dwContentFlags, uint32_t cItem, be<uint32_t>* pcbBuffer, be<uint32_t>* phEnum)
{
    if (dwUserIndex != 0)
    {
        GuestThread::SetLastError(ERROR_NO_SUCH_USER);
        return 0xFFFFFFFF;
    }

    const auto& registry = gContentRegistry[dwContentType - 1];
    const auto& values = registry | std::views::values;
    auto* enumerator = CreateKernelObject<XamEnumerator<decltype(values.begin())>>(cItem, sizeof(_XCONTENT_DATA), values.begin(), values.end());

    if (pcbBuffer)
        *pcbBuffer = sizeof(_XCONTENT_DATA) * cItem;

    *phEnum = GetKernelHandle(enumerator);

    return 0;
}

uint32_t SignalGuestEvent(uint32_t handle);   // imports.cpp

// GoW2: finish an asynchronous XAM call at once (Xenia's CompleteOverlappedImmediate): the caller reads the result from
// the XOVERLAPPED and may wait on its event
uint32_t CompleteOverlapped(XXOVERLAPPED* ov, uint32_t result, uint32_t length)
{
    ov->Error = result;
    ov->Length = length;
    ov->dwExtendedError = result;
    if (ov->hEvent)
        SignalGuestEvent(ov->hEvent);
    return 997; // ERROR_IO_PENDING
}

// GoW2: XamCreateEnumeratorHandle's enumerator: a zeroed private structure the title fills (XamGetPrivateEnumStructureFromHandle);
// the items would come from the XMsg app it names, which is offline here, so there are none
struct XamPrivateEnumerator final : XamEnumeratorBase
{
    uint32_t extra = 0, extraSize = 0;
    ~XamPrivateEnumerator() override { if (extra) g_userHeap.Free(g_memory.Translate(extra)); }
};

uint32_t XamCreateEnumeratorHandle(uint32_t userIndex, uint32_t appId, uint32_t openMessage, uint32_t closeMessage,
    uint32_t extraSize, uint32_t itemCount, uint32_t flags, be<uint32_t>* handle)
{
    auto* e = CreateKernelObject<XamPrivateEnumerator>();
    e->extraSize = extraSize;
    LOGFN("XamCreateEnumeratorHandle: user {:X} app {:X} msgs {:X}/{:X} extra {:X} items {} flags {:X}", userIndex, appId,
        openMessage, closeMessage, extraSize, itemCount, flags);
    // the private structure is a 0x18-byte XAM header followed by the title's extraSize bytes: the title writes
    // p+0x18 .. p+0x18+extraSize (seen at 829A5D4C: string at +0x18, fields up to +0xEC for extraSize 0xD4)
    {
        const uint32_t size = 0x18 + extraSize;
        void* p = g_userHeap.Alloc(size);
        memset(p, 0, size);
        e->extra = g_memory.MapVirtual(p);
    }
    *handle = GetKernelHandle(e);
    return 0;
}

uint32_t XamGetPrivateEnumStructureFromHandle(uint32_t handle, be<uint32_t>* out)
{
    auto* e = IsKernelObject(handle) ? dynamic_cast<XamPrivateEnumerator*>(GetKernelObject(handle)) : nullptr;
    if (!e)
        return 6; // ERROR_INVALID_HANDLE
    *out = e->extra;
    return 0;
}

GUEST_FUNCTION_HOOK(__imp__XamCreateEnumeratorHandle, XamCreateEnumeratorHandle);
GUEST_FUNCTION_HOOK(__imp__XamGetPrivateEnumStructureFromHandle, XamGetPrivateEnumStructureFromHandle);

// GoW2: an enumerator over a fixed list of items (content packages)
struct XamItemEnumerator final : XamEnumeratorBase
{
    std::vector<uint8_t> items;
    uint32_t itemSize = 0, fetch = 1;
    size_t position = 0;

    uint32_t Next(void* buffer) override
    {
        const size_t count = itemSize ? items.size() / itemSize : 0;
        if (position >= count) return uint32_t(-1);
        const size_t n = std::min<size_t>(fetch, count - position);
        if (buffer) memcpy(buffer, items.data() + position * itemSize, n * itemSize);
        position += n;
        return uint32_t(n);
    }
};

uint32_t XamCreateItemEnumerator(std::vector<uint8_t> items, uint32_t itemSize, uint32_t fetch)
{
    auto* e = CreateKernelObject<XamItemEnumerator>();
    e->items = std::move(items);
    e->itemSize = itemSize;
    e->fetch = std::max<uint32_t>(fetch, 1);
    return GetKernelHandle(e);
}

// GoW2: an enumerator with no items (achievements, stats: nothing to list offline)
uint32_t XamCreateEmptyEnumerator()
{
    static std::vector<_XCONTENT_DATA> none;
    return GetKernelHandle(CreateKernelObject<XamEnumerator<decltype(none.begin())>>(1, sizeof(_XCONTENT_DATA), none.begin(), none.end()));
}

uint32_t XamEnumerate(uint32_t hEnum, uint32_t dwFlags, void* pvBuffer, uint32_t cbBuffer, be<uint32_t>* pcItemsReturned, XXOVERLAPPED* pOverlapped)
{
    auto* enumerator = IsKernelObject(hEnum) ? dynamic_cast<XamEnumeratorBase*>(GetKernelObject(hEnum)) : nullptr;
    if (!enumerator)
        return 6; // ERROR_INVALID_HANDLE
    const auto count = enumerator->Next(pvBuffer);
    const uint32_t result = count == -1 ? ERROR_NO_MORE_FILES : ERROR_SUCCESS;

    if (pcItemsReturned)
        *pcItemsReturned = count == -1 ? 0 : count;
    if (pOverlapped)
        return CompleteOverlapped(pOverlapped, result, count == -1 ? 0 : count);

    return result;
}

uint32_t XamContentCreateEx(uint32_t dwUserIndex, const char* szRootName, const XCONTENT_DATA* pContentData,
    uint32_t dwContentFlags, be<uint32_t>* pdwDisposition, be<uint32_t>* pdwLicenseMask,
    uint32_t dwFileCacheSize, uint64_t uliContentSize, PXXOVERLAPPED pOverlapped)
{
    const auto& registry = gContentRegistry[pContentData->dwContentType - 1];
    const auto exists = registry.contains(StringHash(pContentData->szFileName));
    const auto mode = dwContentFlags & 0xF;

    if (mode == CREATE_ALWAYS)
    {
        if (pdwDisposition)
            *pdwDisposition = XCONTENT_NEW;

        if (!exists)
        {
            std::filesystem::path rootPath;

            if (pContentData->dwContentType == XCONTENTTYPE_SAVEDATA)
            {
                rootPath = GetSavePath(true);
            }
            else if (pContentData->dwContentType == XCONTENTTYPE_DLC)
            {
                rootPath = GetGamePath() / "dlc";
            }
            else
            {
                rootPath = GetGamePath();
            }

            const std::string root = (const char*)rootPath.u8string().c_str();
            XamRegisterContent(*pContentData, root);

            std::error_code ec;
            std::filesystem::create_directory(rootPath, ec);

            XamRootCreate(szRootName, root);
        }
        else
        {
            XamRootCreate(szRootName, registry.find(StringHash(pContentData->szFileName))->second.szRoot);
        }

        return ERROR_SUCCESS;
    }

    if (mode == OPEN_EXISTING)
    {
        if (exists)
        {
            if (pdwDisposition)
                *pdwDisposition = XCONTENT_EXISTING;

            XamRootCreate(szRootName, registry.find(StringHash(pContentData->szFileName))->second.szRoot);

            return ERROR_SUCCESS;
        }
        else
        {
            if (pdwDisposition)
                *pdwDisposition = XCONTENT_NEW;

            return ERROR_PATH_NOT_FOUND;
        }
    }

    return ERROR_PATH_NOT_FOUND;
}

uint32_t XamContentClose(const char* szRootName, XXOVERLAPPED* pOverlapped)
{
    gRootMap.erase(StringHash(szRootName));
    return 0;
}

uint32_t XamContentGetDeviceData(uint32_t DeviceID, XDEVICE_DATA* pDeviceData)
{
    pDeviceData->DeviceID = DeviceID;
    pDeviceData->DeviceType = XCONTENTDEVICETYPE_HDD;
    pDeviceData->ulDeviceBytes = 0x10000000;
    pDeviceData->ulDeviceFreeBytes = 0x10000000;
    pDeviceData->wszName[0] = 'S';
    pDeviceData->wszName[1] = 'o';
    pDeviceData->wszName[2] = 'n';
    pDeviceData->wszName[3] = 'i';
    pDeviceData->wszName[4] = 'c';
    pDeviceData->wszName[5] = '\0';

    return 0;
}

uint32_t XamInputGetCapabilities(uint32_t unk, uint32_t userIndex, uint32_t flags, XAMINPUT_CAPABILITIES* caps)
{
    uint32_t result = hid::GetCapabilities(userIndex, caps);

    if (result == ERROR_SUCCESS)
    {
        ByteSwapInplace(caps->Flags);
        ByteSwapInplace(caps->Gamepad.wButtons);
        ByteSwapInplace(caps->Gamepad.sThumbLX);
        ByteSwapInplace(caps->Gamepad.sThumbLY);
        ByteSwapInplace(caps->Gamepad.sThumbRX);
        ByteSwapInplace(caps->Gamepad.sThumbRY);
        ByteSwapInplace(caps->Vibration.wLeftMotorSpeed);
        ByteSwapInplace(caps->Vibration.wRightMotorSpeed);
    }

    return result;
}

uint32_t XamInputGetState(uint32_t userIndex, uint32_t flags, XAMINPUT_STATE* state)
{
    memset(state, 0, sizeof(*state));

    if (hid::IsInputAllowed())
        hid::GetState(userIndex, state);

    // keyboard mapping lives in hid.cpp (GoW2 runtime)

    state->Gamepad.wButtons &= ~hid::g_prohibitedButtons;

    if (hid::g_isLeftStickProhibited)
    {
        state->Gamepad.sThumbLX = 0;
        state->Gamepad.sThumbLY = 0;
    }

    if (hid::g_isRightStickProhibited)
    {
        state->Gamepad.sThumbRX = 0;
        state->Gamepad.sThumbRY = 0;
    }

    ByteSwapInplace(state->Gamepad.wButtons);
    ByteSwapInplace(state->Gamepad.sThumbLX);
    ByteSwapInplace(state->Gamepad.sThumbLY);
    ByteSwapInplace(state->Gamepad.sThumbRX);
    ByteSwapInplace(state->Gamepad.sThumbRY);

    return ERROR_SUCCESS;
}

uint32_t XamInputSetState(uint32_t userIndex, uint32_t flags, XAMINPUT_VIBRATION* vibration)
{
    if (!hid::IsInputDeviceController() || !Config::Vibration)
        return ERROR_SUCCESS;

    ByteSwapInplace(vibration->wLeftMotorSpeed);
    ByteSwapInplace(vibration->wRightMotorSpeed);

    return hid::SetState(userIndex, vibration);
}
