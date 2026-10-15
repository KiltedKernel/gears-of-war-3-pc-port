// Gears of War 3 (PC port) launcher: an options window for gow3.ini (display, graphics, effects, controls, audio),
// multiplayer (profile.ini's gamertag and gow3.ini [network]), a mod manager (the Mods page: enable / disable, load order, install a shared .zip) and a Play button. Edits the ini in place (comments and unknown keys are kept), then starts gow3.exe from the
// same folder with the launcher's own command line forwarded.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <winsock2.h>
#include <ws2tcpip.h>
#include <netfw.h>
#include <commctrl.h>
#include <shellapi.h>
#include <commdlg.h>
#include <shellscalingapi.h>
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include "../runtime/net/code.h"
#include "../runtime/net/vnet_protocol.h"
#include "../runtime/net/upnp.h"

#pragma comment(lib, "comctl32.lib")
#pragma comment(lib, "shcore.lib")
#pragma comment(lib, "comdlg32.lib")
#pragma comment(lib, "ws2_32.lib")

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

    void Remove(const std::string& section, const std::string& key)
    {
        const int i = Find(section, key);
        if (i >= 0) lines.erase(lines.begin() + i);
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

const wchar_t* kPages[] = { L"Display", L"Graphics", L"Effects", L"Controls", L"Audio && Game", L"Mods", L"Multiplayer" };
constexpr int kPageCount = 7;

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
HBRUSH g_tabBrush;   // the tab control's pane as drawn (MakeTabBrush): what labels on a page paint behind them
UINT g_dpi = 96;
Ini g_ini;
std::vector<Option> g_options;
std::vector<HWND> g_pageControls[kPageCount];
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

void SaveMultiplayer();   // the Multiplayer page

bool SaveValues()
{
    SaveMultiplayer();
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

void UpdateMultiplayer();     // the Multiplayer page
void ActivateMultiplayer();

void ShowPage(int page)
{
    for (int p = 0; p < kPageCount; p++)
        for (HWND c : g_pageControls[p]) ShowWindow(c, p == page ? SW_SHOW : SW_HIDE);
    ShowWindow(g_monitorInfo, page == 0 ? SW_SHOW : SW_HIDE);
    UpdateMultiplayer();
    if (page == 6) ActivateMultiplayer();
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

// ---- the Mods page: installed mods (mods\<folder>\mod.ini), enabled state, load order, install from a .zip ----
// The same rules as the runtime (runtime/kernel/mods.cpp) and tools/modtool.py: load order is priority, then name;
// when two enabled mods have the same file, the later one wins.
struct ModEntry
{
    std::wstring folder;            // folder name under mods
    std::string name, version, author, description;
    int priority = 0;
    bool enabled = true;
    std::vector<std::string> files; // lower-case relative paths (game files; not mod.ini, config, TOCs)
    int fragments = 0;              // config\*.ini
};

std::wstring g_modsDir;
std::vector<ModEntry> g_mods;
HWND g_modList, g_modInfo;
bool g_modsLoading = false;
enum { ID_MOD_LIST = 200, ID_MOD_UP, ID_MOD_DOWN, ID_MOD_INSTALL, ID_MOD_FOLDER, ID_MOD_REFRESH, ID_MOD_REMOVE };

std::string Narrow(const std::wstring& w)
{
    if (w.empty()) return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), int(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(size_t(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), int(w.size()), s.data(), n, nullptr, nullptr);
    return s;
}
std::wstring Wide(const std::string& s)
{
    if (s.empty()) return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), nullptr, 0);
    std::wstring w(size_t(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), int(s.size()), w.data(), n);
    return w;
}
std::string LowerA(std::string s)
{
    for (char& c : s) { c = char(tolower((unsigned char)c)); if (c == '/') c = '\\'; }
    return s;
}

void CollectFiles(const std::wstring& dir, const std::wstring& rel, ModEntry& m)
{
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileW((dir + L"\\*").c_str(), &fd);
    if (f == INVALID_HANDLE_VALUE) return;
    do
    {
        const std::wstring n = fd.cFileName;
        if (n == L"." || n == L"..") continue;
        const std::wstring r = rel.empty() ? n : rel + L"\\" + n;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)
        {
            if (rel.empty() && LowerA(Narrow(n)) == "config")
            {
                WIN32_FIND_DATAW cf;
                HANDLE c = FindFirstFileW((dir + L"\\" + n + L"\\*.ini").c_str(), &cf);
                if (c != INVALID_HANDLE_VALUE) { do m.fragments++; while (FindNextFileW(c, &cf)); FindClose(c); }
                continue;
            }
            CollectFiles(dir + L"\\" + n, r, m);
            continue;
        }
        const std::string low = LowerA(Narrow(r));
        if (low == "mod.ini" || (low.rfind("geargame\\xbox360toc", 0) == 0 && low.size() > 4 && low.substr(low.size() - 4) == ".txt")) continue;
        m.files.push_back(low);
    } while (FindNextFileW(f, &fd));
    FindClose(f);
}

void ScanMods()
{
    g_mods.clear();
    WIN32_FIND_DATAW fd;
    HANDLE f = FindFirstFileW((g_modsDir + L"\\*").c_str(), &fd);
    if (f != INVALID_HANDLE_VALUE)
    {
        do
        {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) || fd.cFileName[0] == L'.') continue;
            const std::wstring dir = g_modsDir + L"\\" + fd.cFileName;
            if (GetFileAttributesW((dir + L"\\mod.ini").c_str()) == INVALID_FILE_ATTRIBUTES) continue;
            Ini ini; ini.Load(dir + L"\\mod.ini");
            ModEntry m;
            m.folder = fd.cFileName;
            m.name = ini.Get("mod", "name", Narrow(m.folder));
            m.version = ini.Get("mod", "version", "");
            m.author = ini.Get("mod", "author", "");
            m.description = ini.Get("mod", "description", "");
            m.priority = atoi(ini.Get("mod", "priority", "0").c_str());
            const std::string en = LowerA(ini.Get("mod", "enabled", "true"));
            m.enabled = !(en == "false" || en == "0" || en == "no");
            CollectFiles(dir, L"", m);
            g_mods.push_back(std::move(m));
        } while (FindNextFileW(f, &fd));
        FindClose(f);
    }
    std::stable_sort(g_mods.begin(), g_mods.end(), [](const ModEntry& a, const ModEntry& b) {
        return a.priority != b.priority ? a.priority < b.priority : a.name < b.name; });
}

bool WriteModKey(const ModEntry& m, const char* key, const std::string& value)
{
    Ini ini; ini.Load(g_modsDir + L"\\" + m.folder + L"\\mod.ini");
    ini.Set("mod", key, value);
    return ini.Save();
}

// files this mod provides that another enabled mod also provides: (other mod's name, count)
std::vector<std::pair<std::string, int>> Conflicts(const ModEntry& m)
{
    std::vector<std::pair<std::string, int>> out;
    for (const ModEntry& o : g_mods)
    {
        if (&o == &m || !o.enabled) continue;
        int n = 0;
        for (const auto& f : m.files) if (std::find(o.files.begin(), o.files.end(), f) != o.files.end()) n++;
        if (n) out.emplace_back(o.name, n);
    }
    return out;
}

void ShowModInfo()
{
    const int sel = int(SendMessageW(g_modList, LVM_GETNEXTITEM, WPARAM(-1), LVNI_SELECTED));
    if (sel < 0 || sel >= int(g_mods.size()))
    {
        SetWindowTextW(g_modInfo, g_mods.empty() ? L"No mods installed. Put a mod's folder (with its mod.ini) in the mods folder, or use Install .zip."
                                                 : L"Select a mod to see what it changes. Later mods in the list win when two change the same file.");
        return;
    }
    const ModEntry& m = g_mods[sel];
    std::wstring t = Wide(m.description.empty() ? "(no description)" : m.description);
    t += L"\r\n" + std::to_wstring(m.files.size()) + L" game file(s)";
    if (m.fragments) t += L", " + std::to_wstring(m.fragments) + L" config fragment(s) (merged with other mods')";
    t += L"; folder mods\\" + m.folder + L".";
    if (m.enabled)
        for (const auto& [other, n] : Conflicts(m))
            t += L"\r\nShares " + std::to_wstring(n) + L" file(s) with " + Wide(other) + L" (the later in the list wins).";
    SetWindowTextW(g_modInfo, t.c_str());
}

void FillModList()
{
    g_modsLoading = true;
    SendMessageW(g_modList, LVM_DELETEALLITEMS, 0, 0);
    for (int i = 0; i < int(g_mods.size()); i++)
    {
        const ModEntry& m = g_mods[i];
        std::wstring name = Wide(m.name), ver = Wide(m.version), author = Wide(m.author), prio = std::to_wstring(m.priority);
        LVITEMW it{ LVIF_TEXT }; it.iItem = i; it.pszText = name.data();
        SendMessageW(g_modList, LVM_INSERTITEMW, 0, (LPARAM)&it);
        const std::wstring* cols[] = { &ver, &author, &prio };
        for (int c = 0; c < 3; c++)
        {
            LVITEMW s{ LVIF_TEXT }; s.iItem = i; s.iSubItem = c + 1; s.pszText = (LPWSTR)cols[c]->c_str();
            SendMessageW(g_modList, LVM_SETITEMTEXTW, i, (LPARAM)&s);
        }
        LVITEMW st{}; st.stateMask = LVIS_STATEIMAGEMASK; st.state = INDEXTOSTATEIMAGEMASK(m.enabled ? 2 : 1);
        SendMessageW(g_modList, LVM_SETITEMSTATE, i, (LPARAM)&st);
    }
    g_modsLoading = false;
    ShowModInfo();
}

