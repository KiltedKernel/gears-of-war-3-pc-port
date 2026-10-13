"""Texture2D from big-endian Xbox 360 UE3 packages, Gears of War 2 (575) and 3 (828) alike: properties, mips (inline
in the package, or in a .tfc texture file cache next to it, LZX-compressed), Xenos untiling and decoding to PNG.
Offline analysis of the player's own files. TFC_DIR = the cooked folder, for a package unpacked elsewhere.

Serialized layout after the tagged properties (both versions):
  SourceArt   bulk data header (empty when cooked)
  Mips        int32 count, then per mip: bulk data {u32 flags, i32 elements, i32 size on disk, i32 offset in file}
              (+ the data inline when it is not in a separate file), i32 SizeX, i32 SizeY
  TextureFileCacheGuid (16 bytes); Gears 3 adds 4 more bytes (an empty array)
Bulk flags: 0x01 in a separate file (the .tfc named by TextureFileCacheName), 0x20 empty, 0x80 LZX (an
XMemCompress chunk like a compressed package's: tools/xpack decodes it), 0x10 LZO (Gears 1 (374): every mip is
inline and LZO-compressed; no texture caches, no cache GUID).
Mips whose data lies in the tail of a larger one (MipTailBaseIdx and below) are empty: they are packed into the
last stored mip, as the GPU lays out its mip tail.

usage: python -P texture2d.py <package.xxx> list [filter]
       python -P texture2d.py <package.xxx> info <export index>
       python -P texture2d.py <package.xxx> png <export index> <out.png> [mip]"""
import os, struct, subprocess, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, dxt

# EPixelFormat -> (bytes per 4x4 block, or per texel when uncompressed; is block-compressed)
FORMATS = {'PF_DXT1': (8, True), 'PF_DXT3': (16, True), 'PF_DXT5': (16, True), 'PF_BC5': (16, True),
           'PF_A8R8G8B8': (4, False), 'PF_G8': (1, False), 'PF_V8U8': (2, False)}

# EPixelFormat as a number (version 374 serializes ByteProperty without its enum name)
PIXEL_FORMATS_374 = {2: 'PF_A8R8G8B8', 3: 'PF_G8', 5: 'PF_DXT1', 6: 'PF_DXT3', 7: 'PF_DXT5'}


class Texture:
    def __init__(self, pkg, index):
        self.pkg, self.index = pkg, index
        d = pkg.export_data(index)
        props, o = pkg.properties(d)
        self.props = {n: v for n, t, i, v in props}
        self.format = self.props.get('Format', 'PF_A8R8G8B8')
        if isinstance(self.format, int): self.format = PIXEL_FORMATS_374.get(self.format, self.format)   # Gears 1: a byte
        self.tfc = self.props.get('TextureFileCacheName')
        E, i32, u32 = upk.E, upk.i32, upk.u32
        o += 16                                                     # SourceArt (empty)
        assert u32(d, o - 16) & 0x20 or i32(d, o - 8) == 0, 'SourceArt is not empty'
        n = i32(d, o); o += 4
        self.mips = []
        for _ in range(n):
            flags, count, size, off = struct.unpack_from(E + 'Iiii', d, o); o += 16
            inline = None
            if not flags & 0x21:                                   # stored right here
                inline = d[o:o + size]; o += size
            w, h = i32(d, o), i32(d, o + 4); o += 8
            self.mips.append({'flags': flags, 'count': count, 'size': size, 'offset': off, 'w': w, 'h': h,
                              'inline': inline})
        self.guid = d[o:o + 16]
        self.trailer = d[o + 16:]

    def mip_bytes(self, m):
        """mip m as the GPU stores it (tiled, 16-bit big-endian words), or None when empty"""
        mip = self.mips[m]
        if mip['flags'] & 0x20: return None
        if mip['inline'] is not None: data = mip['inline']
        elif not self.tfc:
            # Gears 1 maps: a "separate" mip is in the texture's own content package (the outermost package of its
            # path, e.g. COG_AssaultRifle.xxx), at that file's raw offset
            path = os.path.join(os.path.dirname(self.pkg.filename), self.pkg.path(self.index).split('.')[0] + '.xxx')
            with open(path, 'rb') as f:
                f.seek(mip['offset']); data = f.read(mip['size'])
        else:
            path = os.path.join(os.path.dirname(self.pkg.filename), self.tfc + '.tfc')
            if not os.path.exists(path) and os.environ.get('TFC_DIR'):     # a package unpacked elsewhere
                path = os.path.join(os.environ['TFC_DIR'], self.tfc + '.tfc')
            with open(path, 'rb') as f:
                f.seek(mip['offset']); data = f.read(mip['size'])
        if mip['flags'] & 0x80: data = lzx(data)
        elif mip['flags'] & 0x10: data = lzx(data, 'lzo')                  # Gears 1: inline LZO chunks
        return data

    def rgba(self, m):
        """mip m as (h, w, 4) uint8"""
        mip, data = self.mips[m], self.mip_bytes(m)
        if data is None: return None
        return decode(data, self.format, mip['w'], mip['h'])


