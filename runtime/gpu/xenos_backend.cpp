// GPU backend selection and the Xenia D3D12 backend's runtime side (the GPU itself: xe_glue + Xenia, BSD-3).
//
// - Guest MMIO: GPU registers (0x7FC80000, plain memory for reads, seeded with the console's values) and the XMA
//   decoder (0x7FEA0000). Accesses are trapped: the page is protected, a fault opens it (for a trapped read, the
//   register's current value is stored first) and single-steps the faulting instruction (trap flag); then a store's
//   value is forwarded and the page is protected again. No instruction decoding, and only that thread is affected.
// - CPU writes to physical pages the GPU caches mirror fault too; the glue invalidates and unprotects them.
#include <stdafx.h>
#include "gpu.h"
#include <cpu/guest_thread.h>
#include <kernel/memory.h>
#include <kernel/heap.h>
#include <kernel/physical.h>
#include <map>
#include <user/settings.h>
#include <os/logger.h>
#include "../../xe_glue/gow2_gpu.h"
#include <filesystem>

std::filesystem::path GetSharedUserPath();   // main.cpp
namespace prompts { void OnTextureLoad(uint32_t, uint32_t, uint32_t, uint32_t, uint32_t, bool); void Start(); }   // ui/prompts.cpp
namespace mouse_menu { void Start(); }   // ui/mouse_menu.cpp
namespace gfx_menu { void Start(); }     // ui/gfx_menu.cpp
namespace pos_log { void Start(); }      // ue3/pos_log.cpp (GOW3_LOG_POS)
namespace mem_find { void Start(); }     // ue3/mem_find.cpp (GOW3_FIND)
namespace profiler { void Start(); }     // ue3/profiler.cpp (GOW3_PROFILE)
namespace prop_log { void Start(); }
namespace campaign_chapters { void Start(); }     // ue3/campaign_chapters.cpp
namespace navmesh_build { void Init(); }  // ue3/navmesh_build.cpp (GOW3_NAVMESH_BUILD)
namespace play_area { void Start(); }     // ue3/play_area.cpp (GOW3_NO_PLAY_AREA_KILL)
namespace system_settings { void Start(); }   // ui/system_settings.cpp

namespace
{
    bool g_xenia = false;
    std::mutex g_interruptMutex;   // the guest's interrupt callback runs on one thread at a time

    uint8_t* MmioHost() { return (uint8_t*)g_memory.Translate(gpu::kMmioBase); }

    // values the D3D reads from registers (Xenia GraphicsSystem::ReadRegister)
    void SeedRegisters()
    {
        auto set = [](uint32_t index, uint32_t value) { *(uint32_t*)(MmioHost() + index * 4) = ByteSwap(value); };
        set(0x0F00, 0x08100748);   // RB_EDRAM_TIMING
        set(0x0F01, 0x0000200E);   // RB_BC_CONTROL
        set(0x0F2D, 0x00BBBBBB);   // RB_HSIO_INTERFACE_ALIGNER_VALUE
        set(0x1951, 1);            // interrupt status: vblank
        set(0x1961, 0x050002D0);   // AVIVO_D1MODE_VIEWPORT_SIZE 1280x720
    }

    // MMIO devices: GPU registers (writes trapped; reads from memory) and the XMA decoder (reads and writes trapped:
    // its CurrentContextIndex must rotate on every read)
    struct MmioDevice
    {
        uint32_t base, size;
        bool trapReads;
        uint32_t (*read)(uint32_t index);               // raw big-endian word to place in memory
        void (*write)(uint32_t index, uint32_t raw);    // raw big-endian word the guest stored
        uint8_t* Host() const { return (uint8_t*)g_memory.Translate(base); }
        void Protect(bool open) const
        {
            DWORD old;
            VirtualProtect(Host(), size, open ? PAGE_READWRITE : (trapReads ? PAGE_NOACCESS : PAGE_READONLY), &old);
        }
    };
    MmioDevice g_devices[] = {
        { gpu::kMmioBase, gpu::kMmioSize, false, nullptr,
          [](uint32_t index, uint32_t raw) { gow2gpu::WriteRegister(index, ByteSwap(raw)); } },
        { 0x7FEA0000, 0x10000, true, [](uint32_t index) {
              static std::mutex m; static std::map<uint32_t, uint32_t> seen;
              // XMA registers are little-endian: the title uses lwbrx/stwbrx. Xenia's register file holds the values
              // those see, and its Read/WriteRegister byte-swap for its MMIO handler; in memory, the LE bytes
              const uint32_t value = ByteSwap(gow2gpu::XmaReadRegister(index));
              { std::lock_guard lock(m); if (seen[index]++ < 1) LOGFN("audio: XMA read [{:04X}] -> {:08X}", index, value); }
              return value; },
          [](uint32_t index, uint32_t raw) {
              static std::mutex m; static std::map<uint32_t, uint32_t> seen;
              { std::lock_guard lock(m); if (seen[index]++ < 2) LOGFN("audio: XMA write [{:04X}] = {:08X}", index, raw); }
              gow2gpu::XmaWriteRegister(index, ByteSwap(raw));   // raw = the value stwbrx stored (LE in memory)
          } },
    };
    size_t g_deviceCount = 0;   // armed devices