void ReloadMods(int select = -1)
{
    ScanMods();
    FillModList();
    if (select >= 0 && select < int(g_mods.size()))
    {
        LVITEMW st{}; st.stateMask = LVIS_SELECTED | LVIS_FOCUSED; st.state = LVIS_SELECTED | LVIS_FOCUSED;
        SendMessageW(g_modList, LVM_SETITEMSTATE, select, (LPARAM)&st);
        SendMessageW(g_modList, LVM_ENSUREVISIBLE, select, FALSE);
    }
}

void Status(const wchar_t* text);

// moves the selected mod one place earlier / later in the load order: priorities are renumbered 0, 10, 20... in
// the new order (written to each mod.ini)
// the selected mod's folder to the Recycle Bin (so a mistake can be undone there)
void RemoveMod(bool ask = true)
{
    const int sel = int(SendMessageW(g_modList, LVM_GETNEXTITEM, WPARAM(-1), LVNI_SELECTED));
    if (sel < 0 || sel >= int(g_mods.size())) { Status(L"Select a mod to remove."); return; }
    const ModEntry& m = g_mods[sel];
    if (m.folder.empty() || m.folder.find(L"..") != std::wstring::npos || m.folder.find_first_of(L"\\/:") != std::wstring::npos) return;
    if (ask && MessageBoxW(g_wnd, (L"Remove \"" + Wide(m.name.empty() ? Narrow(m.folder) : m.name) + L"\"?\n\nIts folder (mods\\" +
                           m.folder + L") goes to the Recycle Bin.").c_str(), L"Remove mod", MB_YESNO | MB_ICONQUESTION) != IDYES) return;
    std::wstring from = g_modsDir + L"\\" + m.folder;
    from.push_back(L'\0');   // SHFileOperation: a double-NUL-terminated list
    SHFILEOPSTRUCTW op{};
    op.hwnd = g_wnd; op.wFunc = FO_DELETE; op.pFrom = from.c_str();
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI;
    const std::wstring folder = m.folder;
    const int r = SHFileOperationW(&op);
    ReloadMods();
    Status(r == 0 && !op.fAnyOperationsAborted ? (L"Removed " + folder + L" (in the Recycle Bin).").c_str()
                                               : (L"Could not remove " + folder + L" (is the game running?).").c_str());
}

void MoveMod(int dir)
{
    const int sel = int(SendMessageW(g_modList, LVM_GETNEXTITEM, WPARAM(-1), LVNI_SELECTED));
    const int to = sel + dir;
    if (sel < 0 || to < 0 || to >= int(g_mods.size())) return;
    std::swap(g_mods[sel], g_mods[to]);
    bool ok = true;
    for (int i = 0; i < int(g_mods.size()); i++)
        if (g_mods[i].priority != i * 10) { g_mods[i].priority = i * 10; ok &= WriteModKey(g_mods[i], "priority", std::to_string(i * 10)); }
    Status(ok ? L"Load order saved." : L"Could not write a mod.ini.");
    ReloadMods(to);
}

int RunQuiet(const std::wstring& cmdline, std::string* output = nullptr)
{
    SECURITY_ATTRIBUTES sa{ sizeof(sa), nullptr, TRUE };
    HANDLE rd = nullptr, wr = nullptr;
    if (!CreatePipe(&rd, &wr, &sa, 0)) return -1;
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);
    STARTUPINFOW si{ sizeof(si) }; si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW; si.wShowWindow = SW_HIDE;
    si.hStdOutput = wr; si.hStdError = wr;
    PROCESS_INFORMATION pi{};
    std::wstring cmd = cmdline;
    if (!CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi))
    { CloseHandle(rd); CloseHandle(wr); return -1; }
    CloseHandle(wr);
    std::string out; char buf[4096]; DWORD n;
    while (ReadFile(rd, buf, sizeof(buf), &n, nullptr) && n) out.append(buf, n);
    CloseHandle(rd);
    WaitForSingleObject(pi.hProcess, INFINITE);
    DWORD code = 0; GetExitCodeProcess(pi.hProcess, &code);
    CloseHandle(pi.hProcess); CloseHandle(pi.hThread);
    if (output) *output = out;
    return int(code);
}

// checks an archive listing (tar -tf): exactly one top folder with a mod.ini, nothing that could land outside it.
// Returns the folder's name, or empty with the reason in why
std::string CheckArchive(const std::string& listing, std::wstring& why)
{
    std::istringstream ls(listing);
    std::string line, top;
    bool hasIni = false;
    while (std::getline(ls, line))
    {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;
        if (line[0] == '/' || line[0] == '\\' || line.find(':') != std::string::npos || line.find("..") != std::string::npos)
        { why = L"the archive has a path outside its folder"; return {}; }
        const std::string first = line.substr(0, line.find_first_of("/\\"));
        if (top.empty()) top = first;
        else if (first != top) { why = L"the archive must hold exactly one mod folder"; return {}; }
        if (LowerA(line) == LowerA(top) + "\\mod.ini") hasIni = true;
    }
    if (top.empty()) { why = L"the archive is empty"; return {}; }
    if (!hasIni) { why = L"no mod.ini in the archive's folder"; return {}; }
    return top;
}

// a shared mod (.zip with one folder holding a mod.ini, as tools/modtool.py pack makes it) into the mods folder
void InstallZipFile(const wchar_t* file, bool ask = true);

void InstallZip()
{
    wchar_t file[MAX_PATH] = {};
    OPENFILENAMEW ofn{ sizeof(ofn) };
    ofn.hwndOwner = g_wnd; ofn.lpstrFilter = L"Mod archives (*.zip)\0*.zip\0"; ofn.lpstrFile = file; ofn.nMaxFile = MAX_PATH;
    ofn.Flags = OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST;
    if (GetOpenFileNameW(&ofn)) InstallZipFile(file);
}

void InstallZipFile(const wchar_t* file, bool ask)
{
    wchar_t sys[MAX_PATH]; GetSystemDirectoryW(sys, MAX_PATH);
    const std::wstring tar = std::wstring(sys) + L"\\tar.exe";
    std::string listing;
    if (RunQuiet(L"\"" + tar + L"\" -tf \"" + file + L"\"", &listing) != 0) { Status(L"Could not read that archive."); return; }
    std::wstring why;
    const std::string top = CheckArchive(listing, why);
    if (top.empty()) { Status((L"Not installed: " + why + L".").c_str()); return; }
    const std::wstring dest = g_modsDir + L"\\" + Wide(top);
    if (GetFileAttributesW(dest.c_str()) != INVALID_FILE_ATTRIBUTES && ask &&
        MessageBoxW(g_wnd, (L"A mod folder named \"" + Wide(top) + L"\" is already installed. Replace its files?").c_str(),
                    L"Install mod", MB_YESNO | MB_ICONQUESTION) != IDYES) return;
    CreateDirectoryW(g_modsDir.c_str(), nullptr);
    if (RunQuiet(L"\"" + tar + L"\" -xf \"" + file + L"\" -C \"" + g_modsDir + L"\"") != 0) { Status(L"Extracting the archive failed."); return; }
    ReloadMods();
    Status((L"Installed " + Wide(top) + L".").c_str());
}