def lzx(chunk, codec='lzx'):
    with tempfile.TemporaryDirectory() as tmp:
        src, out = os.path.join(tmp, 'c.bin'), os.path.join(tmp, 'u.bin')
        open(src, 'wb').write(chunk)
        xpack = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xpack', 'xpack.exe')
        r = subprocess.run([xpack, src, out, '0', codec], capture_output=True, text=True)
        if r.returncode != 0: raise ValueError(f'LZX: {r.stderr.strip()}')
        return open(out, 'rb').read()


def tiled_offsets(bw, bh, bpb):
    """byte offset of each block (bh, bw) in a tiled Xenos 2D surface (Xenia texture_address::Tiled2D), vectorized;
    pitch and height are padded to 32 blocks"""
    y, x = np.mgrid[0:bh, 0:bw].astype(np.int64)
    pitch = (bw + 31) & ~31
    log2 = bpb.bit_length() - 1
    outer = ((y >> 5) * (pitch >> 5) + (x >> 5)) << 6
    inner = (((y >> 1) & 7) << 3) | (x & 7)
    oib = (outer | inner) << log2
    bank = (y >> 4) & 1
    pipe = ((x >> 3) & 3) ^ (((y >> 3) & 1) << 1)
    return ((y & 1) << 4) | (pipe << 6) | (bank << 11) | (oib & 15) | (((oib >> 4) & 1) << 5) | \
           (((oib >> 5) & 7) << 8) | ((oib >> 8) << 12)


