// Xenos command processor (synchronization subset). Packet formats and semantics follow Xenia
// (src/xenia/gpu/command_processor.cc, BSD-3-Clause): PM4 type 0/1 register writes, type 2 NOP, type 3 opcodes.
#include <stdafx.h>
#include "gpu.h"
#include <kernel/memory.h>
#include <kernel/function.h>
#include <cpu/guest_thread.h>
#include <os/logger.h>
#include <thread>

namespace
{
    // PM4 type-3 opcodes we act on (xenia/gpu/xenos.h)
    enum : uint32_t
    {
        PM4_ME_INIT = 0x48, PM4_NOP = 0x10, PM4_INDIRECT_BUFFER = 0x3F, PM4_INDIRECT_BUFFER_PFD = 0x37,
        PM4_WAIT_REG_MEM = 0x3C, PM4_REG_RMW = 0x21, PM4_COND_WRITE = 0x45, PM4_EVENT_WRITE = 0x46,
        PM4_EVENT_WRITE_SHD = 0x58, PM4_EVENT_WRITE_EXT = 0x59, PM4_EVENT_WRITE_ZPD = 0x5B, PM4_MEM_WRITE = 0x3D,
        PM4_INTERRUPT = 0x54, PM4_XE_SWAP = 0x64, PM4_DRAW_INDX = 0x22, PM4_DRAW_INDX_2 = 0x36,
        PM4_SET_CONSTANT = 0x2D, PM4_SET_CONSTANT2 = 0x55, PM4_LOAD_ALU_CONSTANT = 0x2F, PM4_IM_LOAD = 0x27,
        PM4_IM_LOAD_IMMEDIATE = 0x2B, PM4_INVALIDATE_STATE = 0x3B, PM4_SET_SHADER_CONSTANTS = 0x56,
    };

    constexpr uint32_t kRegCount = 0x5003;
    uint32_t g_regs[kRegCount];

    uint32_t g_ringBase = 0, g_ringDwords = 0, g_readPtr = 0, g_writeBack = 0;
    uint32_t g_interruptCallback = 0, g_interruptData = 0;
    std::atomic<uint64_t> g_frames{ 0 }, g_vblanks{ 0 };
    std::atomic<bool> g_started{ false };
    std::mutex g_interruptMutex;   // guest interrupt callback runs on one thread at a time

    // GPU physical address -> host (physical windows mirror 512 MB, kernel/memory.cpp)
    uint8_t* Phys(uint32_t a) { return (uint8_t*)g_memory.Translate(0xA0000000u | (a & 0x1FFFFFFF)); }
    uint32_t ReadPhys32(uint32_t a) { return ByteSwap(*(uint32_t*)Phys(a)); }

    uint32_t GpuSwap(uint32_t v, uint32_t endian)
    {
        switch (endian & 3)
        {
        case 1: return ((v & 0x00FF00FF) << 8) | ((v >> 8) & 0x00FF00FF);   // 8 in 16
        case 2: return ByteSwap(v);                                          // 8 in 32
        case 3: return (v << 16) | (v >> 16);                                // 16 in 32
        default: return v;
        }
    }
    // GOW3_GPU_TRACE=1: log packets, register writes and memory writes (first few thousand)
    const bool g_trace = getenv("GOW3_GPU_TRACE") != nullptr;
    std::atomic<int> g_traceLines = 0;
    template <typename... Args> void Trace(fmt::format_string<Args...> f, Args&&... args)
    {
        if (g_trace && g_traceLines++ < 2000000) LOGFN("gpu trace: {}", fmt::format(f, std::forward<Args>(args)...));
    }

    void WritePhys(uint32_t address, uint32_t value)   // address carries the endian mode in its low 2 bits
    {
        Trace("  mem[{:08X}] = {:08X}", address, value);
        *(uint32_t*)Phys(address & ~3u) = GpuSwap(value, address & 3);
    }

    // the guest's interrupt callback needs a guest thread context: one host thread owns it
    void CallInterrupt(uint32_t source, uint32_t cpu)
    {
        if (!g_interruptCallback)
            return;
        thread_local GuestThreadContext* ctx = nullptr;
        if (!ctx) ctx = new GuestThreadContext(2);
        std::lock_guard lock(g_interruptMutex);
        ctx->ppcContext.r3.u64 = source;
        ctx->ppcContext.r4.u64 = g_interruptData;
        g_memory.FindFunction(g_interruptCallback)(ctx->ppcContext, g_memory.base);
    }

    bool Compare(uint32_t func, uint32_t value, uint32_t ref)
    {
        switch (func & 7)
        {
        case 0: return false;            case 1: return value < ref;   case 2: return value <= ref;
        case 3: return value == ref;     case 4: return value != ref;  case 5: return value >= ref;
        case 6: return value > ref;      default: return true;
        }
    }

    struct Reader
    {
        uint32_t base, dwords, pos;   // pos in dwords; base is a guest virtual address of the buffer
        uint32_t Read() { uint32_t v = ByteSwap(*(uint32_t*)g_memory.Translate(base + (pos % dwords) * 4)); pos++; return v; }
    };

