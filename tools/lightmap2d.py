"""Gears 1 texture light maps as Gears 3 ones (the BSP's: one atlas of three DXT1 textures per section).

Gears 1's light map is three textures, one per basis direction, each an RGB colour times a scale. Gears 3's has two:
the incident light's colour normalised to its brightest channel, and the directional intensities (the brightest
channel per basis direction) under one scale; lighting.gears3_vertex_lightmap writes the same two per vertex. The
converter used to sample the atlas at the BSP's vertices, which lost every shadow edge and sun patch smaller than a
polygon: the prison's floors came out flat where Gears 1 had window light and shade.

Atlas: the three textures' mip 0 decoded into image space (texture2d.tiled_offsets maps blocks), light added where
Gears 1 drew it dynamically (its point lights: add_light, rasterised over the BSP's triangles in light map space),
converted, and written back into the same tiled layout; the packed mip tail is converted block by block in place."""
import struct

import numpy as np

import texture2d


def decode_blocks(data):
    """Xbox DXT1 bytes (16-bit big-endian words) -> (n, 16, 3) float RGB in 0..1, texels row-major in each block"""
    pad = (-len(data)) % 8
    blk = np.frombuffer(bytes(data) + bytes(pad), dtype=np.uint8).reshape(-1, 8)
    le = blk.reshape(-1, 2)[:, ::-1].reshape(-1, 8)
    c0 = le[:, 0].astype(np.int32) | (le[:, 1].astype(np.int32) << 8)
    c1 = le[:, 2].astype(np.int32) | (le[:, 3].astype(np.int32) << 8)
    bits = np.ascontiguousarray(le[:, 4:8]).view('<u4')[:, 0]
    idx = ((bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None]) & 3).astype(np.int64)
    def rgb(c): return np.stack([((c >> 11) & 31) / 31, ((c >> 5) & 63) / 63, (c & 31) / 31], 1)
    p0, p1 = rgb(c0), rgb(c1)
    four = (c0 > c1)[:, None]
    pal = np.stack([p0, p1, np.where(four, (2 * p0 + p1) / 3, (p0 + p1) / 2), np.where(four, (p0 + 2 * p1) / 3, 0.0)], 1)
    return np.take_along_axis(pal, idx[:, :, None].repeat(3, 2), 1).astype(np.float32)


def encode_blocks(rgb):
    """(n, 16, 3) float RGB in 0..1 -> Xbox DXT1 bytes (four-colour blocks: endpoints at the extremes along the
    block's colour range, each texel the nearest of the four)"""
    rgb = np.clip(np.asarray(rgb, np.float32), 0, 1)
    lo, hi = rgb.min(1), rgb.max(1)
    axis = hi - lo
    t = ((rgb - lo[:, None]) * axis[:, None]).sum(2)
    a = np.take_along_axis(rgb, t.argmax(1)[:, None, None].repeat(3, 2), 1)[:, 0]
    b = np.take_along_axis(rgb, t.argmin(1)[:, None, None].repeat(3, 2), 1)[:, 0]
    def q(c):
        r, g, b_ = np.round(c[:, 0] * 31).astype(np.int32), np.round(c[:, 1] * 63).astype(np.int32), np.round(c[:, 2] * 31).astype(np.int32)
        return (r << 11) | (g << 5) | b_
    def dq(v): return np.stack([((v >> 11) & 31) / 31, ((v >> 5) & 63) / 63, (v & 31) / 31], 1)
    c0, c1 = q(a), q(b)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    same = c0 == c1                                       # (one colour: index 0 everywhere)
    p0, p1 = dq(c0), dq(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)
    d = ((rgb[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(3)
    idx = d.argmin(2).astype(np.uint32)
    idx[same] = 0
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))[None]).sum(1).astype('<u4')
    le = np.zeros((len(rgb), 8), np.uint8)
    le[:, 0], le[:, 1] = c0 & 255, c0 >> 8
    le[:, 2], le[:, 3] = c1 & 255, c1 >> 8
    le[:, 4:8] = bits.view(np.uint8).reshape(-1, 4)
    return le.reshape(-1, 2)[:, ::-1].reshape(-1).tobytes()


