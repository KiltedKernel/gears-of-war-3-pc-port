// Button prompts that follow the input device (Gears of War 3): while the player uses keyboard/mouse, the controller-
// button glyphs of the button font are drawn as key and mouse icons; a controller brings the originals back.
//
// The font has two 256x256 DXT5 pages (tiled, identified by content) and a 256-entry FFontCharacter table {StartU,
// StartV, USize, VSize, TextureIndex (byte), VerticalOffset}, big-endian. Page 0 holds the buttons and is full; page 1
// is empty from row 151 down. Icons: build\prompts\kbm_glyphs.* (tools/make_prompts.py), full-size ones under the
// character code and compact ones (the button's own size) under code + 256.
//  - Menus draw with Scaleform (GFx), which copied each glyph's rectangle when the font loaded and caches the layout
//    of text it has shown, so moving glyphs cannot reach them. Instead keyboard mode draws the compact icons over the
//    buttons in page 0 itself, at the same rectangles: every menu, laid out before or after, shows the new picture.
//  - In-game text (UE3 canvas) reads the FFontCharacter table on every draw: keyboard mode points the button
//    characters at the full-size icons, written into page 1's free rows (kept there in both modes).
#include <stdafx.h>
#include <kernel/memory.h>
#include <os/logger.h>
#include <hid/hid.h>
#include <user/settings.h>
#include "../../xe_glue/gow2_gpu.h"
#include <algorithm>
#include <atomic>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <thread>
#include <vector>

namespace prompts
{
namespace
{
    constexpr uint32_t kSize = 256, kBytes = 65536;            // a page: 256x256 DXT5 = 64x64 blocks of 16 bytes
    // FNV-1a 64 of the stock pages as the game loads them (Gears 3; page 0's 'A', 'B', 'Y' records are Gears 2's)
    constexpr uint64_t kPage0Hash = 0x350c61d5241b80a8ull;
    constexpr uint64_t kPage1Hash = 0xf324ae1953e67914ull;
    constexpr int kIconTop = 152;                              // first free row of page 1 (block row 38)
    constexpr int kGlyphs = 256;

    struct Glyph { int32_t u, v, w, h, texture, voffset; };
    struct Rgba { uint8_t r, g, b, a; };
    using Image = std::vector<Rgba>;                           // kSize x kSize

    std::mutex g_mutex;
    std::atomic<uint32_t> g_page0{ 0 };                        // page 0 seen: the font is in use
    std::vector<uint32_t> g_pages0;                            // every copy of page 0 (physical)
    std::vector<uint8_t> g_page0Tex, g_page0KbmTex;            // page 0 as loaded / with the compact icons
    std::vector<uint32_t> g_pages1;                            // every copy of page 1 (physical)
    std::vector<uint8_t> g_page1Tex, g_page1KbmTex;            // page 1 as loaded / with the icons (guest bytes)
    std::vector<uint8_t> g_iconBlocks;                         // page-sized encoding of the icons (rows kIconTop+)

    // page 1 as loaded + the icon block rows
    uint32_t Tiled(uint32_t x, uint32_t y, uint32_t pitchBlocks, uint32_t bppLog2);
    std::vector<uint8_t> WithIcons(std::vector<uint8_t> page)
    {
        for (uint32_t by = kIconTop / 4; by < kSize / 4; by++)
            for (uint32_t bx = 0; bx < kSize / 4; bx++)
            {
                const uint32_t o = Tiled(bx, by, kSize / 4, 4);
                memcpy(page.data() + o, g_iconBlocks.data() + o, 16);
            }
        return page;
    }
    std::array<Glyph, kGlyphs> g_originalGlyphs{}, g_kbmGlyphs{};
    std::array<bool, kGlyphs> g_replaced{};
    uint32_t g_table = 0;                                      // guest address of the FFontCharacter array (first)
    std::vector<uint32_t> g_tables;                            // every font using the page (same 'A'/'B'/'Y' records)
    bool g_built = false, g_failed = false;
    std::atomic<int> g_applied{ 0 };                           // 0 = original, 1 = keyboard/mouse