    void ExecuteBuffer(Reader& r, uint32_t endPos, int depth);

    // Register write with Xenos side effects. Scratch registers 0-7 (0x578-0x57F) are mirrored to memory at
    // SCRATCH_ADDR (0x1DD) + n*4 when their bit is set in SCRATCH_UMSK (0x1DC): the D3D's fences live there.
    constexpr uint32_t REG_SCRATCH_UMSK = 0x01DC, REG_SCRATCH_ADDR = 0x01DD, REG_SCRATCH_REG0 = 0x0578;
    void WriteReg(uint32_t reg, uint32_t value)
    {
        if (reg >= kRegCount) return;
        Trace("  reg[{:04X}] = {:08X}", reg, value);
        g_regs[reg] = value;
        if (reg >= REG_SCRATCH_REG0 && reg < REG_SCRATCH_REG0 + 8)
        {
            const uint32_t n = reg - REG_SCRATCH_REG0;
            if (g_regs[REG_SCRATCH_UMSK] & (1u << n))
                *(uint32_t*)Phys((g_regs[REG_SCRATCH_ADDR] & ~3u) + n * 4) = ByteSwap(value);   // big-endian, like Xenia
        }
    }

    void ExecuteType3(Reader& r, uint32_t opcode, uint32_t count, int depth)
    {
        const uint32_t start = r.pos;
        Trace("type3 op {:02X} count {} at {:08X}+{} depth {}", opcode, count, r.base, start, depth);
        switch (opcode)
        {
        case PM4_INDIRECT_BUFFER:
        case PM4_INDIRECT_BUFFER_PFD:
        {
            uint32_t addr = r.Read() & 0x1FFFFFFF, size = r.Read() & 0xFFFFF;
            if (depth < 4)
            {
                Reader ib{ 0xA0000000u | addr, size ? size : 1, 0 };
                ExecuteBuffer(ib, size, depth + 1);
            }
            break;
        }
        case PM4_WAIT_REG_MEM:
        {
            uint32_t info = r.Read(), poll = r.Read(), ref = r.Read(), mask = r.Read(); r.Read();
            for (int spin = 0; spin < 200000; spin++)   // ~ bounded: never deadlock the ring on a wait we can't satisfy
            {
                uint32_t v = (info & 0x10) ? GpuSwap(*(uint32_t*)Phys(poll & ~3u), poll & 3) : g_regs[poll & 0x7FFF];
                if (Compare(info, v & mask, ref)) break;
                std::this_thread::yield();
            }
            break;
        }
        case PM4_REG_RMW:
        {
            uint32_t info = r.Read(), andMask = r.Read(), orMask = r.Read();
            uint32_t reg = info & 0x1FFF;
            if (reg < kRegCount) WriteReg(reg, (g_regs[reg] & andMask) | orMask);
            break;
        }
        case PM4_COND_WRITE:
        {
            uint32_t info = r.Read(), poll = r.Read(), ref = r.Read(), mask = r.Read(), writeAddr = r.Read(), data = r.Read();
            uint32_t v = (info & 0x10) ? GpuSwap(*(uint32_t*)Phys(poll & ~3u), poll & 3) : g_regs[poll & 0x7FFF];
            if (Compare(info, v & mask, ref))
            {
                if (info & 0x100) WritePhys(writeAddr, data);
                else WriteReg(writeAddr & 0x7FFF, data);
            }
            break;
        }
        case PM4_EVENT_WRITE_SHD:
        {
            uint32_t initiator = r.Read(), address = r.Read(), value = r.Read();
            WritePhys(address, (initiator >> 31) ? uint32_t(g_vblanks.load()) : value);   // fence (or GPU counter)
            break;
        }
        case PM4_EVENT_WRITE_EXT:
        {
            uint32_t initiator = r.Read(), address = r.Read();
            uint16_t extents[6] = { 0, 0x7FFF, 0, 0x7FFF, 0, 0x7FFF };   // screen extents: "everything"
            for (int i = 0; i < 6; i++) ((be<uint16_t>*)Phys(address & ~3u))[i] = extents[i];
            break;
        }
        case PM4_MEM_WRITE:
        {
            uint32_t address = r.Read();
            for (uint32_t i = 1; i < count; i++) { WritePhys(address, r.Read()); address += 4; }
            break;
        }
        case PM4_INTERRUPT:
        {
            uint32_t cpuMask = r.Read();
            for (uint32_t cpu = 0; cpu < 6; cpu++) if (cpuMask & (1u << cpu)) CallInterrupt(1, cpu);
            break;
        }
        case PM4_XE_SWAP:
            g_frames++;
            if (g_frames % 300 == 1)
                LOGFN("gpu: frame {} (swap; rendering not implemented yet)", g_frames.load());
            break;
        default:
            break;   // draws, constants, shader loads, state: consumed but not executed yet
        }
        r.pos = start + count;   // always resynchronize on the packet length
    }

