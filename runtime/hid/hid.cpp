// Input for the GoW2 runtime: XInput controllers, plus keyboard and mouse mapped onto player 1's controller.
//
// Player 1 (user 0) is always connected: a real pad in XInput slot 0 if present, merged with keyboard/mouse.
// Players 2-4 are connected only when a real pad is in their slot.
//
// Mouse look: raw mouse counts go to the engine as UE3 MouseX/MouseY axis events (mouse_look.cpp), the game's own PC
// mouse path: rotation deltas, no stick emulation. Where the engine takes no mouse axes (some menus) the motion
// falls back to a right-stick rate. Left click in the window captures; Alt or losing focus releases.
// Sensitivity, inversion, mode: gow3.ini [input]. F11 / Alt+Enter toggles fullscreen.
//
// Keys (defaults, Gears PC-style):
//   WASD left stick     mouse right stick   LMB fire (RT)       RMB aim (LT)        MMB / Z zoom (RS click)
//   Space / Shift A (cover, roadie run)     F B (melee)         E X (use)           Q Y (look)
//   R RB (reload)       Tab LB (tactical)   C LS click          1-4 / arrows D-pad (weapons)
//   Enter / Esc Start   Backspace Back
#include <stdafx.h>
#include <hid/hid.h>
#include <user/settings.h>
#include <os/logger.h>
#include <Xinput.h>
#include <atomic>
#include <cmath>
#include <fstream>
#include <sstream>
#include "../../xe_glue/gow2_gpu.h"

void XamNotifyEnqueueEvent(uint32_t dwId, uint32_t dwParam);   // kernel/xam.cpp
#pragma comment(lib, "xinput.lib")

namespace
{
    // XINPUT_GAMEPAD_* buttons
    constexpr uint16_t DPAD_UP = 0x0001, DPAD_DOWN = 0x0002, DPAD_LEFT = 0x0004, DPAD_RIGHT = 0x0008, START = 0x0010,
                       BACK = 0x0020, LTHUMB = 0x0040, RTHUMB = 0x0080, LSHOULDER = 0x0100, RSHOULDER = 0x0200,
                       BTN_A = 0x1000, BTN_B = 0x2000, BTN_X = 0x4000, BTN_Y = 0x8000;

    struct KeyBinding { int vk; uint16_t button; };
    constexpr KeyBinding kButtons[] = {
        { VK_SPACE, BTN_A }, { VK_LSHIFT, BTN_A }, { 'F', BTN_B }, { 'E', BTN_X }, { 'Q', BTN_Y },
        { 'R', RSHOULDER }, { VK_TAB, LSHOULDER }, { 'C', LTHUMB }, { 'Z', RTHUMB }, { VK_MBUTTON, RTHUMB },
        { '1', DPAD_UP }, { '2', DPAD_RIGHT }, { '3', DPAD_DOWN }, { '4', DPAD_LEFT },
        { VK_UP, DPAD_UP }, { VK_RIGHT, DPAD_RIGHT }, { VK_DOWN, DPAD_DOWN }, { VK_LEFT, DPAD_LEFT },
        { VK_RETURN, START }, { VK_ESCAPE, START }, { VK_BACK, BACK },
    };

