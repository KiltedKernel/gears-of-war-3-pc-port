#pragma once
// Interface between the port's runtime (clang-cl) and the Xenia GPU glue (xe_glue, built with MSVC inside Xenia's build).
// Plain C++ types only, so neither side needs the other's headers.
#include <cstddef>
#include <cstdint>

namespace portgpu
{
    struct Host
    {
        uint8_t* guestBase;   // 4 GB guest address space: host = guestBase + guest address
        // run the guest's graphics interrupt callback (r3 = source, r4 = user data) on the calling host thread
        void (*dispatchInterrupt)(uint32_t callback, uint32_t userData, uint32_t source, uint32_t cpu);
        const char* windowTitle;
        // run a guest function with arguments in r3.. on the calling host thread (audio callbacks)
        void (*callGuest)(uint32_t function, const uint64_t* args, size_t argCount);
        // system heap for Xenia's own guest-visible allocations (physical: in a 0xA0000000+ window); 0 = failed
        uint32_t (*systemAlloc)(uint32_t size, uint32_t alignment, bool physical);
        void (*systemFree)(uint32_t address);
        // display options (gow3.ini)
        int resolutionScale;   // 1..3
        bool vsync;
        bool fullscreen;
        int vblankHz;
        int windowWidth, windowHeight;   // windowed size (0 = 1280x720)
        uint32_t scaleThreshold;         // draw_resolution_scale_threshold (0 = scale every render target)   // emulated display refresh: 60 on the console; higher lets an unlocked game run faster
        bool xenosSamplePositions;   // rasterize MSAA at the real Xenos sample positions
        // compiled shader/pipeline storage (Xenia shader storage, title 4D53082D); null = off
        const wchar_t* shaderCacheRoot;
        int upscaling;        // 0 bilinear, 1 CAS, 2 FSR (Xenia presenter effect)
        float sharpness;      // CAS additional sharpness 0..1
        int antialiasing;     // 0 none, 1 FXAA, 2 FXAA extreme (swap post effect)
        // called on the GPU thread before a texture's guest data is uploaded (button prompts); may be null
        void (*onTextureLoad)(uint32_t address, uint32_t size, uint32_t format, uint32_t width, uint32_t height, bool tiled);
    };

    // Window + D3D12 + Xenia's command processor. False if D3D12 is unavailable (the runtime keeps its null GPU).
    bool Start(const Host& host);
    // guest physical memory (textures): copy out / in; invalidate = make the GPU reload what it cached from there
    void ReadPhysical(uint32_t address, void* out, size_t size);
    void WritePhysical(uint32_t address, const void* data, size_t size, bool invalidate);
    void Shutdown();

    void InitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2);
    void EnableReadPointerWriteBack(uint32_t ptr, uint32_t blockSizeLog2);
    void SetInterruptCallback(uint32_t callback, uint32_t userData);

    // Guest MMIO at 0x7FC80000: register index = (address & 0xFFFF) / 4
    void WriteRegister(uint32_t index, uint32_t value);
    uint32_t ReadRegister(uint32_t index);

    // CPU write to a physical page the GPU caches watch (textures, buffers). Called from the runtime's vectored
    // exception handler; true = it was a watched page, now unprotected and invalidated: resume execution.
    bool HandleAccessViolation(void* hostAddress, bool isWrite);

    // true while the window is open (closing it ends the game)
    bool IsRunning();

    // the game window (HWND), for keyboard/mouse input; null before Start
    void* GetWindowHandle();

    // ---- audio (Xenia's XAudio2 audio system + XMA decoder) ----
    bool StartAudio();
    // render driver clients (XAudioRegisterRenderDriverClient): returns the client index, or -1
    int AudioRegisterClient(uint32_t callback, uint32_t callbackArg);
    void AudioUnregisterClient(int index);
    void AudioSubmitFrame(int index, uint32_t samplesGuestAddress);   // 256 x 6 big-endian floats
    uint32_t XmaAllocateContext();                                    // guest address, 0 = none free
    void XmaReleaseContext(uint32_t context);
    // XMA MMIO (0x7FEA0000): raw big-endian register words as they sit in guest memory
    uint32_t XmaReadRegister(uint32_t index);
    void XmaWriteRegister(uint32_t index, uint32_t rawValue);

    // save the guest's current output image (what the game presented) as a PNG; false if nothing presented yet
    bool CaptureScreenshot(const char* pngPath);

    // toggle fullscreen (F11 / Alt+Enter)
    void ToggleFullscreen();

    // mouse look: hide the cursor over the window (window thread); the runtime pins it to the center
    void SetMouseCaptured(bool captured);
}