    uint64_t Fnv(const uint8_t* p, size_t n)
    {
        uint64_t h = 0xcbf29ce484222325ull;
        for (size_t i = 0; i < n; i++) h = (h ^ p[i]) * 0x100000001b3ull;
        return h;
    }

    // ---- Xenos tiling (Xenia texture_address::Tiled2D) and DXT5 ----
    uint32_t Tiled(uint32_t x, uint32_t y, uint32_t pitchBlocks, uint32_t bppLog2)
    {
        const uint32_t outer = ((y >> 5) * (pitchBlocks >> 5) + (x >> 5)) << 6;
        const uint32_t inner = (((y >> 1) & 7) << 3) | (x & 7);
        const uint32_t oib = (outer | inner) << bppLog2;
        const uint32_t bank = (y >> 4) & 1, pipe = ((x >> 3) & 3) ^ (((y >> 3) & 1) << 1);
        return ((y & 1) << 4) | (pipe << 6) | (bank << 11) | (oib & 15) | (((oib >> 4) & 1) << 5) |
               (((oib >> 5) & 7) << 8) | ((oib >> 8) << 12);
    }

    void Rgb565(uint16_t c, int out[3])
    {
        out[0] = ((c >> 11) & 31) * 255 / 31; out[1] = ((c >> 5) & 63) * 255 / 63; out[2] = (c & 31) * 255 / 31;
    }

    // guest bytes -> RGBA (blocks are tiled; each 16-bit word is byte-swapped, k_8in16)
    Image Decode(const std::vector<uint8_t>& guest)
    {
        Image img(kSize * kSize);
        for (uint32_t by = 0; by < kSize / 4; by++)
            for (uint32_t bx = 0; bx < kSize / 4; bx++)
            {
                uint8_t b[16];
                const uint8_t* src = guest.data() + Tiled(bx, by, kSize / 4, 4);
                for (int i = 0; i < 16; i += 2) { b[i] = src[i + 1]; b[i + 1] = src[i]; }
                int a[8] = { b[0], b[1] };
                if (a[0] > a[1]) for (int i = 1; i < 7; i++) a[i + 1] = ((7 - i) * a[0] + i * a[1]) / 7;
                else { for (int i = 1; i < 5; i++) a[i + 1] = ((5 - i) * a[0] + i * a[1]) / 5; a[6] = 0; a[7] = 255; }
                uint64_t abits = 0;
                for (int k = 0; k < 6; k++) abits |= uint64_t(b[2 + k]) << (8 * k);
                const uint16_t c0 = b[8] | (b[9] << 8), c1 = b[10] | (b[11] << 8);
                const uint32_t cbits = b[12] | (b[13] << 8) | (b[14] << 16) | (uint32_t(b[15]) << 24);
                int p[4][3];
                Rgb565(c0, p[0]); Rgb565(c1, p[1]);
                for (int k = 0; k < 3; k++) { p[2][k] = (2 * p[0][k] + p[1][k]) / 3; p[3][k] = (p[0][k] + 2 * p[1][k]) / 3; }
                for (int i = 0; i < 16; i++)
                {
                    const int* c = p[(cbits >> (2 * i)) & 3];
                    img[(by * 4 + i / 4) * kSize + bx * 4 + i % 4] =
                        { uint8_t(c[0]), uint8_t(c[1]), uint8_t(c[2]), uint8_t(a[(abits >> (3 * i)) & 7]) };
                }
            }
        return img;
    }

    uint16_t To565(const int c[3])
    {
        return uint16_t(((c[0] * 31 + 127) / 255) << 11 | ((c[1] * 63 + 127) / 255) << 5 | ((c[2] * 31 + 127) / 255));
    }

