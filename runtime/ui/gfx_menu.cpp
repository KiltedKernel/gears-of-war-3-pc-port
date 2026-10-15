// Mouse in Gears of War 3's menus. The menus are Scaleform movies (UGFxMoviePlayer subclasses named UIScene_*).
// A worker finds the movie players through UE3 reflection (ue3/reflection.cpp, Gears 3 layout) and reads their flags
// (bMovieIsOpen, bAllowInput, bCaptureInput); while a menu movie is open and takes input, the mouse is released
// (hid::SetMenuMode), left click = A, right click = B, wheel = d-pad, Esc = B.
//
// Hover: the option under the cursor is selected while the cursor moves, as a mouse-over does on PC.
//  - The cursor goes to the menu movies as Scaleform mouse events, so ActionScript knows where the mouse is.
//    UGFxMoviePlayer +60 (pMovie) -> FGFxMovie: +20/+24 movie size, +52 GFxMovieView. GFxMovieView::HandleEvent is
//    vtable slot 45 (82BCD738); GFxMouseEvent {+0 type 1 move, +4 x, +8 y, +0x10 button, +0x14 mouse index <
//    view+0x9B4}.
//  - ActionScript is reached through the scene's own UGFxMoviePlayer virtuals (an FString path each):
//    GetVariableNumber (vtable +0x148, execGetVariableNumber 82911D90) and Invoke (+0x190, execInvoke 82912FB0).
//  - The menus are CLIK ScrollingLists (gears.controls.MainMenuList, OptionsMenuScrollingList, ...), and the one
//    that takes the d-pad has keyboard focus: Selection.getFocus() names it. Its renderers are list.renderers.N (also
//    when they are placed beside the list, as MenuItemN in the main menu); a renderer's row runs from its _y to the
//    next renderer's (_height is no good: rows with a "new" icon are taller than their row), and the renderer whose
//    row holds its own _ymouse is under the cursor; its `index` is the option. The list's selection is the
//    selectedIndex property; its setter (compiled as the method __set__selectedIndex) is what
//    ScrollingList.handleInput runs for a d-pad press: it moves the highlight, scrolls, updates the tooltip and tells
//    the game. Hover invokes it.
//  - Everything runs on the game's main thread (the input poll from its loop; another thread polls the pad too), once
//    per frame: first the hover pass for the previous frame's MouseMove (Scaleform has applied it in between), then
//    this frame's MouseMove. A pass costs about 1-2 ms of ActionScript lookups, and runs only while the cursor moves;
//    the focused list's path and row positions are cached for 250 ms.
//  - If setting the selection does not take, the highlight is walked there with d-pad steps instead.
//  - Only the top menu is considered: a menu opened over another captures input (bCaptureInput).
// GOW3_MENU_DEBUG=1 logs every change of the open movies; GOW3_NO_HOVER=1 turns hover off; GOW3_HOVER_TIMING=1 logs
// each selection with its time and the cost of the passes.
#include <stdafx.h>
#include <ue3/reflection.h>
#include <hid/hid.h>
#include <kernel/function.h>
#include <kernel/heap.h>
#include <kernel/memory.h>
#include <os/logger.h>
#include <user/settings.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

extern DWORD g_mainThreadId;   // main.cpp

namespace gfx_menu
{
namespace
{
    struct Flags { int offset = -1; uint32_t open = 0, input = 0, capture = 0; };
    struct Target { uint32_t view; float w, h; uint32_t scene; bool capture; };

    std::mutex g_targetsMutex;
    std::vector<Target> g_targets;                 // open menu movies (their GFxMovieView and size)
    std::atomic<bool> g_pendingB{ false };   // a right click: B, unless on the main menu's first page (as Esc)
    std::atomic<bool> g_pendingA{ false };   // a left click waiting for the selection to reach the hovered option
    uint64_t g_pendingSince = 0;

    // scenes that are open and take input without being a menu (always-on layers)
    bool IsMenuScene(const std::string& name)
    {
        // (UIScene_Alert counts as a menu only while it takes input: a dialog is up)
        static const char* notMenus[] = { "UIScene_Background", "UIScene_HUD", "UIScene_CalloutsContent" };
        for (const char* n : notMenus)
            if (name.rfind(n, 0) == 0) return false;
        return true;
    }

    bool IsVtable(uint32_t v) { return v >= 0x82000000 && v < 0x8224B000; }
    bool IsCode(uint32_t f) { return f >= 0x822A0000 && f < 0x82FEA8FC; }

