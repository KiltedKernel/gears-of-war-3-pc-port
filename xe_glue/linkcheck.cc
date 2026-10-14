// Link check for the glue (not shipped): references every glue entry point.
#include "port_gpu.h"
int main()
{
    portgpu::Host host{};
    if (!portgpu::Start(host)) return 1;
    portgpu::InitializeRingBuffer(0, 0);
    portgpu::EnableReadPointerWriteBack(0, 0);
    portgpu::SetInterruptCallback(0, 0);
    portgpu::WriteRegister(0, 0);
    return int(portgpu::ReadRegister(0) + portgpu::HandleAccessViolation(nullptr, true) + portgpu::IsRunning());
}
