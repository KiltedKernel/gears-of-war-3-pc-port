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
#include <set>
#include <mutex>
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
    std::atomic<uint32_t> g_chapterGame{ 0 };   // the GearGameSP_Base while a new chapter's map is loaded (ChapterKeeper)
    std::atomic<bool> g_restore{ false };       // a checkpoint has loaded: its sections back, on the game thread

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
void RestoreSectionsOnGameThread();   // below

void OnGameThread()
{
    if (g_restore && GetCurrentThreadId() == g_mainThreadId) { g_restore = false; RestoreSectionsOnGameThread(); }
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

namespace
{
    // GearGameSP_Base.CurrentChapter comes from the URL's ?chapter= (InitGame); a load from a checkpoint (Continue)
    // has none, and the chapter was 0 then: Restart Chapter (GearPC.RestartChapter: geargame_p?chapter=
    // <CurrentChapter>) loaded the prologue, and the pause menu named it (GearGRI_SP.ChapterNum). While a new
    // chapter's map is loaded, both are kept at its number.
    void ChapterKeeper()
    {
        uint32_t lastGame = 0;
        int chapter = -1;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::seconds(2));
            if (!g_done || g_chapters.empty()) continue;
            static const uint32_t gameClass = ue3::FindClass("GearGameSP_Base"), griClass = ue3::FindClass("GearGRI_SP");
            if (!gameClass) continue;
            const auto games = ue3::FindInstancesOf(gameClass);
            const uint32_t game = games.empty() ? 0 : games.front();
            if (!game) { lastGame = 0; g_chapterGame = 0; continue; }
            if (game != lastGame)                     // a new map: which chapter's (its package loaded)
            {
                lastGame = game; chapter = -1;
                for (auto& [n, map] : g_chapters)
                    if (const uint32_t p = ue3::FindObject(map.c_str()); p && ue3::ObjectName(ue3::ClassOf(p)) == "Package") chapter = n;
                g_chapterGame = chapter >= 0 ? game : 0;
            }
            if (chapter < 0) continue;
            // (CurrentChapter is an int, GearGRI_SP.ChapterNum a byte: written at their own sizes; the first version
            // wrote a byte into the int's top: chapter 536870944, and the restart found no map)
            static const int off = ue3::PropertyOffset(gameClass, "CurrentChapter"), size = ue3::PropertySize(gameClass, "CurrentChapter");
            if (off < 0 || (size != 1 && size != 4)) continue;
            const uint32_t at = game + uint32_t(off);
            const uint32_t cur = size == 4 ? ue3::Read32(at) : *(uint8_t*)g_memory.Translate(at);
            if (cur != uint32_t(chapter))
            {
                LOGFN("campaign chapters: the game's chapter {} -> {} (the map is chapter {}'s)", cur, chapter, chapter);
                if (size == 4) *(uint32_t*)g_memory.Translate(at) = ByteSwap(uint32_t(chapter));
                else *(uint8_t*)g_memory.Translate(at) = uint8_t(chapter);
            }
            if (griClass)
                for (uint32_t gri : ue3::FindInstancesOf(griClass))
                {
                    static const int goff = ue3::PropertyOffset(griClass, "ChapterNum"), gsize = ue3::PropertySize(griClass, "ChapterNum");
                    if (goff < 0) continue;
                    if (gsize == 4) *(uint32_t*)g_memory.Translate(gri + uint32_t(goff)) = ByteSwap(uint32_t(chapter));
                    else if (gsize == 1) *(uint8_t*)g_memory.Translate(gri + uint32_t(goff)) = uint8_t(chapter);
                }
        }
    }
}

namespace
{
    // Checkpoints and Kismet-streamed sections. A checkpoint load (Reload Last Checkpoint, death: GearPC.LoadCheckpoint,
    // in place) leaves every LevelStreamingKismet of the map unloaded (the save lists the levels with both flags 0,
    // Gears 3's own saves too): Gears 3's chapters stream by LevelStreamingVolumes, which bring their sections back by
    // the player's position, but a converted chapter streams by its Kismet alone and reloaded into the sky. So while a
    // new chapter's map is loaded its sections' flags are followed, and once a checkpoint has loaded
    // (GearGameSP_Base.CheckpointLoadComplete makes a new AIVisMan) every section streamed in when it was saved
    // (GearPC.LastCheckpointSaveTime changed) or just before the load is streamed back in, blocking on the load (a
    // section streamed in since the checkpoint may hold where it puts the player back). The chapter's first checkpoint
    // is saved at its start, before its Kismet shows the start's sections (the prison's opening; it also spawns the
    // player's Gears 3 character): those sections come back shown, and the opening plays again.
    struct Section { bool loaded = false, visible = false; };
    using Sections = std::map<uint32_t, Section>;   // by LevelStreamingKismet
    std::mutex g_sectionsMutex;
    Sections g_atSave, g_beforeLoad;
    bool g_atStart = true;                      // no checkpoint saved since the chapter started