    void Worker()
    {
        while (!ue3::Init()) std::this_thread::sleep_for(std::chrono::seconds(2));
        // the class can be found before its properties are linked (an early boot): wait for them
        uint32_t base = 0;
        for (int tries = 0;; tries++)
        {
            base = ue3::FindClass("GFxMoviePlayer");
            if (base && ue3::PropertyOffset(base, "bMovieIsOpen") >= 0 && ue3::PropertyOffset(base, "pMovie") >= 0) break;
            if (tries == 60) { LOGN_WARNING("gfx_menu: GFxMoviePlayer or its properties not found"); return; }
            std::this_thread::sleep_for(std::chrono::seconds(2));
        }
        Flags f;
        f.offset = ue3::PropertyOffset(base, "bMovieIsOpen");
        f.open = ue3::BoolMask(base, "bMovieIsOpen");
        f.input = ue3::BoolMask(base, "bAllowInput");
        f.capture = ue3::BoolMask(base, "bCaptureInput");
        const int pMovie = ue3::PropertyOffset(base, "pMovie");
        LOGFN("gfx_menu: GFxMoviePlayer {:08X}, flags at +{:X} (open {:08X}, input {:08X}, capture {:08X}), pMovie +{}",
            base, f.offset, f.open, f.input, f.capture, pMovie);
        if (f.offset < 0 || !f.open) return;
        const bool debug = getenv("GOW3_MENU_DEBUG") != nullptr;
        std::vector<uint32_t> movies;
        std::string last;
        bool wasMenu = false;
        for (int tick = 0;; tick++)
        {
            if (tick % 20 == 0) movies = ue3::FindInstancesOf(base);   // every 2 s: new scenes
            std::string open;
            bool menu = false;
            std::vector<Target> targets;
            for (uint32_t m : movies)
            {
                const uint32_t flags = ue3::Read32(m + uint32_t(f.offset));
                if (!(flags & f.open)) continue;
                const std::string name = ue3::ObjectName(m);
                const bool input = (flags & f.input) != 0;
                open += fmt::format(" {}{}{}", name, input ? "+in" : "", (flags & f.capture) ? "+cap" : "");
                if (!input || !IsMenuScene(name)) continue;
                menu = true;
                const uint32_t movie = pMovie >= 0 ? ue3::Read32(m + uint32_t(pMovie)) : 0;
                const uint32_t view = movie ? ue3::Read32(movie + 52) : 0;
                if (view && IsVtable(ue3::Read32(view)))
                {
                    const int w = int(ue3::Read32(movie + 20)), h = int(ue3::Read32(movie + 24));
                    targets.push_back({ view, float(w > 0 && w < 8192 ? w : 1280), float(h > 0 && h < 8192 ? h : 720), m,
                                        (flags & f.capture) != 0 });
                }
            }
            {
                std::lock_guard lock(g_targetsMutex);
                g_targets = std::move(targets);
            }
            if (debug && open != last) { LOGFN("gfx_menu: open:{}", open.empty() ? " (none)" : open); last = open; }
            if (menu != wasMenu)
            {
                hid::SetMenuMode(menu); wasMenu = menu;
                static int logged = 0;
                if (logged++ < 200) LOGFN("gfx_menu: menu mode {} ({})", menu ? "on" : "off", open.empty() ? "no scenes" : open.substr(1));
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }

    // in a menu: left click = A (once hover has caught up), right click = B, wheel = d-pad up/down (one press per
    // notch), polled every 15 ms. (The cursor position is read on the game thread.)
    void MouseWorker()
    {
        bool left = false, right = false;
        int pending = 0;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(15));
            const hid::MenuMouse m = hid::PollMenuMouse();
            if (!hid::MenuMode()) { left = m.left; right = m.right; pending = 0; continue; }
            static int logged = 0;
            if ((m.left && !left) || (m.right && !right) || m.wheel)
                if (logged++ < 30) LOGFN("gfx_menu: mouse {}{}{} at {:.0f},{:.0f} inside {}", m.left && !left ? "left " : "",
                    m.right && !right ? "right " : "", m.wheel ? fmt::format("wheel {} ", m.wheel) : "", m.x, m.y, m.inside);
            if (m.left && !left && m.inside) { g_pendingSince = GetTickCount64(); g_pendingA = true; }
            else if (m.right && !right && m.inside) g_pendingB = true;   // B, decided on the game thread
            left = m.left; right = m.right;
            pending += m.wheel;
            static uint64_t nextWheel = 0;
            if (pending != 0 && GetTickCount64() >= nextWheel)
            {
                hid::InjectButtons(pending > 0 ? 0x0001 : 0x0002, 60);              // d-pad up / down
                pending += pending > 0 ? -1 : 1;
                nextWheel = GetTickCount64() + 120;                                  // the menu needs a release between
            }
        }
    }

