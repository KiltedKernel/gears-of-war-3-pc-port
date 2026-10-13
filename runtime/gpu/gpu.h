#pragma once
// Xenos GPU side of the Gears of War 3 runtime. The game's own (recompiled) Xbox D3D writes PM4 packets into a ring
// buffer and the GPU registers (MMIO at 0x7FC80000); the command processor consumes them on a host thread.
// Backends: Xenia's D3D12 GPU (gpu/xenos_backend.cpp + xe_glue, renders) or the null command processor
// (gpu/command_processor.cpp: synchronization only, draws skipped; GOW3_NULL_GPU=1).
#include <cstdint>
#include <xbox.h>

namespace gpu
{
    constexpr uint32_t kDisplayWidth = 1280, kDisplayHeight = 720;
    constexpr uint32_t kMmioBase = 0x7FC80000, kMmioSize = 0x10000;

    void InitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2);
    void EnableReadPointerWriteBack(uint32_t ptr, uint32_t blockLog2);
    void SetInterruptCallback(uint32_t callback, uint32_t userData);
    void SetSystemCommandBufferIdentifier(uint32_t address);
    constexpr uint32_t kPm4XeSwap = 0x64;   // PM4 opcode of the swap packet VdSwap writes

    // picks the backend: call once before the guest starts
    void Initialize();
    // run the guest's graphics interrupt callback (r3 = source, r4 = user data) on the calling host thread
    void CallGuestInterrupt(uint32_t callback, uint32_t userData, uint32_t source, uint32_t cpu);

    namespace null
    {
        void InitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2);
        void EnableReadPointerWriteBack(uint32_t ptr, uint32_t blockLog2);
        void SetInterruptCallback(uint32_t callback, uint32_t userData);
        void SetSystemCommandBufferIdentifier(uint32_t address);
        void Start();             // command processor + vblank threads
        uint64_t FrameCount();
    }
}