    // ---- scripted input (GOW3_INPUT_SCRIPT=<file>): timed events played into this layer, for testing without
    // touching the real keyboard/mouse or needing window focus. Lines: "<seconds> key <vk hex> <hold ms>",
    // "<seconds> mouse <dx> <dy> [ms]" (spread over ms, default 200), "<seconds> capture". '#' starts a comment.
    bool g_scripted = false;
    std::atomic<bool> g_scriptKeys[256];
    std::atomic<uint8_t> g_scriptLT{ 0 }, g_scriptRT{ 0 };   // "t trig L|R ms": a virtual controller's triggers
    std::atomic<uint16_t> g_scriptPadButtons{ 0 };   // "t pad <XInput buttons hex> ms": a virtual controller
    // scripted menu mouse: "t cursor <x> <y>" (UI space), "t click L|R [ms]", "t wheel <notches>"
    std::atomic<int> g_scriptCursorX{ -1 }, g_scriptCursorY{ -1 }, g_scriptWheel{ 0 };
    std::atomic<bool> g_scriptLeft{ false }, g_scriptRight{ false };
    std::atomic<bool> g_menuMode{ false };
    std::atomic<int> g_wheel{ 0 };                    // raw wheel notches (real mouse)
    std::atomic<int64_t> g_anyMouse{ 0 };              // any raw mouse event while focused (device tracking)
    std::atomic<uint16_t> g_injected{ 0 };
    std::atomic<uint64_t> g_injectedUntil{ 0 };
    std::atomic<int64_t> g_rawXScript{ 0 }, g_rawYScript{ 0 };

    bool Down(int vk)
    {
        // scripted: only the script's keys (the real keyboard belongs to whoever is using the desktop)
        if (g_scripted) return vk >= 0 && vk < 256 && g_scriptKeys[vk];
        return (GetAsyncKeyState(vk) & 0x8000) != 0;
    }

    HWND Window() { return (HWND)gow2gpu::GetWindowHandle(); }
    bool Focused() { HWND w = Window(); return w && (g_scripted || GetForegroundWindow() == w); }

    // ---- mouse ----
    // Raw Input (WM_INPUT on a message-only window): device counts, unaffected by Windows pointer speed and
    // acceleration. Counted only while captured (left click in the window; Alt or losing focus releases).
    std::atomic<bool> g_captured{ false };
    std::atomic<int64_t> g_rawX{ 0 }, g_rawY{ 0 };       // all counts while captured
    std::atomic<uint64_t> g_nativeTick{ 0 };            // last time the engine took mouse axes (mouse_look.cpp)
    float g_sensitivity = 1.0f;
    bool g_invertY = false;
    bool g_nativeMode = true;

    POINT WindowCenter()
    {
        RECT r;
        GetClientRect(Window(), &r);
        POINT c{ (r.left + r.right) / 2, (r.top + r.bottom) / 2 };
        ClientToScreen(Window(), &c);
        return c;
    }

    void SetCaptured(bool on)
    {
        if (g_scripted) { g_captured = true; return; }   // scripted: always captured, the real cursor untouched
        if (g_captured.exchange(on) == on) return;
        gow2gpu::SetMouseCaptured(on);   // hides the cursor over the window
        if (on) { POINT c = WindowCenter(); SetCursorPos(c.x, c.y); }
        else ClipCursor(nullptr);        // release the pin (MouseThread)
    }

    LRESULT CALLBACK RawInputProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
    {
        if (msg == WM_INPUT)
        {
            RAWINPUT ri;
            UINT size = sizeof(ri);
            if (GetRawInputData((HRAWINPUT)lp, RID_INPUT, &ri, &size, sizeof(RAWINPUTHEADER)) != UINT(-1) &&
                ri.header.dwType == RIM_TYPEMOUSE && !g_scripted && Focused())
            {
                g_anyMouse++;
                if (ri.data.mouse.usButtonFlags & RI_MOUSE_WHEEL)
                    g_wheel += short(ri.data.mouse.usButtonData) / WHEEL_DELTA;
            }
            if (GetRawInputData((HRAWINPUT)lp, RID_INPUT, &ri, &size, sizeof(RAWINPUTHEADER)) != UINT(-1) &&
                ri.header.dwType == RIM_TYPEMOUSE && g_captured && !g_scripted)
            {
                static LONG lastAbsX = 0, lastAbsY = 0;
                static bool haveAbs = false;
                if (ri.data.mouse.usFlags & MOUSE_MOVE_ABSOLUTE)
                {
                    // remote desktop / tablets report absolute positions (0..65535): use the change
                    if (haveAbs)
                    {
                        g_rawX += (ri.data.mouse.lLastX - lastAbsX) * GetSystemMetrics(SM_CXSCREEN) / 65536;
                        g_rawY += (ri.data.mouse.lLastY - lastAbsY) * GetSystemMetrics(SM_CYSCREEN) / 65536;
                    }
                    lastAbsX = ri.data.mouse.lLastX; lastAbsY = ri.data.mouse.lLastY; haveAbs = true;
                }
                else
                {
                    g_rawX += ri.data.mouse.lLastX;
                    g_rawY += ri.data.mouse.lLastY;
                    haveAbs = false;
                }
            }
        }
        return DefWindowProcW(hwnd, msg, wp, lp);
    }