    // ---- ActionScript through the scene (game thread) ----

    // the FString argument {UTF-16BE data, count, max}
    uint32_t g_fstring = 0, g_chars = 0;
    void SetPath(const std::string& path)
    {
        if (!g_fstring)
        {
            g_fstring = g_memory.MapVirtual(g_userHeap.Alloc(16));
            g_chars = g_memory.MapVirtual(g_userHeap.Alloc(512));
        }
        const size_t n = std::min<size_t>(path.size(), 250);
        for (size_t i = 0; i < n; i++) *(uint16_t*)g_memory.Translate(g_chars + 2 * uint32_t(i)) = ByteSwap(uint16_t(path[i]));
        *(uint16_t*)g_memory.Translate(g_chars + 2 * uint32_t(n)) = 0;
        *(uint32_t*)g_memory.Translate(g_fstring) = ByteSwap(g_chars);
        *(uint32_t*)g_memory.Translate(g_fstring + 4) = ByteSwap(uint32_t(n + 1));
        *(uint32_t*)g_memory.Translate(g_fstring + 8) = ByteSwap(uint32_t(256));
    }

    // the scene's virtual at a vtable offset (scenes are live UObjects: plain reads, as ue3::Read32 asks VirtualQuery
    // each time, which cost more than the lookups themselves)
    uint32_t Virtual(uint32_t scene, uint32_t offset)
    {
        const uint32_t vtable = ByteSwap(*(uint32_t*)g_memory.Translate(scene));
        return IsVtable(vtable) ? ByteSwap(*(uint32_t*)g_memory.Translate(vtable + offset)) : 0;
    }

    // UGFxMoviePlayer::GetVariableNumber (0 when missing). Getters run ActionScript: read plain fields
    float GetNumber(uint32_t scene, const std::string& path)
    {
        const uint32_t fn = Virtual(scene, 0x148);
        if (!IsCode(fn)) return 0;
        SetPath(path);
        return GuestToHostFunction<float>(fn, scene, g_fstring);
    }

    // UGFxMoviePlayer::Invoke: r3 the FASValue result, r4 the scene, r5 the FString method path, r6 a
    // TArray<FASValue> {data, count, max} of arguments. FASValue (0x18): +0 Type (byte: 0 undefined, 1 null, 2 number,
    // 3 string, 4 boolean), +4 B, +8 N (float), +0xC S (FString). With one number argument, or none (hasArg false);
    // returns the result's string, if it is one
    std::string Invoke(uint32_t scene, const std::string& method, bool hasArg = false, float arg = 0)
    {
        static uint32_t args = 0, array = 0, result = 0;
        const uint32_t fn = Virtual(scene, 0x190);
        if (!IsCode(fn)) return {};
        if (!args)
        {
            args = g_memory.MapVirtual(g_userHeap.Alloc(0x18));
            array = g_memory.MapVirtual(g_userHeap.Alloc(12));
            result = g_memory.MapVirtual(g_userHeap.Alloc(0x18));
        }
        memset(g_memory.Translate(args), 0, 0x18);
        memset(g_memory.Translate(array), 0, 12);
        memset(g_memory.Translate(result), 0, 0x18);
        if (hasArg)
        {
            *(uint8_t*)g_memory.Translate(args) = 2;
            uint32_t bits; memcpy(&bits, &arg, 4);
            *(uint32_t*)g_memory.Translate(args + 8) = ByteSwap(bits);
            *(uint32_t*)g_memory.Translate(array) = ByteSwap(args);
            *(uint32_t*)g_memory.Translate(array + 4) = ByteSwap(1u);
            *(uint32_t*)g_memory.Translate(array + 8) = ByteSwap(1u);
        }
        SetPath(method);
        GuestToHostFunction<void>(fn, result, scene, g_fstring, array);
        const uint8_t type = *(uint8_t*)g_memory.Translate(result);
        const uint32_t data = ue3::Read32(result + 0xC), count = ue3::Read32(result + 0x10);
        std::string out;
        if (type == 3 && data && count > 0 && count < 512)
            for (uint32_t i = 0; i + 1 < count; i++)
            {
                const uint16_t c = ByteSwap(*(uint16_t*)g_memory.Translate(data + 2 * i));
                out += c < 128 ? char(c) : '?';
            }
        return out;
    }

    // a clip's own bounds hold the mouse (sliders, stepper buttons)
    bool Inside(uint32_t scene, const std::string& clip)
    {
        const float my = GetNumber(scene, clip + "._ymouse");
        if (!(my >= 0) || !(my < GetNumber(scene, clip + "._height"))) return false;
        const float mx = GetNumber(scene, clip + "._xmouse");
        return mx >= 0 && mx < GetNumber(scene, clip + "._width") && GetNumber(scene, clip + "._visible") > 0;
    }

