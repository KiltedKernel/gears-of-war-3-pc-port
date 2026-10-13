// Gears of War 3 (PC port) launcher: an options window for gow3.ini (display, graphics, effects, controls, audio)
// with a Play button. Edits the ini in place (comments and unknown keys are kept), then starts gow3.exe from the
// same folder with the launcher's own command line forwarded.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <commctrl.h>
#include <shellapi.h>
#include <shellscalingapi.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "shcore.lib")

namespace
{
// ---- ini file: lines kept as they are; values looked up and replaced by section.key ----
struct Ini
{
    std::vector<std::string> lines;
    std::wstring path;

    static std::string Trim(std::string s)
    {
        const auto a = s.find_first_not_of(" \t\r"), b = s.find_last_not_of(" \t\r");
        return a == std::string::npos ? std::string() : s.substr(a, b - a + 1);
    }

    void Load(const std::wstring& p)
    {
        path = p;
        lines.clear();
        std::ifstream f(p);
        std::string l;
        while (std::getline(f, l)) lines.push_back(l);
    }

    // line index of section.key, -1 if missing; also the index after the section's last line (for inserts)
    int Find(const std::string& section, const std::string& key, int* sectionEnd = nullptr) const
    {
        std::string cur;
        int end = -1;
        for (int i = 0; i < int(lines.size()); i++)
        {
            const std::string t = Trim(lines[i]);
            if (t.size() > 2 && t.front() == '[' && t.back() == ']') { cur = t.substr(1, t.size() - 2); continue; }
            if (cur != section) continue;
            end = i + 1;
            if (t.empty() || t[0] == ';' || t[0] == '#') continue;
            const auto eq = t.find('=');
            if (eq != std::string::npos && Trim(t.substr(0, eq)) == key) return i;
        }
        if (sectionEnd) *sectionEnd = end;
        return -1;
    }

    std::string Get(const std::string& section, const std::string& key, const std::string& def) const
    {
        const int i = Find(section, key);
        if (i < 0) return def;
        std::string v = Trim(lines[i].substr(lines[i].find('=') + 1));
        if (const auto c = v.find(';'); c != std::string::npos) v = Trim(v.substr(0, c));
        return v;
    }

    void Set(const std::string& section, const std::string& key, const std::string& value)
    {
        int end = -1;
        const int i = Find(section, key, &end);
        const std::string line = key + " = " + value;
        if (i >= 0) { lines[i] = line; return; }
        if (end < 0)
        {
            if (!lines.empty() && !Trim(lines.back()).empty()) lines.push_back("");
            lines.push_back("[" + section + "]");
            lines.push_back(line);
            return;
        }
        // after the section's last key (before its trailing blank lines)
        int at = end;
        while (at > 0 && Trim(lines[at - 1]).empty()) at--;
        lines.insert(lines.begin() + at, line);
    }