    void RawInputThread()
    {
        WNDCLASSW wc{};
        wc.lpfnWndProc = RawInputProc;
        wc.hInstance = GetModuleHandleW(nullptr);
        wc.lpszClassName = L"GoW2RawInput";
        RegisterClassW(&wc);
        HWND hwnd = CreateWindowExW(0, wc.lpszClassName, L"", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, wc.hInstance, nullptr);
        RAWINPUTDEVICE rid{ 0x01, 0x02, RIDEV_INPUTSINK, hwnd };   // generic desktop / mouse, also when not focused
        if (!hwnd || !RegisterRawInputDevices(&rid, 1, sizeof(rid)))
        {
            LOGFN_WARNING("input: raw mouse input unavailable ({})", GetLastError());
            return;
        }
        MSG m;
        while (GetMessageW(&m, nullptr, 0, 0) > 0) DispatchMessageW(&m);
    }

    void MouseThread()
    {
        bool hotkeyWasDown = false;
        for (;;)
        {
            Sleep(2);
            if (!Window()) continue;
            const bool hotkey = Focused() && (Down(VK_F11) || (Down(VK_MENU) && Down(VK_RETURN)));
            if (hotkey && !hotkeyWasDown) { SetCaptured(false); gow2gpu::ToggleFullscreen(); }
            hotkeyWasDown = hotkey;
            static bool wasMenu = false;
            const bool menu = g_menuMode;
            if (!Focused() || Down(VK_MENU) || menu) { SetCaptured(false); wasMenu = menu; continue; }
            // a menu just closed: take the mouse back at once (else the next click only recaptures, and fires)
            if (wasMenu) { wasMenu = false; SetCaptured(true); continue; }
            if (!g_captured)
            {
                // capture on a left click inside the client area
                POINT p; RECT r;
                if (Down(VK_LBUTTON) && GetCursorPos(&p) && ScreenToClient(Window(), &p) && GetClientRect(Window(), &r) && PtInRect(&r, p))
                    SetCaptured(true);
                continue;
            }
            if (g_scripted) continue;
            // pin the cursor to the window center while captured (motion comes from raw input, which reads the
            // device, not the cursor). Re-applied every pass: Windows drops a cursor clip on focus changes, window
            // moves/resizes and fullscreen toggles, which let the cursor wander outside the window.
            const POINT c = WindowCenter();
            const RECT pin{ c.x, c.y, c.x + 1, c.y + 1 };
            ClipCursor(&pin);
        }
    }

    bool NativeActive() { return g_nativeMode && GetTickCount64() - g_nativeTick.load() < 250; }

    // fallback where the engine takes no mouse axes (some menus): motion since the previous poll as a stick rate,
    // starting just past the 7849 right-stick deadzone
    int16_t MouseToStick(double countsPerSecond)
    {
        if (countsPerSecond == 0) return 0;
        constexpr double kDeadzone = 7849, kGain = 18.0;
        double v = std::min(kDeadzone + std::abs(countsPerSecond) * kGain * g_sensitivity, 32767.0);
        return int16_t(countsPerSecond < 0 ? -v : v);
    }

