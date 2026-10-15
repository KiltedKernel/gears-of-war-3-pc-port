// Sessions (XSession*): the session object handle (XamSessionCreateHandle / XamSessionRefObjByHandle) and the XGI
// session messages (x360_xmsg.cpp hands app 0xFB's 0x000B00xx here). Xenia only stubs these (its sessions are offline);
// system link needs XSessionCreate to give the host's session a real XSESSION_INFO: the title advertises it on the
// LAN, and a joining player connects to its hostAddress (the host's virtual address, net/vnet.cpp) with its key.
//   XSESSION_INFO: XNKID sessionID (8) + XNADDR hostAddress (36) + XNKEY keyExchangeKey (16)
//   XNKID's top 3 bits say the kind: 000 = system link (online peer would be 100)
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include "function.h"
#include "memory.h"
#include "heap.h"
#include "xdm.h"
#include <os/logger.h>
#include <net/vnet.h>
#include <map>
#include <mutex>
#include <random>

namespace x360k
{
namespace
{
    constexpr uint32_t X_ERROR_INVALID_HANDLE = 6;
    constexpr uint32_t XSESSION_CREATE_HOST = 0x1;
    constexpr uint32_t kObjectSize = 0x200;            // opaque to the title: it only passes the pointer back

    std::mutex g_mutex;
    std::map<uint32_t, uint32_t> g_objects;            // handle -> guest address of the session object
    uint32_t g_nextHandle = 0xF5E50000;

    uint32_t Be32(uint32_t guest) { return ByteSwap(*(uint32_t*)g_memory.Translate(guest)); }

    void Random(uint8_t* out, size_t n)
    {
        static std::random_device rd;
        for (size_t i = 0; i < n; i++) out[i] = uint8_t(rd());
    }