def untile(data, w, h, bpb, compressed):
    """tiled big-endian surface -> linear little-endian blocks (or texels), row-major"""
    bw, bh = ((w + 3) // 4, (h + 3) // 4) if compressed else (w, h)
    offs = tiled_offsets(max(bw, 1), max(bh, 1), bpb)
    need = int(offs.max()) + bpb
    buf = np.frombuffer(data + bytes(max(0, need - len(data))), dtype=np.uint8)
    lin = buf[offs.reshape(-1, 1) + np.arange(bpb)[None]]               # (blocks, bpb)
    if compressed or bpb == 2:                                          # 16-bit words are big-endian
        lin = lin.reshape(-1, 2)[:, ::-1]
    return np.ascontiguousarray(lin).tobytes()


def decode_dxt1(data, w, h, alpha_block=None):
    bw, bh = (w + 3) // 4, (h + 3) // 4
    blk = np.frombuffer(data, dtype=np.uint8, count=bw * bh * 8).reshape(-1, 8)
    c0 = blk[:, 0].astype(np.uint16) | (blk[:, 1].astype(np.uint16) << 8)
    c1 = blk[:, 2].astype(np.uint16) | (blk[:, 3].astype(np.uint16) << 8)
    bits = blk[:, 4:8].copy().view('<u4')[:, 0]
    p0, p1 = dxt._565(c0), dxt._565(c1)
    four = (c0 > c1)[:, None] | (alpha_block is not None)
    pal = np.stack([p0, p1, np.where(four, (2 * p0 + p1) // 3, (p0 + p1) // 2),
                    np.where(four, (p0 + 2 * p1) // 3, 0)], 1)
    idx = ((bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None]) & 3).astype(np.int64)
    rgb = np.take_along_axis(pal, idx[:, :, None].repeat(3, 2), 1)
    a = np.where((idx == 3) & ~four, 0, 255) if alpha_block is None else alpha_block
    px = np.concatenate([rgb, a[:, :, None]], 2).astype(np.uint8)
    return px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)[:h, :w]


def decode_bc4(blk8):
    """(n, 8) BC4/DXT5-alpha blocks -> (n, 16) values"""
    a0, a1 = blk8[:, 0].astype(np.int32), blk8[:, 1].astype(np.int32)
    bits = np.zeros(len(blk8), dtype=np.uint64)
    for k in range(6): bits |= blk8[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    i = np.arange(8)
    p8 = ((7 - i)[None] * a0[:, None] + i[None] * a1[:, None] + 3) // 7
    p6 = ((5 - i)[None] * a0[:, None] + i[None] * a1[:, None] + 2) // 5
    p6[:, 6], p6[:, 7] = 0, 255
    pal = np.where((a0 > a1)[:, None], p8, p6)
    pal[:, 0], pal[:, 1] = a0, a1
    idx = ((bits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))[None]) & np.uint64(7)).astype(np.int64)
    return np.take_along_axis(pal, idx, 1)


def decode(data, fmt, w, h):
    bpb, compressed = FORMATS[fmt]
    lin = untile(data, w, h, bpb, compressed)
    bw, bh = (w + 3) // 4, (h + 3) // 4
    if fmt == 'PF_DXT1': return decode_dxt1(lin, w, h)
    if fmt == 'PF_DXT5': return dxt.decode_dxt5(lin, w, h)
    if fmt in ('PF_DXT3', 'PF_BC5'):
        blk = np.frombuffer(lin, dtype=np.uint8).reshape(-1, 16)
        if fmt == 'PF_DXT3':
            a4 = np.unpackbits(blk[:, :8], axis=1, bitorder='little').reshape(-1, 16, 4)
            a = (a4 * (1 << np.arange(4))).sum(2) * 17
            return decode_dxt1(blk[:, 8:].tobytes(), w, h, alpha_block=a)
        r, g = decode_bc4(blk[:, :8]), decode_bc4(blk[:, 8:])         # normal map: x, y; z rebuilt
        nx, ny = r / 127.5 - 1, g / 127.5 - 1
        b = (np.sqrt(np.clip(1 - nx * nx - ny * ny, 0, 1)) * 127.5 + 127.5).astype(np.int32)
        px = np.stack([r, g, b, np.full_like(r, 255)], 2).astype(np.uint8)
        return px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)[:h, :w]
    a = np.frombuffer(lin, dtype=np.uint8).reshape(h, w, bpb)
    if fmt == 'PF_A8R8G8B8': return a[:, :, [1, 2, 3, 0]].copy()          # big-endian ARGB
    if fmt == 'PF_G8': return np.concatenate([a.repeat(3, 2), np.full((h, w, 1), 255, np.uint8)], 2)
    return np.concatenate([a, np.zeros((h, w, 1), np.uint8), np.full((h, w, 1), 255, np.uint8)], 2)


if __name__ == '__main__':
    pkg = upk.Package(sys.argv[1])
    cmd = sys.argv[2] if len(sys.argv) > 2 else 'list'
    if cmd == 'list':
        flt = sys.argv[3].lower() if len(sys.argv) > 3 else ''
        for i, e in enumerate(pkg.exports):
            if pkg.class_name(e['class']) != 'Texture2D' or flt not in pkg.path(i).lower(): continue
            t = Texture(pkg, i)
            top = next((m for m in t.mips if not m['flags'] & 0x20), None)
            print(f"{i:6} {t.format:12} {t.props.get('SizeX')}x{t.props.get('SizeY')} top {top and top['w']}x{top and top['h']} "
                  f"{len(t.mips)} mips {t.tfc or '-':12} {pkg.path(i)}")
    elif cmd == 'info':
        t = Texture(pkg, int(sys.argv[3]))
        for k, v in t.props.items(): print(f'  {k} = {v if not isinstance(v, tuple) else v[0]}')
        for m, mip in enumerate(t.mips):
            where = 'empty' if mip['flags'] & 0x20 else 'inline' if mip['inline'] is not None else f"{t.tfc}.tfc@{mip['offset']:#x}"
            print(f"  mip {m}: {mip['w']}x{mip['h']} flags {mip['flags']:#x} {mip['count']} bytes ({mip['size']} on disk) {where}")
        print('  guid', t.guid.hex(), 'trailer', t.trailer.hex())
    elif cmd == 'png':
        from PIL import Image
        t = Texture(pkg, int(sys.argv[3]))
        m = int(sys.argv[5]) if len(sys.argv) > 5 else next(k for k, mip in enumerate(t.mips) if not mip['flags'] & 0x20)
        Image.fromarray(t.rgba(m), 'RGBA').save(sys.argv[4])
        print(f"mip {m} {t.mips[m]['w']}x{t.mips[m]['h']} {t.format} -> {sys.argv[4]}")
