// Logger for the GoW2 runtime: stdout + gow3.log next to the executable. Repeated identical lines are collapsed.
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

void os::logger::Init()
{
    g_file = fopen("gow3.log", "w");
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
