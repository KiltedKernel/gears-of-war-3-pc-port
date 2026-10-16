// New campaign chapters (the trilogy's, and mods'): each loads its own map.
//
// Gears 3 starts a chapter as GearGame_P?game=...GearGameSP?chapter=N (GearMenuGame.BuildCampaignURL), and native
// code loads the map named by Default__GearTypes.ChapterLevelNames[N]: 31 FNames in GearGame.xxx, one per chapter up
// to CHAP_Gameover. The menu lists chapters from config ([ChapterN GearCampaignChapterData] in Xbox360-GearGame.ini;
// their MapName is shown, not loaded). EChapterPoint reserves CHAP_DLC_1..18 (32..49) for downloadable campaigns.
// So a chapter N >= 32 declared in config (a mod's merged Coalesced_INT.bin, tools/campaign.py) gets its MapName put
// into ChapterLevelNames[N] here, at run time: GearGame.xxx stays the disc's (it is a whole-file LZX package, and
// tools/xpack cannot decode all of it).
//   FName construction: sub_82302F28(FName* out r3, const TCHAR* name r4 (UTF-16BE), EFindName r5 = 1 FNAME_Add), as
//   the static initializer at 824F10A0 builds the engine's key names.
// GOW3_NO_CAMPAIGN_CHAPTERS=1 turns it off.
#include <stdafx.h>
#include <ue3/reflection.h>
#include <kernel/function.h>
#include <kernel/heap.h>
#include <kernel/memory.h>
#include <kernel/mods.h>
#include <user/paths.h>
#include <os/logger.h>
#include <atomic>
#include <fstream>
#include <map>
#include <thread>

extern DWORD g_mainThreadId;   // main.cpp

namespace campaign_chapters
{
namespace
{
    constexpr uint32_t kFNameCtor = 0x82302F28;
    constexpr int kFirstChapter = 32, kLastChapter = 49;

    std::map<int, std::string> g_chapters;      // chapter number -> map name
    std::atomic<uint32_t> g_types{ 0 };         // Default__GearTypes, once found
    std::atomic<bool> g_done{ false };

    // the packed config: big-endian int32 counts, strings as int32 length (negative: UTF-16LE units incl. NUL)
    struct Reader
    {
        const std::vector<uint8_t>& d; size_t p = 0;
        bool ok = true;
        int32_t I32()
        {
            if (p + 4 > d.size()) { ok = false; return 0; }
            const int32_t v = int32_t(uint32_t(d[p]) << 24 | uint32_t(d[p + 1]) << 16 | uint32_t(d[p + 2]) << 8 | d[p + 3]);
            p += 4; return v;
        }
        std::string Str()
        {
            const int32_t n = I32();
            std::string s;
            if (n > 0) { if (p + n > d.size()) { ok = false; return s; } s.assign((const char*)&d[p], n - 1); p += n; }
            else if (n < 0)
            {
                const size_t units = size_t(-n);
                if (p + 2 * units > d.size()) { ok = false; return s; }
                for (size_t i = 0; i + 1 < units; i++) { const uint16_t c = uint16_t(d[p + 2 * i] | d[p + 2 * i + 1] << 8); s += c < 128 ? char(c) : '?'; }
                p += 2 * units;
            }
            return s;
        }
    };