void BuildModsPage(std::vector<HWND>& page)
{
    page.push_back(Make(L"STATIC", L"Installed mods", SS_LEFT, 28, 118, 300, 22, 0, g_fontBold));
    page.push_back(Make(L"STATIC", L"Checked mods are on. The game loads them top to bottom: later mods win when two change the same file.",
                        SS_LEFT | SS_ENDELLIPSIS, 28, 140, 580, 18, 0, g_fontSmall));
    g_modList = Make(WC_LISTVIEWW, L"", LVS_REPORT | LVS_SINGLESEL | LVS_SHOWSELALWAYS | LVS_NOSORTHEADER | WS_BORDER | WS_TABSTOP,
                     28, 164, 470, 230, ID_MOD_LIST, g_font);
    SendMessageW(g_modList, LVM_SETEXTENDEDLISTVIEWSTYLE, 0, LVS_EX_CHECKBOXES | LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
    const struct { const wchar_t* title; int width; } cols[] = { { L"Mod", 210 }, { L"Version", 70 }, { L"Author", 110 }, { L"Priority", 60 } };
    for (int i = 0; i < 4; i++)
    {
        LVCOLUMNW c{ LVCF_TEXT | LVCF_WIDTH }; c.pszText = (LPWSTR)cols[i].title; c.cx = S(cols[i].width);
        SendMessageW(g_modList, LVM_INSERTCOLUMNW, i, (LPARAM)&c);
    }
    page.push_back(g_modList);
    page.push_back(Make(L"BUTTON", L"Up", BS_PUSHBUTTON | WS_TABSTOP, 508, 164, 100, 30, ID_MOD_UP, g_font));
    page.push_back(Make(L"BUTTON", L"Down", BS_PUSHBUTTON | WS_TABSTOP, 508, 200, 100, 30, ID_MOD_DOWN, g_font));
    page.push_back(Make(L"BUTTON", L"Install .zip...", BS_PUSHBUTTON | WS_TABSTOP, 508, 252, 100, 30, ID_MOD_INSTALL, g_font));
    page.push_back(Make(L"BUTTON", L"Open folder", BS_PUSHBUTTON | WS_TABSTOP, 508, 288, 100, 30, ID_MOD_FOLDER, g_font));
    page.push_back(Make(L"BUTTON", L"Refresh", BS_PUSHBUTTON | WS_TABSTOP, 508, 324, 100, 30, ID_MOD_REFRESH, g_font));
    page.push_back(Make(L"BUTTON", L"Remove", BS_PUSHBUTTON | WS_TABSTOP, 508, 364, 100, 30, ID_MOD_REMOVE, g_font));
    g_modInfo = Make(L"STATIC", L"", SS_LEFT, 28, 404, 580, 150, 0, g_fontSmall);
    page.push_back(g_modInfo);
    ReloadMods();
}

// list notifications: a checkbox toggled (enabled = in mod.ini), the selection changed
void OnModListNotify(NMHDR* h)
{
    if (h->code != LVN_ITEMCHANGED || g_modsLoading) return;
    const auto* lv = (NMLISTVIEW*)h;
    if (lv->iItem < 0 || lv->iItem >= int(g_mods.size()) || !(lv->uChanged & LVIF_STATE)) return;
    if ((lv->uNewState ^ lv->uOldState) & LVIS_STATEIMAGEMASK)
    {
        ModEntry& m = g_mods[lv->iItem];
        const bool on = ((lv->uNewState & LVIS_STATEIMAGEMASK) >> 12) == 2;
        if (on != m.enabled)
        {
            m.enabled = on;
            Status(WriteModKey(m, "enabled", on ? "true" : "false") ? (on ? L"Mod enabled." : L"Mod disabled.") : L"Could not write the mod.ini.");
        }
    }
    ShowModInfo();
}

// ---- the Multiplayer page: profile (gamertag) and how to play online (gow3.ini [network], runtime/net/vnet.cpp) ----
// Three choices, no host / join: Offline; With friends (games on one network find each other; over the internet a
// friend's code, which the launcher shows and checks); On a server (a list of servers with who is on them, and one
// to start on this PC). Below: Windows Firewall (checked, and fixed on request with an elevated netsh) and the port.
// Network work (UPnP, queries) runs on threads that post the results back (WM_APP_*).
enum { ID_MP_GAMERTAG = 300, ID_MP_OFFLINE, ID_MP_FRIENDS, ID_MP_SERVER, ID_MP_CODE, ID_MP_COPY, ID_MP_JOINBOX, ID_MP_CHECK,
       ID_MP_SERVERLIST, ID_MP_REFRESH, ID_MP_REMOVE, ID_MP_RUNSERVER, ID_MP_ADDBOX, ID_MP_ADD, ID_MP_ROOM, ID_MP_PASSWORD,
       ID_MP_FIREWALL, ID_MP_PORT, ID_MP_UPNP };
enum { WM_APP_CODE = WM_APP + 1, WM_APP_SERVERINFO, WM_APP_CHECK, WM_APP_FIREWALL };
HWND g_mpGamertag, g_mpOffline, g_mpFriends, g_mpServer, g_mpHint, g_mpCode, g_mpCodeStatus, g_mpJoinBox, g_mpCheckResult,
     g_mpServerList, g_mpServerInfo, g_mpAddBox, g_mpRoom, g_mpPassword, g_mpFirewallStatus, g_mpFirewallButton, g_mpPort, g_mpUpnp;
std::vector<HWND> g_mpFriendsPanel, g_mpServerPanel;
std::wstring g_profilePath;
bool g_noNetwork = false;           // test mode (GOW3_LAUNCHER_SHOT): no UPnP, no queries

struct ServerRow { std::string address; bool answered = false, queried = false; int ping = 0; vnet::Info info{}; };
std::vector<ServerRow> g_servers;
int g_serverGeneration = 0;         // a refresh's results are dropped once another refresh started

std::wstring UserDir()
{
    wchar_t dir[MAX_PATH] = {};
    if (GetEnvironmentVariableW(L"GOW3_DATA_DIR", dir, MAX_PATH) > 0) return dir;
    GetEnvironmentVariableW(L"LOCALAPPDATA", dir, MAX_PATH);
    return std::wstring(dir) + L"\\GoW3Recomp";
}

std::string ReadGamertag()
{
    Ini p; p.Load(g_profilePath);
    return p.Get("profile", "gamertag", "Player");
}

bool WriteGamertag(const std::string& name)
{
    std::string clean;
    for (char c : name)
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == ' ') clean += c;
    while (!clean.empty() && clean.front() == ' ') clean.erase(clean.begin());
    while (!clean.empty() && clean.back() == ' ') clean.pop_back();
    if (clean.size() > 15) clean.resize(15);
    if (clean.empty()) return false;
    CreateDirectoryW(UserDir().c_str(), nullptr);
    Ini p; p.Load(g_profilePath);           // keeps the xuid the game made
    p.Set("profile", "gamertag", clean);
    return p.Save();
}

std::string GetText(HWND h)
{
    wchar_t buf[256] = {};
    GetWindowTextW(h, buf, 256);
    return Ini::Trim(Narrow(buf));
}

bool Checked(HWND h) { return SendMessageW(h, BM_GETCHECK, 0, 0) == BST_CHECKED; }
int Port() { const int p = atoi(GetText(g_mpPort).c_str()); return p > 0 && p < 65536 ? p : 3074; }

// ---- network helpers (the protocol of runtime/net/vnet_protocol.h) ----
bool ResolveTarget(const std::string& text, uint16_t defaultPort, sockaddr_in& out)
{
    uint32_t ip = 0; uint16_t port = 0;
    out = {}; out.sin_family = AF_INET;
    if (code::Decode(text, ip, port)) { out.sin_addr.s_addr = htonl(ip); out.sin_port = htons(port); return true; }
    std::string host = text;
    port = defaultPort;
    if (auto c = host.rfind(':'); c != std::string::npos) { port = uint16_t(atoi(host.c_str() + c + 1)); host.resize(c); }
    addrinfo hints{}; hints.ai_family = AF_INET; hints.ai_socktype = SOCK_DGRAM;
    addrinfo* res = nullptr;
    if (host.empty() || getaddrinfo(host.c_str(), nullptr, &hints, &res) != 0 || !res) return false;
    out = *(sockaddr_in*)res->ai_addr;
    out.sin_port = htons(port ? port : defaultPort);
    freeaddrinfo(res);
    return true;
}

// asks a game or server who is there; false: no answer within the time
bool QueryHub(const sockaddr_in& to, vnet::Info& info, int& pingMs, int timeoutMs = 1500)
{
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    DWORD t = 250;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&t, sizeof(t));
    vnet::Query q{};
    q.h = vnet::MakeHeader(vnet::kQuery);
    q.token = GetTickCount();
    const ULONGLONG start = GetTickCount64();
    bool ok = false;
    for (ULONGLONG last = 0; !ok && GetTickCount64() - start < ULONGLONG(timeoutMs);)
    {
        if (GetTickCount64() - last > 500) { sendto(s, (const char*)&q, sizeof(q), 0, (const sockaddr*)&to, sizeof(to)); last = GetTickCount64(); }
        char buf[1024]; sockaddr_in from{}; int fl = sizeof(from);
        const int n = recvfrom(s, buf, sizeof(buf), 0, (sockaddr*)&from, &fl);
        if (n >= int(sizeof(vnet::Info)) && vnet::Valid(buf, n) && ((vnet::Header*)buf)->type == vnet::kInfo && ((vnet::Info*)buf)->token == q.token)
        {
            info = *(vnet::Info*)buf;
            pingMs = int(GetTickCount64() - start);
            ok = true;
        }
    }
    closesocket(s);
    return ok;
}

