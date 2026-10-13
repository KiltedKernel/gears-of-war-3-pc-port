// Mouse in menus. GoW2's UI is UE3's UIScene system; its PC mouse path is never fed on a console build, so this works
// on top of it without calling engine code: a worker thread reads the scene tree (ue3 reflection: the
// GearGameUISceneClient's ActiveScenes, every widget's RenderBounds / StateStack / flags) and turns the mouse into
// virtual controller buttons, which the menus already understand:
//   hover   - d-pad steps until the hovered widget has focus      left click  - A on it (or the callout's own button,
//   wheel   - d-pad up / down                                                  or right on an option list)
//   right click - B (back)
// A menu is up when the topmost active scene has a focused widget (the HUD and toasts have none); then the mouse is
// released and the cursor shows (hid::SetMenuMode). Coordinates are the UI canvas, 1280x720.
#include <stdafx.h>
#include <kernel/function.h>
#include <hid/hid.h>
#include <os/logger.h>
#include <ue3/reflection.h>
#include <atomic>
#include <cmath>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace mouse_menu
{
namespace
{
    constexpr uint16_t DPAD_UP = 0x0001, DPAD_DOWN = 0x0002, DPAD_LEFT = 0x0004, DPAD_RIGHT = 0x0008, START = 0x0010,
                       BACK = 0x0020, LTHUMB = 0x0040, RTHUMB = 0x0080, LB = 0x0100, RB = 0x0200, BTN_A = 0x1000,
                       BTN_B = 0x2000, BTN_X = 0x4000, BTN_Y = 0x8000;

    struct Layout
    {
        int children = -1, stateStack = -1, screenFlags = -1, renderBounds = -1, sceneFlags = -1, activeScenes = -1,
            calloutAlias = -1, owner = -1;
        uint32_t hiddenMask = 0, neverFocusMask = 0;
        uint32_t clsFocused = 0, clsDisabled = 0, clsCallout = 0, clsOptionList = 0, clsSlider = 0, clsCheckbox = 0,
                 clsButton = 0, clsList = 0, clsEditBox = 0, clsOptionButton = 0;
        uint32_t sceneClient = 0;
        std::unordered_map<uint32_t, uint16_t> aliasButtons;   // alias name index -> XInput button
    };
    Layout L;

    struct Rect { float l, t, r, b; bool Contains(float x, float y) const { return x >= l && x < r && y >= t && y < b; } };

    Rect Bounds(uint32_t w)
    {
        return { ue3::ReadFloat(w + L.renderBounds), ue3::ReadFloat(w + L.renderBounds + 4),
                 ue3::ReadFloat(w + L.renderBounds + 8), ue3::ReadFloat(w + L.renderBounds + 12) };
    }

    template <class F> void ForEachChild(uint32_t w, F&& f)
    {
        const uint32_t data = ue3::Read32(w + L.children), n = ue3::Read32(w + L.children + 4);
        if (!data || n > 512) return;
        for (uint32_t i = 0; i < n; i++)
            if (const uint32_t c = ue3::Read32(data + 4 * i)) f(c);
    }

    bool HasState(uint32_t w, uint32_t stateClass)
    {
        const uint32_t data = ue3::Read32(w + L.stateStack), n = ue3::Read32(w + L.stateStack + 4);
        if (!data || n > 32) return false;
        for (uint32_t i = 0; i < n; i++)
            if (ue3::IsA(ue3::Read32(data + 4 * i), stateClass)) return true;
        return false;
    }

    bool Hidden(uint32_t w) { return (ue3::Read32(w + L.screenFlags) & L.hiddenMask) != 0; }

    // a is b or one of b's owners (row button > option list > its arrow buttons)
    bool Contains(uint32_t a, uint32_t b)
    {
        for (int i = 0; b && i < 32; i++, b = ue3::Read32(b + L.owner))
            if (a == b) return true;
        return false;
    }
    bool Related(uint32_t a, uint32_t b) { return a && b && (Contains(a, b) || Contains(b, a)); }
    bool Is(uint32_t w, uint32_t cls) { return w && cls && ue3::IsA(w, cls); }

    // the deepest focused widget (focus propagates down from the scene to the leaf)
    uint32_t FocusedLeaf(uint32_t w, int depth = 0)
    {
        if (depth > 24) return 0;
        uint32_t found = 0;
        ForEachChild(w, [&](uint32_t c) {
            if (found || Hidden(c) || !HasState(c, L.clsFocused)) return;
            const uint32_t deeper = FocusedLeaf(c, depth + 1);
            found = deeper ? deeper : c;
        });
        return found;
    }

    bool Clickable(uint32_t w)
    {
        for (uint32_t cls : { L.clsButton, L.clsCallout, L.clsOptionList, L.clsSlider, L.clsCheckbox, L.clsList, L.clsEditBox })
            if (cls && ue3::IsA(w, cls)) return true;
        return false;
    }

    // the deepest visible clickable widget under (x, y); later children draw on top
    uint32_t HitTest(uint32_t w, float x, float y, int depth = 0)
    {
        if (depth > 24) return 0;
        uint32_t hit = 0;
        ForEachChild(w, [&](uint32_t c) {
            if (Hidden(c)) return;
            if (const uint32_t deeper = HitTest(c, x, y, depth + 1)) { hit = deeper; return; }
            if (Clickable(c) && !HasState(c, L.clsDisabled) && Bounds(c).Contains(x, y)) hit = c;
        });
        return hit;
    }

    uint32_t MenuScene()
    {
        const uint32_t data = ue3::Read32(L.sceneClient + L.activeScenes), n = ue3::Read32(L.sceneClient + L.activeScenes + 4);
        if (!data || n > 64) return 0;
        for (int i = int(n) - 1; i >= 0; i--)
        {
            const uint32_t s = ue3::Read32(data + 4 * i);
            if (s && FocusedLeaf(s)) return s;
        }
        return 0;
    }

    uint16_t ButtonForKey(const std::string& key)
    {
        static const std::unordered_map<std::string, uint16_t> map = {
            { "XboxTypeS_A", BTN_A }, { "XboxTypeS_B", BTN_B }, { "XboxTypeS_X", BTN_X }, { "XboxTypeS_Y", BTN_Y },
            { "XboxTypeS_LeftShoulder", LB }, { "XboxTypeS_RightShoulder", RB }, { "XboxTypeS_Start", START },
            { "XboxTypeS_Back", BACK }, { "XboxTypeS_LeftThumbStick", LTHUMB }, { "XboxTypeS_RightThumbStick", RTHUMB },
            { "XboxTypeS_DPad_Up", DPAD_UP }, { "XboxTypeS_DPad_Down", DPAD_DOWN }, { "XboxTypeS_DPad_Left", DPAD_LEFT },
            { "XboxTypeS_DPad_Right", DPAD_RIGHT } };
        auto it = map.find(key);
        return it != map.end() ? it->second : 0;
    }

    bool Setup()
    {
        if (!ue3::Init()) return false;
        const uint32_t screen = ue3::FindClass("UIScreenObject"), object = ue3::FindClass("UIObject"), scene = ue3::FindClass("UIScene");
        const uint32_t client = ue3::FindClass("GameUISceneClient"), callout = ue3::FindClass("UICalloutButton");
        if (!screen || !object || !scene || !client) return false;
        L.children = ue3::PropertyOffset(screen, "Children");
        L.stateStack = ue3::PropertyOffset(screen, "StateStack");
        L.screenFlags = ue3::PropertyOffset(screen, "bHidden");
        L.hiddenMask = ue3::BoolMask(screen, "bHidden");
        L.neverFocusMask = ue3::BoolMask(screen, "bNeverFocus");
        L.renderBounds = ue3::PropertyOffset(object, "RenderBounds");
        L.sceneFlags = ue3::PropertyOffset(scene, "bDisplayCursor");
        L.activeScenes = ue3::PropertyOffset(client, "ActiveScenes");
        L.calloutAlias = callout ? ue3::PropertyOffset(callout, "InputAliasTag") : -1;
        L.clsFocused = ue3::FindClass("UIState_Focused");
        L.clsDisabled = ue3::FindClass("UIState_Disabled");
        L.clsCallout = callout;
        L.clsOptionList = ue3::FindClass("UIOptionListBase");
        L.clsSlider = ue3::FindClass("UISlider");
        L.clsCheckbox = ue3::FindClass("UICheckbox");
        L.clsButton = ue3::FindClass("UIButton");
        L.clsList = ue3::FindClass("UIList");
        L.clsEditBox = ue3::FindClass("UIEditBox");
        L.clsOptionButton = ue3::FindClass("UIOptionListButton");
        L.owner = ue3::PropertyOffset(object, "Owner");
        if (L.children < 0 || L.stateStack < 0 || L.renderBounds < 0 || L.activeScenes < 0 || L.owner < 0 || !L.clsFocused) return false;
        // the input alias data store: InputAliases (AliasName, then the first platform key's name at +8, 80 bytes each)
        if (const uint32_t store = ue3::FindInstance("UIDataStore_InputAlias"))
        {
            const int arr = ue3::PropertyOffset(ue3::ClassOf(store), "InputAliases");
            const uint32_t data = ue3::Read32(store + arr), n = ue3::Read32(store + arr + 4);
            for (uint32_t i = 0; arr >= 0 && data && i < n && i < 1024; i++)
                if (const uint16_t b = ButtonForKey(ue3::Name(ue3::Read32(data + 80 * i + 8))))
                    L.aliasButtons[ue3::Read32(data + 80 * i)] = b;
        }
        LOGFN("mouse menus: ready ({} callout aliases, render bounds +{:X}, states +{:X})", L.aliasButtons.size(), L.renderBounds, L.stateStack);
        return true;
    }

    // focus requests: the worker picks the widget, the game thread runs UIScreenObject::SetFocus (vtable +344, as
    // execSetFocus 8242F198 calls it: r3 widget, r4 sender, r5 player index) from its controller poll
    std::atomic<uint32_t> g_focusRequest{ 0 };

    void Press(uint16_t button)
    {
        static const bool debug = getenv("GOW3_MOUSE_DEBUG") != nullptr;
        if (debug) LOGFN("mouse menus: press {:04X}", button);
        hid::InjectButtons(button, 120);
    }

    void Worker()
    {
        // the engine's tables exist once the game has booted; scanning during boot only competes with it
        std::this_thread::sleep_for(std::chrono::seconds(15));
        while (!Setup()) std::this_thread::sleep_for(std::chrono::seconds(3));
        uint64_t nextStep = 0, lastMove = 0;
        float lastX = -1, lastY = -1;
        bool wasLeft = false, wasRight = false, wasMenu = false;
        uint32_t pendingClick = 0;   // widget to press A on once it has focus
        int steps = 0;
        uint32_t stepTarget = 0;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(15));
            if (!L.sceneClient) L.sceneClient = ue3::FindInstance("GearGameUISceneClient");
            if (!L.sceneClient) continue;
            const uint32_t menu = MenuScene();
            const bool inMenu = menu != 0;
            if (inMenu != wasMenu)
            {
                hid::SetMenuMode(inMenu); wasMenu = inMenu; pendingClick = 0;
                LOGFN("mouse menus: {} ({})", inMenu ? "menu" : "gameplay", inMenu ? ue3::ObjectName(menu) : std::string("-"));
            }
            if (!inMenu) continue;

            const hid::MenuMouse m = hid::PollMenuMouse();
            const uint64_t now = GetTickCount64();
            if (!m.inside) { wasLeft = m.left; wasRight = m.right; continue; }
            if (std::fabs(m.x - lastX) + std::fabs(m.y - lastY) > 1.5f) { lastMove = now; lastX = m.x; lastY = m.y; }

            const uint32_t focused = FocusedLeaf(menu);
            const uint32_t hovered = HitTest(menu, m.x, m.y);
            const bool callout = hovered && L.clsCallout && ue3::IsA(hovered, L.clsCallout);

            if (m.wheel) { Press(m.wheel > 0 ? DPAD_UP : DPAD_DOWN); nextStep = now + 90; }

            static const bool debug = getenv("GOW3_MOUSE_DEBUG") != nullptr;
            if (debug && m.left && !wasLeft)
                LOGFN("mouse menus: click at ({:.0f}, {:.0f}) hovered {} ({}) focused {} ({})", m.x, m.y,
                    ue3::ObjectName(hovered), ue3::ObjectName(ue3::ClassOf(hovered)), ue3::ObjectName(focused),
                    ue3::ObjectName(ue3::ClassOf(focused)));
            // left click (press edge)
            if (m.left && !wasLeft && hovered)
            {
                if (callout)
                {
                    const uint32_t alias = L.calloutAlias >= 0 ? ue3::Read32(hovered + L.calloutAlias) : 0;
                    auto it = L.aliasButtons.find(alias);
                    if (it != L.aliasButtons.end()) Press(it->second);
                }
                else if (Related(hovered, focused))
                {
                    // option lists: the left arrow goes back, anything else on the value goes forward
                    if (Is(hovered, L.clsOptionButton))
                    {
                        const uint32_t list = ue3::Read32(hovered + L.owner);
                        const Rect a = Bounds(hovered), r = Bounds(list);
                        Press((a.l + a.r) / 2 < (r.l + r.r) / 2 ? DPAD_LEFT : DPAD_RIGHT);
                    }
                    else if (Is(hovered, L.clsOptionList) || Is(hovered, L.clsSlider)) Press(DPAD_RIGHT);
                    else Press(BTN_A);
                }
                else pendingClick = hovered;
                nextStep = std::max(nextStep, now + 90);
            }
            if (m.right && !wasRight) Press(BTN_B);
            wasLeft = m.left; wasRight = m.right;

            // hover: step focus toward the widget under the cursor (only after the mouse moved, so keyboard and pad
            // navigation are not fought; or to reach a clicked widget)
            uint32_t target = pendingClick ? pendingClick : ((!callout && now - lastMove < 800) ? hovered : 0);
            if (Is(target, L.clsOptionButton)) target = ue3::Read32(target + L.owner);   // an arrow: its option list
            if (target && target != stepTarget) { stepTarget = target; steps = 0; }
            if (target && focused && now >= nextStep)
            {
                if (Related(target, focused))
                {
                    if (pendingClick) { Press(BTN_A); pendingClick = 0; nextStep = now + 120; }
                }
                else if (steps < 4)
                {
                    // jump straight to it (the game thread calls SetFocus; see OnGameThread)
                    g_focusRequest = target;
                    steps++;
                    nextStep = now + 60;
                }
                else pendingClick = 0;   // it does not take focus
            }
        }
    }
}

// from the game thread's controller poll (hid::GetState): run a pending focus request
void OnGameThread()
{
    const uint32_t w = g_focusRequest.exchange(0);
    if (!w || !L.clsFocused) return;
    const uint32_t vtable = ue3::Read32(w);
    if (vtable < 0x82000000 || vtable >= 0x82300000) return;     // gone, or not a widget any more
    const uint32_t setFocus = ue3::Read32(vtable + 344);
    if (setFocus < 0x82200000 || setFocus >= 0x83000000) return;
    GuestToHostFunction<uint32_t>(setFocus, w, 0u, 0u);
}

void Start()
{
    if (getenv("GOW3_NO_MOUSE_MENU")) return;
    static std::once_flag once;
    std::call_once(once, [] { std::thread(Worker).detach(); });
}
}
