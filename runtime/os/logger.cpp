// Logger for the port's runtime: stdout + gow3.log next to the executable (gow3-2.log... for more copies at once). Repeated identical lines are collapsed.
#include <stdafx.h>
#include "logger.h"
#include <cstdio>
#include <unordered_map>

namespace
{
    std::mutex g_mutex;
    FILE* g_file = nullptr;
    std::unordered_map<std::string, int> g_counts;
}

int GetInstanceNumber();   // main.cpp

void os::logger::Init()
{
    // a second copy of the game on this PC logs to gow3-2.log...
    const int n = GetInstanceNumber();
    g_file = fopen(n == 1 ? "gow3.log" : fmt::format("gow3-{}.log", n).c_str(), "w");
}

void os::logger::Log(const std::string_view str, ELogType type, const char* func)
{
    std::lock_guard lock(g_mutex);
    static const char* tags[] = { "", "[stub] ", "[warn] ", "[ERROR] " };
    std::string line = fmt::format("{}{}{}{}", tags[int(type)], func ? func : "", func ? ": " : "", str);
    if (++g_counts[line] > 3)
        return; // same message more than 3 times: keep the log readable
    fputs(line.c_str(), stdout); fputc('\n', stdout);
    if (g_file) { fputs(line.c_str(), g_file); fputc('\n', g_file); fflush(g_file); }
}