    struct PendingAccess { const MmioDevice* device; uintptr_t at; bool write; };
    thread_local PendingAccess t_pending{};

    LONG CALLBACK GpuExceptionHandler(EXCEPTION_POINTERS* info)
    {
        const auto* rec = info->ExceptionRecord;
        if (rec->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && rec->NumberParameters >= 2)
        {
            const bool write = rec->ExceptionInformation[0] == 1;
            const uintptr_t at = rec->ExceptionInformation[1];
            for (size_t i = 0; i < g_deviceCount; i++)
            {
                const MmioDevice& d = g_devices[i];
                const uintptr_t host = uintptr_t(d.Host());
                if (at < host || at >= host + d.size) continue;
                d.Protect(true);
                const uintptr_t word = at & ~uintptr_t(3);
                if (!write && d.read)
                    *(uint32_t*)word = d.read(uint32_t(word - host) / 4);   // the value the load will see
                t_pending = { &d, word, write };
                info->ContextRecord->EFlags |= 0x100;   // trap after this one instruction
                return EXCEPTION_CONTINUE_EXECUTION;
            }
            if (write && gow2gpu::HandleAccessViolation((void*)at, true))
                return EXCEPTION_CONTINUE_EXECUTION;
        }
        else if (rec->ExceptionCode == EXCEPTION_SINGLE_STEP && t_pending.device)
        {
            const PendingAccess p = t_pending;
            t_pending = {};
            info->ContextRecord->EFlags &= ~0x100u;
            const uint32_t raw = *(uint32_t*)p.at;
            p.device->Protect(false);
            if (p.write)
                p.device->write(uint32_t(p.at - uintptr_t(p.device->Host())) / 4, raw);
            return EXCEPTION_CONTINUE_EXECUTION;
        }
        return EXCEPTION_CONTINUE_SEARCH;
    }

    // guest function call on the calling host thread (Xenia's audio callbacks)
    void CallGuest(uint32_t function, const uint64_t* args, size_t count)
    {
        thread_local GuestThreadContext* ctx = nullptr;
        if (!ctx) ctx = new GuestThreadContext(3);
        for (size_t i = 0; i < count && i < 8; i++) (&ctx->ppcContext.r3)[i].u64 = args[i];
        g_memory.FindFunction(function)(ctx->ppcContext, g_memory.base);
    }

    uint32_t SystemAlloc(uint32_t size, uint32_t alignment, bool physical)
    {
        if (physical || alignment > 16)
        {
            uint32_t off = physical::Alloc(size, std::max<uint32_t>(alignment, 0x1000));
            return off == 0xFFFFFFFF ? 0 : 0xA0000000u | off;
        }
        void* p = g_userHeap.Alloc(size);
        return p ? g_memory.MapVirtual(p) : 0;
    }

    void SystemFree(uint32_t address)
    {
        if (address >= 0xA0000000u) physical::Free(physical::ToPhysical(address));
        else g_userHeap.Free(g_memory.Translate(address));
    }

    void DispatchInterrupt(uint32_t callback, uint32_t userData, uint32_t source, uint32_t cpu)
    {
        gpu::CallGuestInterrupt(callback, userData, source, cpu);
    }
}

void gpu::CallGuestInterrupt(uint32_t callback, uint32_t userData, uint32_t source, uint32_t cpu)
{
    if (!callback)
        return;
    thread_local GuestThreadContext* ctx = nullptr;
    if (!ctx) ctx = new GuestThreadContext(2);
    std::lock_guard lock(g_interruptMutex);
    ctx->ppcContext.r3.u64 = source;
    ctx->ppcContext.r4.u64 = userData;
    g_memory.FindFunction(callback)(ctx->ppcContext, g_memory.base);
}