    // a clip and every clip above it are visible (a menu's lists stay loaded while hidden: the main menu keeps its
    // list under a submenu, and hovering it moved an invisible selection that the pad then drove)
    bool Shown(uint32_t scene, const std::string& path)
    {
        std::string p = path;
        for (int depth = 0; depth < 12 && p.size() > 7; depth++)
        {
            if (!(GetNumber(scene, p + "._visible") > 0) || !(GetNumber(scene, p + "._alpha") > 0)) return false;
            const size_t dot = p.rfind('.');
            if (dot == std::string::npos) break;
            p.resize(dot);
            if (p == "_level0") break;
        }
        return true;
    }

    // ---- what is under the cursor ----

    // the focused list of the top menus, with its renderers' row tops (cached: getFocus is the dearest lookup)
    struct Focus
    {
        uint32_t scene = 0;
        std::string list;
        std::vector<float> rowTop;   // renderers' _y
        uint64_t at = 0;
        int lastHit = 0;
    };
    Focus g_focus;

    void RefreshFocus(uint64_t now)
    {
        Focus f;
        f.at = now;
        std::vector<Target> targets;
        { std::lock_guard lock(g_targetsMutex); targets = g_targets; }
        // a menu opened over another captures input (bCaptureInput); the one underneath keeps its lists loaded
        if (std::any_of(targets.begin(), targets.end(), [](const Target& t) { return t.capture; }))
            targets.erase(std::remove_if(targets.begin(), targets.end(), [](const Target& t) { return !t.capture; }), targets.end());
        for (const Target& t : targets)
        {
            const std::string list = Invoke(t.scene, "Selection.getFocus");
            if (list.empty() || !Shown(t.scene, list)) continue;
            const int count = std::min(int(GetNumber(t.scene, list + ".renderers.length")), 64);
            if (count <= 0) continue;
            f.scene = t.scene;
            f.list = list;
            for (int k = 0; k < count; k++) f.rowTop.push_back(GetNumber(t.scene, fmt::format("{}.renderers.{}._y", list, k)));
            f.lastHit = std::min(g_focus.list == list ? g_focus.lastHit : 0, count - 1);
            break;
        }
        g_focus = std::move(f);
    }

    struct Hit
    {
        int state = 0;            // 0 no list, 1 a list but no option under the cursor, 2 an option
        std::string list, renderer;
        float hovered = -1, selected = -1;
        uint32_t scene = 0;
    };

    Hit Find(bool fresh = false)
    {
        const uint64_t now = GetTickCount64();
        if (fresh || now >= g_focus.at + 250) RefreshFocus(now);
        Hit hit;
        if (g_focus.list.empty()) return hit;
        hit.state = 1;
        const uint32_t sc = g_focus.scene;
        const auto& top = g_focus.rowTop;
        const int count = int(top.size());
        // a row runs from its renderer's _y to the next row's (the nearest below it); the last one is as tall as the
        // one before it. The renderer's own _ymouse is the cursor relative to that top
        auto rowHeight = [&](int k) {
            float next = FLT_MAX;
            for (int j = 0; j < count; j++) if (top[j] > top[k]) next = std::min(next, top[j]);
            if (next != FLT_MAX) return next - top[k];
            float prev = -FLT_MAX;
            for (int j = 0; j < count; j++) if (top[j] < top[k]) prev = std::max(prev, top[j]);
            return prev != -FLT_MAX ? top[k] - prev : GetNumber(sc, fmt::format("{}.renderers.{}._height", g_focus.list, k));
        };
        // the last row hit first, then its neighbours outwards (the cursor moves a row at a time)
        for (int d = 0; d < 2 * count; d++)
        {
            const int k = g_focus.lastHit + ((d & 1) ? -(d + 1) / 2 : d / 2);
            if (k < 0 || k >= count) continue;
            const std::string r = fmt::format("{}.renderers.{}", g_focus.list, k);
            const float my = GetNumber(sc, r + "._ymouse");
            if (!(my >= 0 && my < rowHeight(k))) continue;
            const float mx = GetNumber(sc, r + "._xmouse");
            if (!(mx >= 0 && mx < GetNumber(sc, r + "._width")) || !(GetNumber(sc, r + "._visible") > 0)) return hit;
            g_focus.lastHit = k;
            hit.state = 2;
            hit.list = g_focus.list;
            hit.renderer = r;
            hit.hovered = GetNumber(sc, r + ".index");   // the option (lists can scroll)
            hit.selected = GetNumber(sc, g_focus.list + "._selectedIndex");
            hit.scene = sc;
            // a row past the list's data: Gears 3 reuses the main menu's list for its submenus (Campaign: 3 options in
            // the 7 rows of the main menu), and the spare renderers keep their old index. Selecting one put
            // selectedIndex past the end: the highlight sat on an empty row and the pad could not move it any more
            float length = GetNumber(sc, g_focus.list + ".dataProvider.length");
            if (!(length > 0)) length = GetNumber(sc, g_focus.list + "._dataProvider.length");
            static const bool allRows = getenv("GOW3_HOVER_ALL_ROWS") != nullptr;   // (testing Heal: the old behaviour)
            if (hit.hovered < 0 || (!allRows && length > 0 && hit.hovered >= length)) hit.state = 1;
            return hit;
        }
        return hit;
    }