// LAN: the games open on this network (as the game finds them): address, name, code
struct LanGame { sockaddr_in addr{}; std::string name, code; int players = 0; };
std::vector<LanGame> FindLanGames(int ms)
{
    std::vector<LanGame> found;
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    BOOL yes = TRUE;
    setsockopt(s, SOL_SOCKET, SO_BROADCAST, (const char*)&yes, sizeof(yes));
    DWORD t = 100;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char*)&t, sizeof(t));
    sockaddr_in local{}; local.sin_family = AF_INET;
    char only[32] = {};   // GOW3_NET_BIND (tests): this PC only, as the game takes it
    const bool thisPcOnly = GetEnvironmentVariableA("GOW3_NET_BIND", only, sizeof(only)) > 0;
    if (thisPcOnly) inet_pton(AF_INET, only, &local.sin_addr);
    bind(s, (sockaddr*)&local, sizeof(local));
    const vnet::Header ask = vnet::MakeHeader(vnet::kDiscover);
    const ULONGLONG start = GetTickCount64();
    for (ULONGLONG last = 0; GetTickCount64() - start < ULONGLONG(ms);)
    {
        if (GetTickCount64() - last > 300)
        {
            for (uint32_t target : { uint32_t(INADDR_BROADCAST), uint32_t(INADDR_LOOPBACK) })
            {
                if (thisPcOnly && target == INADDR_BROADCAST) continue;
                sockaddr_in to{}; to.sin_family = AF_INET; to.sin_port = htons(vnet::kDiscoveryPort); to.sin_addr.s_addr = htonl(target);
                sendto(s, (const char*)&ask, sizeof(ask), 0, (sockaddr*)&to, sizeof(to));
            }
            last = GetTickCount64();
        }
        char buf[256]; sockaddr_in from{}; int fl = sizeof(from);
        const int n = recvfrom(s, buf, sizeof(buf), 0, (sockaddr*)&from, &fl);
        if (n < int(sizeof(vnet::Announce)) || !vnet::Valid(buf, n) || ((vnet::Header*)buf)->type != vnet::kAnnounce) continue;
        const auto& a = *(vnet::Announce*)buf;
        bool known = false;
        for (const auto& g : found) known |= g.addr.sin_addr.s_addr == from.sin_addr.s_addr && ntohs(g.addr.sin_port) == a.port;
        if (known) continue;
        LanGame g; g.addr = from; g.addr.sin_port = htons(a.port); g.name = vnet::Text(a.name); g.code = vnet::Text(a.code); g.players = a.players;
        found.push_back(g);
    }
    closesocket(s);
    return found;
}

std::string LocalAddress()
{
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in to{}; to.sin_family = AF_INET; to.sin_port = htons(9); to.sin_addr.s_addr = htonl(0x08080808);   // picks the route; sends nothing
    connect(s, (sockaddr*)&to, sizeof(to));
    sockaddr_in me{}; int len = sizeof(me);
    getsockname(s, (sockaddr*)&me, &len);
    closesocket(s);
    char buf[32]; inet_ntop(AF_INET, &me.sin_addr, buf, sizeof(buf));
    return buf;
}

void PostText(UINT msg, WPARAM wp, const std::wstring& text) { PostMessageW(g_wnd, msg, wp, (LPARAM)new std::wstring(text)); }
void TakeText(LPARAM lp, std::wstring& out) { auto* p = (std::wstring*)lp; out = *p; delete p; }

// ---- "With friends": this PC's code (UPnP, on a thread) and checking a friend's code ----
void FetchCode()
{
    if (g_noNetwork) { SetWindowTextW(g_mpCode, L""); SetWindowTextW(g_mpCodeStatus, L"(not checked in test mode)"); return; }
    if (!Checked(g_mpUpnp))
    {
        SetWindowTextW(g_mpCode, L"");
        SetWindowTextW(g_mpCodeStatus, L"UPnP is off (Advanced): friends over the internet need the port forwarded by hand, then your public address.");
        return;
    }
    SetWindowTextW(g_mpCode, L"");
    SetWindowTextW(g_mpCodeStatus, L"Asking your router to open the port...");
    const uint16_t port = uint16_t(Port());
    std::thread([port] {
        CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        uint32_t publicIp = 0;
        const bool ok = upnp::ForwardUdp(port, publicIp);
        CoUninitialize();
        std::wstring result;
        if (ok && publicIp) result = Wide(code::Encode(publicIp, port)) + L"\n";
        else result = L"\nYour router did not open the port (UPnP off or not supported). Friends on your network can still join; "
                      L"over the internet, join a friend's code instead, or play on a server.";
        PostText(WM_APP_CODE, 0, result);
    }).detach();
}

void OnCode(LPARAM lp)
{
    std::wstring r; TakeText(lp, r);
    const auto nl = r.find(L'\n');
    const std::wstring code = r.substr(0, nl), why = r.substr(nl + 1);
    SetWindowTextW(g_mpCode, code.c_str());
    SetWindowTextW(g_mpCodeStatus, code.empty() ? why.c_str()
        : L"Your router opened the port. Friends type this code under \"Join a friend\" (it stays the same while your address does).");
}

void CopyCode()
{
    const std::wstring c = Wide(GetText(g_mpCode));
    if (c.empty()) { Status(L"No code to copy yet."); return; }
    if (!OpenClipboard(g_wnd)) return;
    EmptyClipboard();
    HGLOBAL mem = GlobalAlloc(GMEM_MOVEABLE, (c.size() + 1) * sizeof(wchar_t));
    memcpy(GlobalLock(mem), c.c_str(), (c.size() + 1) * sizeof(wchar_t));
    GlobalUnlock(mem);
    SetClipboardData(CF_UNICODETEXT, mem);
    CloseClipboard();
    Status(L"Code copied.");
}

// what the game will do with the friend's box: find their game (here first: same network), and say what it found
void CheckFriend()
{
    const std::string text = GetText(g_mpJoinBox);
    if (g_noNetwork) return;
    SetWindowTextW(g_mpCheckResult, L"Checking...");
    std::thread([text] {
        std::wstring result;
        const auto lan = FindLanGames(1000);
        if (text.empty())
        {
            if (lan.empty()) result = L"No game is open on your network yet. When you play, yours opens and others on your network (or with your code) join it.";
            else
            {
                result = L"On your network: ";
                for (size_t i = 0; i < lan.size(); i++) result += (i ? L", " : L"") + Wide(lan[i].name) + L" (" + std::to_wstring(lan[i].players) + L")";
                result += L". You will join " + Wide(lan[0].name) + L"'s game.";
            }
            PostText(WM_APP_CHECK, 0, result);
            return;
        }
        sockaddr_in target{};
        if (!ResolveTarget(text, vnet::kDefaultPort, target)) { PostText(WM_APP_CHECK, 0, L"That is not a code, and no PC has that name. Check it with your friend."); return; }
        for (const auto& g : lan)
        {
            uint32_t ip = 0; uint16_t port = 0;
            const bool sameCode = !g.code.empty() && code::Decode(g.code, ip, port) && htonl(ip) == target.sin_addr.s_addr && htons(port) == target.sin_port;
            if (sameCode || (g.addr.sin_addr.s_addr == target.sin_addr.s_addr && g.addr.sin_port == target.sin_port))
            {
                PostText(WM_APP_CHECK, 0, L"Found " + Wide(g.name) + L"'s game on your network (" + std::to_wstring(g.players) +
                                          L" playing): the game connects there directly.");
                return;
            }
        }
        vnet::Info info{}; int ping = 0;
        if (QueryHub(target, info, ping, 2000))
            result = L"Found " + Wide(vnet::Text(info.name)) + ((info.flags & vnet::kHubServer) ? L" (a server)" : L"'s game") + L": " +
                     std::to_wstring(info.players) + L" playing, " + std::to_wstring(ping) + L" ms. Ready to play.";
        else
            result = L"No answer. Your friend's game must be running (Multiplayer: With friends). If it is, their router may block "
                     L"friends: they can join your code instead, or you can both use a server.";
        PostText(WM_APP_CHECK, 0, result);
    }).detach();
}

// ---- "On a server": the list (gow3.ini [network] servers), queried on threads ----
void FillServerList()
{
    SendMessageW(g_mpServerList, LVM_DELETEALLITEMS, 0, 0);
    for (int i = 0; i < int(g_servers.size()); i++)
    {
        const ServerRow& r = g_servers[i];
        std::wstring name = r.answered ? Wide(vnet::Text(r.info.name)) : Wide(r.address);
        std::wstring players = !r.queried ? L"..." : r.answered ? std::to_wstring(r.info.players) + L"/" + std::to_wstring(r.info.maxPlayers) : L"-";
        std::wstring ping = !r.queried ? L"" : r.answered ? std::to_wstring(r.ping) + L" ms" : L"no answer";
        std::wstring addr = Wide(r.address);
        if (r.answered && (r.info.flags & vnet::kHubPassword)) name += L" (password)";
        LVITEMW it{ LVIF_TEXT }; it.iItem = i; it.pszText = name.data();
        SendMessageW(g_mpServerList, LVM_INSERTITEMW, 0, (LPARAM)&it);
        const std::wstring* cols[] = { &players, &ping, &addr };
        for (int c = 0; c < 3; c++)
        {
            LVITEMW s{ LVIF_TEXT }; s.iItem = i; s.iSubItem = c + 1; s.pszText = (LPWSTR)cols[c]->c_str();
            SendMessageW(g_mpServerList, LVM_SETITEMTEXTW, i, (LPARAM)&s);
        }
    }
}

int SelectedServer() { return int(SendMessageW(g_mpServerList, LVM_GETNEXTITEM, WPARAM(-1), LVNI_SELECTED)); }

