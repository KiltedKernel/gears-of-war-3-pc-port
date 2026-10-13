"""Vectorized DXT1/DXT5 decode and DXT5 encode (numpy) for linear little-endian block data (PC textures), used for
the button-prompt glyph sheets. Encoding is a simple range-fit (good enough for flat icon art)."""
import numpy as np


def _565(c):
    r = ((c >> 11) & 31).astype(np.int32) * 255 // 31
    g = ((c >> 5) & 63).astype(np.int32) * 255 // 63
    b = (c & 31).astype(np.int32) * 255 // 31
    return np.stack([r, g, b], -1)


def decode_dxt5(data, width, height):
    bw, bh = (width + 3) // 4, (height + 3) // 4
    blk = np.frombuffer(data, dtype=np.uint8, count=bw * bh * 16).reshape(bh * bw, 16)
    a0 = blk[:, 0].astype(np.int32); a1 = blk[:, 1].astype(np.int32)
    abits = np.zeros(len(blk), dtype=np.uint64)
    for k in range(6): abits |= blk[:, 2 + k].astype(np.uint64) << np.uint64(8 * k)
    i = np.arange(1, 7)
    pal8 = np.where(a0[:, None] > a1[:, None], ((7 - np.arange(8))[None] * a0[:, None] + np.arange(8)[None] * a1[:, None]) // 7, 0)
    pal8[:, 0] = a0; pal8[:, 1] = a1
    alt = np.zeros((len(blk), 8), dtype=np.int32)
    alt[:, 0] = a0; alt[:, 1] = a1
    alt[:, 2:6] = ((5 - np.arange(1, 5))[None] * a0[:, None] + np.arange(1, 5)[None] * a1[:, None]) // 5
    alt[:, 6] = 0; alt[:, 7] = 255
    pal8[:, 2:] = np.where((a0 > a1)[:, None], ((7 - i)[None] * a0[:, None] + i[None] * a1[:, None]) // 7, alt[:, 2:])
    aidx = ((abits[:, None] >> (np.uint64(3) * np.arange(16, dtype=np.uint64))[None]) & np.uint64(7)).astype(np.int64)
    alpha = np.take_along_axis(pal8, aidx, 1)
    c0 = blk[:, 8].astype(np.uint16) | (blk[:, 9].astype(np.uint16) << 8)
    c1 = blk[:, 10].astype(np.uint16) | (blk[:, 11].astype(np.uint16) << 8)
    cbits = blk[:, 12].astype(np.uint32) | (blk[:, 13].astype(np.uint32) << 8) | (blk[:, 14].astype(np.uint32) << 16) | (blk[:, 15].astype(np.uint32) << 24)
    p0, p1 = _565(c0), _565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], 1)
    cidx = ((cbits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None]) & 3).astype(np.int64)
    rgb = np.take_along_axis(pal, cidx[:, :, None].repeat(3, 2), 1)
    px = np.concatenate([rgb, alpha[:, :, None]], 2).astype(np.uint8)          # (blocks, 16, 4)
    img = px.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:height, :width]


def encode_dxt5(img):
    """img: (h, w, 4) uint8, h and w multiples of 4 -> linear DXT5 blocks (little-endian PC layout)"""
    h, w = img.shape[:2]
    bh, bw = h // 4, w // 4
    px = img.reshape(bh, 4, bw, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * bw, 16, 4).astype(np.int32)
    out = np.zeros((bh * bw, 16), dtype=np.uint8)
    # alpha: 8-level ramp between max and min
    amax, amin = px[:, :, 3].max(1), px[:, :, 3].min(1)
    out[:, 0], out[:, 1] = amax, amin
    rng = np.maximum(amax - amin, 1)
    t = ((amax[:, None] - px[:, :, 3]) * 7 + rng[:, None] // 2) // rng[:, None]        # 0 = amax ... 7 = amin
    remap = np.array([0, 2, 3, 4, 5, 6, 7, 1])                                          # ramp order -> DXT5 index
    aidx = remap[np.clip(t, 0, 7)].astype(np.uint64)
    aidx[amax == amin] = 0
    abits = np.zeros(len(px), dtype=np.uint64)
    for k in range(16): abits |= aidx[:, k] << np.uint64(3 * k)
    for k in range(6): out[:, 2 + k] = ((abits >> np.uint64(8 * k)) & np.uint64(255)).astype(np.uint8)
    # color: endpoints = brightest / darkest (by luma) of the visible texels
    luma = px[:, :, 0] * 3 + px[:, :, 1] * 6 + px[:, :, 2]
    luma = np.where(px[:, :, 3] > 8, luma, -1)
    hi = np.take_along_axis(px[:, :, :3], luma.argmax(1)[:, None, None].repeat(3, 2), 1)[:, 0]
    luma2 = np.where(px[:, :, 3] > 8, luma, 1 << 20)
    lo = np.take_along_axis(px[:, :, :3], luma2.argmin(1)[:, None, None].repeat(3, 2), 1)[:, 0]
    def to565(c): return ((c[:, 0] * 31 + 127) // 255 << 11) | ((c[:, 1] * 63 + 127) // 255 << 5) | ((c[:, 2] * 31 + 127) // 255)
    c0, c1 = to565(hi), to565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    c1 = np.where(c0 == c1, np.maximum(c0 - 1, 0), c1)        # keep 4-color mode (c0 > c1)
    p0, p1 = _565(c0.astype(np.uint16)), _565(c1.astype(np.uint16))
    pal = np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], 1)                 # (n, 4, 3)
    d = ((px[:, :, None, :3] - pal[:, None]) ** 2).sum(-1)                              # (n, 16, 4)
    cidx = d.argmin(2).astype(np.uint32)
    cbits = np.zeros(len(px), dtype=np.uint32)
    for k in range(16): cbits |= cidx[:, k] << np.uint32(2 * k)
    out[:, 8], out[:, 9] = c0 & 255, c0 >> 8
    out[:, 10], out[:, 11] = c1 & 255, c1 >> 8
    for k in range(4): out[:, 12 + k] = (cbits >> np.uint32(8 * k)) & 255
    return out.tobytes()