    // ---- hover ----

    int g_steps = 0, g_dir = 0, g_failedPasses = 0;
    bool g_setWorks = true;           // setting selectedIndex takes (false after it failed twice: d-pad steps)
    int g_setFails = 0;
    uint64_t g_lastPress = 0;         // our last d-pad/A press: no lookups while the menu reads it
    bool g_dirty = false;             // the cursor moved since the last pass
    const bool g_timing = getenv("GOW3_HOVER_TIMING") != nullptr;

    // the pass for the previous frame's move: the hovered option becomes the selection. Returns true while the
    // selection is not on the hovered option yet (a pass is due, or d-pad steps are under way)
    bool Hover(int y)
    {
        const uint64_t now = GetTickCount64();
        if (g_steps > 0)
        {
            if (now >= g_lastPress + 250)
            {
                hid::InjectButtons(g_dir > 0 ? 0x0002 : 0x0001, 30);   // d-pad down / up (about 2 frames: longer reads as a repeat in some menus)
                g_lastPress = now; g_steps--;
                if (g_steps == 0) g_dirty = true;   // check where it landed
            }
            return true;
        }
        if (!g_dirty) return false;
        if (now < g_lastPress + 300) return true;   // our press is being read (a menu may be opening)
        g_dirty = false;
        LARGE_INTEGER t0, t1;
        QueryPerformanceCounter(&t0);
        const Hit hit = Find();
        bool moving = false;
        static int logged = 0;
        if (hit.state == 2 && hit.hovered != hit.selected)
        {
            // the list must still have focus (a click or a pad press may have opened another menu since the lookup):
            // else find it again next pass
            if (Invoke(hit.scene, "Selection.getFocus") != hit.list) { g_focus.at = 0; g_dirty = true; return true; }
            if (g_setWorks)
            {
                // the setter runs the list's ActionScript at once: read the result back
                Invoke(hit.scene, hit.list + ".__set__selectedIndex", true, hit.hovered);
                const float result = GetNumber(hit.scene, hit.list + "._selectedIndex");
                if (logged++ < 400 || g_timing)
                    LOGFN("gfx_menu: hover {}: select {} (was {}){}", hit.list, hit.hovered, hit.selected,
                        g_timing ? fmt::format(" t {} y {}", GetTickCount64(), y) : "");
                if (result != hit.hovered && ++g_setFails >= 2)
                {
                    g_setWorks = false;
                    LOGFN("gfx_menu: setting selectedIndex does not take: d-pad steps");
                }
                moving = result != hit.hovered;
                if (moving) g_dirty = true;   // again next frame (or walk)
            }
            else if (g_failedPasses++ < 3)
            {
                g_dir = hit.hovered > hit.selected ? 1 : -1;
                g_steps = int(std::abs(hit.hovered - hit.selected));
                if (logged++ < 20) LOGFN("gfx_menu: hover {}: option {} selected {} ({} steps)", hit.list, hit.hovered, hit.selected, g_steps);
                moving = true;
            }
        }
        else g_failedPasses = 0;
        if (g_timing)
        {
            QueryPerformanceCounter(&t1);
            LARGE_INTEGER freq; QueryPerformanceFrequency(&freq);
            static double total = 0, worst = 0;
            static int passes = 0;
            const double ms = double(t1.QuadPart - t0.QuadPart) * 1000.0 / double(freq.QuadPart);
            total += ms; worst = std::max(worst, ms);
            if (++passes % 50 == 0) LOGFN("gfx_menu: {} hover passes, {:.2f} ms average, {:.2f} ms worst", passes, total / passes, worst);
        }
        return moving;
    }