    void ReadConfig()
    {
        const std::string rel = "\\GearGame\\CookedXbox360\\Coalesced_INT.bin";
        std::filesystem::path path = mods::Resolve(rel, GetGamePath());
        if (path.empty()) path = GetGamePath() / "GearGame" / "CookedXbox360" / "Coalesced_INT.bin";
        std::ifstream f(path, std::ios::binary);
        std::vector<uint8_t> d((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
        Reader r{ d };
        const int32_t files = r.I32();
        for (int32_t fi = 0; fi < files && r.ok; fi++)
        {
            r.Str();
            const int32_t sections = r.I32();
            for (int32_t si = 0; si < sections && r.ok; si++)
            {
                const std::string sec = r.Str();
                const int32_t keys = r.I32();
                int num = -1;
                if (sec.rfind("Chapter", 0) == 0 && sec.find(" GearCampaignChapterData") != std::string::npos)
                    num = atoi(sec.c_str() + 7);
                for (int32_t k = 0; k < keys && r.ok; k++)
                {
                    const std::string key = r.Str(), value = r.Str();
                    if (num >= kFirstChapter && num <= kLastChapter && key == "MapName" && !value.empty()) g_chapters[num] = value;
                }
            }
        }
        if (!r.ok) LOGN_WARNING("campaign chapters: config unreadable");
        for (auto& [n, m] : g_chapters) LOGFN("campaign chapters: chapter {} -> {} ({})", n, m, path.string());
    }

    void Worker()
    {
        ReadConfig();
        if (g_chapters.empty()) return;
        // polled closely: a chapter URL on the command line (GOW3_COMMAND_LINE) reads the table seconds after
        // GearGame.xxx has loaded, and a table filled later sends that boot nowhere
        while (!ue3::Init()) std::this_thread::sleep_for(std::chrono::milliseconds(50));
        for (int tries = 0; tries < 6000 && !g_types; tries++)
        {
            g_types = ue3::FindObject("Default__GearTypes");
            if (!g_types) std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        if (!g_types) LOGN_WARNING("campaign chapters: Default__GearTypes not found");
    }

    uint32_t Alloc(uint32_t size) { return g_memory.MapVirtual(g_userHeap.Alloc(size)); }
    void Put32(uint32_t a, uint32_t v) { *(uint32_t*)g_memory.Translate(a) = ByteSwap(v); }
    uint32_t Get32(uint32_t a) { return ByteSwap(*(uint32_t*)g_memory.Translate(a)); }

    // an FName (index, number) for a string, through the game's own constructor
    void MakeName(const std::string& s, uint32_t& index, uint32_t& number)
    {
        static uint32_t out = 0, str = 0;
        if (!out) { out = Alloc(8); str = Alloc(256); }
        const size_t n = std::min<size_t>(s.size(), 120);
        for (size_t i = 0; i < n; i++) *(uint16_t*)g_memory.Translate(str + 2 * uint32_t(i)) = ByteSwap(uint16_t(uint8_t(s[i])));
        *(uint16_t*)g_memory.Translate(str + 2 * uint32_t(n)) = 0;
        GuestToHostFunction<uint32_t>(kFNameCtor, out, str, 1u);
        index = Get32(out); number = Get32(out + 4);
    }
}

// from the game thread's controller poll (hid::GetState), once Default__GearTypes is known
void OnGameThread()
{
    if (g_done || !g_types || GetCurrentThreadId() != g_mainThreadId) return;
    g_done = true;
    const uint32_t types = g_types;
    const int off = ue3::PropertyOffset(ue3::ClassOf(types), "ChapterLevelNames");
    if (off < 0) { LOGN_WARNING("campaign chapters: no ChapterLevelNames"); return; }
    const uint32_t arr = types + uint32_t(off);
    const uint32_t oldData = Get32(arr), oldCount = Get32(arr + 4);
    const uint32_t count = std::max<uint32_t>(oldCount, uint32_t(g_chapters.rbegin()->first + 1));
    const uint32_t data = Alloc(8 * count);
    for (uint32_t k = 0; k < count; k++)
    {
        const bool old = k < oldCount && oldData;
        Put32(data + 8 * k, old ? Get32(oldData + 8 * k) : 0);          // 'None' in the gap (CHAP_Gameover...)
        Put32(data + 8 * k + 4, old ? Get32(oldData + 8 * k + 4) : 0);
    }
    for (auto& [n, map] : g_chapters)
    {
        uint32_t index = 0, number = 0;
        MakeName(map, index, number);
        Put32(data + 8 * uint32_t(n), index); Put32(data + 8 * uint32_t(n) + 4, number);
        LOGFN("campaign chapters: ChapterLevelNames[{}] = {} (name {} {})", n, ue3::Name(index), index, number);
    }
    Put32(arr, data); Put32(arr + 4, count); Put32(arr + 8, count);
    LOGFN("campaign chapters: {} chapter maps (was {})", count, oldCount);
}

void Start()
{
    if (getenv("GOW3_NO_CAMPAIGN_CHAPTERS")) return;
    std::thread(Worker).detach();
}
}