    // RGBA -> guest bytes (range-fit DXT5, then tiled and byte-swapped)
    std::vector<uint8_t> Encode(const Image& img)
    {
        std::vector<uint8_t> guest(kBytes);
        for (uint32_t by = 0; by < kSize / 4; by++)
            for (uint32_t bx = 0; bx < kSize / 4; bx++)
            {
                Rgba px[16];
                for (int i = 0; i < 16; i++) px[i] = img[(by * 4 + i / 4) * kSize + bx * 4 + i % 4];
                uint8_t b[16] = {};
                int amax = 0, amin = 255;
                for (auto& q : px) { amax = std::max<int>(amax, q.a); amin = std::min<int>(amin, q.a); }
                b[0] = uint8_t(amax); b[1] = uint8_t(amin);
                static const int remap[8] = { 0, 2, 3, 4, 5, 6, 7, 1 };   // ramp step -> DXT5 index
                uint64_t abits = 0;
                if (amax != amin)
                    for (int i = 0; i < 16; i++)
                    {
                        const int t = std::clamp(((amax - px[i].a) * 7 + (amax - amin) / 2) / (amax - amin), 0, 7);
                        abits |= uint64_t(remap[t]) << (3 * i);
                    }
                for (int k = 0; k < 6; k++) b[2 + k] = uint8_t(abits >> (8 * k));
                // colour endpoints: brightest and darkest visible texels
                int hi[3] = { 0, 0, 0 }, lo[3] = { 0, 0, 0 }, hiL = -1, loL = 1 << 30;
                for (auto& q : px)
                {
                    if (q.a <= 8) continue;
                    const int l = q.r * 3 + q.g * 6 + q.b;
                    if (l > hiL) { hiL = l; hi[0] = q.r; hi[1] = q.g; hi[2] = q.b; }
                    if (l < loL) { loL = l; lo[0] = q.r; lo[1] = q.g; lo[2] = q.b; }
                }
                uint16_t c0 = To565(hi), c1 = To565(lo);
                if (c0 < c1) std::swap(c0, c1);
                if (c0 == c1 && c0) c1 = uint16_t(c0 - 1); else if (c0 == c1) c0 = 1;
                int p[4][3];
                Rgb565(c0, p[0]); Rgb565(c1, p[1]);
                for (int k = 0; k < 3; k++) { p[2][k] = (2 * p[0][k] + p[1][k]) / 3; p[3][k] = (p[0][k] + 2 * p[1][k]) / 3; }
                uint32_t cbits = 0;
                for (int i = 0; i < 16; i++)
                {
                    int best = 0, bestD = 1 << 30;
                    for (int k = 0; k < 4; k++)
                    {
                        const int d = (px[i].r - p[k][0]) * (px[i].r - p[k][0]) + (px[i].g - p[k][1]) * (px[i].g - p[k][1]) +
                                      (px[i].b - p[k][2]) * (px[i].b - p[k][2]);
                        if (d < bestD) { bestD = d; best = k; }
                    }
                    cbits |= uint32_t(best) << (2 * i);
                }
                b[8] = uint8_t(c0); b[9] = uint8_t(c0 >> 8); b[10] = uint8_t(c1); b[11] = uint8_t(c1 >> 8);
                for (int k = 0; k < 4; k++) b[12 + k] = uint8_t(cbits >> (8 * k));
                uint8_t* dst = guest.data() + Tiled(bx, by, kSize / 4, 4);
                for (int i = 0; i < 16; i += 2) { dst[i] = b[i + 1]; dst[i + 1] = b[i]; }
            }
        return guest;
    }

    // ---- keyboard/mouse glyphs (tools/make_prompts.py) ----
    struct Strip { uint32_t w = 0, h = 0; std::vector<Rgba> px; std::vector<std::array<int, 5>> glyphs; };

    bool LoadStrip(Strip& s)
    {
        wchar_t exe[MAX_PATH];
        GetModuleFileNameW(nullptr, exe, MAX_PATH);
        const auto dir = std::filesystem::path(exe).parent_path() / "prompts";
        std::ifstream idx(dir / "kbm_glyphs.txt");
        std::ifstream raw(dir / "kbm_glyphs.rgba", std::ios::binary);
        if (!idx || !raw || !(idx >> s.w >> s.h)) return false;
        std::array<int, 5> g;
        while (idx >> g[0] >> g[1] >> g[2] >> g[3] >> g[4]) s.glyphs.push_back(g);
        s.px.resize(size_t(s.w) * s.h);
        raw.read((char*)s.px.data(), std::streamsize(s.px.size() * 4));
        return bool(raw) && !s.glyphs.empty();
    }