    // a click once the selection is on the hovered option. Option rows (OptionListItemRenderer) carry a value
    // control: a slider (MySlider) goes to the clicked point, a stepper (MyOptionStepper) steps left on its prevBtn
    // and right anywhere else. Their change only reaches the game from their d-pad handler (Slider.handleInput
    // dispatches "change"; setting the value does not), so a slider is set one step short of the target and the last
    // step is a d-pad press. Anything else is A; a click beside the options of a list does nothing.
    void Click()
    {
        const Hit hit = Find(true);
        static int logged = 0;
        const bool log = logged++ < 400;
        if (hit.state == 1) { if (log) LOGFN("gfx_menu: click beside the options"); return; }
        if (hit.state == 2)
        {
            const uint32_t sc = hit.scene;
            const std::string slider = hit.renderer + ".MySlider", stepper = hit.renderer + ".MyOptionStepper";
            if (GetNumber(sc, slider + "._visible") > 0)
            {
                if (!Inside(sc, slider)) { if (log) LOGFN("gfx_menu: click on a slider's label"); return; }
                const float lo = GetNumber(sc, slider + "._minimum"), hi = GetNumber(sc, slider + "._maximum");
                float step = GetNumber(sc, slider + "._snapInterval");
                const float cur = GetNumber(sc, slider + "._value");
                const float left = GetNumber(sc, slider + ".offsetLeft"), right = GetNumber(sc, slider + ".offsetRight");
                const float width = GetNumber(sc, slider + ".__width") - left - right;
                if (!(hi > lo) || !(width > 0)) return;
                if (!(step > 0)) step = (hi - lo) / 10;
                // Slider.trackPress: the value at the mouse, snapped
                const float raw = (GetNumber(sc, slider + "._xmouse") - left) / width * (hi - lo) + lo;
                const float target = std::clamp(lo + std::round((raw - lo) / step) * step, lo, hi);
                if (target == cur) return;
                const bool up = target > cur;
                Invoke(sc, slider + ".__set__value", true, up ? target - step : target + step);
                hid::InjectButtons(up ? 0x0008 : 0x0004, 60);   // d-pad right / left: the last step, with "change"
                if (log) LOGFN("gfx_menu: click on a slider: {} -> {}", cur, target);
                return;
            }
            if (GetNumber(sc, stepper + "._visible") > 0)
            {
                const bool prev = Inside(sc, stepper + ".prevBtn");
                hid::InjectButtons(prev ? 0x0004 : 0x0008, 60);   // d-pad left / right
                if (log) LOGFN("gfx_menu: click on a stepper: {}", prev ? "previous" : "next");
                return;
            }
        }
        hid::InjectButtons(0x1000, 80);   // A on the hovered option (or in a menu without a list)
        if (log) LOGFN("gfx_menu: click -> A");
    }
}

namespace
{
    // where the front end's main menu (gears.views.MainMenuView at _level0.MainMenuSP.MainMenu) is: its title screen
    // (PRESS START: only Start is read there, MainMenuView.handleInput), its first page (B there is
    // Callback_TransitionToTitle: back to the title screen, reloading the front end for seconds, which looked to
    // players like the controls had stopped), or anywhere else (a submenu, or another scene has the input)
    enum class MainMenuPage { Other, Title, First };
    MainMenuPage FindMainMenuPage()
    {
        const std::string view = "_level0.MainMenuSP.MainMenu";
        if (GetTickCount64() >= g_focus.at + 250) RefreshFocus(GetTickCount64());
        std::vector<Target> targets;
        { std::lock_guard lock(g_targetsMutex); targets = g_targets; }
        const bool overlay = std::any_of(targets.begin(), targets.end(), [](const Target& t) { return t.capture; });
        for (const Target& t : targets)
        {
            if (overlay && !t.capture) continue;   // a message box or options over it: B is theirs
            if (!(GetNumber(t.scene, view + "._visible") > 0)) continue;
            if (GetNumber(t.scene, view + ".bOnTitleScreen") > 0) return MainMenuPage::Title;
            if (GetNumber(t.scene, view + ".MenuOptionsHistory.length") > 0) return MainMenuPage::Other;
            if (!g_focus.list.empty() && g_focus.list.rfind(view + ".", 0) != 0) return MainMenuPage::Other;
            return MainMenuPage::First;
        }
        return MainMenuPage::Other;
    }