void SelectServer(int i)
{
    if (i < 0 || i >= int(g_servers.size())) return;
    LVITEMW st{}; st.stateMask = LVIS_SELECTED | LVIS_FOCUSED; st.state = LVIS_SELECTED | LVIS_FOCUSED;
    SendMessageW(g_mpServerList, LVM_SETITEMSTATE, i, (LPARAM)&st);
}

void ShowServerInfo()
{
    const int i = SelectedServer();
    if (i < 0 || i >= int(g_servers.size()))
    {
        SetWindowTextW(g_mpServerInfo, g_servers.empty() ? L"No servers yet: add one a friend or community runs, or run your own."
                                                         : L"Select the server to play on.");
        return;
    }
    const ServerRow& r = g_servers[i];
    std::wstring t;
    if (!r.queried) t = L"Asking " + Wide(r.address) + L"...";
    else if (!r.answered) t = L"No answer from " + Wide(r.address) + L": is the server running, and is the address right?";
    else
    {
        const std::string motd = vnet::Text(r.info.motd);
        if (!motd.empty()) t = Wide(motd) + L"\r\n";
        t += r.info.listed ? L"Online: " : L"Nobody online right now.";
        for (int k = 0; k < r.info.listed; k++) t += (k ? L", " : L"") + Wide(vnet::Text(r.info.gamertags[k]));
        if (r.info.players > r.info.listed) t += L" and " + std::to_wstring(r.info.players - r.info.listed) + L" more";
    }
    SetWindowTextW(g_mpServerInfo, t.c_str());
}

void RefreshServers()
{
    const int gen = ++g_serverGeneration;
    for (auto& r : g_servers) r.queried = r.answered = false;
    const int keep = SelectedServer();
    FillServerList();
    SelectServer(keep);
    ShowServerInfo();
    if (g_noNetwork) return;
    for (int i = 0; i < int(g_servers.size()); i++)
    {
        const std::string address = g_servers[i].address;
        std::thread([i, gen, address] {
            auto* row = new ServerRow;
            row->address = address;
            row->queried = true;
            sockaddr_in to{};
            if (ResolveTarget(address, vnet::kDefaultPort, to)) row->answered = QueryHub(to, row->info, row->ping, 2000);
            PostMessageW(g_wnd, WM_APP_SERVERINFO, MAKEWPARAM(i, gen), (LPARAM)row);
        }).detach();
    }
}

void OnServerInfo(WPARAM wp, LPARAM lp)
{
    std::unique_ptr<ServerRow> row((ServerRow*)lp);
    const int i = LOWORD(wp), gen = HIWORD(wp);
    if (gen != (g_serverGeneration & 0xFFFF) || i >= int(g_servers.size()) || g_servers[i].address != row->address) return;
    g_servers[i] = *row;
    const int keep = SelectedServer();
    FillServerList();
    SelectServer(keep);
    ShowServerInfo();
}

void AddServer()
{
    const std::string a = GetText(g_mpAddBox);
    if (a.empty()) { Status(L"Type a server's address or code first."); return; }
    for (int i = 0; i < int(g_servers.size()); i++)
        if (g_servers[i].address == a) { SelectServer(i); Status(L"That server is already in the list."); return; }
    g_servers.push_back({ a });
    SetWindowTextW(g_mpAddBox, L"");
    RefreshServers();
    SelectServer(int(g_servers.size()) - 1);
    Status(L"Server added.");
}

void RemoveServer()
{
    const int i = SelectedServer();
    if (i < 0 || i >= int(g_servers.size())) { Status(L"Select a server to remove."); return; }
    g_servers.erase(g_servers.begin() + i);
    RefreshServers();
}

void RunServer()
{
    const std::wstring exe = g_exeDir + L"\\gow3_server.exe";
    const std::string name = ReadGamertag() + "'s server";
    std::wstring cmd = L"\"" + exe + L"\" --port " + std::to_wstring(Port()) + L" --name \"" + Wide(name) + L"\"";
    STARTUPINFOW si{ sizeof(si) };
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(exe.c_str(), cmd.data(), nullptr, nullptr, FALSE, CREATE_NEW_CONSOLE, nullptr, g_exeDir.c_str(), &si, &pi))
    { Status(L"Could not start gow3_server.exe (is it next to the launcher?)."); return; }
    CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
    const std::string self = "127.0.0.1:" + std::to_string(Port());
    int at = -1;
    for (int i = 0; i < int(g_servers.size()); i++) if (g_servers[i].address == self) at = i;
    if (at < 0) { g_servers.push_back({ self }); at = int(g_servers.size()) - 1; }
    Sleep(300);
    RefreshServers();
    SelectServer(at);
    MessageBoxW(g_wnd, (L"Your server runs in its own window (closing it stops the server), and it is selected here.\n\n"
                        L"Friends on your network add this address:  " + Wide(LocalAddress()) + L":" + std::to_wstring(Port()) +
                        L"\nOver the internet, they add the server code shown in the server's window.").c_str(),
                L"Server started", MB_OK | MB_ICONINFORMATION);
}

// ---- Windows Firewall: rules for gow3.exe (a blocked game cannot be joined on the network) ----
enum class Firewall { Unknown, Allowed, Blocked, NotSet };
Firewall CheckFirewall(const std::wstring& exe)
{
    Firewall result = Firewall::NotSet;
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    INetFwPolicy2* policy = nullptr;
    if (FAILED(CoCreateInstance(__uuidof(NetFwPolicy2), nullptr, CLSCTX_INPROC_SERVER, __uuidof(INetFwPolicy2), (void**)&policy)))
    { CoUninitialize(); return Firewall::Unknown; }
    INetFwRules* rules = nullptr;
    IUnknown* e = nullptr;
    IEnumVARIANT* it = nullptr;
    if (SUCCEEDED(policy->get_Rules(&rules)) && SUCCEEDED(rules->get__NewEnum(&e)) && SUCCEEDED(e->QueryInterface(IID_PPV_ARGS(&it))))
    {
        bool allowed = false, blocked = false;
        VARIANT v; VariantInit(&v);
        while (it->Next(1, &v, nullptr) == S_OK)
        {
            INetFwRule* r = nullptr;
            if (v.vt == VT_DISPATCH && SUCCEEDED(v.pdispVal->QueryInterface(IID_PPV_ARGS(&r))))
            {
                BSTR app = nullptr; VARIANT_BOOL enabled = VARIANT_FALSE; NET_FW_RULE_DIRECTION dir; NET_FW_ACTION action;
                if (SUCCEEDED(r->get_ApplicationName(&app)) && app && _wcsicmp(app, exe.c_str()) == 0 &&
                    SUCCEEDED(r->get_Enabled(&enabled)) && enabled && SUCCEEDED(r->get_Direction(&dir)) && dir == NET_FW_RULE_DIR_IN &&
                    SUCCEEDED(r->get_Action(&action)))
                    (action == NET_FW_ACTION_BLOCK ? blocked : allowed) = true;
                if (app) SysFreeString(app);
                r->Release();
            }
            VariantClear(&v);
        }
        result = blocked ? Firewall::Blocked : allowed ? Firewall::Allowed : Firewall::NotSet;
    }
    if (it) it->Release();
    if (e) e->Release();
    if (rules) rules->Release();
    policy->Release();
    CoUninitialize();
    return result;
}

void RefreshFirewall()
{
    if (g_noNetwork) { SetWindowTextW(g_mpFirewallStatus, L"(not checked in test mode)"); return; }
    const std::wstring exe = g_exeDir + L"\\gow3.exe";
    std::thread([exe] { PostMessageW(g_wnd, WM_APP_FIREWALL, WPARAM(CheckFirewall(exe)), 0); }).detach();
}

void OnFirewall(WPARAM wp)
{
    const auto f = Firewall(wp);
    SetWindowTextW(g_mpFirewallStatus,
        f == Firewall::Allowed ? L"Windows Firewall lets other players reach the game."
        : f == Firewall::Blocked ? L"Windows Firewall BLOCKS the game: others cannot join you. Fix it with the button."
        : f == Firewall::NotSet ? L"Not set yet: Windows asks the first time you play online (choose Allow), or allow it now."
                                : L"Could not read the Windows Firewall settings.");
    SetWindowTextW(g_mpFirewallButton, f == Firewall::Blocked ? L"Fix" : L"Allow");
    EnableWindow(g_mpFirewallButton, f != Firewall::Allowed);
}

// replaces the game's (and server's) inbound rules, including "block" rules Windows made when its prompt was
// dismissed, with one allow rule each (UDP). Needs administrator rights: Windows asks.
void AllowFirewall()
{
    std::wstring cmd = L"/c";
    for (const wchar_t* name : { L"gow3.exe", L"gow3_server.exe" })
    {
        const std::wstring exe = g_exeDir + L"\\" + name;
        cmd += L" netsh advfirewall firewall delete rule name=all program=\"" + exe + L"\" >nul &";
        cmd += L" netsh advfirewall firewall add rule name=\"Gears of War 3 PC port (" + std::wstring(name) +
               L")\" dir=in action=allow protocol=UDP profile=any program=\"" + exe + L"\" >nul &";
    }
    cmd += L" exit 0";
    SHELLEXECUTEINFOW sei{ sizeof(sei) };
    sei.fMask = SEE_MASK_NOCLOSEPROCESS;
    sei.hwnd = g_wnd; sei.lpVerb = L"runas"; sei.lpFile = L"cmd.exe"; sei.lpParameters = cmd.c_str(); sei.nShow = SW_HIDE;
    if (!ShellExecuteExW(&sei)) { Status(L"Firewall not changed (administrator permission was not given)."); return; }
    if (sei.hProcess) { WaitForSingleObject(sei.hProcess, 15000); CloseHandle(sei.hProcess); }
    RefreshFirewall();
    Status(L"Windows Firewall updated.");
}

