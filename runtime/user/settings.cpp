#include <stdafx.h>
#include "settings.h"
#include <os/logger.h>
#include <fstream>
#include <sstream>

namespace
{
    constexpr const char* kDefaults =
        "; Gears of War 3 (PC port) settings. Restart the game after editing.\n"
        "\n"
        "[display]\n"
        "; start in fullscreen (F11 or Alt+Enter toggles it while playing)\n"
        "fullscreen = false\n"
        "; internal rendering resolution: 1 = 1280x720 (console), 2 = 2560x1440, 3 = 3840x2160,\n"
        "; auto = the closest to your monitor (1440p and 1080p: 2, 4K: 3)\n"
        "resolution_scale = auto\n"
        "; scaling to the window: fsr (AMD FSR upscaling + sharpening), cas (sharpening only), bilinear\n"
        "upscaling = fsr\n"
        "; extra sharpening, 0.0 - 1.0\n"
        "sharpness = 0.0\n"
        "; post-process anti-aliasing: none, fxaa, fxaa_extreme\n"
        "antialiasing = none\n"
        "vsync = true\n"
        "; skip the Epic and Microsoft Game Studios logo movies at startup (or start gow3.exe with -nointro)\n"
        "skip_intro_movies = false\n"
        "; frame rate: 30 = the original console lock; 60, 120, 144, ... unlock it (game speed is unaffected)\n"
        "; (auto = the monitor's refresh rate)\n"
        "frame_rate = 60\n"
        "; window size in windowed mode\n"
        "window_width = 1280\n"
        "window_height = 720\n"
        "\n"
        "[graphics]\n"
        "; sharper textures at angles\n"
        "anisotropic_16x = true\n"
        "; larger texture streaming pool: less texture pop-in (recommended with resolution_scale 2-3)\n"
        "texture_streaming_fix = false\n"
        "; no black/blocky shading on characters (turns off the game's tiled MSAA rendering)\n"
        "black_shading_fix = true\n"
        "; MSAA at the console's sample positions (fewer broken edges and triangles)\n"
        "xenos_sample_positions = true\n"
        "; keep compiled shaders on disk (%LOCALAPPDATA%\\GoW3Recomp\\shader_cache): no stutter the second time\n"
        "shader_cache = true\n"
        "; shadow map size cap: 0 = the game's 800; up to 1536 (more detailed character and world shadows)\n"
        "shadow_resolution = 1280\n"
        "; characters cast shadows onto themselves (off on the console)\n"
        "self_shadowing = false\n"
        "; render targets this narrow (pixels) are not upscaled: Gears 3's tiny ambient-light buffer turns shadows green\n"
        "scale_threshold = 160\n"
        "; post-processing and effects\n"
        "motion_blur = true\n"
        "depth_of_field = true\n"
        "bloom = true\n"
        "ambient_occlusion = true\n"
        "lens_flares = true\n"
        "distortion = true\n"
        "fog_volumes = true\n"
        "\n"
        "[input]\n"
        "; mouse look speed (1.0 = default)\n"
        "mouse_sensitivity = 1.0\n"
        "invert_mouse_y = false\n"
        "; native = the engine's own mouse look (1:1); stick = the mouse drives the right stick\n"
        "mouse_mode = native\n"
        "; button prompts: auto (follow the last device used), keyboard, controller\n"
        "prompts = auto\n"
        "\n"
        "[audio]\n"
        "enabled = true\n";

    // build\gow3.ini; GOW3_SETTINGS=<file> uses another (tools/playtest.ps1: tests run with the defaults, not the
    // player's settings)
    std::filesystem::path Path()
    {
        wchar_t file[MAX_PATH] = {};
        const DWORD n = GetEnvironmentVariableW(L"GOW3_SETTINGS", file, MAX_PATH);
        if (n > 0 && n < MAX_PATH) return std::filesystem::path(file);
        wchar_t exe[MAX_PATH];
        GetModuleFileNameW(nullptr, exe, MAX_PATH);
        return std::filesystem::path(exe).parent_path() / "gow3.ini";
    }

    bool ParseBool(const std::string& v) { return v == "true" || v == "1" || v == "yes" || v == "on"; }