    struct MouseLook { int16_t x, y; };
    MouseLook PollMouse()
    {
        static LARGE_INTEGER freq{}, last{};
        static int64_t lastX = 0, lastY = 0;
        static MouseLook held{};
        if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
        LARGE_INTEGER now; QueryPerformanceCounter(&now);
        const double dt = last.QuadPart ? double(now.QuadPart - last.QuadPart) / freq.QuadPart : 0;
        if (dt < 0.008 && last.QuadPart) return held;   // several polls per frame: measure over >= 8 ms
        last = now;
        const int64_t x = g_rawX, y = g_rawY;
        const double dx = double(x - lastX), dy = double(y - lastY);
        lastX = x; lastY = y;
        if (!g_captured || NativeActive() || dt <= 0 || dt > 0.25) return held = {};
        held = { MouseToStick(dx / dt), MouseToStick((g_invertY ? dy : -dy) / dt) };   // raw y grows downward
        return held;
    }

    void ScriptThread(std::string path)
    {
        struct Ev { double t; std::string kind; double a, b, c; };
        std::vector<Ev> evs;
        std::ifstream f(path);
        std::string line;
        while (std::getline(f, line))
        {
            if (auto h = line.find('#'); h != std::string::npos) line.resize(h);
            std::istringstream in(line);
            Ev e{};
            if (!(in >> e.t >> e.kind)) continue;
            if (e.kind == "key") { std::string vk; in >> vk >> e.b; e.a = double(std::stoul(vk, nullptr, 16)); if (!e.b) e.b = 250; }
            else if (e.kind == "mouse") { in >> e.a >> e.b >> e.c; if (!e.c) e.c = 200; }
            else if (e.kind == "pad") { std::string b; in >> b >> e.b; e.a = double(std::stoul(b, nullptr, 16)); if (!e.b) e.b = 250; }
            else if (e.kind == "cursor") { in >> e.a >> e.b; }
            else if (e.kind == "glide") { in >> e.a >> e.b >> e.c; if (!e.c) e.c = 500; }
            else if (e.kind == "click") { std::string which; in >> which >> e.b; e.a = (which == "R") ? 1 : 0; if (!e.b) e.b = 120; }
            else if (e.kind == "wheel") { in >> e.a; }
            else if (e.kind == "trig") { std::string which; in >> which >> e.b; e.a = (which == "R") ? 1 : 0; if (!e.b) e.b = 200; }
            evs.push_back(e);
        }
        std::sort(evs.begin(), evs.end(), [](const Ev& x, const Ev& y) { return x.t < y.t; });
        LOGFN("input: playing {} scripted events from {}", evs.size(), path);
        const uint64_t start = GetTickCount64();
        for (const Ev& e : evs)
        {
            while (double(GetTickCount64() - start) / 1000.0 < e.t) Sleep(1);
            if (e.kind == "key")
            {
                const int vk = int(e.a) & 0xFF;
                std::thread([vk, hold = e.b] { g_scriptKeys[vk] = true; Sleep(DWORD(hold)); g_scriptKeys[vk] = false; }).detach();
            }
            else if (e.kind == "mouse")
            {
                std::thread([dx = e.a, dy = e.b, ms = e.c] {
                    const int steps = std::max(1, int(ms / 5));
                    for (int i = 0; i < steps; i++) { g_rawX += int64_t(dx / steps); g_rawY += int64_t(dy / steps); Sleep(5); }
                }).detach();
            }
            else if (e.kind == "capture") g_captured = true;
            else if (e.kind == "cursor") { g_scriptCursorX = int(e.a); g_scriptCursorY = int(e.b); }
            else if (e.kind == "glide")   // the cursor moves to (a, b) over c ms, as a hand would
            {
                std::thread([x1 = e.a, y1 = e.b, ms = e.c] {
                    const double x0 = g_scriptCursorX, y0 = g_scriptCursorY;
                    const uint64_t t0 = GetTickCount64();
                    LOGFN("input: glide {:.0f},{:.0f} -> {:.0f},{:.0f} over {:.0f} ms from t {}", x0, y0, x1, y1, ms, t0);
                    for (;;)
                    {
                        const double f = std::min(1.0, double(GetTickCount64() - t0) / ms);
                        g_scriptCursorX = int(std::lround(x0 + (x1 - x0) * f)); g_scriptCursorY = int(std::lround(y0 + (y1 - y0) * f));
                        if (f >= 1) break;
                        Sleep(2);
                    }
                }).detach();
            }
            else if (e.kind == "click")
            {
                std::thread([right = e.a != 0, hold = e.b] {
                    (right ? g_scriptRight : g_scriptLeft) = true; Sleep(DWORD(hold)); (right ? g_scriptRight : g_scriptLeft) = false;
                }).detach();
            }
            else if (e.kind == "wheel") g_scriptWheel += int(e.a);
            else if (e.kind == "trig")
            {
                std::thread([right = e.a != 0, hold = e.b] {
                    (right ? g_scriptRT : g_scriptLT) = 255; Sleep(DWORD(hold)); (right ? g_scriptRT : g_scriptLT) = 0;
                }).detach();
            }
            else if (e.kind == "pad")
            {
                std::thread([b = uint16_t(e.a), hold = e.b] { g_scriptPadButtons = b; Sleep(DWORD(hold)); g_scriptPadButtons = 0; }).detach();
            }
        }
    }