    void ExecuteBuffer(Reader& r, uint32_t endPos, int depth)
    {
        while (r.pos < endPos)
        {
            uint32_t p = r.Read();
            switch (p >> 30)
            {
            case 0:   // type 0: count registers from base (or one register, repeatedly)
            {
                uint32_t count = ((p >> 16) & 0x3FFF) + 1, reg = p & 0x7FFF;
                bool one = (p >> 15) & 1;
                for (uint32_t i = 0; i < count; i++)
                {
                    uint32_t v = r.Read();
                    WriteReg(one ? reg : reg + i, v);
                }
                break;
            }
            case 1:   // type 1: two registers
            {
                uint32_t a = p & 0x7FF, b = (p >> 11) & 0x7FF;
                uint32_t va = r.Read(), vb = r.Read();
                WriteReg(a, va); WriteReg(b, vb);
                break;
            }
            case 2:   // type 2: NOP
                break;
            case 3:
            {
                uint32_t opcode = (p >> 8) & 0x7F, count = ((p >> 16) & 0x3FFF) + 1;
                ExecuteType3(r, opcode, count, depth);
                break;
            }
            }
        }
    }

    // CP_RB_WPTR: the D3D's write pointer register (MMIO). Register index from Xenia's register table.
    constexpr uint32_t REG_CP_RB_WPTR = 0x01C5;

    void CommandProcessorThread()
    {
        GuestThreadContext ctx(1);   // packets may call guest code (interrupts)
        for (;;)
        {
            if (!g_ringBase) { Sleep(1); continue; }
            uint32_t wptr = ByteSwap(*(uint32_t*)g_memory.Translate(gpu::kMmioBase + REG_CP_RB_WPTR * 4)) % g_ringDwords;
            if (wptr == g_readPtr) { std::this_thread::yield(); continue; }
            uint32_t end = wptr >= g_readPtr ? wptr : wptr + g_ringDwords;
            Reader r{ g_ringBase, g_ringDwords, g_readPtr };
            ExecuteBuffer(r, end, 0);
            g_readPtr = r.pos % g_ringDwords;
            if (g_writeBack)
                *(uint32_t*)Phys(g_writeBack) = ByteSwap(g_readPtr);   // the D3D polls the read pointer here
        }
    }

    // Debug (GOW3_GPU_DEBUG=1): every 2 s, the ring state and every MMIO register the D3D has written
    void DebugThread()
    {
        for (;;)
        {
            Sleep(2000);
            std::string regs;
            for (uint32_t i = 0; i < gpu::kMmioSize / 4; i++)
            {
                uint32_t v = ByteSwap(*(uint32_t*)g_memory.Translate(gpu::kMmioBase + i * 4));
                if (v) regs += fmt::format(" [{:04X}]={:08X}", i, v);
                if (regs.size() > 1500) break;
            }
            LOGFN("gpu debug: rptr {} ring {:08X}/{} frames {} vblanks {}; mmio:{}", g_readPtr, g_ringBase, g_ringDwords,
                g_frames.load(), g_vblanks.load(), regs);
        }
    }

    void VblankThread()
    {
        auto next = std::chrono::steady_clock::now();
        for (;;)
        {
            next += std::chrono::microseconds(16667);
            std::this_thread::sleep_until(next);
            g_vblanks++;
            CallInterrupt(0, 2);   // source 0 = vblank
        }
    }
}

void gpu::null::InitializeRingBuffer(uint32_t ptr, uint32_t sizeLog2)
{
    g_ringBase = 0xA0000000u | (ptr & 0x1FFFFFFF);
    g_ringDwords = (1u << (sizeLog2 + 3)) / 4;   // size = 2^(log2+3) bytes (Xenia CommandProcessor::InitializeRingBuffer)
    if (!g_ringDwords) g_ringDwords = 1;
    g_readPtr = 0;
    LOGFN("gpu: ring buffer at {:08X}, {} dwords", g_ringBase, g_ringDwords);
    gpu::null::Start();
}

void gpu::null::EnableReadPointerWriteBack(uint32_t ptr, uint32_t blockLog2)
{
    g_writeBack = ptr & 0x1FFFFFFF;   // physical (MmGetPhysicalAddress is exact now)
    LOGFN("gpu: read pointer write-back at {:08X}", g_writeBack);
}

void gpu::null::SetInterruptCallback(uint32_t callback, uint32_t userData)
{
    g_interruptCallback = callback; g_interruptData = userData;
    LOGFN("gpu: interrupt callback {:08X} ({:08X})", callback, userData);
}

void gpu::null::SetSystemCommandBufferIdentifier(uint32_t address) {}

void gpu::null::Start()
{
    if (g_started.exchange(true))
        return;
    std::thread(CommandProcessorThread).detach();
    std::thread(VblankThread).detach();
    if (getenv("GOW3_GPU_DEBUG")) std::thread(DebugThread).detach();
}

uint64_t gpu::null::FrameCount() { return g_frames; }