// ---- the page ----
void UpdateMultiplayer()
{
    const bool friends = Checked(g_mpFriends), server = Checked(g_mpServer);
    const bool visible = int(SendMessageW(g_tabs, TCM_GETCURSEL, 0, 0)) == 6;
    for (HWND c : g_mpFriendsPanel) ShowWindow(c, visible && friends ? SW_SHOW : SW_HIDE);
    for (HWND c : g_mpServerPanel) ShowWindow(c, visible && server ? SW_SHOW : SW_HIDE);
    SetWindowTextW(g_mpHint,
        friends ? L"Games on your network find each other by themselves. Over the internet: give friends your code, or type theirs. "
                  L"In the game: Party Options (Back / Backspace) > System Link."
        : server ? L"Everyone on the server sees each other's parties: in the game, Party Options (Back / Backspace) > System Link. "
                   L"A private room keeps your group to itself: everyone types the same name."
                 : L"Campaign, splitscreen and matches with bots. Nothing is sent to the network.");
}

void SelectMode(int id)
{
    for (HWND r : { g_mpOffline, g_mpFriends, g_mpServer })
        SendMessageW(r, BM_SETCHECK, GetDlgCtrlID(r) == id ? BST_CHECKED : BST_UNCHECKED, 0);
    UpdateMultiplayer();
    if (id == ID_MP_FRIENDS && GetText(g_mpCode).empty()) FetchCode();
    if (id == ID_MP_SERVER) RefreshServers();
}

void LoadMultiplayer(bool defaults)
{
    SetWindowTextW(g_mpGamertag, Wide(ReadGamertag()).c_str());
    auto get = [defaults](const char* key, const char* def) { return defaults ? std::string(def) : g_ini.Get("network", key, def); };
    std::string online = get("online", ""), server = get("server", "");
    const std::string mode = get("mode", "offline"), relay = get("relay", "");   // 0.2.0 betas
    if (online.empty()) online = mode == "host" || mode == "join" ? "true" : "false";
    if (server.empty()) server = relay;
    g_servers.clear();
    std::stringstream list(get("servers", ""));
    for (std::string s; std::getline(list, s, ',');)
        if (!(s = Ini::Trim(s)).empty()) g_servers.push_back({ s });
    int selected = -1;
    if (!server.empty())
    {
        for (int i = 0; i < int(g_servers.size()); i++) if (g_servers[i].address == server) selected = i;
        if (selected < 0) { g_servers.push_back({ server }); selected = int(g_servers.size()) - 1; }
    }
    FillServerList();
    SelectServer(selected);
    SetWindowTextW(g_mpJoinBox, Wide(get("join", "")).c_str());
    SetWindowTextW(g_mpRoom, Wide(get("room", "")).c_str());
    SetWindowTextW(g_mpPassword, Wide(get("password", "")).c_str());
    SetWindowTextW(g_mpPort, Wide(get("port", "3074")).c_str());
    const std::string upnp = get("upnp", "true");
    SendMessageW(g_mpUpnp, BM_SETCHECK, upnp == "false" || upnp == "0" ? BST_UNCHECKED : BST_CHECKED, 0);
    const bool on = online == "true" || online == "1" || online == "yes";
    for (HWND r : { g_mpOffline, g_mpFriends, g_mpServer }) SendMessageW(r, BM_SETCHECK, BST_UNCHECKED, 0);
    SendMessageW(!on ? g_mpOffline : server.empty() ? g_mpFriends : g_mpServer, BM_SETCHECK, BST_CHECKED, 0);
    UpdateMultiplayer();
}

// called when the page is first shown: the slow checks start only for players who look at it
void ActivateMultiplayer()
{
    static bool done = false;
    if (done) return;
    done = true;
    RefreshFirewall();
    if (Checked(g_mpFriends)) FetchCode();
    if (Checked(g_mpServer) || !g_servers.empty()) RefreshServers();
}

void SaveMultiplayer()
{
    const bool friends = Checked(g_mpFriends), server = Checked(g_mpServer);
    std::string list;
    for (const auto& r : g_servers) list += (list.empty() ? "" : ", ") + r.address;
    const int sel = SelectedServer();
    const std::string chosen = server && sel >= 0 && sel < int(g_servers.size()) ? g_servers[sel].address : "";
    g_ini.Set("network", "online", friends || (server && !chosen.empty()) ? "true" : "false");
    g_ini.Set("network", "server", chosen);
    g_ini.Set("network", "servers", list);
    g_ini.Set("network", "join", GetText(g_mpJoinBox));
    g_ini.Set("network", "room", GetText(g_mpRoom));
    g_ini.Set("network", "password", GetText(g_mpPassword));
    g_ini.Set("network", "port", std::to_string(Port()));
    g_ini.Set("network", "upnp", Checked(g_mpUpnp) ? "true" : "false");
    g_ini.Remove("network", "mode");     // 0.2.0 betas
    g_ini.Remove("network", "relay");
    WriteGamertag(GetText(g_mpGamertag));   // an empty or invalid gamertag keeps the old one
    if (server && chosen.empty()) Status(L"No server selected: the game starts offline.");
}

