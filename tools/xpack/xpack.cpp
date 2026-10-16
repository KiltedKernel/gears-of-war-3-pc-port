// Decompress a fully compressed Xbox 360 UE3 package (.xxx: tag 9E2A83C1, block size, {compressed, uncompressed}
// summary, then one {compressed, uncompressed} pair per block, then the blocks), LZX with a 128 KB window (XMemCompress),
// decoded with libmspack's lzxd. Offline tool for the player's own files.
// usage: xpack <in.xxx> <out.upk> [offset hex] [lzx|lzo]   (offset: one compressed chunk at that file offset;
// lzo: Gears of War 1's LZO1X chunks; build_xpack.cmd)
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
extern "C" {
#include <mspack.h>
#include <lzx.h>
extern int lzxd_no_odd_pad;   // lzxd_xmem.c: libmspack's lzxd, an odd-sized uncompressed block's pad byte optional
}

namespace
{
    struct MemFile { std::vector<uint8_t>* buf; size_t pos; size_t limit; };

    int Read(mspack_file* f, void* out, int n)
    {
        auto* m = (MemFile*)f;
        const size_t k = std::min<size_t>(size_t(n), m->limit - m->pos);
        memcpy(out, m->buf->data() + m->pos, k);
        m->pos += k;
        return int(k);
    }
    int Write(mspack_file* f, void* in, int n)
    {
        auto* m = (MemFile*)f;
        const size_t k = std::min<size_t>(size_t(n), m->limit - m->pos);
        memcpy(m->buf->data() + m->pos, in, k);
        m->pos += k;
        return int(k);
    }
    void* Alloc(mspack_system*, size_t n) { return calloc(1, n); }
    void Free(void* p) { free(p); }
    void Copy(void* s, void* d, size_t n) { memmove(d, s, n); }

    bool g_little = false;   // the stream's header and block table are little-endian (Gears 1's whole-file packages)
    uint32_t BE(const std::vector<uint8_t>& d, size_t o)
    {
        if (g_little) return uint32_t(d[o + 3]) << 24 | uint32_t(d[o + 2]) << 16 | uint32_t(d[o + 1]) << 8 | d[o];
        return uint32_t(d[o]) << 24 | uint32_t(d[o + 1]) << 16 | uint32_t(d[o + 2]) << 8 | d[o + 3];
    }