    struct StreamingProps
    {
        uint32_t cls = 0; int name = -1, loaded = -1, visible = -1, block = -1;
        uint32_t loadedMask = 0, visibleMask = 0, blockMask = 0;
        bool Find()
        {
            if (cls) return name >= 0 && loaded >= 0 && visible >= 0 && loadedMask && visibleMask;
            cls = ue3::FindClass("LevelStreamingKismet");
            if (!cls) return false;
            name = ue3::PropertyOffset(cls, "PackageName");
            loaded = ue3::PropertyOffset(cls, "bShouldBeLoaded"); loadedMask = ue3::BoolMask(cls, "bShouldBeLoaded");
            visible = ue3::PropertyOffset(cls, "bShouldBeVisible"); visibleMask = ue3::BoolMask(cls, "bShouldBeVisible");
            block = ue3::PropertyOffset(cls, "bShouldBlockOnLoad"); blockMask = ue3::BoolMask(cls, "bShouldBlockOnLoad");
            return name >= 0 && loaded >= 0 && visible >= 0 && loadedMask && visibleMask;
        }
        Section Read(uint32_t ls) const
        {
            return { (ue3::Read32(ls + uint32_t(loaded)) & loadedMask) != 0, (ue3::Read32(ls + uint32_t(visible)) & visibleMask) != 0 };
        }
        std::string NameOf(uint32_t ls) const { return ue3::Name(ue3::Read32(ls + uint32_t(name))); }
    } g_sp;

    std::string List(const Sections& sections)
    {
        std::string list;
        for (auto& [ls, s] : sections)
            if (s.loaded || s.visible) list += fmt::format(" {}{}", g_sp.NameOf(ls), s.visible ? "" : "(hidden)");
        return list;
    }

    void CheckpointStreaming()
    {
        uint32_t lastGame = 0, lastVisMan = 0, pc = 0;
        float lastSave = 0;
        std::vector<uint32_t> streams;
        int ticks = 0;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            const uint32_t game = g_chapterGame;
            if (!game) { lastGame = 0; continue; }
            static const uint32_t gameClass = ue3::FindClass("GearGameSP_Base"), pcClass = ue3::FindClass("GearPC");
            static const int visManOff = ue3::PropertyOffset(gameClass, "AIVisMan");
            static const int saveTimeOff = pcClass ? ue3::PropertyOffset(pcClass, "LastCheckpointSaveTime") : -1;
            if (visManOff < 0 || saveTimeOff < 0 || !g_sp.Find())
            {
                static bool warned = false;
                if (!warned) LOGFN_WARNING("campaign chapters: checkpoint streaming off (properties not found: {} {} {})", visManOff, saveTimeOff, g_sp.name);
                warned = true;
                continue;
            }
            const bool start = game != lastGame;
            if (start || ++ticks % 20 == 0)
            {
                const auto pcs = ue3::FindInstancesOf(pcClass);
                pc = pcs.empty() ? 0 : pcs.front();
                streams = ue3::FindInstancesOf(g_sp.cls);
            }
            const uint32_t visMan = ue3::Read32(game + uint32_t(visManOff));
            const float saveTime = pc ? ue3::ReadFloat(pc + uint32_t(saveTimeOff)) : 0.f;
            Sections now;
            bool anyVisible = false;
            for (uint32_t ls : streams) { now[ls] = g_sp.Read(ls); anyVisible |= now[ls].visible; }
            std::lock_guard lock(g_sectionsMutex);
            if (start)
            {
                lastGame = game; lastVisMan = visMan; g_beforeLoad.clear();
                g_atSave = now;          // a chapter's first checkpoint can be saved on its first frame, before the map is known here
                g_atStart = true;
                lastSave = saveTime;
                LOGFN("campaign chapters: {} sections; at the start:{}", streams.size(), List(now));
            }
            else if (pc && saveTime != lastSave && saveTime > 0)
            {
                lastSave = saveTime; g_atSave = now; g_atStart = false;
                LOGFN("campaign chapters: checkpoint saved with{}", List(now));
            }
            if (visMan && visMan != lastVisMan) { lastVisMan = visMan; g_restore = true; }   // a checkpoint loaded
            else if (anyVisible) g_beforeLoad = now;    // (not once the load has cleared them)
        }
    }

    void RestoreSections()
    {
        Sections sections;
        {
            std::lock_guard lock(g_sectionsMutex);
            sections = g_atSave;
            if (g_atStart)          // the chapter's first checkpoint: its start again, shown (the start's Kismet shows them
                for (auto& [ls, s] : sections) s.visible |= s.loaded;   // after the save; its opening then plays again)
            else
                for (auto& [ls, s] : g_beforeLoad) { sections[ls].loaded |= s.loaded; sections[ls].visible |= s.visible; }
        }
        auto set = [](uint32_t ls, int off, uint32_t mask, bool on)
        {
            if (off < 0 || !mask) return;
            const uint32_t v = ue3::Read32(ls + uint32_t(off));
            *(uint32_t*)g_memory.Translate(ls + uint32_t(off)) = ByteSwap(on ? (v | mask) : (v & ~mask));
        };
        for (auto& [ls, s] : sections)
        {
            if (!s.loaded && !s.visible) continue;
            if (!ue3::IsA(ls, g_sp.cls)) continue;      // (gone)
            set(ls, g_sp.loaded, g_sp.loadedMask, true);
            set(ls, g_sp.visible, g_sp.visibleMask, s.visible);
            set(ls, g_sp.block, g_sp.blockMask, true);
        }
        LOGFN("campaign chapters: checkpoint loaded: streamed back in:{}", List(sections));
    }
}