    // XNADDR as XNetGetTitleXnAddr gives it (x360_net.cpp): the virtual address, and a MAC that follows it
    void OwnXnAddr(uint8_t* out)
    {
        memset(out, 0, 36);
        const uint32_t vip = vnet::Enabled() ? vnet::OwnVip() : 0x7F000001;
        const uint32_t be = ByteSwap(vip);
        memcpy(out + 0, &be, 4);                       // ina
        memcpy(out + 4, &be, 4);                       // inaOnline
        const uint8_t mac[6] = { 0x00, 0x22, 0x48, uint8_t(vip >> 16), uint8_t(vip >> 8), uint8_t(vip) };
        memcpy(out + 10, mac, 6);                      // abEnet
        memcpy(out + 16, mac, 6);                      // abOnline
    }
}

uint32_t XamSessionCreateHandle(be<uint32_t>* handle)
{
    void* p = g_userHeap.Alloc(kObjectSize);
    memset(p, 0, kObjectSize);
    std::lock_guard lock(g_mutex);
    const uint32_t h = g_nextHandle++;
    g_objects[h] = g_memory.MapVirtual(p);
    *handle = h;
    return 0;
}

uint32_t XamSessionRefObjByHandle(uint32_t handle, be<uint32_t>* object)
{
    std::lock_guard lock(g_mutex);
    const auto it = g_objects.find(handle);
    if (it == g_objects.end()) { LOGFN_WARNING("XamSessionRefObjByHandle({:08X}): unknown handle", handle); return X_ERROR_INVALID_HANDLE; }
    *object = it->second;
    return 0;
}

// an XGI session message; false: not a session message (the caller answers it)
bool XgiSession(uint32_t message, uint32_t buffer, uint32_t length, uint32_t& result)
{
    result = 0;
    switch (message)
    {
    case 0x000B0010:   // XSessionCreate { session, flags, public slots, private slots, xuid ptr, XSESSION_INFO*, nonce* }
    {
        if (!buffer || length < 0x1C) return true;
        const uint32_t flags = Be32(buffer + 4), info = Be32(buffer + 0x14), nonce = Be32(buffer + 0x18);
        const bool host = (flags & XSESSION_CREATE_HOST) != 0;
        if (host && info)
        {
            uint8_t* si = (uint8_t*)g_memory.Translate(info);
            Random(si, 8);
            si[0] &= 0x1F;                             // XNKID: system link
            OwnXnAddr(si + 8);
            Random(si + 44, 16);                       // key exchange key
        }
        if (host && nonce) Random((uint8_t*)g_memory.Translate(nonce), 8);
        if (vnet::Enabled()) vnet::NoteSession();     // this game's hub (if it runs one) now stays where it is
        LOGFN("XSessionCreate: flags {:X}, slots {} public / {} private, {}", flags, Be32(buffer + 8), Be32(buffer + 0xC),
            host ? "host (session info made)" : "joining the advertised session");
        return true;
    }
    case 0x000B0011:   // XSessionDelete
    case 0x000B0012:   // XSessionJoinLocal
    case 0x000B0013:   // XSessionJoinRemote
    case 0x000B0014:   // XSessionStart
    case 0x000B0015:   // XSessionEnd
    case 0x000B0016:   // XSessionLeaveLocal
    case 0x000B0017:   // XSessionLeaveRemote
    case 0x000B0018:   // XSessionModify
    case 0x000B0019:   // XSessionMigrateHost
    {
        static std::map<uint32_t, int> logged;
        if (logged[message]++ < 5) LOGFN("XSession message {:08X} ({} bytes): accepted", message, length);
        return true;
    }
    case 0x000B0021:   // XUserReadStats { title, xuid count, xuids*, spec count, specs*, results size, results* }
    {
        // nobody has stats yet: every requested player gets a row with each asked column empty (XUSER_DATA_TYPE_NULL).
        // XUSER_STATS_READ_RESULTS { views, view* } -> XUSER_STATS_VIEW { id, total rows, rows, row* } (16) ->
        // XUSER_STATS_ROW { xuid, rank, -, rating (8), gamertag[16], columns, column* } (48) ->
        // XUSER_STATS_COLUMN { id (2), -, XUSER_DATA { type, -, value (8) } } (24); a spec: { view, count, ids[64] }
        if (!buffer || length < 0x1C) { result = 0x57; return true; }   // ERROR_INVALID_PARAMETER
        {
            std::string words;
            for (uint32_t i = 0; i < std::min<uint32_t>(length, 0x40); i += 4) words += fmt::format(" {:08X}", Be32(buffer + i));
            static int dumped = 0;
            const uint32_t sp = Be32(buffer + 0x10);
            if (sp) { words += " | spec"; for (uint32_t i = 0; i < 16; i += 4) words += fmt::format(" {:08X}", Be32(sp + i)); }
            if (dumped++ < 4) LOGFN("XUserReadStats ({} bytes):{}", length, words);
        }
        const uint32_t xuidCount = Be32(buffer + 4), xuids = Be32(buffer + 8), specCount = Be32(buffer + 0xC),
                       specs = Be32(buffer + 0x10), have = Be32(buffer + 0x14), results = Be32(buffer + 0x18);
        constexpr uint32_t kSpecSize = 8 + 64 * 2;
        uint32_t need = 8 + specCount * 16;
        for (uint32_t s = 0; s < specCount; s++)
            need += xuidCount * (48 + std::min<uint32_t>(Be32(specs + s * kSpecSize + 4), 64) * 24);
        if (!results || have < need)
        {
            LOGFN_WARNING("XUserReadStats: {} bytes given, {} needed", have, need);
            result = 0x7A;                                // ERROR_INSUFFICIENT_BUFFER
            return true;
        }
        uint8_t* base = (uint8_t*)g_memory.Translate(results);
        memset(base, 0, need);
        auto put32 = [&](uint32_t guest, uint32_t v) { *(be<uint32_t>*)g_memory.Translate(guest) = v; };
        auto put16 = [&](uint32_t guest, uint16_t v) { *(be<uint16_t>*)g_memory.Translate(guest) = v; };
        uint32_t views = results + 8, next = views + specCount * 16;
        put32(results, specCount);
        put32(results + 4, views);
        for (uint32_t s = 0; s < specCount; s++)
        {
            const uint32_t spec = specs + s * kSpecSize, columns = std::min<uint32_t>(Be32(spec + 4), 64);
            const uint32_t view = views + s * 16, rows = next;
            next += xuidCount * 48;
            put32(view, Be32(spec));                      // view id
            put32(view + 4, 0);                           // nobody on the leaderboard
            put32(view + 8, xuidCount);
            put32(view + 12, rows);
            for (uint32_t r = 0; r < xuidCount; r++)
            {
                const uint32_t row = rows + r * 48, cols = next;
                next += columns * 24;
                memcpy(g_memory.Translate(row), g_memory.Translate(xuids + r * 8), 8);
                put32(row + 40, columns);
                put32(row + 44, cols);
                for (uint32_t c = 0; c < columns; c++)
                {
                    put16(cols + c * 24, *(be<uint16_t>*)g_memory.Translate(spec + 8 + c * 2));
                    *(uint8_t*)g_memory.Translate(cols + c * 24 + 8) = 0xFF;   // XUSER_DATA_TYPE_NULL
                }
            }
        }
        static int logged = 0;
        if (logged++ < 5) LOGFN("XUserReadStats: {} players x {} views: empty rows", xuidCount, specCount);
        return true;
    }
    default:
        return false;
    }
}

} // namespace x360k

GUEST_FUNCTION_HOOK(__imp__XamSessionCreateHandle, x360k::XamSessionCreateHandle);
GUEST_FUNCTION_HOOK(__imp__XamSessionRefObjByHandle, x360k::XamSessionRefObjByHandle);
