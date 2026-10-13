// Link check for the glue (not shipped): references every glue entry point.
#include "gow2_gpu.h"
int main()
{
    gow2gpu::Host host{};
    if (!gow2gpu::Start(host)) return 1;
    gow2gpu::InitializeRingBuffer(0, 0);
    gow2gpu::EnableReadPointerWriteBack(0, 0);
    gow2gpu::SetInterruptCallback(0, 0);
    gow2gpu::WriteRegister(0, 0);
    return int(gow2gpu::ReadRegister(0) + gow2gpu::HandleAccessViolation(nullptr, true) + gow2gpu::IsRunning());
}