void BuildMultiplayerPage(std::vector<HWND>& page)
{
    const int x = 28;
    auto add = [&page](HWND h) { page.push_back(h); return h; };
    auto friendsOnly = [&](HWND h) { g_mpFriendsPanel.push_back(h); return h; };   // shown by UpdateMultiplayer, not ShowPage
    auto serverOnly = [&](HWND h) { g_mpServerPanel.push_back(h); return h; };
    add(Make(L"STATIC", L"Gamertag", SS_LEFT, x, 120, 80, 22, 0, g_fontBold));
    g_mpGamertag = add(Make(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, x + 84, 116, 160, 24, ID_MP_GAMERTAG, g_font));
    SendMessageW(g_mpGamertag, EM_LIMITTEXT, 15, 0);
    add(Make(L"STATIC", L"What other players see (1-15 letters, digits, spaces).", SS_LEFT, x + 256, 120, 330, 18, 0, g_fontSmall));

    add(Make(L"STATIC", L"How do you want to play?", SS_LEFT, x, 156, 300, 22, 0, g_fontBold));
    g_mpOffline = add(Make(L"BUTTON", L"Offline", BS_AUTORADIOBUTTON | WS_GROUP | WS_TABSTOP, x, 180, 120, 22, ID_MP_OFFLINE, g_font));
    g_mpFriends = add(Make(L"BUTTON", L"With friends", BS_AUTORADIOBUTTON, x + 130, 180, 140, 22, ID_MP_FRIENDS, g_font));
    g_mpServer = add(Make(L"BUTTON", L"On a server", BS_AUTORADIOBUTTON, x + 280, 180, 140, 22, ID_MP_SERVER, g_font));
    g_mpHint = add(Make(L"STATIC", L"", SS_LEFT, x, 206, 572, 34, 0, g_fontSmall));

    // with friends
    friendsOnly(Make(L"STATIC", L"Your code, for friends over the internet", SS_LEFT, x, 250, 400, 18, 0, g_fontSmall));
    g_mpCode = friendsOnly(Make(L"EDIT", L"", ES_READONLY | WS_BORDER, x, 270, 150, 26, ID_MP_CODE, g_fontBold));
    friendsOnly(Make(L"BUTTON", L"Copy", BS_PUSHBUTTON | WS_TABSTOP, x + 156, 269, 64, 28, ID_MP_COPY, g_font));
    g_mpCodeStatus = friendsOnly(Make(L"STATIC", L"", SS_LEFT, x + 230, 266, 350, 34, 0, g_fontSmall));
    friendsOnly(Make(L"STATIC", L"Join a friend (optional): their code or address. Leave it empty to open your own game.", SS_LEFT, x, 312, 572, 18, 0, g_fontSmall));
    g_mpJoinBox = friendsOnly(Make(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, x, 332, 300, 26, ID_MP_JOINBOX, g_font));
    friendsOnly(Make(L"BUTTON", L"Check", BS_PUSHBUTTON | WS_TABSTOP, x + 306, 331, 80, 28, ID_MP_CHECK, g_font));
    g_mpCheckResult = friendsOnly(Make(L"STATIC", L"", SS_LEFT, x, 366, 572, 48, 0, g_fontSmall));

    // on a server
    serverOnly(Make(L"STATIC", L"Servers (select the one to play on)", SS_LEFT, x, 250, 400, 18, 0, g_fontSmall));
    g_mpServerList = serverOnly(Make(WC_LISTVIEWW, L"", LVS_REPORT | LVS_SINGLESEL | LVS_SHOWSELALWAYS | LVS_NOSORTHEADER | WS_BORDER | WS_TABSTOP,
                                     x, 270, 470, 104, ID_MP_SERVERLIST, g_font));
    SendMessageW(g_mpServerList, LVM_SETEXTENDEDLISTVIEWSTYLE, 0, LVS_EX_FULLROWSELECT | LVS_EX_DOUBLEBUFFER);
    const struct { const wchar_t* title; int width; } cols[] = { { L"Server", 190 }, { L"Players", 60 }, { L"Ping", 70 }, { L"Address", 140 } };
    for (int i = 0; i < 4; i++)
    {
        LVCOLUMNW c{ LVCF_TEXT | LVCF_WIDTH }; c.pszText = (LPWSTR)cols[i].title; c.cx = S(cols[i].width);
        SendMessageW(g_mpServerList, LVM_INSERTCOLUMNW, i, (LPARAM)&c);
    }
    serverOnly(Make(L"BUTTON", L"Refresh", BS_PUSHBUTTON | WS_TABSTOP, x + 480, 270, 100, 28, ID_MP_REFRESH, g_font));
    serverOnly(Make(L"BUTTON", L"Remove", BS_PUSHBUTTON | WS_TABSTOP, x + 480, 304, 100, 28, ID_MP_REMOVE, g_font));
    serverOnly(Make(L"BUTTON", L"Run my own", BS_PUSHBUTTON | WS_TABSTOP, x + 480, 346, 100, 28, ID_MP_RUNSERVER, g_font));
    g_mpServerInfo = serverOnly(Make(L"STATIC", L"", SS_LEFT, x, 378, 572, 32, 0, g_fontSmall));
    serverOnly(Make(L"STATIC", L"Add a server", SS_LEFT, x, 418, 80, 18, 0, g_fontSmall));
    g_mpAddBox = serverOnly(Make(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, x + 84, 414, 250, 24, ID_MP_ADDBOX, g_font));
    serverOnly(Make(L"BUTTON", L"Add", BS_PUSHBUTTON | WS_TABSTOP, x + 340, 412, 70, 28, ID_MP_ADD, g_font));
    serverOnly(Make(L"STATIC", L"Private room", SS_LEFT, x, 450, 80, 18, 0, g_fontSmall));
    g_mpRoom = serverOnly(Make(L"EDIT", L"", ES_AUTOHSCROLL | WS_BORDER | WS_TABSTOP, x + 84, 446, 130, 24, ID_MP_ROOM, g_font));
    serverOnly(Make(L"STATIC", L"Password", SS_LEFT, x + 226, 450, 60, 18, 0, g_fontSmall));
    g_mpPassword = serverOnly(Make(L"EDIT", L"", ES_AUTOHSCROLL | ES_PASSWORD | WS_BORDER | WS_TABSTOP, x + 290, 446, 120, 24, ID_MP_PASSWORD, g_font));
    serverOnly(Make(L"STATIC", L"(both optional)", SS_LEFT, x + 420, 450, 150, 18, 0, g_fontSmall));

    // both: the firewall and the port
    add(Make(L"STATIC", L"Windows Firewall", SS_LEFT, x, 486, 140, 20, 0, g_fontBold));
    g_mpFirewallStatus = add(Make(L"STATIC", L"Checking...", SS_LEFT, x + 140, 486, 340, 32, 0, g_fontSmall));
    g_mpFirewallButton = add(Make(L"BUTTON", L"Allow", BS_PUSHBUTTON | WS_TABSTOP, x + 480, 484, 100, 28, ID_MP_FIREWALL, g_font));
    add(Make(L"STATIC", L"UDP port", SS_LEFT, x, 526, 60, 18, 0, g_fontSmall));
    g_mpPort = add(Make(L"EDIT", L"", ES_NUMBER | WS_BORDER | WS_TABSTOP, x + 64, 522, 64, 24, ID_MP_PORT, g_font));
    g_mpUpnp = add(Make(L"BUTTON", L"Let the router open it (UPnP)", BS_AUTOCHECKBOX | WS_TABSTOP, x + 140, 524, 240, 22, ID_MP_UPNP, g_font));
    add(Make(L"STATIC", L"Advanced: 3074 suits almost everyone.", SS_LEFT, x + 384, 526, 200, 18, 0, g_fontSmall));
}

// the tab control's client area drawn once into a bitmap: a pattern brush that matches the themed page background
void MakeTabBrush()
{
    RECT rc; GetClientRect(g_tabs, &rc);
    HDC screen = GetDC(g_tabs), dc = CreateCompatibleDC(screen);
    HBITMAP bmp = CreateCompatibleBitmap(screen, rc.right, rc.bottom);
    HGDIOBJ old = SelectObject(dc, bmp);
    SendMessageW(g_tabs, WM_PRINTCLIENT, (WPARAM)dc, PRF_CLIENT | PRF_ERASEBKGND);
    SelectObject(dc, old);
    g_tabBrush = CreatePatternBrush(bmp);
    DeleteObject(bmp); DeleteDC(dc); ReleaseDC(g_tabs, screen);
}

