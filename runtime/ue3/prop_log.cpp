// GOW3_LOG_PROPS (development): every 2 s, log chosen properties and the current UnrealScript state of every live
// instance of chosen classes, through UE3 reflection. Format: "Class:Prop,Prop;Class2:Prop" (subclasses included);
// each value is shown as int/float (bools as 0/1); "Prop#" shows an array's count, "Prop+N" the dword N bytes in. The state: a State object referenced from the object's
// StateFrame (UObject +20).
// GOW3_WATCH_PROP=Class:Prop arms a write watchpoint (watch.cpp) on that property of the first live instance: each
// hit names the guest function writing it.
#include <stdafx.h>
#include <ue3/reflection.h>
#include <os/logger.h>
#include <chrono>
#include <sstream>
#include <thread>
#include <unordered_map>
#include <algorithm>

namespace watch { bool Add(uint32_t guest, uint32_t len, bool reads); }   // watch.cpp

namespace prop_log
{
namespace
{
    std::string StateOf(uint32_t object)
    {
        const uint32_t frame = ue3::Read32(object + 20);
        if (!frame) return "-";
        static const uint32_t stateClass = ue3::FindClass("State");
        std::string out;
        for (uint32_t k = 0; k < 24; k++)
        {
            const uint32_t v = ue3::Read32(frame + 4 * k);
            if (v > 0x40000000 && v < 0x60000000 && stateClass && ue3::ClassOf(v) == stateClass)
                out += (out.empty() ? "" : "/") + ue3::ObjectName(v) + "@" + std::to_string(4 * k);
        }
        return out.empty() ? "?" : out;
    }
}

void Start()
{
    const char* spec = getenv("GOW3_LOG_PROPS");
    const char* watchSpec = getenv("GOW3_WATCH_PROP");
    if (!spec && !watchSpec) return;
    std::string s = spec ? spec : "", ws = watchSpec ? watchSpec : "";
    std::thread([s, ws] {
        while (!ue3::Init()) std::this_thread::sleep_for(std::chrono::seconds(2));
        struct Watch { std::string cls; std::vector<std::string> props; };
        std::vector<Watch> watches;
        std::stringstream ss(s); std::string part;
        while (std::getline(ss, part, ';'))
        {
            Watch w; const size_t c = part.find(':'); w.cls = part.substr(0, c);
            if (c != std::string::npos) { std::stringstream ps(part.substr(c + 1)); std::string p; while (std::getline(ps, p, ',')) w.props.push_back(p); }
            watches.push_back(w);
        }
        bool armed = ws.empty();
        // GOW3_LOG_PROPS_MS=<ms>: sample that often and log an object's line only when it changes
        const char* msEnv = getenv("GOW3_LOG_PROPS_MS");
        const int intervalMs = msEnv ? std::max(20, atoi(msEnv)) : 2000;
        std::unordered_map<uint32_t, std::string> last;
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(intervalMs));
            if (!armed)
            {
                // Class:Prop[@ObjectName][,rw]: that instance (else the first), reads too with ,rw
                std::string spec = ws;
                const bool reads = spec.size() > 3 && spec.substr(spec.size() - 3) == ",rw";
                if (reads) spec.resize(spec.size() - 3);
                std::string objName;
                if (const size_t at = spec.find('@'); at != std::string::npos) { objName = spec.substr(at + 1); spec.resize(at); }
                const size_t c = spec.find(':');
                const uint32_t cls = ue3::FindClass(spec.substr(0, c).c_str());
                for (uint32_t o : cls ? ue3::FindInstancesOf(cls) : std::vector<uint32_t>{})
                {
                    if (!objName.empty() && ue3::ObjectName(o) != objName) continue;
                    const int off = ue3::PropertyOffset(ue3::ClassOf(o), spec.substr(c + 1).c_str());
                    if (off < 0) continue;
                    armed = watch::Add(o + off, 4, reads);
                    LOGFN("props: watching {} of {} at {:08X}: {}", ws, ue3::ObjectName(o), o + off, armed ? "armed" : "failed");
                    break;
                }
            }
            for (auto& w : watches)
            {
                const uint32_t cls = ue3::FindClass(w.cls.c_str());
                if (!cls) continue;
                for (uint32_t o : ue3::FindInstancesOf(cls))
                {
                    const uint32_t oc = ue3::ClassOf(o);
                    std::string line = fmt::format("props: {} ({}) in {} state {}", ue3::ObjectName(o), ue3::ObjectName(oc),
                                                   ue3::ObjectName(ue3::Read32(o + 40)), StateOf(o));
                    for (auto& p0 : w.props)
                    {
                        // "Name#": a dynamic array's element count (the int after its data pointer)
                        // "Name+N": the dword N bytes into the property (a struct's later field: Rotation+4 = yaw)
                        const bool count = !p0.empty() && p0.back() == '#';
                        std::string p = count ? p0.substr(0, p0.size() - 1) : p0;
                        int extra = 0;
                        if (const size_t plus = p.find('+'); plus != std::string::npos) { extra = atoi(p.c_str() + plus + 1); p = p.substr(0, plus); }
                        int off = ue3::PropertyOffset(oc, p.c_str());
                        if (extra && off >= 0) { off += extra; p = p0; }
                        if (count && off >= 0) { line += fmt::format(" {}={}", p0, int32_t(ue3::Read32(o + off + 4))); continue; }
                        if (off < 0) { line += " " + p + "=?"; continue; }
                        const uint32_t raw = ue3::Read32(o + off);
                        if (const uint32_t mask = ue3::BoolMask(oc, p.c_str())) line += fmt::format(" {}={}", p, (raw & mask) ? 1 : 0);
                        else line += fmt::format(" {}={}/{:.3f}", p, int32_t(raw), ue3::ReadFloat(o + off));
                    }
                    if (msEnv)
                    {
                        const std::string body = line.substr(line.find(')') + 1);
                        if (last[o] == body) continue;
                        last[o] = body;
                    }
                    LOGFN("{}", line);
                }
            }
        }
    }).detach();
}
}
