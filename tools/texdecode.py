"""Decode raw Xbox 360 texture dumps (GOW3_TEXTURE_DUMP: <addr>_f<format>_<w>x<h>_p<pitch>_<t|l>.bin) to PNG, and
re-encode (untiled DXT blocks -> tiled guest layout) for replacements. Xenos tiling from Xenia's texture_address.h.
usage: texdecode.py <dump.bin|folder> [out folder]"""
import os, re, struct, sys
import numpy as np
from PIL import Image

FORMATS = {18: ('DXT1', 8), 19: ('DXT3', 16), 20: ('DXT5', 16), 6: ('8888', 4)}


def tiled2d(x, y, pitch_aligned, bpb_log2):
    """byte offset of block (x, y) in a tiled 2D surface (Xenia texture_address::Tiled2D)"""
    outer = ((y >> 5) * (pitch_aligned >> 5) + (x >> 5)) << 6
    inner = (((y >> 1) & 7) << 3) | (x & 7)
    oib = (outer | inner) << bpb_log2
    bank = (y >> 4) & 1
    pipe = ((x >> 3) & 3) ^ (((y >> 3) & 1) << 1)
    return ((y & 1) << 4) | (pipe << 6) | (bank << 11) | (oib & 15) | (((oib >> 4) & 1) << 5) | \
           (((oib >> 5) & 7) << 8) | ((oib >> 8) << 12)


def block_layout(width, height, bpb, tiled):
    """offset of every block, row-major (bw x bh)"""
    bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
    if bpb == 4:   # uncompressed: 'blocks' are texels
        bw, bh = width, height
    pitch = (bw + 31) & ~31
    log2 = bpb.bit_length() - 1
    offs = np.zeros((bh, bw), dtype=np.int64)
    for y in range(bh):
        for x in range(bw):
            offs[y, x] = tiled2d(x, y, pitch, log2) if tiled else (y * pitch + x) * bpb
    return offs


def swap16(b):
    a = np.frombuffer(b, dtype=np.uint8).copy()
    a[0::2], a[1::2] = a[1::2].copy(), a[0::2].copy()
    return a.tobytes()


def rgb565(c):
    return np.array([((c >> 11) & 31) * 255 // 31, ((c >> 5) & 63) * 255 // 63, (c & 31) * 255 // 31], dtype=np.int32)


def decode_color(block, out, dxt1):
    c0, c1, bits = struct.unpack('<HHI', block)
    p0, p1 = rgb565(c0), rgb565(c1)
    if c0 > c1 or not dxt1:
        pal = [p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3]
        alpha = [255] * 4
    else:
        pal = [p0, p1, (p0 + p1) // 2, np.zeros(3, dtype=np.int32)]
        alpha = [255, 255, 255, 0]
    for i in range(16):
        k = (bits >> (2 * i)) & 3
        out[i // 4, i % 4, :3] = pal[k]
        out[i // 4, i % 4, 3] = alpha[k]


def decode_alpha5(block, out):
    a0, a1 = block[0], block[1]
    bits = int.from_bytes(block[2:8], 'little')
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    for i in range(16):
        out[i // 4, i % 4, 3] = pal[(bits >> (3 * i)) & 7]


def decode(path):
    m = re.search(r'([0-9A-F]{8})_f(\d+)_(\d+)x(\d+)_p(\d+)_([tl])\.bin$', os.path.basename(path))
    fmt, w, h, tiled = int(m.group(2)), int(m.group(3)), int(m.group(4)), m.group(6) == 't'
    if fmt not in FORMATS: return None
    name, bpb = FORMATS[fmt]
    data = open(path, 'rb').read()
    if name != '8888': data = swap16(data)
    offs = block_layout(w, h, bpb, tiled)
    img = np.zeros((h, w, 4), dtype=np.uint8)
    if name == '8888':
        for y in range(h):
            for x in range(w):
                o = offs[y, x]
                a, r, g, b = data[o:o + 4]   # k_8_8_8_8 big-endian ARGB
                img[y, x] = (r, g, b, a)
        return img
    tmp = np.zeros((4, 4, 4), dtype=np.int32)
    for by in range(offs.shape[0]):
        for bx in range(offs.shape[1]):
            o = offs[by, bx]
            blk = data[o:o + bpb]
            if len(blk) < bpb: continue
            if name == 'DXT1':
                decode_color(blk, tmp, True)
            else:
                decode_color(blk[8:16], tmp, False)
                decode_alpha5(blk[:8], tmp) if name == 'DXT5' else None
            img[by * 4:by * 4 + 4, bx * 4:bx * 4 + 4] = tmp[:min(4, h - by * 4), :min(4, w - bx * 4)]
    return img


if __name__ == '__main__':
    src = sys.argv[1]
    files = [os.path.join(src, f) for f in sorted(os.listdir(src)) if f.endswith('.bin')] if os.path.isdir(src) else [src]
    out = sys.argv[2] if len(sys.argv) > 2 else (src if os.path.isdir(src) else os.path.dirname(src))
    os.makedirs(out, exist_ok=True)
    for f in files:
        img = decode(f)
        if img is None: print('skip', f); continue
        Image.fromarray(img, 'RGBA').save(os.path.join(out, os.path.basename(f)[:-4] + '.png'))
        print('decoded', os.path.basename(f))