void Build()
{
    const int left = 28, labelW = 220, ctrlX = 260, ctrlW = 320, top = 118, rowH = 50;
    Make(L"STATIC", L"GEARS OF WAR 3", SS_LEFT | WS_VISIBLE, 24, 14, 500, 34, 0, g_fontTitle);
    Make(L"STATIC", L"PC port " GOW3_VERSION L" \x2014 options, mods and Play", SS_LEFT | WS_VISIBLE, 26, 48, 400, 20, 0, g_fontSmall);
    g_tabs = Make(WC_TABCONTROLW, L"", WS_VISIBLE | WS_CLIPSIBLINGS, 16, 76, 608, 494, ID_TABS, g_font);
    for (int i = 0; i < kPageCount; i++)
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
    BuildModsPage(g_pageControls[5]);
    BuildMultiplayerPage(g_pageControls[6]);
    Make(L"BUTTON", L"Restore defaults", BS_PUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 16, 584, 140, 34, ID_DEFAULTS, g_font);
    g_status = Make(L"STATIC", L"", SS_LEFT | WS_VISIBLE | SS_ENDELLIPSIS, 166, 593, 150, 20, 0, g_fontSmall);
    Make(L"BUTTON", L"Save", BS_PUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 324, 584, 90, 34, ID_SAVE, g_font);
    Make(L"BUTTON", L"Close", BS_PUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 422, 584, 90, 34, ID_CLOSE, g_font);
    Make(L"BUTTON", L"Play", BS_DEFPUSHBUTTON | WS_VISIBLE | WS_TABSTOP, 520, 584, 104, 34, ID_PLAY, g_fontBold);
    MakeTabBrush();
    // tabs draw over siblings unless they come last in the z order
    SetWindowPos(g_tabs, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE);
    LoadValues(false);
    LoadMultiplayer(false);
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
        else if (((NMHDR*)lp)->idFrom == ID_MOD_LIST) OnModListNotify((NMHDR*)lp);
        else if (((NMHDR*)lp)->idFrom == ID_MP_SERVERLIST)
        {
            if (((NMHDR*)lp)->code == LVN_ITEMCHANGED) ShowServerInfo();
            else if (((NMHDR*)lp)->code == NM_DBLCLK) RefreshServers();
        }
        return 0;
    case WM_HSCROLL:
        for (Option& o : g_options) if ((HWND)lp == o.control) SliderText(o);
        return 0;
    case WM_COMMAND:
        switch (LOWORD(wp))
        {
        case ID_PLAY: if (Play()) DestroyWindow(h); return 0;
        case ID_SAVE: Status(SaveValues() ? L"Saved." : L"Could not save gow3.ini."); return 0;
        case ID_DEFAULTS: LoadValues(true); LoadMultiplayer(true); Status(L"Defaults restored (not saved yet)."); return 0;
        case ID_CLOSE: DestroyWindow(h); return 0;
        case ID_MOD_UP: MoveMod(-1); return 0;
        case ID_MOD_DOWN: MoveMod(1); return 0;
        case ID_MOD_INSTALL: InstallZip(); return 0;
        case ID_MOD_FOLDER:
            CreateDirectoryW(g_modsDir.c_str(), nullptr);
            ShellExecuteW(h, L"open", g_modsDir.c_str(), nullptr, nullptr, SW_SHOWNORMAL);
            return 0;
        case ID_MOD_REFRESH: ReloadMods(); Status(L"Mod list refreshed."); return 0;
        case ID_MOD_REMOVE: RemoveMod(); return 0;
        case ID_MP_OFFLINE: case ID_MP_FRIENDS: case ID_MP_SERVER: SelectMode(LOWORD(wp)); Status(L""); return 0;
        case ID_MP_COPY: CopyCode(); return 0;
        case ID_MP_CHECK: CheckFriend(); return 0;
        case ID_MP_REFRESH: RefreshServers(); return 0;
        case ID_MP_REMOVE: RemoveServer(); return 0;
        case ID_MP_RUNSERVER: RunServer(); return 0;
        case ID_MP_ADD: AddServer(); return 0;
        case ID_MP_FIREWALL: AllowFirewall(); return 0;
        case ID_MP_UPNP: if (Checked(g_mpFriends)) FetchCode(); return 0;
        case ID_MP_JOINBOX: if (HIWORD(wp) == EN_CHANGE) SetWindowTextW(g_mpCheckResult, L""); return 0;
        }
        if (HIWORD(wp) == BN_CLICKED || HIWORD(wp) == CBN_SELCHANGE) Status(L"");
        return 0;
    case WM_APP_CODE: OnCode(lp); return 0;
    case WM_APP_SERVERINFO: OnServerInfo(wp, lp); return 0;
    case WM_APP_CHECK: { std::wstring t; TakeText(lp, t); SetWindowTextW(g_mpCheckResult, t.c_str()); return 0; }
    case WM_APP_FIREWALL: OnFirewall(wp); return 0;
    case WM_CTLCOLORSTATIC:
    {
        // labels, check boxes and sliders on the tab page: the page's own background (a brush of the tab control's
        // drawn pane, aligned to the control); elsewhere, and read-only edits: the window colour
        SetBkMode((HDC)wp, TRANSPARENT);
        const HWND c = (HWND)lp;
        wchar_t cls[16] = {};
        GetClassNameW(c, cls, 16);
        RECT r; GetWindowRect(c, &r);
        MapWindowPoints(nullptr, g_tabs, (POINT*)&r, 2);
        RECT pane; GetClientRect(g_tabs, &pane);
        SendMessageW(g_tabs, TCM_ADJUSTRECT, FALSE, (LPARAM)&pane);
        if (g_tabBrush && _wcsicmp(cls, L"Edit") != 0 && r.left >= pane.left && r.top >= pane.top && r.right <= pane.right && r.bottom <= pane.bottom)
        {
            SetBrushOrgEx((HDC)wp, -r.left, -r.top, nullptr);
            return (LRESULT)g_tabBrush;
        }
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
    INITCOMMONCONTROLSEX icc{ sizeof(icc), ICC_TAB_CLASSES | ICC_BAR_CLASSES | ICC_STANDARD_CLASSES | ICC_LISTVIEW_CLASSES };
    InitCommonControlsEx(&icc);

    wchar_t exe[MAX_PATH];
    GetModuleFileNameW(nullptr, exe, MAX_PATH);
    g_exeDir = exe;
    g_exeDir.resize(g_exeDir.find_last_of(L"\\/"));
    g_modsDir = g_exeDir + L"\\mods";
    {   // GOW3_MODS_DIR=<folder>: another mods folder, as the game takes it (tests)
        wchar_t dir[MAX_PATH] = {};
        const DWORD n = GetEnvironmentVariableW(L"GOW3_MODS_DIR", dir, MAX_PATH);
        if (n > 0 && n < MAX_PATH) g_modsDir = dir;
    }
    // test mode (GOW3_LAUNCHER_SHOT=<folder>): each page drawn off-screen to page<N>.bmp, the ini saved, no focus taken
    wchar_t shotDir[MAX_PATH] = {};
    const bool shot = GetEnvironmentVariableW(L"GOW3_LAUNCHER_SHOT", shotDir, MAX_PATH) > 0;
    wchar_t iniOverride[MAX_PATH] = {};
    g_ini.Load(GetEnvironmentVariableW(L"GOW3_LAUNCHER_INI", iniOverride, MAX_PATH) > 0 ? std::wstring(iniOverride)
                                                                                       : g_exeDir + L"\\gow3.ini");
    g_options = MakeOptions();
    WSADATA wsa; WSAStartup(MAKEWORD(2, 2), &wsa);
    // tests: nothing sent to the network, unless GOW3_LAUNCHER_NETTEST=1 (the Multiplayer page's queries, against a
    // test server on this PC: mp_server_live / mp_friends_live.bmp)
    const bool netTest = GetEnvironmentVariableW(L"GOW3_LAUNCHER_NETTEST", nullptr, 0) > 0;
    g_noNetwork = shot && !netTest;
    g_profilePath = UserDir() + L"\\profile.ini";  // in the game's user folder (GOW3_DATA_DIR for tests)

    WNDCLASSEXW wc{ sizeof(wc) };
    wc.lpfnWndProc = WndProc;
    wc.hInstance = inst;
    wc.hCursor = LoadCursorW(nullptr, IDC_ARROW);
    wc.hbrBackground = GetSysColorBrush(COLOR_WINDOW);
    wc.lpszClassName = L"GoW3Launcher";
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
        auto capture = [&](const wchar_t* label) {
            RedrawWindow(g_wnd, nullptr, nullptr, RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW);
            HDC screen = GetDC(nullptr), dc = CreateCompatibleDC(screen);
            BITMAPINFO bi{ { sizeof(BITMAPINFOHEADER), w, -h, 1, 32, BI_RGB } };
            void* bits = nullptr;
            HBITMAP bmp = CreateDIBSection(screen, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
            SelectObject(dc, bmp);
            PrintWindow(g_wnd, dc, PW_RENDERFULLCONTENT);
            BITMAPFILEHEADER fh{ 0x4D42, DWORD(sizeof(fh) + sizeof(BITMAPINFOHEADER) + w * h * 4), 0, 0, DWORD(sizeof(fh) + sizeof(BITMAPINFOHEADER)) };
            wchar_t name[MAX_PATH]; swprintf(name, MAX_PATH, L"%s\\%s.bmp", shotDir, label);
            if (FILE* f = _wfopen(name, L"wb"))
            {
                fwrite(&fh, sizeof(fh), 1, f);
                fwrite(&bi.bmiHeader, sizeof(BITMAPINFOHEADER), 1, f);
                fwrite(bits, 4, size_t(w) * h, f);
                fclose(f);
            }
            DeleteObject(bmp); DeleteDC(dc); ReleaseDC(nullptr, screen);
        };
        // GOW3_LAUNCHER_MODTEST=1: the Mods page's actions, as a player would do them: select the second mod, turn the
        // first off, move the third up, install GOW3_LAUNCHER_INSTALL (a .zip) if set; mods_selected / mods_after.bmp
        if (GetEnvironmentVariableW(L"GOW3_LAUNCHER_MODTEST", nullptr, 0) > 0)
        {
            SendMessageW(g_tabs, TCM_SETCURSEL, 5, 0);
            ShowPage(5);
            auto select = [](int i) {
                LVITEMW st{}; st.stateMask = LVIS_SELECTED | LVIS_FOCUSED; st.state = LVIS_SELECTED | LVIS_FOCUSED;
                SendMessageW(g_modList, LVM_SETITEMSTATE, i, (LPARAM)&st);
            };
            select(1);
            capture(L"mods_selected");
            LVITEMW off{}; off.stateMask = LVIS_STATEIMAGEMASK; off.state = INDEXTOSTATEIMAGEMASK(1);
            SendMessageW(g_modList, LVM_SETITEMSTATE, 0, (LPARAM)&off);
            select(2);
            MoveMod(-1);
            wchar_t zip[MAX_PATH] = {};
            if (GetEnvironmentVariableW(L"GOW3_LAUNCHER_INSTALL", zip, MAX_PATH) > 0) InstallZipFile(zip, false);
            capture(L"mods_after");
            // GOW3_LAUNCHER_REMOVE=1: then remove the first mod in the list (to the Recycle Bin); mods_removed.bmp
            if (GetEnvironmentVariableW(L"GOW3_LAUNCHER_REMOVE", nullptr, 0) > 0) { select(0); RemoveMod(false); capture(L"mods_removed"); }
        }
        if (netTest)
        {
            // what a player sees after a moment: the server list answered, the friend's code checked, the firewall read
            auto pump = [](int ms) {
                for (const ULONGLONG end = GetTickCount64() + ms; GetTickCount64() < end;)
                {
                    MSG m;
                    while (PeekMessageW(&m, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&m); DispatchMessageW(&m); }
                    Sleep(20);
                }
            };
            SendMessageW(g_tabs, TCM_SETCURSEL, 6, 0);
            ShowPage(6);
            SelectMode(ID_MP_SERVER);
            pump(3000);
            capture(L"mp_server_live");
            SelectMode(ID_MP_FRIENDS);
            CheckFriend();
            pump(3500);
            capture(L"mp_friends_live");
        }
        // the Multiplayer page in each mode: mp_offline / mp_friends / mp_server.bmp (the ini's mode is put back after)
        {
            SendMessageW(g_tabs, TCM_SETCURSEL, 6, 0);
            ShowPage(6);
            HWND before = Checked(g_mpServer) ? g_mpServer : Checked(g_mpFriends) ? g_mpFriends : g_mpOffline;
            for (auto [id, label] : { std::pair{ ID_MP_OFFLINE, L"mp_offline" }, { ID_MP_FRIENDS, L"mp_friends" }, { ID_MP_SERVER, L"mp_server" } })
            {
                SelectMode(id);
                capture(label);
            }
            SelectMode(GetDlgCtrlID(before));
        }
        for (int page = 0; page < kPageCount; page++)
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