    // the front end main menu's own input state (MainMenuView, UI_FrontEndAssets' ActionScript), for the log: its
    // handleInput drops every press while bDenyInput is set (transitions: SetDenyInput; the stacker's StartStack until
    // its shift_up tween completes, AddMovieClipToStack), reads only Start on the title screen, and B on an empty
    // MenuOptionsHistory goes back to the title (Callback_TransitionToTitle). Empty when the main menu is not open
    std::string MainMenuState(uint32_t* sceneOut = nullptr)
    {
        const std::string view = "_level0.MainMenuSP.MainMenu";
        std::vector<Target> targets;
        { std::lock_guard lock(g_targetsMutex); targets = g_targets; }
        for (const Target& t : targets)
        {
            if (!(GetNumber(t.scene, view + "._visible") > 0)) continue;
            if (sceneOut) *sceneOut = t.scene;
            return fmt::format("deny {} title {} history {} stacker {} animating {} server {}{}",
                GetNumber(t.scene, view + ".bDenyInput") > 0 ? 1 : 0, GetNumber(t.scene, view + ".bOnTitleScreen") > 0 ? 1 : 0,
                GetNumber(t.scene, view + ".MenuOptionsHistory.length"), GetNumber(t.scene, view + ".bIsDisplayingStacker") > 0 ? 1 : 0,
                GetNumber(t.scene, view + ".Stacker.bIsAnimating") > 0 ? 1 : 0, GetNumber(t.scene, view + ".bIsServer") > 0 ? 1 : 0,
                std::any_of(targets.begin(), targets.end(), [](const Target& x) { return x.capture; }) ? " overlay" : "");
        }
        return {};
    }

    // the main menu is in a transition (bDenyInput): its own handleInput drops presses then, and a selection written
    // by the hover during a forward transition left the submenu's header over the old list with bDenyInput set for
    // good (player log 2026-10-15, tests/menu_click_during_transition.txt). Hover and clicks wait until it ends
    bool MainMenuDenies()
    {
        uint32_t scene = 0;
        std::vector<Target> targets;
        { std::lock_guard lock(g_targetsMutex); targets = g_targets; }
        const std::string view = "_level0.MainMenuSP.MainMenu";
        for (const Target& t : targets)
            if (GetNumber(t.scene, view + "._visible") > 0) { scene = t.scene; break; }
        if (scene && GetNumber(scene, view + ".bDenyInput") > 0) return true;
        // the other menus on gears.controls.MainMenuList (Options, custom weapon spawns, mutators): its handleInput
        // drops input while its Owner's bDenyInput is set
        return !g_focus.list.empty() && GetNumber(g_focus.scene, g_focus.list + ".Owner.bDenyInput") > 0;
    }

    // a main menu that denies input for longer than any transition (2 s; a stacker tween is 0.2-0.3 s) is logged once
    // per episode with its state: the trace for "the menus stopped answering"
    void WatchDeny()
    {
        static uint64_t next = 0, since = 0;
        static bool reported = false;
        const uint64_t now = GetTickCount64();
        if (now < next) return;
        next = now + 500;
        uint32_t scene = 0;
        const std::string state = MainMenuState(&scene);
        const bool deny = scene && GetNumber(scene, "_level0.MainMenuSP.MainMenu.bDenyInput") > 0;
        if (!deny) { since = 0; reported = false; return; }
        if (!since) since = now;
        if (!reported && now - since >= 2000)
        {
            reported = true;
            LOGFN_WARNING("gfx_menu: the main menu has refused input for {} ms ({})", now - since, state);
        }
    }

    // Esc or a right click in a menu: B, except on the main menu's first page and its title screen (PC convention:
    // Esc on a game's top menu does nothing; the controller's B keeps the console's way)
    void Back(const char* what)
    {
        const MainMenuPage page = FindMainMenuPage();
        static int logged = 0;
        if (page != MainMenuPage::Other)
        {
            if (logged++ < 50) LOGFN("gfx_menu: {} on the main menu's {}: ignored", what, page == MainMenuPage::Title ? "title screen" : "first page");
            return;
        }
        hid::InjectButtons(0x2000, 80);
        if (logged++ < 50) LOGFN("gfx_menu: {} -> B (main menu: {})", what, MainMenuState());
    }