    std::once_flag g_init;
    void Init()
    {
        if (const char* script = getenv("GOW3_INPUT_SCRIPT"))
        {
            g_scripted = true;
            std::thread(ScriptThread, std::string(script)).detach();
        }
        g_sensitivity = settings::Get().mouseSensitivity;
        g_invertY = settings::Get().invertMouseY;
        g_nativeMode = settings::Get().nativeMouse;
        std::thread(RawInputThread).detach();
        std::thread(MouseThread).detach();
        LOGFN("input: XInput pads + keyboard/mouse on player 1 (mouse {}, sensitivity {}, invert {})",
            g_nativeMode ? "native" : "as right stick", g_sensitivity, g_invertY);
    }

    int16_t Axis(bool neg, bool pos) { return int16_t((pos ? 32767 : 0) - (neg ? 32767 : 0)); }

    // keyboard + mouse as a gamepad (zero when the window isn't focused)
    XINPUT_GAMEPAD KeyboardMouse()
    {
        XINPUT_GAMEPAD g{};
        const MouseLook look = PollMouse();
        if (!Focused()) return g;
        const bool alt = Down(VK_MENU);   // Alt+Enter is the fullscreen toggle, not Start
        for (const auto& b : kButtons)
        {
            if (!Down(b.vk) || (alt && b.vk == VK_RETURN)) continue;
            // in menus Esc goes back (B), the PC convention; in play it pauses (Start)
            g.wButtons |= (b.vk == VK_ESCAPE && g_menuMode) ? BTN_B : b.button;
        }
        g.sThumbLX = Axis(Down('A'), Down('D'));
        g.sThumbLY = Axis(Down('S'), Down('W'));
        g.sThumbRX = look.x;
        g.sThumbRY = look.y;
        if (g_captured)
        {
            if (Down(VK_LBUTTON)) g.bRightTrigger = 255;
            if (Down(VK_RBUTTON)) g.bLeftTrigger = 255;
        }
        return g;
    }

    int16_t Pick(int16_t pad, int16_t kbm) { return kbm ? kbm : pad; }