    settings::Settings Load()
    {
        settings::Settings s;
        const auto path = Path();
        std::error_code ec;
        if (!std::filesystem::exists(path, ec))
        {
            std::ofstream(path) << kDefaults;
            LOGFN("settings: wrote defaults to {}", path.string());
        }
        std::ifstream f(path);
        std::string line, section;
        while (std::getline(f, line))
        {
            if (auto c = line.find_first_of(";#"); c != std::string::npos) line.resize(c);
            auto trim = [](std::string t) {
                const auto a = t.find_first_not_of(" \t\r"), b = t.find_last_not_of(" \t\r");
                return a == std::string::npos ? std::string() : t.substr(a, b - a + 1);
            };
            line = trim(line);
            if (line.empty()) continue;
            if (line.front() == '[') { section = trim(line.substr(1, line.find(']') - 1)); continue; }
            const auto eq = line.find('=');
            if (eq == std::string::npos) continue;
            std::string key = trim(line.substr(0, eq)), value = trim(line.substr(eq + 1));
            for (auto& ch : value) ch = char(tolower((unsigned char)ch));
            const std::string k = section + "." + key;
            if (k == "display.fullscreen") s.fullscreen = ParseBool(value);
            else if (k == "display.resolution_scale") s.resolutionScale = value == "auto" ? 0 : std::clamp(atoi(value.c_str()), 1, 3);
            else if (k == "display.upscaling") s.upscaling = value == "bilinear" ? 0 : value == "cas" ? 1 : 2;
            else if (k == "display.sharpness") s.sharpness = std::clamp(float(atof(value.c_str())), 0.0f, 1.0f);
            else if (k == "display.antialiasing") s.antialiasing = value == "fxaa" ? 1 : value == "fxaa_extreme" ? 2 : 0;
            else if (k == "display.vsync") s.vsync = ParseBool(value);
            else if (k == "display.frame_rate")
            {
                if (value == "auto")
                {
                    // the monitor's refresh rate (the primary display's current mode)
                    DEVMODEW dm{}; dm.dmSize = sizeof(dm);
                    s.frameRate = EnumDisplaySettingsW(nullptr, ENUM_CURRENT_SETTINGS, &dm) && dm.dmDisplayFrequency > 1
                        ? std::clamp(int(dm.dmDisplayFrequency), 30, 360) : 60;
                }
                else s.frameRate = std::clamp(atoi(value.c_str()), 30, 360);
            }
            else if (k == "graphics.anisotropic_16x") s.anisotropic16x = ParseBool(value);
            else if (k == "graphics.texture_streaming_fix") s.textureStreamingFix = ParseBool(value);
            else if (k == "graphics.black_shading_fix") s.blackShadingFix = ParseBool(value);
            else if (k == "graphics.xenos_sample_positions") s.xenosSamplePositions = ParseBool(value);
            else if (k == "graphics.shader_cache") s.shaderCache = ParseBool(value);
            else if (k == "display.skip_intro_movies") s.skipIntroMovies = ParseBool(value);
            else if (k == "display.window_width") s.windowWidth = std::clamp(atoi(value.c_str()), 640, 7680);
            else if (k == "display.window_height") s.windowHeight = std::clamp(atoi(value.c_str()), 360, 4320);
            else if (k == "graphics.motion_blur") s.motionBlur = ParseBool(value);
            else if (k == "graphics.depth_of_field") s.depthOfField = ParseBool(value);
            else if (k == "graphics.bloom") s.bloom = ParseBool(value);
            else if (k == "graphics.ambient_occlusion") s.ambientOcclusion = ParseBool(value);
            else if (k == "graphics.lens_flares") s.lensFlares = ParseBool(value);
            else if (k == "graphics.distortion") s.distortion = ParseBool(value);
            else if (k == "graphics.fog_volumes") s.fogVolumes = ParseBool(value);
            else if (k == "graphics.self_shadowing") s.selfShadowing = ParseBool(value);
            else if (k == "graphics.scale_threshold") s.scaleThreshold = std::clamp(atoi(value.c_str()), 0, 1280);
            else if (k == "input.prompts") s.prompts = value == "keyboard" ? 1 : value == "controller" ? 2 : 0;
            else if (k == "graphics.shadow_resolution") s.shadowResolution = value == "0" ? 0 : std::clamp(atoi(value.c_str()), 256, 1536);
            else if (k == "input.mouse_sensitivity") s.mouseSensitivity = std::clamp(float(atof(value.c_str())), 0.05f, 20.0f);
            else if (k == "input.invert_mouse_y") s.invertMouseY = ParseBool(value);
            else if (k == "input.mouse_mode") s.nativeMouse = value != "stick";
            else if (k == "audio.enabled") s.audio = ParseBool(value);
        }
        // environment overrides for testing
        if (const char* v = getenv("GOW3_MOUSE_SENS")) s.mouseSensitivity = float(atof(v));
        if (getenv("GOW3_NO_AUDIO")) s.audio = false;
        if (s.resolutionScale == 0)
        {
            // auto: the scale nearest the monitor's height in 720-line steps (1080 -> 2: rendered above, scaled down)
            const int h = GetSystemMetrics(SM_CYSCREEN);
            s.resolutionScale = std::clamp(int(std::lround(h / 720.0)), 1, 3);
            LOGFN("settings: resolution scale auto -> {} (monitor height {})", s.resolutionScale, h);
        }
        LOGFN("settings: fullscreen {}, resolution scale {}, vsync {}, frame rate {}, mouse {} (invert {}, {}), audio {}",
            s.fullscreen, s.resolutionScale, s.vsync, s.frameRate, s.mouseSensitivity, s.invertMouseY,
            s.nativeMouse ? "native" : "stick", s.audio);
        return s;
    }
}

const settings::Settings& settings::Get()
{
    static const Settings s = Load();
    return s;
}
