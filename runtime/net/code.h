#pragma once
// Join codes: a host's public IPv4 address and UDP port as 10 Crockford base32 characters, shown as XXXXX-XXXXX
// (no I, L, O or U; case, spaces and dashes are ignored when typed). The launcher shows the host's code; players type
// it in the launcher's Join box (gow3.ini [network] join). Header-only: the launcher includes it too.
#include <cstdint>
#include <string>

namespace code
{
    inline const char* Alphabet() { return "0123456789ABCDEFGHJKMNPQRSTVWXYZ"; }

    inline std::string Encode(uint32_t ip, uint16_t port)
    {
        const uint64_t v = (uint64_t(ip) << 16) | port;   // 48 bits -> 10 x 5 bits (top 2 bits zero)
        std::string s;
        for (int i = 9; i >= 0; i--) s += Alphabet()[(v >> (i * 5)) & 31];
        return s.substr(0, 5) + "-" + s.substr(5);
    }

    inline bool Decode(const std::string& text, uint32_t& ip, uint16_t& port)
    {
        uint64_t v = 0;
        int n = 0;
        for (char c : text)
        {
            if (c == '-' || c == ' ') continue;
            if (c >= 'a' && c <= 'z') c = char(c - 'a' + 'A');
            if (c == 'O') c = '0';
            if (c == 'I' || c == 'L') c = '1';
            const char* p = nullptr;
            for (const char* a = Alphabet(); *a; a++) if (*a == c) { p = a; break; }
            if (!p) return false;
            v = (v << 5) | uint64_t(p - Alphabet());
            n++;
        }
        if (n != 10 || (v >> 48) != 0) return false;
        ip = uint32_t(v >> 16); port = uint16_t(v & 0xFFFF);
        return ip != 0 && port != 0;
    }
}