    // the icons into page 1's free rows (shelves of 26 + 1), deduplicated by content; only those block rows are
    // re-encoded, the rest of page 1 keeps its original bytes
    bool Build(const Strip& strip)
    {
        Image page(kSize * kSize, Rgba{ 0, 0, 0, 0 });   // page 1's rows from kIconTop down are empty on the stock page
        g_kbmGlyphs = g_originalGlyphs;
        g_replaced.fill(false);
        struct Placed { const Rgba* src; int w, h, x, y; };
        std::vector<Placed> placed;
        int x = 0, y = kIconTop;
        for (const auto& g : strip.glyphs)
        {
            const int c = g[0], w = g[3], h = g[4];
            if (c < 0 || c >= kGlyphs || g_originalGlyphs[c].texture != 0 || g_originalGlyphs[c].w <= 0) continue;
            const Rgba* src = strip.px.data() + size_t(g[2]) * strip.w + g[1];
            auto same = std::find_if(placed.begin(), placed.end(), [&](const Placed& p) {
                if (p.w != w || p.h != h) return false;
                for (int r = 0; r < h; r++)
                    if (memcmp(p.src + size_t(r) * strip.w, src + size_t(r) * strip.w, size_t(w) * 4)) return false;
                return true; });
            if (same == placed.end())
            {
                if (x + w > int(kSize)) { x = 0; y += 27; }
                if (y + h > int(kSize)) { LOGFN_WARNING("prompts: keyboard icons do not fit page 1"); return false; }
                placed.push_back({ src, w, h, x, y });
                for (int r = 0; r < h; r++) std::copy_n(src + size_t(r) * strip.w, w, page.data() + size_t(y + r) * kSize + x);
                same = placed.end() - 1;
                x += w + 1;
            }
            g_kbmGlyphs[c] = { same->x, same->y, w, h, 1, g_originalGlyphs[c].voffset };
            g_replaced[c] = true;
        }
        g_iconBlocks = Encode(page);

        // page 0: the compact icons over the buttons, each resized to its glyph's rectangle; only the 4x4 blocks they
        // touch are re-encoded
        Image page0 = Decode(g_page0Tex);
        std::vector<bool> touched(size_t(kSize / 4) * (kSize / 4), false);
        int compact = 0;
        for (const auto& g : strip.glyphs)
        {
            const int c = g[0] - kGlyphs;
            if (c < 0 || c >= kGlyphs || !g_replaced[c]) continue;
            const Glyph& o = g_originalGlyphs[c];
            const Rgba* src = strip.px.data() + size_t(g[2]) * strip.w + g[1];
            const int sw = g[3], sh = g[4];
            for (int y = 0; y < o.h; y++)
                for (int x = 0; x < o.w; x++)
                {
                    // bilinear sample of the compact icon at the glyph's size
                    const float fx = (x + 0.5f) * sw / o.w - 0.5f, fy = (y + 0.5f) * sh / o.h - 0.5f;
                    const int x0 = std::clamp(int(fx), 0, sw - 1), y0 = std::clamp(int(fy), 0, sh - 1);
                    const int x1 = std::min(x0 + 1, sw - 1), y1 = std::min(y0 + 1, sh - 1);
                    const float ax = std::clamp(fx - x0, 0.0f, 1.0f), ay = std::clamp(fy - y0, 0.0f, 1.0f);
                    auto at = [&](int xx, int yy) { return src[size_t(yy) * strip.w + xx]; };
                    const Rgba p00 = at(x0, y0), p10 = at(x1, y0), p01 = at(x0, y1), p11 = at(x1, y1);
                    auto mix = [&](uint8_t a, uint8_t b, uint8_t cc, uint8_t d) {
                        return uint8_t(std::lround((a * (1 - ax) + b * ax) * (1 - ay) + (cc * (1 - ax) + d * ax) * ay)); };
                    page0[size_t(o.v + y) * kSize + o.u + x] = { mix(p00.r, p10.r, p01.r, p11.r), mix(p00.g, p10.g, p01.g, p11.g),
                        mix(p00.b, p10.b, p01.b, p11.b), mix(p00.a, p10.a, p01.a, p11.a) };
                }
            for (int by = o.v / 4; by <= (o.v + o.h - 1) / 4; by++)
                for (int bx = o.u / 4; bx <= (o.u + o.w - 1) / 4; bx++) touched[size_t(by) * (kSize / 4) + bx] = true;
            compact++;
        }
        const std::vector<uint8_t> enc0 = Encode(page0);
        g_page0KbmTex = g_page0Tex;
        for (uint32_t by = 0; by < kSize / 4; by++)
            for (uint32_t bx = 0; bx < kSize / 4; bx++)
                if (touched[size_t(by) * (kSize / 4) + bx])
                {
                    const uint32_t o = Tiled(bx, by, kSize / 4, 4);
                    memcpy(g_page0KbmTex.data() + o, enc0.data() + o, 16);
                }
        LOGFN("prompts: {} compact icons over page 0's buttons", compact);
        LOGFN("prompts: {} keyboard/mouse icons in page 1 (rows {}-{})", placed.size(), kIconTop, y + 26);
        return true;
    }