    // last used device: keys, mouse buttons or mouse movement -> keyboard/mouse; pad buttons, a stick past 30% or a
    // trigger -> controller
    std::atomic<hid::Device> g_device{ hid::Device::KeyboardMouse };
    int64_t g_lastRawX = 0, g_lastRawY = 0;
    void TrackDevice(const XINPUT_STATE* pad, const XINPUT_GAMEPAD& k)
    {
        const int64_t rx = g_rawX, ry = g_rawY;
        static int64_t lastAny = 0;
        const int64_t any = g_anyMouse;
        const bool mouseMoved = std::abs(rx - g_lastRawX) + std::abs(ry - g_lastRawY) > 4 || (any != lastAny && g_menuMode);
        lastAny = any;
        g_lastRawX = rx; g_lastRawY = ry;
        if (k.wButtons || k.bLeftTrigger || k.bRightTrigger || k.sThumbLX || k.sThumbLY || mouseMoved ||
            (Focused() && (Down(VK_LBUTTON) || Down(VK_RBUTTON))))
        {
            g_device = hid::Device::KeyboardMouse;
            return;
        }
        if (!pad) return;
        const XINPUT_GAMEPAD& p = pad->Gamepad;
        constexpr int kStick = 32767 * 30 / 100;
        if (p.wButtons || p.bLeftTrigger > 40 || p.bRightTrigger > 40 || std::abs(p.sThumbLX) > kStick ||
            std::abs(p.sThumbLY) > kStick || std::abs(p.sThumbRX) > kStick || std::abs(p.sThumbRY) > kStick)
            g_device = hid::Device::Controller;
    }

    // current state of a user's controller; false = not connected
    bool Read(uint32_t user, XINPUT_GAMEPAD& out)
    {
        std::call_once(g_init, Init);
        XINPUT_STATE pad{};
        bool hasPad = !g_scripted && XInputGetState(user, &pad) == ERROR_SUCCESS;
        if (g_scripted && user == 0 && g_scriptPadButtons) { pad.Gamepad.wButtons = g_scriptPadButtons; hasPad = true; }
        if (g_scripted && user == 0 && (g_scriptLT || g_scriptRT))
        {
            pad.Gamepad.bLeftTrigger = g_scriptLT; pad.Gamepad.bRightTrigger = g_scriptRT; hasPad = true;
        }
        if (user != 0)
        {
            if (hasPad) out = pad.Gamepad;
            return hasPad;
        }
        const XINPUT_GAMEPAD k = KeyboardMouse();
        TrackDevice(hasPad ? &pad : nullptr, k);
        out = hasPad ? pad.Gamepad : XINPUT_GAMEPAD{};
        out.wButtons |= k.wButtons;
        const uint16_t injected = GetTickCount64() < g_injectedUntil ? uint16_t(g_injected) : 0;
        out.wButtons |= injected;
        // in menus, every change of buttons by source (a player's report of menus not answering can then be read
        // from build\gow3.log): pad, keyboard/mouse, and presses injected by the menu mouse (ui/gfx_menu.cpp)
        if (g_menuMode)
        {
            static uint16_t lastPad = 0, lastKey = 0, lastInj = 0;
            static int logged = 0;
            const uint16_t p = hasPad ? pad.Gamepad.wButtons : 0;
            if ((p != lastPad || k.wButtons != lastKey || injected != lastInj) && logged++ < 600)
                LOGFN("menu input: pad {:04X} keyboard {:04X} injected {:04X}", p, k.wButtons, injected);
            lastPad = p; lastKey = k.wButtons; lastInj = injected;
        }
        out.bLeftTrigger = std::max(out.bLeftTrigger, k.bLeftTrigger);
        out.bRightTrigger = std::max(out.bRightTrigger, k.bRightTrigger);
        out.sThumbLX = Pick(out.sThumbLX, k.sThumbLX);
        out.sThumbLY = Pick(out.sThumbLY, k.sThumbLY);
        out.sThumbRX = Pick(out.sThumbRX, k.sThumbRX);
        out.sThumbRY = Pick(out.sThumbRY, k.sThumbRY);
        return true;
    }

    // connection changes are announced like the console does (Xenia InputSystem): XN_SYS_INPUTDEVICESCHANGED, which
    // makes the title re-query capabilities. Player 1 counts as connected from the first poll (keyboard).
    std::atomic<uint32_t> g_connected{ 0 };
    void UpdateConnected(uint32_t user, bool connected)
    {
        const uint32_t bit = 1u << user;
        const uint32_t old = connected ? g_connected.fetch_or(bit) : g_connected.fetch_and(~bit);
        if (((old & bit) != 0) != connected)
        {
            LOGFN("input: controller {} {}", user + 1, connected ? "connected" : "disconnected");
            XamNotifyEnqueueEvent(0x00000012, 0);
        }
    }