void RestoreSectionsOnGameThread() { RestoreSections(); }

namespace
{
    // A converted chapter keeps objects nothing else refers to in Kismet object lists named Trilogy*: the shared
    // textures (TrilogySharedTextures, the persistent level) and each carrier's character classes
    // (TrilogyCarrierClasses). That held over garbage collection while playing, but not over a checkpoint load: after
    // Reload Last Checkpoint the replayed opening drew Marcus and Dom untextured and the flashback Dom's factory got a
    // None class (null + 0xEC, sub_822C3C20 from sub_82A61AA8). So while the chapter is loaded, each listed object
    // joins the root set (UObject flags, low word at +12: RF_RootSet 0x4000), and leaves it when the chapter ends.
    // GOW3_NO_TRILOGY_ROOTS=1: off.
    void TrilogyRoots()
    {
        if (getenv("GOW3_NO_TRILOGY_ROOTS")) return;
        constexpr uint32_t kFlagsLow = 12, kRootSet = 0x4000;
        std::set<uint32_t> rooted;
        auto setRoot = [](uint32_t o, bool on)
        {
            if (!ue3::Readable(o + kFlagsLow, 4)) return;
            uint32_t* flags = (uint32_t*)g_memory.Translate(o + kFlagsLow);
            *flags = ByteSwap(on ? (ByteSwap(*flags) | kRootSet) : (ByteSwap(*flags) & ~kRootSet));
        };
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            if (!g_chapterGame)
            {
                if (!rooted.empty())
                {
                    for (uint32_t o : rooted) setRoot(o, false);
                    LOGFN("campaign chapters: {} trilogy objects out of the root set", rooted.size());
                    rooted.clear();
                }
                continue;
            }
            static const uint32_t listClass = ue3::FindClass("SeqVar_ObjectList");
            static const int listOff = listClass ? ue3::PropertyOffset(listClass, "ObjList") : -1;
            if (!listClass || listOff < 0) continue;
            const size_t before = rooted.size();
            for (uint32_t list : ue3::FindInstancesOf(listClass))
            {
                if (ue3::ObjectName(list).rfind("Trilogy", 0) != 0) continue;
                const uint32_t data = ue3::Read32(list + uint32_t(listOff)), count = ue3::Read32(list + uint32_t(listOff) + 4);
                if (!data || count > 100000) continue;
                for (uint32_t k = 0; k < count; k++)
                {
                    const uint32_t o = ue3::Read32(data + 4 * k);
                    if (o && rooted.insert(o).second) setRoot(o, true);
                }
            }
            if (rooted.size() != before)
                LOGFN("campaign chapters: {} trilogy objects in the root set (+{})", rooted.size(), rooted.size() - before);
        }
    }
}

bool InNewChapter() { return g_chapterGame != 0; }      // a new chapter's map is loaded (ChapterKeeper, every 2 s)

void Start()
{
    if (getenv("GOW3_NO_CAMPAIGN_CHAPTERS")) return;
    std::thread(Worker).detach();
    std::thread(ChapterKeeper).detach();
    std::thread(CheckpointStreaming).detach();
    std::thread(TrilogyRoots).detach();
}
}
