// Native mouse look: the Xbox build of Gears of War 3 (like Gears 2) still carries UE3's PC mouse path. Xe-GearInput.ini binds
// MouseX/MouseY to "Count bXAxis | Axis aMouseX" / aMouseY, and PlayerInput applies those as rotation deltas
// (MouseSensitivity, FOV scaling, mouse smoothing) instead of a stick rate. The gamepad pump calls
// UPlayerInput::InputAxis (8273B518) for every stick axis every frame, so right after its right-stick calls we call
// InputAxis again with KEY_MouseX / KEY_MouseY and the raw mouse counts gathered since the previous frame
// (hid::TakeMouseDelta). That makes the mouse the engine's mouse: 1:1, no deadzone, no turn acceleration, no cap.
//
// Addresses (Gears of War 3 XEX): InputAxis(this r3, ControllerId r4, FName Key r5 (index << 32 | number), Delta f1,
// DeltaTime f2, bGamepad r6) at 8273B518 (compares the key with KEY_MouseX, keeps mouse sampling stats, stores the
// key at this+0xE8, tail-calls 8273AFC0), found from the KEY_MouseX global; key FNames written by the static
// initializer at 824F10F8: KEY_MouseX 834440C8, KEY_MouseY 834440D0, KEY_XboxTypeS_RightX 83444438, RightY 83444440.
#include <stdafx.h>
#include <cpu/ppc_context.h>
#include <kernel/memory.h>
#include <hid/hid.h>
#include <os/logger.h>

extern "C" PPC_FUNC(__imp__sub_8273B518);

namespace
{
    constexpr uint32_t KEY_MouseX = 0x834440C8, KEY_MouseY = 0x834440D0, KEY_RightX = 0x83444438, KEY_RightY = 0x83444440;

    uint64_t Name(uint32_t global) { return ByteSwap(*(uint64_t*)g_memory.Translate(global)); }   // FName as passed in r5

    // call InputAxis with another key and delta, keeping the caller's view of the registers it may rely on
    void InjectAxis(PPCContext& ctx, uint8_t* base, uint32_t self, uint32_t controller, uint64_t key, double delta, double deltaTime)
    {
        const PPCRegister r3 = ctx.r3, r4 = ctx.r4, r5 = ctx.r5, r6 = ctx.r6;
        const PPCRegister f1 = ctx.f1, f2 = ctx.f2;
        ctx.r3.u64 = self;
        ctx.r4.u64 = controller;
        ctx.r5.u64 = key;
        ctx.f1.f64 = delta;
        ctx.f2.f64 = deltaTime;
        ctx.r6.u64 = 0;   // bGamepad = false: a mouse
        __imp__sub_8273B518(ctx, base);
        ctx.r3 = r3; ctx.r4 = r4; ctx.r5 = r5; ctx.r6 = r6; ctx.f1 = f1; ctx.f2 = f2;
    }
}

PPC_FUNC(sub_8273B518)
{
    const uint32_t self = ctx.r3.u32, controller = ctx.r4.u32;
    static const bool countCalls = getenv("GOW3_INPUT_COUNT") != nullptr;
    if (countCalls)
    {
        static std::atomic<uint32_t> calls{ 0 };
        static uint64_t since = GetTickCount64();
        calls++;
        static const uint64_t start = GetTickCount64();
        if (GetTickCount64() - since >= 2000) { LOGFN("input: t={} s InputAxis {} calls in {} ms", (GetTickCount64() - start) / 1000, calls.exchange(0), GetTickCount64() - since); since = GetTickCount64(); }
    }
    const uint64_t key = ctx.r5.u64;
    const double deltaTime = ctx.f2.f64;
    __imp__sub_8273B518(ctx, base);

    if (controller != 0 || !hid::IsNativeMouseEnabled())
        return;
    static uint64_t keyRightX = 0, keyRightY = 0, keyMouseX = 0, keyMouseY = 0;
    if (!keyRightX)
    {
        keyRightX = Name(KEY_RightX); keyRightY = Name(KEY_RightY);
        keyMouseX = Name(KEY_MouseX); keyMouseY = Name(KEY_MouseY);
        LOGFN("input: native mouse look (MouseX {:X}, MouseY {:X}, RightX {:X}, RightY {:X})", keyMouseX >> 32, keyMouseY >> 32,
            keyRightX >> 32, keyRightY >> 32);
    }
    static thread_local float pendingY = 0;
    if (key == keyRightX)
    {
        float dx = 0, dy = 0;
        hid::TakeMouseDelta(dx, dy);   // raw counts since the previous frame (also marks the native path active)
        pendingY = dy;
        if (dx != 0) InjectAxis(ctx, base, self, controller, keyMouseX, dx, deltaTime);
        static const bool debug = getenv("GOW3_INPUT_DEBUG") != nullptr;
        if (debug && (dx != 0 || dy != 0)) LOGFN("input: mouse axes {:.1f},{:.1f} (dt {:.4f}, this {:08X})", dx, dy, deltaTime, self);
    }
    else if (key == keyRightY)
    {
        // UE3 MouseY: positive = mouse moved up (look up); raw input: positive = moved toward the user (down)
        if (pendingY != 0) InjectAxis(ctx, base, self, controller, keyMouseY, -pendingY, deltaTime);
        pendingY = 0;
    }
}