    uint32_t g_packet[4] = {};
    XINPUT_GAMEPAD g_last[4] = {};
    const bool g_debug = getenv("GOW3_INPUT_DEBUG") != nullptr;
}

uint32_t hid::GetCapabilities(uint32_t userIndex, XAMINPUT_CAPABILITIES* caps)
{
    XINPUT_GAMEPAD g;
    if (userIndex >= 4)
        return ERROR_DEVICE_NOT_CONNECTED;
    const bool connected = Read(userIndex, g);
    UpdateConnected(userIndex, connected);
    if (!connected)
        return ERROR_DEVICE_NOT_CONNECTED;
    memset(caps, 0, sizeof(*caps));
    caps->Type = 1;                         // XINPUT_DEVTYPE_GAMEPAD
    caps->SubType = 1;                      // XINPUT_DEVSUBTYPE_GAMEPAD
    caps->Gamepad.wButtons = 0xF3FF;        // every button
    caps->Gamepad.bLeftTrigger = caps->Gamepad.bRightTrigger = 0xFF;
    caps->Gamepad.sThumbLX = caps->Gamepad.sThumbLY = caps->Gamepad.sThumbRX = caps->Gamepad.sThumbRY = int16_t(0xFFC0);
    caps->Vibration.wLeftMotorSpeed = caps->Vibration.wRightMotorSpeed = 0xFFFF;
    return ERROR_SUCCESS;
}

// host-endian here; the XAM layer byte-swaps
namespace mouse_menu { void OnGameThread(); }   // ui/mouse_menu.cpp
namespace gfx_menu { void OnGameThread(); }     // ui/gfx_menu.cpp
namespace navmesh_build { void OnGameThread(); }   // ue3/navmesh_build.cpp
namespace campaign_chapters { void OnGameThread(); }   // ue3/campaign_chapters.cpp

uint32_t hid::GetState(uint32_t userIndex, XAMINPUT_STATE* state)
{
    if (userIndex == 0) { mouse_menu::OnGameThread(); gfx_menu::OnGameThread(); navmesh_build::OnGameThread(); campaign_chapters::OnGameThread(); }   // the game thread, once a frame
    XINPUT_GAMEPAD g;
    if (userIndex >= 4)
        return ERROR_DEVICE_NOT_CONNECTED;
    const bool connected = Read(userIndex, g);
    UpdateConnected(userIndex, connected);
    if (!connected)
        return ERROR_DEVICE_NOT_CONNECTED;
    if (g_debug)
    {
        static int polls[4] = {};
        for (int vk = 0x08; vk <= 0x5A; vk++)
            if (GetAsyncKeyState(vk) & 0x8000) { LOGFN("input: raw key {:02X} down (focused {})", vk, Focused()); break; }
        if (polls[userIndex]++ % 600 == 0)
            LOGFN("input: poll #{} user {} focused {} captured {}", polls[userIndex], userIndex, Focused(), g_captured.load());
    }
    if (memcmp(&g, &g_last[userIndex], sizeof(g)) != 0)
    {
        if (g_debug)
            LOGFN("input: user {} buttons {:04X} LT {} RT {} LS {},{} RS {},{}", userIndex, g.wButtons, g.bLeftTrigger,
                g.bRightTrigger, g.sThumbLX, g.sThumbLY, g.sThumbRX, g.sThumbRY);
        g_last[userIndex] = g;
        g_packet[userIndex]++;   // titles skip unchanged packets
    }
    state->dwPacketNumber = g_packet[userIndex];
    state->Gamepad.wButtons = g.wButtons;
    state->Gamepad.bLeftTrigger = g.bLeftTrigger;
    state->Gamepad.bRightTrigger = g.bRightTrigger;
    state->Gamepad.sThumbLX = g.sThumbLX;
    state->Gamepad.sThumbLY = g.sThumbLY;
    state->Gamepad.sThumbRX = g.sThumbRX;
    state->Gamepad.sThumbRY = g.sThumbRY;
    return ERROR_SUCCESS;
}

