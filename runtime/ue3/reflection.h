#pragma once
// UE3 reflection over the running game's memory (GNames / GObjObjects / UClass property chains), for features that
// read engine objects without hardcoding layouts (menus: runtime/ui/gfx_menu.cpp, campaign chapters). Read-only; every read is
// guarded, so a stale pointer from another thread returns 0 instead of crashing.
#include <cstdint>
#include <string>
#include <vector>

namespace ue3
{
    bool Ready();                                        // tables found (Init ran successfully)
    bool Init();                                         // finds the tables; call from a worker thread, retries are cheap

    uint32_t Read32(uint32_t address);                   // 0 when unreadable
    float ReadFloat(uint32_t address);
    bool Readable(uint32_t address, uint32_t size);

    std::string Name(uint32_t index);
    std::string ObjectName(uint32_t object);             // "Name" or "Name_<n>"
    uint32_t ClassOf(uint32_t object);
    uint32_t FindClass(const char* name);                // the UClass object
    bool IsA(uint32_t object, uint32_t cls);             // object's class is cls or derives from it
    uint32_t FindInstance(const char* className);        // first non-default instance of exactly that class
    std::vector<uint32_t> FindInstancesOf(uint32_t cls); // every non-default instance of cls or a subclass
    std::vector<uint32_t> AllObjects();                  // every object in GObjObjects
    uint32_t FindObject(const char* name);               // the first object of that name (Default__X included)

    // property offset (-1 if missing) and, for bools, the bit mask
    int PropertyOffset(uint32_t cls, const char* property);
    uint32_t BoolMask(uint32_t cls, const char* property);
}
