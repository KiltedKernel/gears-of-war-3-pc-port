#pragma once
// Player settings: gow3.ini next to the executable (created with defaults on first run).
#include <cstdint>
#include <string>

namespace settings
{
    struct Settings
    {
        // [display]
        bool fullscreen = false;
        int resolutionScale = 0;      // internal resolution multiplier: 1 = 1280x720, 2 = 2560x1440, 3 = 3840x2160; 0 = auto
        int upscaling = 2;            // display scaling: 0 bilinear, 1 CAS (sharpen), 2 FSR (upscale + sharpen)
        float sharpness = 0.0f;       // extra CAS sharpening 0..1
        int antialiasing = 0;         // 0 none, 1 FXAA, 2 FXAA extreme
        bool vsync = true;
        int frameRate = 60;           // 30 = the console's lock; 60 / 120 / 144 / ...: unlocked (vblank at max(60, rate)); ini "auto" = monitor
        int windowWidth = 1280, windowHeight = 720;   // windowed size (fullscreen covers the monitor)
        // [graphics]
        bool anisotropic16x = true;
        bool xenosSamplePositions = true;
        int prompts = 0;                 // button prompts: 0 follow the device, 1 always keyboard/mouse, 2 always controller
        bool skipIntroMovies = false;    // no Epic / Microsoft Game Studios logos at startup (also: gow3.exe -nointro)
        bool shaderCache = true;         // keep compiled shaders on disk: no first-use hitches next time
        // render targets this narrow (pitch in pixels) are not upscaled by resolution scaling: Gears of War 3 builds its
        // ambient lighting from a tiny buffer (64 < pitch <= 160) that turns shadowed areas green when it is upscaled
        int scaleThreshold = 160;
        // post-processing toggles (hooks in kernel/gow3_patches.cpp)
        bool motionBlur = true, depthOfField = true, lensFlares = true;
        bool ambientOcclusion = true;
        // [input]
        float mouseSensitivity = 1.0f;
        bool invertMouseY = false;
        bool nativeMouse = true;      // engine mouse look (true) or mouse as right stick (false)
        // [audio]
        bool audio = true;
        // [network] (runtime/net/vnet.cpp, docs/MULTIPLAYER.md): system link with other players
        bool netOnline = false;          // false: offline (nothing is sent to the network)
        std::string netServer;           // a server's address[:port]: play there; empty: direct play with friends
        std::string netJoin;             // direct: a friend's code or address[:port]; empty: find a game on this
                                         // network, or open one that friends can join
        std::string netRoom;             // server: a private room's name (empty: the server's lobby)
        std::string netPassword;         // server: its password, if it has one
        int netPort = 3074;              // UDP port this PC's game opens to friends (direct)
        bool netUpnp = true;             // direct: ask the router to forward the port (UPnP)
    };

    const Settings& Get();   // loaded on first use
}
