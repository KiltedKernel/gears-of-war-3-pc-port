// GOW3_KISMET_TRACE (development): which Kismet ops run. Every 100 ms the live sequence ops are sampled through UE3
// reflection, and each change of an op's SequenceOp.ActivateCount (actions, conditions, sequences) or an event's
// SequenceEvent.TriggerCount is logged with its package and sequence path:
//   kismet: [GOW1_SP_Prison_P] Main_Sequence.initialPlayerStuff.SeqAct_Teleport_4 (SeqAct_Teleport) x1
// The value filters by package (a substring of the package name: GOW1_ for the trilogy's converted levels); 1 traces
// every package. Sampling sees the count, not each activation: two activations within 100 ms log once, as x2.
#include <stdafx.h>
#include <ue3/reflection.h>
#include <os/logger.h>
#include <chrono>
#include <thread>
#include <unordered_map>

namespace kismet_trace
{
namespace
{
    std::string PackageOf(uint32_t o)
    {
        uint32_t top = o;
        for (int k = 0; k < 24 && ue3::Read32(top + 40); k++) top = ue3::Read32(top + 40);
        return ue3::ObjectName(top);
    }

    // the op's path below its level: Main_Sequence.<subsequence>... .<op>
    std::string PathOf(uint32_t o)
    {
        std::string path = ue3::ObjectName(o);
        uint32_t outer = ue3::Read32(o + 40);
        for (int k = 0; k < 16 && outer; k++)
        {
            const std::string n = ue3::ObjectName(outer);
            if (n == "PersistentLevel") break;
            path = n + "." + path;
            outer = ue3::Read32(outer + 40);
        }
        return path;
    }
}

void Start()
{
    const char* spec = getenv("GOW3_KISMET_TRACE");
    if (!spec || !*spec) return;
    const std::string filter = std::string(spec) == "1" ? "" : spec;
    std::thread([filter] {
        while (!ue3::Init()) std::this_thread::sleep_for(std::chrono::seconds(1));
        // (the tables are found early in the boot, before Engine's classes load: wait for them)
        uint32_t opClass = 0, eventClass = 0;
        for (int tries = 0; tries < 600 && !opClass; tries++)
        {
            opClass = ue3::FindClass("SequenceOp");
            if (!opClass) std::this_thread::sleep_for(std::chrono::seconds(1));
        }
        eventClass = ue3::FindClass("SequenceEvent");
        if (!opClass) { LOGN_WARNING("kismet: SequenceOp not found"); return; }
        std::unordered_map<uint32_t, int> opOff, evOff;     // per class: ActivateCount / TriggerCount offsets
        std::unordered_map<uint32_t, uint32_t> last;        // op -> last count
        // op -> (its outermost object, in the filtered packages): an address freed with its level and reused by
        // another level's op is judged again (cached by address alone, S04's ops were never traced after the menu's)
        std::unordered_map<uint32_t, std::pair<uint32_t, bool>> wanted;
        int logged = 0;
        LOGFN("kismet: tracing sequence ops{}", filter.empty() ? "" : " in packages with \"" + filter + "\"");
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            for (uint32_t o : ue3::FindInstancesOf(opClass))
            {
                uint32_t top = o;
                for (int k = 0; k < 24 && ue3::Read32(top + 40); k++) top = ue3::Read32(top + 40);
                auto w = wanted.find(o);
                if (w == wanted.end() || w->second.first != top)
                {
                    wanted[o] = { top, filter.empty() || PackageOf(o).find(filter) != std::string::npos };
                    last.erase(o);
                    w = wanted.find(o);
                }
                if (!w->second.second) continue;
                const uint32_t cls = ue3::ClassOf(o);
                const bool isEvent = eventClass && ue3::IsA(o, eventClass);
                auto& offs = isEvent ? evOff : opOff;
                auto it = offs.find(cls);
                if (it == offs.end()) it = offs.emplace(cls, ue3::PropertyOffset(cls, isEvent ? "TriggerCount" : "ActivateCount")).first;
                if (it->second < 0) continue;
                const uint32_t count = ue3::Read32(o + uint32_t(it->second));
                auto [l, fresh] = last.emplace(o, count);
                if (fresh) { if (count == 0) continue; }      // first seen: report what already ran
                else if (l->second == count) continue;
                // (a count that went down: the op's level was reloaded, its counts back at 0)
                const uint32_t before = fresh || count < l->second ? 0 : l->second;
                l->second = count;
                if (count == before) continue;
                if (logged++ < 20000)
                    LOGFN("kismet: [{}] {} ({}) x{}", PackageOf(o), PathOf(o), ue3::ObjectName(cls), count - before);
            }
        }
    }).detach();
}
}
