// Gears of War 3 recompilation: host entry point.
// Boot order follows Unleashed Recompiled's KiSystemStartup/LdrLoadModule: guest memory + heaps, content roots
// (game:\ and D:\ = the player's disc files), load the XEX, then run the entry point on a guest thread.
#include <stdafx.h>
#include <gpu/gpu.h>
#include <timeapi.h>
#pragma comment(lib, "winmm.lib")
#include <kernel/memory.h>
#include <kernel/heap.h>
#include <kernel/xam.h>
#include <cpu/guest_thread.h>
#include <user/paths.h>
#include <apu/audio.h>
#include <os/logger.h>
#include "loader.h"
void InstallCrashHandler();
namespace watch { void Install(); void ArmCurrentThread(); }   // watch.cpp
void InstallHangDump();
#include <thread>

Memory g_memory;
bool g_skipIntroArg = false;   // -nointro (kernel/gow2_files.cpp)
Heap g_userHeap;

static std::filesystem::path g_gamePath;

std::filesystem::path GetGamePath() { return g_gamePath; }

// %LOCALAPPDATA%\GoW3Recomp: saves, profile, caches. GOW3_DATA_DIR=<folder> uses another one (tools/playtest.ps1 does,
// so scripted tests never touch the player's saves); the shader cache stays shared (GetSharedUserPath)
std::filesystem::path GetSharedUserPath()
{
    wchar_t buf[MAX_PATH];
    GetEnvironmentVariableW(L"LOCALAPPDATA", buf, MAX_PATH);
    return std::filesystem::path(buf) / "GoW3Recomp";
}

std::filesystem::path GetUserPath()
{
    static const std::filesystem::path path = [] {
        wchar_t buf[MAX_PATH];
        const DWORD n = GetEnvironmentVariableW(L"GOW3_DATA_DIR", buf, MAX_PATH);
        return n > 0 && n < MAX_PATH ? std::filesystem::path(buf) : GetSharedUserPath();
    }();
    return path;
}

std::filesystem::path GetSavePath(bool) { return GetUserPath() / "save"; }

static void KiSystemStartup()
{
    if (!g_memory.base)
    {
        LOGN_ERROR("cannot reserve 4 GB of guest memory");
        std::_Exit(1);
    }
    g_userHeap.Init();

    const auto gameContent = XamMakeContent(XCONTENTTYPE_RESERVED, "Game");
    XamRegisterContent(gameContent, (const char*)g_gamePath.u8string().c_str());
    XamContentCreateEx(0, "game", &gameContent, OPEN_EXISTING, nullptr, nullptr, 0, 0, nullptr);
    XamContentCreateEx(0, "D", &gameContent, OPEN_EXISTING, nullptr, nullptr, 0, 0, nullptr);   // the OS mounts the disc at D:

    std::error_code ec;
    std::filesystem::create_directories(GetSavePath(), ec);
    std::filesystem::create_directories(GetUserPath() / "cache", ec);   // cache:\ (game data cache partition)
    std::filesystem::create_directories(GetUserPath() / "hdd", ec);     // \Device\Harddisk0\Partition1
    XamRegisterContent(XamMakeContent(XCONTENTTYPE_SAVEDATA, "GearsSave"), (const char*)GetSavePath().u8string().c_str());

    XAudioInitializeSystem();

    // KeTimeStampBundle is read directly by the game: keep it current (the real kernel updates it every ms)
    std::thread([] { for (;;) { UpdateTimeStampBundle(); Sleep(1); } }).detach();
}

// the thread that runs the game's main loop (_xstart runs on it): the engine's game thread
DWORD g_mainThreadId = 0;

// wmain: the CRT then initializes _wpgmptr, which Xenia's base library uses to find the executable folder
int wmain(int argc, wchar_t* argv[])
{
    g_mainThreadId = GetCurrentThreadId();
    os::logger::Init();
    // 1 ms timer resolution (the default 15.6 ms makes every timed wait overshoot: audio pacing, timers, vblank)
    timeBeginPeriod(1);
    InstallCrashHandler();
    InstallHangDump();
    // usage: gow3.exe [folder with the player's disc files]. Default: %GOW3_GAME%\disc, else the layout tools/setup.ps1
    // makes (gow3-decomp\disc next to the repository; the exe is in <repository>\build)
    // options ("-nointro" / "--skip-intro") may come anywhere; the first other argument is the disc folder
    const wchar_t* discArg = nullptr;
    for (int i = 1; i < argc; i++)
    {
        const std::wstring a = argv[i];
        if (a == L"-nointro" || a == L"--nointro" || a == L"-skipintro" || a == L"--skip-intro") g_skipIntroArg = true;
        else if (!discArg && !a.empty() && a[0] != L'-') discArg = argv[i];
    }
    if (discArg)
        g_gamePath = discArg;
    else if (const wchar_t* game = _wgetenv(L"GOW3_GAME"))
        g_gamePath = std::filesystem::path(game) / "disc";
    else
    {
        wchar_t exe[MAX_PATH];
        GetModuleFileNameW(nullptr, exe, MAX_PATH);
        g_gamePath = std::filesystem::path(exe).parent_path().parent_path().parent_path() / "gow3-decomp" / "disc";
    }
    LOGFN("Gears of War 3 recompiled; game files: {}", g_gamePath.string());

    KiSystemStartup();
    gpu::Initialize();   // window + Direct3D 12 (Xenia's GPU), before the game touches the GPU
    uint32_t entry = LdrLoadModule(g_gamePath / "default.xex");
    if (!entry)
        return 1;

    watch::Install();            // GOW3_WATCH (debugging): hardware write watchpoint
    watch::ArmCurrentThread();
    GuestThread::Start({ entry, 0, 0 });
    LOGN("main thread returned");
    return 0;
}