    // LZO1X decompression (Gears of War 1's packages: compression flag 2), the format of lzo1x_decompress_safe;
    // false on any out-of-bounds read or write
    bool Lzo1x(const uint8_t* in, size_t inLen, uint8_t* out, size_t outLen)
    {
        size_t ip = 0, op = 0, t = 0, m = 0;
        auto need = [&](size_t n) { return ip + n <= inLen; };
        auto room = [&](size_t n) { return op + n <= outLen; };
        auto literals = [&](size_t n) {
            if (!need(n) || !room(n)) return false;
            memcpy(out + op, in + ip, n); ip += n; op += n; return true;
        };
        auto copyMatch = [&](size_t dist, size_t n) {          // overlapping copy from op - dist
            if (dist == 0 || dist > op || !room(n)) return false;
            for (size_t k = 0; k < n; k++, op++) out[op] = out[op - dist];
            return true;
        };
        auto run = [&](size_t base) {                          // a length with zero-byte extensions
            size_t n = 0;
            while (need(1) && in[ip] == 0) { n += 255; ip++; }
            if (!need(1)) return size_t(-1);
            return n + base + in[ip++];
        };
        enum { Loop, FirstLiteralRun, Match, MatchDone, MatchNext } state = Loop;
        if (!need(1)) return false;
        if (in[0] > 17)
        {
            t = in[ip++] - 17;
            if (t < 4) state = MatchNext;
            else { if (!literals(t)) return false; state = FirstLiteralRun; }
        }
        for (;;)
        {
            switch (state)
            {
            case Loop:
                if (!need(1)) return false;
                t = in[ip++];
                if (t >= 16) { state = Match; break; }
                if (t == 0) { t = run(15); if (t == size_t(-1)) return false; }
                if (!literals(t + 3)) return false;
                state = FirstLiteralRun;
                break;
            case FirstLiteralRun:
                if (!need(1)) return false;
                t = in[ip++];
                if (t >= 16) { state = Match; break; }
                if (!need(1)) return false;
                m = 1 + 0x0800 + (t >> 2) + (size_t(in[ip++]) << 2);
                if (!copyMatch(m, 3)) return false;
                state = MatchDone;
                break;
            case Match:
                if (t >= 64)
                {
                    if (!need(1)) return false;
                    m = 1 + ((t >> 2) & 7) + (size_t(in[ip++]) << 3);
                    t = (t >> 5) - 1;
                }
                else if (t >= 32)
                {
                    t &= 31;
                    if (t == 0) { t = run(31); if (t == size_t(-1)) return false; }
                    if (!need(2)) return false;
                    m = 1 + (in[ip] >> 2) + (size_t(in[ip + 1]) << 6); ip += 2;
                }
                else if (t >= 16)
                {
                    m = (t & 8) << 11;
                    t &= 7;
                    if (t == 0) { t = run(7); if (t == size_t(-1)) return false; }
                    if (!need(2)) return false;
                    m += (in[ip] >> 2) + (size_t(in[ip + 1]) << 6); ip += 2;
                    if (m == 0) return op == outLen;           // end of stream
                    m += 0x4000;
                }
                else
                {
                    if (!need(1)) return false;
                    m = 1 + (t >> 2) + (size_t(in[ip++]) << 2);
                    if (!copyMatch(m, 2)) return false;
                    state = MatchDone;
                    break;
                }
                if (!copyMatch(m, t + 2)) return false;
                state = MatchDone;
                break;
            case MatchDone:
                t = in[ip - 2] & 3;
                if (t == 0) { state = Loop; break; }
                state = MatchNext;
                break;
            case MatchNext:
                if (!literals(t) || !need(1)) return false;
                t = in[ip++];
                state = Match;
                break;
            }
        }
    }
}

