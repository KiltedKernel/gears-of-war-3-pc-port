#pragma once
// Input for the port's runtime: XInput pads (hid.cpp)
namespace hid
{
    inline uint16_t g_prohibitedButtons = 0;
    inline bool g_isLeftStickProhibited = false;
    inline bool g_isRightStickProhibited = false;
    uint32_t GetCapabilities(uint32_t userIndex, XAMINPUT_CAPABILITIES* caps);
    uint32_t GetState(uint32_t userIndex, XAMINPUT_STATE* state);
    uint32_t SetState(uint32_t userIndex, XAMINPUT_VIBRATION* vibration);
    inline bool IsInputAllowed() { return true; }
    // native mouse look (hid/mouse_look.cpp): raw counts since the previous call, scaled by the sensitivity setting
    bool IsNativeMouseEnabled();
    void TakeMouseDelta(float& dx, float& dy);
    inline bool IsInputDeviceController() { return true; }
    // the device the player used last (button prompts follow it: runtime/ui/prompts.cpp); starts as keyboard/mouse
    enum class Device { KeyboardMouse, Controller };
    Device ActiveDevice();

    // menus (runtime/ui/gfx_menu.cpp): while a menu has focus the mouse is not captured and the cursor shows;
    // the menu code reads it in UI space (1280x720) and presses virtual controller buttons for hover and clicks
    void SetMenuMode(bool on);
    bool MenuMode();
    struct MenuMouse { float x, y; bool inside, left, right; int wheel; };
    MenuMouse PollMenuMouse();                       // wheel: notches since the previous poll
    bool MenuCursor(float& x, float& y);              // the cursor on the UI canvas now (false: outside / unfocused)
    void InjectButtons(uint16_t xinputButtons, int milliseconds);   // not counted as controller use
    // Esc in a menu: not a button by itself. The menu code (ui/gfx_menu.cpp) takes the press and decides: B, or nothing
    // on the main menu's first page (B there leaves for the title screen, which reloads the front end)
    bool TakeMenuEscape();
}