    // ---- the font's character table: found by its 'A' and 'B' records (stock layout) ----
    uint32_t BE(uint32_t a) { return ByteSwap(*(uint32_t*)g_memory.Translate(a)); }

    Glyph ReadGlyph(uint32_t a)
    {
        Glyph g;
        g.u = int32_t(BE(a)); g.v = int32_t(BE(a + 4)); g.w = int32_t(BE(a + 8)); g.h = int32_t(BE(a + 12));
        g.texture = int32_t(BE(a + 16) >> 24); g.voffset = int32_t(BE(a + 20));
        return g;
    }

    void WriteGlyph(uint32_t a, const Glyph& g)
    {
        auto put = [&](uint32_t o, uint32_t v) { *(uint32_t*)g_memory.Translate(a + o) = ByteSwap(v); };
        put(0, g.u); put(4, g.v); put(8, g.w); put(12, g.h);
        put(16, (BE(a + 16) & 0x00FFFFFFu) | (uint32_t(g.texture) << 24)); put(20, uint32_t(g.voffset));
    }

    std::vector<uint32_t> FindTables()
    {
        std::vector<uint32_t> found;
        static const uint32_t a[12] = { 201, 55, 24, 26, 0, 0, 227, 55, 26, 26, 0, 0 };   // 'A', 'B'
        uint32_t pattern[12];
        for (int i = 0; i < 12; i++) pattern[i] = ByteSwap(a[i]);
        for (uint64_t base = 0x40000000; base < 0x80000000;)
        {
            MEMORY_BASIC_INFORMATION mbi;
            uint8_t* host = (uint8_t*)g_memory.Translate(uint32_t(base));
            if (!VirtualQuery(host, &mbi, sizeof(mbi))) break;
            const uint64_t end = std::min<uint64_t>(0x80000000, base + uint64_t((uint8_t*)mbi.BaseAddress + mbi.RegionSize - host));
            if (mbi.State == MEM_COMMIT && !(mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)))
            {
                const uint32_t* w = (const uint32_t*)host;
                const size_t n = size_t(end - base) / 4;
                for (size_t i = 0; i + 12 <= n; i++)
                    if (w[i] == pattern[0] && memcmp(w + i, pattern, sizeof(pattern)) == 0)
                    {
                        const uint32_t record = uint32_t(base + i * 4), table = record - 'A' * 24;
                        if (ReadGlyph(table + 'Y' * 24).u == 222) found.push_back(table);   // stock 'Y' at (222, 109)
                    }
            }
            base = end;
        }
        return found;
    }

    void WriteTables(const std::vector<uint32_t>& tables, bool kbm)
    {
        for (int c = 0; c < kGlyphs; c++)
            if (g_replaced[c])
                for (uint32_t t : tables) WriteGlyph(t + c * 24, kbm ? g_kbmGlyphs[c] : g_originalGlyphs[c]);
    }

    void Apply(bool kbm)
    {
        WriteTables(g_tables, kbm);
        const auto& tex = kbm ? g_page0KbmTex : g_page0Tex;
        for (uint32_t pg : g_pages0) gow2gpu::WritePhysical(pg, tex.data(), kBytes, true);
        g_applied = kbm ? 1 : 0;
        LOGFN("prompts: {} prompts", kbm ? "keyboard/mouse" : "controller");
    }