int main(int argc, char** argv)
{
    if (argc < 3) { fprintf(stderr, "usage: xpack <in.xxx> <out.upk> [chunk offset hex] [lzx|lzo]\n"); return 2; }
    FILE* f = fopen(argv[1], "rb");
    if (!f) { fprintf(stderr, "cannot open %s\n", argv[1]); return 1; }
    std::vector<uint8_t> in;
    fseek(f, 0, SEEK_END); in.resize(size_t(ftell(f))); fseek(f, 0, SEEK_SET);
    fread(in.data(), 1, in.size(), f); fclose(f);
    // optional 3rd argument: one compressed chunk at that file offset (packages with compressed chunks)
    const size_t base = argc > 3 ? size_t(strtoull(argv[3], nullptr, 16)) : 0;
    if (in.size() >= base + 4 && BE(in, base) == 0xC1832A9E) g_little = true;
    if (in.size() < base + 16 || BE(in, base) != 0x9E2A83C1) { fprintf(stderr, "no compressed stream at %zx\n", base); return 1; }
    // optional 4th argument: the codec, "lzx" (Gears 2/3, the default) or "lzo" (Gears 1: compression flag 2)
    const bool lzo = argc > 4 && strcmp(argv[4], "lzo") == 0;
    uint32_t blockSize = BE(in, base + 4);
    const uint32_t totalComp = BE(in, base + 8), totalUncomp = BE(in, base + 12);
    if (blockSize == 0x9E2A83C1) blockSize = 0x20000;   // older chunks (Gears 1, version 374): the tag again, 128 KB blocks
    if (blockSize != 0x20000) { fprintf(stderr, "not a fully compressed package (version field %08X): copy as is\n", blockSize); return 3; }
    std::vector<std::pair<uint32_t, uint32_t>> blocks;
    size_t o = base + 16;
    uint64_t sum = 0;
    while (sum < totalUncomp && o + 8 <= in.size()) { blocks.push_back({ BE(in, o), BE(in, o + 4) }); sum += BE(in, o + 4); o += 8; }
    std::vector<uint8_t> out(totalUncomp);
    mspack_system sys{};
    sys.read = Read; sys.write = Write; sys.alloc = Alloc; sys.free = Free; sys.copy = Copy;
    size_t outPos = 0;
    for (size_t b = 0; b < blocks.size(); b++)
    {
        const auto [comp, uncomp] = blocks[b];
        if (lzo)
        {
            if (o + comp > in.size() || !Lzo1x(in.data() + o, comp, out.data() + outPos, uncomp))
            {
                if (!getenv("XPACK_SKIP_BAD")) { fprintf(stderr, "block %zu: lzo error\n", b); return 1; }
                fprintf(stderr, "block %zu: lzo error, zero-filled\n", b);
                std::fill(out.begin() + outPos, out.begin() + outPos + uncomp, 0);
            }
            outPos += uncomp; o += comp;
            continue;
        }
        // XMemCompress framing: each frame is [compressed size u16 BE] (32 KB of output), or [FF][uncompressed size
        // u16 BE][compressed size u16 BE] for a shorter frame; the LZX bitstream is the payloads back to back
        std::vector<uint8_t> src;
        for (size_t p = o, end = o + comp; p + 2 <= end;)
        {
            uint32_t frameComp;
            if (in[p] == 0xFF) { frameComp = uint32_t(in[p + 3]) << 8 | in[p + 4]; p += 5; }
            else { frameComp = uint32_t(in[p]) << 8 | in[p + 1]; p += 2; }
            if (frameComp == 0 || p + frameComp > end) break;
            src.insert(src.end(), in.begin() + p, in.begin() + p + frameComp);
            p += frameComp;
        }
        const uint32_t srcLen = uint32_t(src.size());
        std::vector<uint8_t> dst(uncomp);
        MemFile mi{ &src, 0, srcLen }, mo{ &dst, 0, uncomp };
        lzxd_stream* lzx = lzxd_init(&sys, (mspack_file*)&mi, (mspack_file*)&mo, 17, 0, 0x8000, uncomp, 0);
        int rc = lzx ? lzxd_decompress(lzx, uncomp) : -1;
        if (rc != MSPACK_ERR_OK)
        {
            // an odd-sized LZX uncompressed block: the CAB form pads it to an even length, these streams (some blocks
            // of Gears of War 2's and 3's packages) do not; decoded again without the pad byte
            if (lzx) lzxd_free(lzx);
            mi.pos = 0; mo.pos = 0; lzxd_no_odd_pad = 1;
            lzx = lzxd_init(&sys, (mspack_file*)&mi, (mspack_file*)&mo, 17, 0, 0x8000, uncomp, 0);
            rc = lzx ? lzxd_decompress(lzx, uncomp) : -1;
            lzxd_no_odd_pad = 0;
        }
        if (rc != MSPACK_ERR_OK && lzx && getenv("XPACK_DEBUG"))   // where the bitstream stopped decoding
            fprintf(stderr, "block %zu: lzx stopped in frame %u (output %lld of %u), block type %u, %u left in the LZX block, input %zu of %u\n",
                    b, lzx->frame, (long long)lzx->offset, uncomp, lzx->block_type, lzx->block_remaining, mi.pos, srcLen);
        if (lzx) lzxd_free(lzx);
        if (rc != MSPACK_ERR_OK)
        {
            // XPACK_SKIP_BAD=1: zero-fill the block and go on (the rest of the package stays readable)
            if (!getenv("XPACK_SKIP_BAD")) { fprintf(stderr, "block %zu: lzx error %d\n", b, rc); return 1; }
            fprintf(stderr, "block %zu: lzx error %d, zero-filled (%u bytes at %zx)\n", b, rc, uncomp, outPos);
            std::fill(dst.begin(), dst.end(), 0);
        }
        memcpy(out.data() + outPos, dst.data(), uncomp);
        outPos += uncomp; o += comp;
    }
    FILE* g = fopen(argv[2], "wb");
    if (!g) { fprintf(stderr, "cannot write %s\n", argv[2]); return 1; }
    fwrite(out.data(), 1, out.size(), g); fclose(g);
    printf("%s: %zu blocks, %u -> %u bytes\n", argv[1], blocks.size(), totalComp, totalUncomp);
    return 0;
}