def block_grid(w, h):
    """(bh, bw) block offsets of a w x h DXT1 mip in the tiled layout"""
    bw, bh = max(1, (w + 3) // 4), max(1, (h + 3) // 4)
    return texture2d.tiled_offsets(bw, bh, 8)


def to_image(data, w, h):
    """a tiled DXT1 mip -> (h, w, 3) float"""
    offs = block_grid(w, h)
    bh, bw = offs.shape
    flat = np.frombuffer(bytes(data), np.uint8)
    blocks = np.stack([flat[o:o + 8] for o in offs.ravel()]) if len(offs.ravel()) else np.zeros((0, 8), np.uint8)
    px = decode_blocks(blocks.tobytes()).reshape(bh, bw, 4, 4, 3)
    return px.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 3)[:h, :w]


def from_image(img, template):
    """(h, w, 3) float -> tiled DXT1 bytes in template's layout (its length; padding kept)"""
    h, w = img.shape[:2]
    offs = block_grid(w, h)
    bh, bw = offs.shape
    full = np.zeros((bh * 4, bw * 4, 3), np.float32); full[:h, :w] = img
    blocks = full.reshape(bh, 4, bw, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3)
    enc = np.frombuffer(encode_blocks(blocks), np.uint8).reshape(-1, 8)
    out = bytearray(template)
    for o, b in zip(offs.ravel(), enc):
        out[o:o + 8] = b.tobytes()
    return bytes(out)


def gears3_coefficients(coef, top=None):
    """(..., 3 basis, 3 rgb) Gears 1 light -> (colour (..., 3), directional (..., 3) / top, top), as
    lighting.gears3_vertex_lightmap does per vertex"""
    coef = np.clip(coef, 0, 2.0)
    avg = coef.mean(-2)
    peak = np.maximum(avg.max(-1, keepdims=True), 1e-6)
    colour = np.clip(avg / peak, 0, 1)
    direc = coef.max(-1)
    if top is None: top = max(float(np.percentile(direc, 99)), 1e-6)
    return colour, np.clip(direc / top, 0, 1), top