uint32_t hid::SetState(uint32_t userIndex, XAMINPUT_VIBRATION* vibration)
{
    if (userIndex >= 4)
        return ERROR_DEVICE_NOT_CONNECTED;
    XINPUT_VIBRATION v{ vibration->wLeftMotorSpeed, vibration->wRightMotorSpeed };
    const uint32_t r = XInputSetState(userIndex, &v);
    return userIndex == 0 ? ERROR_SUCCESS : r;   // player 1 is connected even without a pad (keyboard)
}

// ---- native mouse look (mouse_look.cpp) ----
bool hid::IsNativeMouseEnabled() { return g_nativeMode && g_captured; }

void hid::TakeMouseDelta(float& dx, float& dy)
{
    // the engine turns MouseSensitivity (60) rotation units per count: 600 counts = ~200 degrees. 0.2 per count at
    // sensitivity 1.0 makes 360 degrees about 5.5 in (14 cm) at 800 DPI, a usual third-person speed
    constexpr float kBase = 0.2f;
    static int64_t lastX = 0, lastY = 0;
    const int64_t x = g_rawX, y = g_rawY;
    dx = float(x - lastX) * kBase * g_sensitivity;
    dy = float(y - lastY) * kBase * g_sensitivity * (g_invertY ? -1.0f : 1.0f);
    lastX = x; lastY = y;
    g_nativeTick = GetTickCount64();
}

hid::Device hid::ActiveDevice() { return g_device; }

void hid::SetMenuMode(bool on) { g_menuMode = on; }
bool hid::MenuMode() { return g_menuMode; }

void hid::InjectButtons(uint16_t buttons, int milliseconds)
{
    g_injected = buttons;
    g_injectedUntil = GetTickCount64() + uint64_t(milliseconds);
}

bool hid::MenuCursor(float& x, float& y)
{
    if (g_scripted)
    {
        x = float(g_scriptCursorX); y = float(g_scriptCursorY);
        return g_scriptCursorX >= 0;
    }
    HWND w = Window();
    POINT p; RECT r;
    if (!w || !Focused() || !GetCursorPos(&p) || !ScreenToClient(w, &p) || !GetClientRect(w, &r) || r.right <= 0 || r.bottom <= 0)
        return false;
    x = float(p.x) * 1280.0f / float(r.right);
    y = float(p.y) * 720.0f / float(r.bottom);
    return PtInRect(&r, p) != 0;
}

hid::MenuMouse hid::PollMenuMouse()
{
    MenuMouse m{ -1, -1, false, false, false, 0 };
    if (g_scripted)
    {
        m.x = float(g_scriptCursorX); m.y = float(g_scriptCursorY);
        m.inside = g_scriptCursorX >= 0;
        m.left = g_scriptLeft; m.right = g_scriptRight;
        m.wheel = g_scriptWheel.exchange(0);
        return m;
    }
    HWND w = Window();
    POINT p; RECT r;
    if (!w || !Focused() || !GetCursorPos(&p) || !ScreenToClient(w, &p) || !GetClientRect(w, &r) || r.right <= 0 || r.bottom <= 0)
        return m;
    // the game's UI canvas is 1280x720 stretched over the whole client area
    m.x = float(p.x) * 1280.0f / float(r.right);
    m.y = float(p.y) * 720.0f / float(r.bottom);
    m.inside = PtInRect(&r, p) != 0;
    m.left = Down(VK_LBUTTON);
    m.right = Down(VK_RBUTTON);
    m.wheel = g_wheel.exchange(0);
    return m;
}