void gpu::Initialize()
{
    if (getenv("GOW3_NULL_GPU"))
    {
        LOGN("gpu: null command processor (GOW3_NULL_GPU)");
        return;
    }
    gow2gpu::Host host{};
    host.guestBase = g_memory.base;
    host.dispatchInterrupt = DispatchInterrupt;
    host.windowTitle = "Gears of War 3";
    host.callGuest = CallGuest;
    host.systemAlloc = SystemAlloc;
    host.systemFree = SystemFree;
    const auto& cfg = settings::Get();
    host.resolutionScale = cfg.resolutionScale;
    host.vsync = cfg.vsync;
    host.fullscreen = cfg.fullscreen;
    host.vblankHz = std::max(60, cfg.frameRate);
    host.xenosSamplePositions = cfg.xenosSamplePositions;
    static const std::wstring shaderCache = (GetSharedUserPath() / "shader_cache").wstring();
    host.shaderCacheRoot = cfg.shaderCache ? shaderCache.c_str() : nullptr;
    host.upscaling = cfg.upscaling;
    host.sharpness = cfg.sharpness;
    host.antialiasing = cfg.antialiasing;
    host.windowWidth = cfg.windowWidth;
    host.windowHeight = cfg.windowHeight;
    host.scaleThreshold = uint32_t(cfg.scaleThreshold);
    host.onTextureLoad = prompts::OnTextureLoad;
    if (!gow2gpu::Start(host))
    {
        LOGN_WARNING("gpu: Direct3D 12 backend unavailable, using the null command processor");
        return;
    }
    g_xenia = true;
    prompts::Start();
    // mouse_menu::Start();      // Gears 2 UI object layout (ui/mouse_menu.cpp): not mapped for Gears 3 yet
    gfx_menu::Start();           // Gears 3 menus are Scaleform movies (ui/gfx_menu.cpp)
    pos_log::Start();            // GOW3_LOG_POS: pawn locations every 2 s (placing converted level content)
    mem_find::Start();           // GOW3_FIND: where byte patterns are in guest memory (with GOW3_WATCH ...,rw)
    profiler::Start();           // GOW3_PROFILE: sampling profiler of the guest threads, every 10 s
    campaign_chapters::Start();  // chapters 32+ declared in config: their maps in ChapterLevelNames (ue3/campaign_chapters.cpp)
    prop_log::Start();           // GOW3_LOG_PROPS: chosen properties and states of live objects, every 2 s
    navmesh_build::Init();       // GOW3_NAVMESH_BUILD: Gears 3's leftover navmesh generator passes on the Pylons
    play_area::Start();          // GOW3_NO_PLAY_AREA_KILL: no out-of-play-area kill (free-roam converted levels)
    // system_settings::Start(); // Gears 2 GSystemSettings address (ui/system_settings.cpp): not found in Gears 3 yet
    SeedRegisters();
    AddVectoredExceptionHandler(1, GpuExceptionHandler);   // first: ahead of the crash reporter
    g_deviceCount = 1;
    g_devices[0].Protect(false);
    LOGN("gpu: Direct3D 12 (Xenia GPU)");

    // GOW3_SCREENSHOTS="t1,t2,...": save the presented image at those seconds (build/shots/shot_<t>.png), for testing
    if (const char* list = getenv("GOW3_SCREENSHOTS"))
    {
        std::thread([times = std::string(list)] {
            std::filesystem::create_directories("shots");
            const uint64_t start = GetTickCount64();
            size_t pos = 0;
            while (pos < times.size())
            {
                const size_t comma = times.find(',', pos);
                const int t = atoi(times.substr(pos, comma - pos).c_str());
                pos = comma == std::string::npos ? times.size() : comma + 1;
                while (GetTickCount64() - start < uint64_t(t) * 1000) Sleep(50);
                const std::string path = fmt::format("shots/shot_{:03}s.png", t);
                LOGFN("screenshot {}: {}", path, gow2gpu::CaptureScreenshot(path.c_str()) ? "saved" : "failed");
            }
        }).detach();
    }

    if (!cfg.audio || !gow2gpu::StartAudio())
        LOGN_WARNING("audio: disabled");
    else
    {
        g_deviceCount = 2;
        g_devices[1].Protect(false);
        LOGN("audio: XAudio2 + XMA decoder (Xenia)");
    }
}

// ---- the Vd* entry points (kernel/gow2_video.cpp) ----
void gpu::InitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2)
{
    if (g_xenia) gow2gpu::InitializeRingBuffer(ptr, sizeLog2);
    else null::InitializeRingBuffer(ptr, sizeLog2);
}

void gpu::EnableReadPointerWriteBack(uint32_t ptr, uint32_t blockLog2)
{
    if (g_xenia) gow2gpu::EnableReadPointerWriteBack(ptr, blockLog2);
    else null::EnableReadPointerWriteBack(ptr, blockLog2);
}

void gpu::SetInterruptCallback(uint32_t callback, uint32_t userData)
{
    if (g_xenia) gow2gpu::SetInterruptCallback(callback, userData);
    else null::SetInterruptCallback(callback, userData);
}

void gpu::SetSystemCommandBufferIdentifier(uint32_t address) { null::SetSystemCommandBufferIdentifier(address); }