class Atlas:
    """a Gears 1 light map atlas (three textures with scales) in image space, light added, written as Gears 3's"""

    def __init__(self, p, textures):
        self.t = [texture2d.Texture(p, ref - 1) for ref, scale in textures]
        self.scales = [np.asarray(scale, np.float32) for ref, scale in textures]
        self.first = next(k for k, mm in enumerate(self.t[0].mips) if not mm['flags'] & 0x20)
        m0 = self.t[0].mips[self.first]
        self.w, self.h = m0['w'], m0['h']
        self.coef = np.stack([to_image(t.mip_bytes(self.first), self.w, self.h) * s for t, s in zip(self.t, self.scales)], 2)
        self.added = np.zeros((self.h, self.w, 3), np.float32)

    def add_light(self, P, N, UV, tris, bake):
        """light baked at the texels the triangles (index triples into P / N / UV; UV in 0..1 atlas space) cover:
        bake(P (k, 3), N (k, 3)) -> (k, 3) rgb, added to every basis direction"""
        pts, nrm, at = [], [], []
        for a, b, c in tris:
            uv = UV[[a, b, c]] * [self.w, self.h] - 0.5
            lo, hi = np.floor(uv.min(0)).astype(int) - 1, np.ceil(uv.max(0)).astype(int) + 1
            xs, ys = np.meshgrid(np.arange(max(lo[0], 0), min(hi[0], self.w - 1) + 1), np.arange(max(lo[1], 0), min(hi[1], self.h - 1) + 1))
            if not xs.size: continue
            px, py = xs.ravel().astype(np.float64), ys.ravel().astype(np.float64)
            (x0, y0), (x1, y1), (x2, y2) = uv
            d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
            if abs(d) < 1e-9: continue
            l0 = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / d
            l1 = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / d
            l2 = 1 - l0 - l1
            # (a texel within about one texel of the triangle: its edge texels are sampled too)
            tol = 1.0 / max(np.sqrt(abs(d)), 1.0)
            inside = (l0 > -tol) & (l1 > -tol) & (l2 > -tol)
            if not inside.any(): continue
            L = np.stack([l0[inside], l1[inside], l2[inside]], 1)
            pts.append(L @ P[[a, b, c]]); nrm.append(L @ N[[a, b, c]])
            at.append(np.stack([py[inside], px[inside]], 1).astype(np.int64))
        if not pts: return 0
        Pt, Nt, At = np.concatenate(pts), np.concatenate(nrm), np.concatenate(at)
        Nt /= np.linalg.norm(Nt, axis=1, keepdims=True) + 1e-9
        light = bake(Pt, Nt)
        # (a texel in several triangles: its brightest)
        np.maximum.at(self.added, (At[:, 0], At[:, 1]), light.astype(np.float32))
        return len(At)

    def gears3(self):
        """(texture-like sources for Gears 3's two coefficient textures, scale vectors [(3,), (3,), (3,)])"""
        coef = self.coef + self.added[:, :, None, :]
        colour, direc, top = gears3_coefficients(coef)
        img = [colour, direc]
        outs = [{}, {}]
        mips = self.t[0].mips
        tail = int(self.t[0].props.get('MipTailBaseIdx', len(mips) - 1))
        for k in range(self.first, len(mips)):
            if mips[k]['flags'] & 0x20: continue
            raw = [t.mip_bytes(k) for t in self.t]
            if raw[0] is None: continue
            w, h = mips[k]['w'], mips[k]['h']
            if k < tail and w >= 4 and h >= 4:
                for j in range(2):
                    im = img[j]
                    while im.shape[0] > h or im.shape[1] > w:        # (2 x 2 averages down the chain)
                        im = im[:im.shape[0] // 2 * 2, :im.shape[1] // 2 * 2]
                        im = im.reshape(im.shape[0] // 2, 2, im.shape[1] // 2, 2, 3).mean((1, 3))
                    img[j] = im
                    outs[j][k] = from_image(im, raw[0])
            else:                                                     # the packed tail: block by block in place
                blocks = np.stack([decode_blocks(r) * s for r, s in zip(raw, self.scales)], 2)   # (n, 16, 3, 3)
                c_, d_, _ = gears3_coefficients(blocks, top)
                outs[0][k] = encode_blocks(c_)[:len(raw[0])]
                outs[1][k] = encode_blocks(d_)[:len(raw[0])]
        return [Coefficients(self.t[0], outs[0]), Coefficients(self.t[0], outs[1])], \
               [np.ones(3, np.float32), np.full(3, top, np.float32), np.ones(3, np.float32)]


class Coefficients:
    """a stand-in for texture2d.Texture (materialconv.texture_828 reads format, props, mips, mip_bytes)"""

    def __init__(self, like, data):
        self.format, self.props, self.mips, self._data = like.format, like.props, like.mips, data
        self.pkg, self.index = like.pkg, like.index

    def mip_bytes(self, m):
        return self._data.get(m)


def gears3_reference(textures, scales, light_guids=(), owner=0):
    """Gears 3 LODData bytes (one LOD; the component's own tail included) for a texture light map: two coefficient
    textures (object refs) with scale vectors, UVs used as they are (the mesh's light map UVs are atlas UVs)"""
    out = bytearray(struct.pack('>iii', 1, 0, 0))                         # one LOD, no shadow maps / vertex buffers
    out += struct.pack('>i', 2) + struct.pack('>i', len(light_guids)) + b''.join(light_guids)
    refs = list(textures) + [0] * (3 - len(textures))
    for ref, s in zip(refs, scales):
        out += struct.pack('>i', ref) + struct.pack('>3f', *s)
    out += struct.pack('>4f', 1, 1, 0, 0)                                 # coordinate scale, bias
    # then the LOD's own tail, 5 bytes (a Gears 3 level's: zeros). (The vertex writer's four more ints before it are
    # the 1D light map's second, empty, sample array: here they misaligned the component and the load hung)
    out += b'\0' + struct.pack('>i', 0)
    return bytes(out)