    void Worker()
    {
        Strip strip;
        const bool haveStrip = LoadStrip(strip);
        if (!haveStrip) LOGFN("prompts: no build\\prompts\\kbm_glyphs.* (run tools/make_prompts.py): controller prompts only");
        for (;;)
        {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
            if (!haveStrip || g_failed || !g_page0 || g_page0Tex.empty()) continue;
            std::lock_guard lock(g_mutex);
            if (!g_built)
            {
                g_tables = FindTables();
                g_table = g_tables.empty() ? 0 : g_tables[0];
                if (!g_table) { static int tries = 0; if (++tries == 50) LOGFN_WARNING("prompts: font table not found"); continue; }
                for (int c = 0; c < kGlyphs; c++) g_originalGlyphs[c] = ReadGlyph(g_table + c * 24);
                if (!Build(strip)) { g_failed = true; continue; }
                if (!g_page1Tex.empty())   // page 1 already uploaded: add the icons now
                {
                    g_page1KbmTex = WithIcons(g_page1Tex);
                    for (uint32_t pg : g_pages1) gow2gpu::WritePhysical(pg, g_page1KbmTex.data(), kBytes, true);
                }
                g_built = true;
                std::string list;
                for (uint32_t t : g_tables) list += fmt::format(" {:08X}", t);
                for (uint32_t pg : g_pages1) list += fmt::format(" page1 {:08X}", pg);
                LOGFN("prompts: font tables{}", list);
            }
            if (g_applied == 1)
            {
                // a reloaded font (new character array) still holds the stock records: rewrite it
                static int tick = 0;
                bool stale = false;
                if (++tick % 5 == 0)
                    for (uint32_t t : g_tables)
                    {
                        const Glyph now = ReadGlyph(t + 'A' * 24), want = g_kbmGlyphs['A'];
                        if (now.u != want.u || now.v != want.v || now.texture != want.texture) { stale = true; break; }
                    }
                if (stale)
                {
                    const auto found = FindTables();
                    for (uint32_t t : found)
                        if (std::find(g_tables.begin(), g_tables.end(), t) == g_tables.end()) g_tables.push_back(t);
                    WriteTables(found, true);
                    static int logged = 0;
                    if (logged++ < 10) LOGFN("prompts: font table changed, {} stock table(s) rewritten", found.size());
                }
            }
            const int mode = settings::Get().prompts;   // 0 auto, 1 keyboard, 2 controller
            const bool want = mode == 1 || (mode == 0 && hid::ActiveDevice() == hid::Device::KeyboardMouse);
            if (want != (g_applied == 1)) Apply(want);
        }
    }
}

// from the GPU thread (texture cache), just before the texture's guest data is uploaded
void OnTextureLoad(uint32_t address, uint32_t size, uint32_t format, uint32_t width, uint32_t height, bool tiled)
{
    if (format != 20 || width != kSize || height != kSize || !tiled || size != kBytes) return;
    std::vector<uint8_t> data(kBytes);
    gow2gpu::ReadPhysical(address, data.data(), kBytes);
    const uint64_t h = Fnv(data.data(), kBytes);
    std::lock_guard lock(g_mutex);
    if (h == kPage0Hash)
    {
        g_page0 = address;
        if (g_page0Tex.empty()) g_page0Tex = data;
        if (std::find(g_pages0.begin(), g_pages0.end(), address) == g_pages0.end()) g_pages0.push_back(address);
        // a reload while keyboard prompts are on: the upload that follows reads the icons
        if (g_built && g_applied == 1) gow2gpu::WritePhysical(address, g_page0KbmTex.data(), kBytes, false);
    }
    else if (h == kPage1Hash)
    {
        if (g_page1Tex.empty()) g_page1Tex = data;
        if (std::find(g_pages1.begin(), g_pages1.end(), address) == g_pages1.end()) g_pages1.push_back(address);
        // the icons stay in page 1 in both modes; the upload that follows reads this
        if (g_built)
        {
            if (g_page1KbmTex.empty()) g_page1KbmTex = WithIcons(g_page1Tex);
            gow2gpu::WritePhysical(address, g_page1KbmTex.data(), kBytes, false);
        }
    }
}

void Start()
{
    static std::once_flag once;
    std::call_once(once, [] { std::thread(Worker).detach(); });
}
} // namespace prompts