    bool Save() const
    {
        std::ofstream f(path, std::ios::trunc);
        for (const auto& l : lines) f << l << "\n";
        return bool(f);
    }
};

// ---- the options ----
enum class Kind { Combo, Check, Slider, WindowSize };
struct Choice { const wchar_t* label; const char* value; };
struct Option
{
    int page;
    Kind kind;
    const char* section;
    const char* key;
    const char* def;
    const wchar_t* label;
    const wchar_t* help;
    std::vector<Choice> choices = {};     // Combo
    double minV = 0, maxV = 1, step = 0.1; // Slider
    const wchar_t* format = L"%.2f";
    HWND control = nullptr, valueLabel = nullptr;
};

const wchar_t* kPages[] = { L"Display", L"Graphics", L"Effects", L"Controls", L"Audio && Game" };

std::vector<Option> MakeOptions()
{
    return {
        { 0, Kind::Combo, "display", "fullscreen", "false", L"Display mode", L"Fullscreen covers the whole monitor (borderless). F11 or Alt+Enter switches while playing.",
          { { L"Windowed", "false" }, { L"Fullscreen (borderless)", "true" } } },
        { 0, Kind::WindowSize, "display", "window_width", "1280", L"Window size", L"The window's size in windowed mode." },
        { 0, Kind::Combo, "display", "resolution_scale", "auto", L"Render resolution", L"The resolution the game is drawn at. Higher is sharper and costs GPU time.",
          { { L"Auto (match the monitor)", "auto" }, { L"1280 x 720 (console)", "1" }, { L"2560 x 1440", "2" }, { L"3840 x 2160", "3" } } },
        { 0, Kind::Combo, "display", "frame_rate", "60", L"Frame rate limit", L"Game speed stays correct at any rate. Matching the monitor's refresh rate gives the smoothest motion.",
          { { L"Match monitor refresh rate", "auto" }, { L"30 (console)", "30" }, { L"60", "60" }, { L"90", "90" }, { L"120", "120" }, { L"144", "144" }, { L"165", "165" }, { L"240", "240" } } },
        { 0, Kind::Check, "display", "vsync", "true", L"Vertical sync", L"Waits for the monitor: no tearing, slightly more input latency." },
        { 0, Kind::Combo, "display", "upscaling", "fsr", L"Scaling filter", L"How the picture is scaled to the window. FSR upscales and sharpens.",
          { { L"AMD FSR", "fsr" }, { L"AMD CAS (sharpen only)", "cas" }, { L"Bilinear", "bilinear" } } },
        { 0, Kind::Slider, "display", "sharpness", "0.0", L"Extra sharpening", L"Additional CAS sharpening on top of the scaling filter.", {}, 0.0, 1.0, 0.05, L"%.2f" },
        { 0, Kind::Combo, "display", "antialiasing", "none", L"Anti-aliasing", L"Post-process anti-aliasing (smooths jagged edges, slightly softer image).",
          { { L"Off", "none" }, { L"FXAA", "fxaa" }, { L"FXAA (extreme)", "fxaa_extreme" } } },

        { 1, Kind::Check, "graphics", "anisotropic_16x", "true", L"16x anisotropic filtering", L"Sharper textures at angles (floors, walls)." },
        { 1, Kind::Check, "graphics", "xenos_sample_positions", "true", L"Accurate MSAA sample positions", L"Fewer broken edges and stray triangles." },
        { 1, Kind::Check, "graphics", "shader_cache", "true", L"Shader cache", L"Keeps compiled shaders on disk, so effects stutter only the first time you see them." },

        { 2, Kind::Check, "graphics", "motion_blur", "true", L"Motion blur", L"Blur from fast camera and object movement." },
        { 2, Kind::Check, "graphics", "depth_of_field", "true", L"Depth of field", L"Blurs what is out of focus (aiming, cutscenes)." },
        { 2, Kind::Check, "graphics", "bloom", "true", L"Bloom", L"The glow around bright light; also part of the game's colour grading." },
        { 2, Kind::Check, "graphics", "lens_flares", "true", L"Lens flares", L"Flares from bright lights." },
        { 2, Kind::Check, "graphics", "ambient_occlusion", "true", L"Ambient occlusion", L"Soft contact shadows in corners and under objects." },

        { 3, Kind::Slider, "input", "mouse_sensitivity", "1.0", L"Mouse sensitivity", L"Mouse look speed (1.0 = default).", {}, 0.1, 5.0, 0.05, L"%.2f" },
        { 3, Kind::Check, "input", "invert_mouse_y", "false", L"Invert mouse Y axis", L"Moving the mouse up looks down." },
        { 3, Kind::Combo, "input", "mouse_mode", "native", L"Mouse look", L"Native: the engine's own 1:1 mouse look. Stick: the mouse drives the right stick.",
          { { L"Native (recommended)", "native" }, { L"Right-stick emulation", "stick" } } },
        { 3, Kind::Combo, "input", "prompts", "auto", L"Button prompts", L"Which button icons the game shows.",
          { { L"Follow the device in use", "auto" }, { L"Always keyboard and mouse", "keyboard" }, { L"Always controller", "controller" } } },

        { 4, Kind::Check, "audio", "enabled", "true", L"Sound", L"All game audio." },
        { 4, Kind::Check, "display", "skip_intro_movies", "false", L"Skip intro movies", L"No Epic Games / Microsoft Game Studios logos at startup (same as gow3.exe -nointro)." },
    };
}

const std::pair<int, int> kWindowSizes[] = { { 1280, 720 }, { 1600, 900 }, { 1920, 1080 }, { 2560, 1440 }, { 3200, 1800 }, { 3840, 2160 } };

// ---- window ----
HINSTANCE g_inst;
HWND g_wnd, g_tabs, g_status, g_monitorInfo;
HFONT g_font, g_fontBold, g_fontSmall, g_fontTitle;
UINT g_dpi = 96;
Ini g_ini;
std::vector<Option> g_options;
std::vector<HWND> g_pageControls[5];
std::wstring g_exeDir;
enum { ID_TABS = 100, ID_PLAY, ID_SAVE, ID_DEFAULTS, ID_CLOSE, ID_OPTION0 = 1000 };

int S(int v) { return MulDiv(v, int(g_dpi), 96); }
std::wstring W(const std::string& s) { return std::wstring(s.begin(), s.end()); }

HWND Make(const wchar_t* cls, const wchar_t* text, DWORD style, int x, int y, int w, int h, int id, HFONT font, DWORD ex = 0)
{
    HWND c = CreateWindowExW(ex, cls, text, WS_CHILD | style, S(x), S(y), S(w), S(h), g_wnd, (HMENU)(INT_PTR)id, g_inst, nullptr);
    SendMessageW(c, WM_SETFONT, (WPARAM)font, TRUE);
    return c;
}

void SliderText(Option& o)
{
    const int pos = int(SendMessageW(o.control, TBM_GETPOS, 0, 0));
    wchar_t buf[32];
    swprintf(buf, 32, o.format, o.minV + pos * o.step);
    SetWindowTextW(o.valueLabel, buf);
}

std::string FormatValue(double v)
{
    char buf[32];
    snprintf(buf, sizeof(buf), "%.2f", v);
    return buf;
}

void LoadValues(bool defaults)
{
    for (Option& o : g_options)
    {
        const std::string v = defaults ? o.def : g_ini.Get(o.section, o.key, o.def);
        switch (o.kind)
        {
        case Kind::Check:
            SendMessageW(o.control, BM_SETCHECK, (v == "true" || v == "1" || v == "on" || v == "yes") ? BST_CHECKED : BST_UNCHECKED, 0);
            break;
        case Kind::Combo:
        {
            int sel = 0;
            for (size_t i = 0; i < o.choices.size(); i++) if (v == o.choices[i].value) sel = int(i);
            SendMessageW(o.control, CB_SETCURSEL, sel, 0);
            break;
        }
        case Kind::Slider:
        {
            const double d = std::clamp(atof(v.c_str()), o.minV, o.maxV);
            SendMessageW(o.control, TBM_SETPOS, TRUE, LPARAM(std::lround((d - o.minV) / o.step)));
            SliderText(o);
            break;
        }
        case Kind::WindowSize:
        {
            const int w = defaults ? 1280 : atoi(g_ini.Get("display", "window_width", "1280").c_str());
            const int h = defaults ? 720 : atoi(g_ini.Get("display", "window_height", "720").c_str());
            int sel = -1;
            for (int i = 0; i < int(std::size(kWindowSizes)); i++) if (kWindowSizes[i].first == w && kWindowSizes[i].second == h) sel = i;
            const int count = int(SendMessageW(o.control, CB_GETCOUNT, 0, 0));
            if (count > int(std::size(kWindowSizes))) SendMessageW(o.control, CB_DELETESTRING, count - 1, 0);
            if (sel < 0)   // a custom size from the ini: keep it as an extra entry
            {
                wchar_t buf[48]; swprintf(buf, 48, L"%d x %d (custom)", w, h);
                sel = int(SendMessageW(o.control, CB_ADDSTRING, 0, (LPARAM)buf));
                SendMessageW(o.control, CB_SETITEMDATA, sel, MAKELPARAM(w, h));
            }
            SendMessageW(o.control, CB_SETCURSEL, sel, 0);
            break;
        }
        }
    }
}

bool SaveValues()
{
    for (Option& o : g_options)
    {
        switch (o.kind)
        {
        case Kind::Check:
            g_ini.Set(o.section, o.key, SendMessageW(o.control, BM_GETCHECK, 0, 0) == BST_CHECKED ? "true" : "false");
            break;
        case Kind::Combo:
        {
            const int sel = int(SendMessageW(o.control, CB_GETCURSEL, 0, 0));
            if (sel >= 0 && sel < int(o.choices.size())) g_ini.Set(o.section, o.key, o.choices[sel].value);
            break;
        }
        case Kind::Slider:
            g_ini.Set(o.section, o.key, FormatValue(o.minV + SendMessageW(o.control, TBM_GETPOS, 0, 0) * o.step));
            break;
        case Kind::WindowSize:
        {
            const int sel = int(SendMessageW(o.control, CB_GETCURSEL, 0, 0));
            const LPARAM d = SendMessageW(o.control, CB_GETITEMDATA, sel, 0);
            g_ini.Set("display", "window_width", std::to_string(LOWORD(d)));
            g_ini.Set("display", "window_height", std::to_string(HIWORD(d)));
            break;
        }
        }
    }
    return g_ini.Save();
}

void ShowPage(int page)
{
    for (int p = 0; p < 5; p++)
        for (HWND c : g_pageControls[p]) ShowWindow(c, p == page ? SW_SHOW : SW_HIDE);
    ShowWindow(g_monitorInfo, page == 0 ? SW_SHOW : SW_HIDE);
}

void Status(const wchar_t* text) { SetWindowTextW(g_status, text); }

bool Play()
{
    if (!SaveValues()) { Status(L"Could not save gow3.ini."); return false; }
    const std::wstring exe = g_exeDir + L"\\gow3.exe";
    // forward the launcher's own arguments (a disc folder, -nointro ...)
    std::wstring args;
    int argc = 0;
    if (LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc))
    {
        for (int i = 1; i < argc; i++) { args += L" \""; args += argv[i]; args += L"\""; }
        LocalFree(argv);
    }
    std::wstring cmd = L"\"" + exe + L"\"" + args;
    STARTUPINFOW si{ sizeof(si) };
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(exe.c_str(), cmd.data(), nullptr, nullptr, FALSE, 0, nullptr, g_exeDir.c_str(), &si, &pi))
    {
        Status(L"Could not start gow3.exe (is it next to the launcher?).");
        return false;
    }
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return true;
}

