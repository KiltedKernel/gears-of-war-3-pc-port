// Port: the Xbox 360 timebase (mftb) at 49.875 MHz, the rate KeQueryPerformanceFrequency reports, from the host's
// QueryPerformanceCounter. Recompiled mftb calls this (XenonRecomp emits PPCQueryTimebase()).
#include <stdafx.h>

namespace
{
    constexpr uint64_t kTimebaseHz = 49875000;
    const uint64_t g_qpcHz = [] { LARGE_INTEGER f; QueryPerformanceFrequency(&f); return uint64_t(f.QuadPart); }();
}

extern "C" uint64_t PPCQueryTimebase()
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    const uint64_t t = uint64_t(now.QuadPart);
    // t * kTimebaseHz / g_qpcHz without overflow
    return (t / g_qpcHz) * kTimebaseHz + (t % g_qpcHz) * kTimebaseHz / g_qpcHz;
}