    // a selection outside the focused list's data (the reused main-menu list: a submenu with fewer options than the
    // menu it came from) leaves the highlight on an empty row and the list ignoring the d-pad. Whatever put it there
    // (a hover, a click, Esc / B back to a shorter menu...), it is moved back onto the last option, twice a second
    void Heal()
    {
        static uint64_t next = 0;
        const uint64_t now = GetTickCount64();
        if (now < next || now < g_lastPress + 300) return;
        next = now + 500;
        if (now >= g_focus.at + 250) RefreshFocus(now);
        if (g_focus.list.empty()) return;
        const uint32_t sc = g_focus.scene;
        float length = GetNumber(sc, g_focus.list + ".dataProvider.length");
        if (!(length > 0)) length = GetNumber(sc, g_focus.list + "._dataProvider.length");
        const float sel = GetNumber(sc, g_focus.list + "._selectedIndex");
        if (!(length > 0) || !(sel >= length)) return;
        Invoke(sc, g_focus.list + ".__set__selectedIndex", true, length - 1);
        static int logged = 0;
        if (logged++ < 50)
            LOGFN("gfx_menu: {} had option {} selected of {}: moved to {}", g_focus.list, sel, length,
                  GetNumber(sc, g_focus.list + "._selectedIndex"));
    }
}

// the game's input poll (hid::GetState): on the main loop's thread, once a frame
void OnGameThread()
{
    // the game's main loop only: another thread polls the pad too, and Scaleform belongs to the game thread
    if (GetCurrentThreadId() != g_mainThreadId) return;
    static int lastX = -2, lastY = -2;
    if (!hid::MenuMode()) { lastX = -2; g_pendingB = false; return; }
    if (hid::TakeMenuEscape()) Back("Esc / F");
    if (g_pendingB.exchange(false)) Back("right click");
    WatchDeny();
    const bool denies = MainMenuDenies();   // a transition: nothing may write the selection (Heal, hover)
    if (!denies) Heal();
    float fx, fy;
    if (!hid::MenuCursor(fx, fy)) return;   // read now, not from the 15 ms mouse poll
    const int x = int(fx), y = int(fy);
    // first hover for the previous frame's move (Scaleform has applied it by now), then this frame's move; a click
    // waits until the selection is on the hovered option (at most 1.5 s)
    static const bool noHover = getenv("GOW3_NO_HOVER") != nullptr;
    if (denies)
    {
        g_dirty = true;   // hover again once the transition is over (the cursor's move is sent then too)
        if (g_pendingA && GetTickCount64() - g_pendingSince > 1500) g_pendingA = false;   // the game would drop it too
        return;
    }
    const bool moving = !noHover && Hover(lastY);
    if (g_pendingA && (!moving || GetTickCount64() - g_pendingSince > 1500) && GetTickCount64() >= g_lastPress + 150)
    {
        g_pendingA = false;
        if (FindMainMenuPage() == MainMenuPage::Title)
        {
            hid::InjectButtons(0x0010, 80);   // PRESS START reads only Start
            static int logged = 0;
            if (logged++ < 20) LOGFN("gfx_menu: click on the title screen -> Start");
        }
        else if (noHover || moving) hid::InjectButtons(0x1000, 80);   // A on whatever is selected
        else Click();
        static int logged = 0;
        if (logged++ < 200) LOGFN("gfx_menu: click{} (main menu: {})", moving ? " while the selection moves -> A" : "", MainMenuState());
        g_lastPress = GetTickCount64();   // no lookups while the press is read
        g_focus.at = 0;                   // the menu may change: find the focused list again
    }
    if (x == lastX && y == lastY) return;
    lastX = x; lastY = y;
    g_dirty = true;
    std::vector<Target> targets;
    {
        std::lock_guard lock(g_targetsMutex);
        targets = g_targets;
    }
    static uint32_t event = 0;
    if (!event) event = g_memory.MapVirtual(g_userHeap.Alloc(0x20));
    auto put = [](uint32_t a, uint32_t v) { *(uint32_t*)g_memory.Translate(a) = ByteSwap(v); };
    auto putf = [&](uint32_t a, float f) { uint32_t v; memcpy(&v, &f, 4); put(a, v); };
    for (const Target& t : targets)
    {
        const uint32_t vtable = ue3::Read32(t.view);
        if (!IsVtable(vtable)) continue;   // closed meanwhile
        const uint32_t handleEvent = ue3::Read32(vtable + 45 * 4);
        if (!IsCode(handleEvent)) continue;
        // the Xbox movies may be set up with no mouse (count 0 at +0x9B4): events for mouse 0 would be dropped. The
        // mouse states are a fixed array of 4, so a count of 1 is enough to make mouse 0 live
        if (ue3::Read32(t.view + 0x9B4) == 0) put(t.view + 0x9B4, 1);
        put(event + 0x00, 1);   // MouseMove
        putf(event + 0x04, x * t.w / 1280.0f);
        putf(event + 0x08, y * t.h / 720.0f);
        put(event + 0x0C, 0); put(event + 0x10, 0); put(event + 0x14, 0);
        GuestToHostFunction<uint32_t>(handleEvent, t.view, event);
    }
}

void Start()
{
    if (getenv("GOW3_NO_MOUSE_MENU")) return;
    static std::once_flag once;
    std::call_once(once, [] { std::thread(Worker).detach(); std::thread(MouseWorker).detach(); });
}
}