void Build()
{
    const int left = 28, labelW = 220, ctrlX = 260, ctrlW = 320, top = 118, rowH = 50;
    Make(L"STATIC", L"GEARS OF WAR 3", SS_LEFT | WS_VISIBLE, 24, 14, 500, 34, 0, g_fontTitle);
    Make(L"STATIC", L"PC port \x2014 options", SS_LEFT | WS_VISIBLE, 26, 48, 400, 20, 0, g_fontSmall);
    g_tabs = Make(WC_TABCONTROLW, L"", WS_VISIBLE | WS_CLIPSIBLINGS, 16, 76, 608, 494, ID_TABS, g_font);
    for (int i = 0; i < 5; i++)
    {
        TCITEMW t{ TCIF_TEXT };
        t.pszText = (LPWSTR)kPages[i];
        SendMessageW(g_tabs, TCM_INSERTITEMW, i, (LPARAM)&t);
    }
    // the monitor, for "match the monitor" choices
    DEVMODEW dm{}; dm.dmSize = sizeof(dm);
    EnumDisplaySettingsW(nullptr, ENUM_CURRENT_SETTINGS, &dm);
    wchar_t mon[96];
    swprintf(mon, 96, L"Monitor: %u x %u at %u Hz", dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency);
    g_monitorInfo = Make(L"STATIC", mon, SS_RIGHT | WS_VISIBLE, 330, 50, 280, 20, 0, g_fontSmall);

    int rowOnPage[5] = {};
    for (size_t i = 0; i < g_options.size(); i++)
    {
        Option& o = g_options[i];
        const int y = top + rowOnPage[o.page]++ * rowH;
        auto& page = g_pageControls[o.page];
        page.push_back(Make(L"STATIC", o.label, SS_LEFT, left, y + 3, labelW, 22, 0, g_fontBold));
        page.push_back(Make(L"STATIC", o.help, SS_LEFT | SS_ENDELLIPSIS, left, y + 24, 580, 18, 0, g_fontSmall));
        const int id = ID_OPTION0 + int(i);
        switch (o.kind)
        {
        case Kind::Check:
            o.control = Make(L"BUTTON", L"", BS_AUTOCHECKBOX | WS_TABSTOP, ctrlX, y, 30, 24, id, g_font);
            break;
        case Kind::Combo:
            o.control = Make(WC_COMBOBOXW, L"", CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP, ctrlX, y, ctrlW, 300, id, g_font);
            for (const Choice& c : o.choices) SendMessageW(o.control, CB_ADDSTRING, 0, (LPARAM)c.label);
            break;
        case Kind::WindowSize:
            o.control = Make(WC_COMBOBOXW, L"", CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP, ctrlX, y, ctrlW, 300, id, g_font);
            for (auto [w, h] : kWindowSizes)
            {
                wchar_t buf[32]; swprintf(buf, 32, L"%d x %d", w, h);
                const int k = int(SendMessageW(o.control, CB_ADDSTRING, 0, (LPARAM)buf));
                SendMessageW(o.control, CB_SETITEMDATA, k, MAKELPARAM(w, h));
            }
            break;
        case Kind::Slider:
            o.control = Make(TRACKBAR_CLASSW, L"", TBS_HORZ | TBS_NOTICKS | WS_TABSTOP, ctrlX - 6, y - 2, ctrlW - 50, 28, id, g_font);
            SendMessageW(o.control, TBM_SETRANGE, TRUE, MAKELPARAM(0, int(std::lround((o.maxV - o.minV) / o.step))));
            o.valueLabel = Make(L"STATIC", L"", SS_RIGHT, ctrlX + ctrlW - 50, y + 3, 50, 22, 0, g_font);
            page.push_back(o.valueLabel);
            break;
        }
        page.push_back(o.control);
    }
    Make(L"BUTTON", L"Restore defaults", BS_PUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 16, 584, 140, 34, ID_DEFAULTS, g_font);
    g_status = Make(L"STATIC", L"", SS_LEFT | WS_VISIBLE | SS_ENDELLIPSIS, 166, 593, 150, 20, 0, g_fontSmall);
    Make(L"BUTTON", L"Save", BS_PUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 324, 584, 90, 34, ID_SAVE, g_font);
    Make(L"BUTTON", L"Close", BS_PUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 422, 584, 90, 34, ID_CLOSE, g_font);
    Make(L"BUTTON", L"Play", BS_DEFPUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 520, 584, 104, 34, ID_PLAY, g_fontBold);
    // tabs draw over siblings unless they come last in the z order
    SetWindowPos(g_tabs, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
    LoadValues(false);
    ShowPage(0);
}

void MakeFonts()
{
    auto font = [](int pt, int weight, const wchar_t* face) {
        return CreateFontW(-MulDiv(pt, int(g_dpi), 72), 0, 0, 0, weight, FALSE, FALSE, FALSE, DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
            CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY, DEFAULT_PITCH, face);
    };
    g_font = font(9, FW_NORMAL, L"Segoe UI");
    g_fontBold = font(9, FW_SEMIBOLD, L"Segoe UI");
    g_fontSmall = font(8, FW_NORMAL, L"Segoe UI");
    g_fontTitle = font(18, FW_BOLD, L"Segoe UI");
}

LRESULT CALLBACK WndProc(HWND h, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg)
    {
    case WM_NOTIFY:
        if (((NMHDR*)lp)->idFrom == ID_TABS && ((NMHDR*)lp)->code == TCN_SELCHANGE)
            ShowPage(int(SendMessageW(g_tabs, TCM_GETCURSEL, 0, 0)));
        return 0;
    case WM_HSCROLL:
        for (Option& o : g_options) if ((HWND)lp == o.control) SliderText(o);
        return 0;
    case WM_COMMAND:
        switch (LOWORD(wp))
        {
        case ID_PLAY: if (Play()) DestroyWindow(h); return 0;
        case ID_SAVE: Status(SaveValues() ? L"Saved." : L"Could not save gow3.ini."); return 0;
        case ID_DEFAULTS: LoadValues(true); Status(L"Defaults restored (not saved yet)."); return 0;
        case ID_CLOSE: DestroyWindow(h); return 0;
        }
        if (HIWORD(wp) == BN_CLICKED || HIWORD(wp) == CBN_SELCHANGE) Status(L"");
        return 0;
    case WM_CTLCOLORSTATIC:
    {
        // labels on the tab page: the window colour (the tab control paints that colour)
        SetBkMode((HDC)wp, TRANSPARENT);
        return (LRESULT)GetSysColorBrush(COLOR_WINDOW);
    }
    case WM_DESTROY:
        PostQuitMessage(0);
        return 0;
    }
    return DefWindowProcW(h, msg, wp, lp);
}
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE, LPWSTR, int show)
{
    g_inst = inst;
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    INITCOMMONCONTROLSEX icc{ sizeof(icc), ICC_TAB_CLASSES | ICC_BAR_CLASSES | ICC_STANDARD_CLASSES };
    InitCommonControlsEx(&icc);

    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    g_exeDir = exe;
    g_exeDir.resize(g_exeDir.find_last_of(L"\\/"));
    // test mode (GOW3_LAUNCHER_SHOT=<folder>): each page drawn off-screen to page<N>.bmp, the ini saved, no focus taken
    wchar_t shotDir[MAX_PATH] = {};
    const bool shot = GetEnvironmentVariableW(L"GOW3_LAUNCHER_SHOT", shotDir, MAX_PATH) > 0;
    wchar_t iniOverride[MAX_PATH] = {};
    g_ini.Load(GetEnvironmentVariableW(L"GOW3_LAUNCHER_INI", iniOverride, MAX_PATH) > 0 ? std::wstring(iniOverride)
                                                                                       : g_exeDir + L"\\gow3.ini");
    g_options = MakeOptions();

    WNDCLASSEXW wc{ sizeof(wc) };
    wc.lpfnWndProc = WndProc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = GetSysColorBrush(COLOR_WINDOW);
    wc.lpszClassName = L"GoW2Launcher";
    wc.hIcon = LoadIconW(inst, MAKEINTRESOURCEW(1));
    RegisterClassExW(&wc);

    // size the client area for the system DPI, then build at the window's own DPI
    g_dpi = GetDpiForSystem();
    RECT r{ 0, 0, S(640), S(634) };
    const DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    AdjustWindowRectExForDpi(&r, style, FALSE, 0, g_dpi);
    g_wnd = CreateWindowExW(shot ? WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW : 0, wc.lpszClassName, L"Gears of War 3 \x2014 Launcher", style, shot ? -20000 : CW_USEDEFAULT, shot ? -20000 : CW_USEDEFAULT,
        r.right - r.left, r.bottom - r.top, nullptr, nullptr, inst, nullptr);
    g_dpi = GetDpiForWindow(g_wnd);
    MakeFonts();
    Build();
    if (shot)
    {
        ShowWindow(g_wnd, SW_SHOWNOACTIVATE);
        RECT wr; GetWindowRect(g_wnd, &wr);
        const int w = wr.right - wr.left, h = wr.bottom - wr.top;
        for (int page = 0; page < 5; page++)
        {
            SendMessageW(g_tabs, TCM_SETCURSEL, page, 0);
            ShowPage(page);
            RedrawWindow(g_wnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW);
            HDC screen = GetDC(nullptr), dc = CreateCompatibleDC(screen);
            BITMAPINFO bi{ { sizeof(BITMAPINFOHEADER), w, -h, 1, 32, BI_RGB } };
            void* bits = nullptr;
            HBITMAP bmp = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
            SelectObject(dc, bmp);
            PrintWindow(g_wnd, dc, PW_RENDERFULLCONTENT);
            BITMAPFILEHEADER fh{ 0x4D42, DWORD(sizeof(fh) + sizeof(BITMAPINFOHEADER) + w * h * 4), 0, 0, DWORD(sizeof(fh) + sizeof(BITMAPINFOHEADER)) };
            wchar_t name[MAX_PATH]; swprintf(name, MAX_PATH, L"%s\\page%d.bmp", shotDir, page);
            if (FILE* f = _wfopen(name, L"wb"))
            {
                fwrite(&fh, sizeof(fh), 1, f);
                fwrite(&bi.bmiHeader, sizeof(BITMAPINFOHEADER), 1, f);
                fwrite(bits, 4, size_t(w) * h, f);
                fclose(f);
            }
            DeleteObject(bmp); DeleteDC(dc); ReleaseDC(nullptr, screen);
        }
        SaveValues();
        return 0;
    }
    ShowWindow(g_wnd, show);

    MSG m;
    while (GetMessageW(&m, nullptr, 0, 0) > 0)
    {
        if (IsDialogMessageW(g_wnd, &m)) continue;
        TranslateMessage(&m);
        DispatchMessageW(&m);
    }
    return 0;
}
