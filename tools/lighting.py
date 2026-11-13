"""Static lighting of StaticMeshComponents (native LODData after the tagged properties), Gears 1 (374) and Gears 3
(828), and conversion of Gears 1's lighting into a Gears 3 per-vertex lightmap. Offline.

Gears 1 LODData: count, then per LOD: ShadowMaps (refs), ShadowVertexBuffers (refs), the light map:
  type 0  none
  type 1  per vertex: LightGuids (count + 16 bytes each), owner ref, bulk {flags, count, size, offset} of 3 FColors
          per vertex (three directional coefficients), then 3 scale vectors (float3)
  type 2  textures: LightGuids (no owner), 3 x {LightMapTexture2D ref, scale float3}, CoordinateScale (2f),
          CoordinateBias (2f); the mesh's lightmap UVs (LightMapCoordinateIndex) address the textures
FColor bytes are A R G B (a big-endian DWORD).
Gears 3 per-vertex light map (one shipped example, SP_Nightmare_01 StaticMeshActor_SMC_110):
  LODData count 1, ShadowMaps 0, ShadowVertexBuffers (count, refs), type 1, LightGuids (count), owner ref,
  bulk {0, vertices, 8 x vertices, file offset} of 2 FColors per vertex, 3 scale vectors (float3), an empty bulk
  header (16 bytes), one byte, then int 0 (the component's own tail)
  The two colours follow Gears 3's texture lightmaps (NormalizedAverageColor, DirectionalMaxComponent): the average
  colour normalized to its largest channel, and the three directional intensities.
usage: python -P lighting.py <gears1 level package> check     (every component's LODData must parse to the end)"""
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk


class Cursor:
    def __init__(self, d, o=0): self.d, self.o = d, o
    def i32(self): v = struct.unpack_from('>i', self.d, self.o)[0]; self.o += 4; return v
    def f32(self, n): v = struct.unpack_from(f'>{n}f', self.d, self.o); self.o += 4 * n; return v
    def raw(self, n): v = self.d[self.o:self.o + n]; self.o += n; return v


def read_lod_data_374(d, o):
    """[{'type', 'samples' (n,3,3) floats or None, 'textures': [(ref, scale)], 'coord': (scale2, bias2)}], end"""
    c = Cursor(d, o)
    lods = []
    for _ in range(c.i32()):
        lod = {'shadow_maps': [c.i32() for _ in range(c.i32())]}
        lod['shadow_vertex_buffers'] = [c.i32() for _ in range(c.i32())]
        lod.update(read_lightmap_374(c))
        lods.append(lod)
    return lods, c.o


def read_lightmap_374(c):
    """one FLightMapRef at the cursor (static mesh LODs and BSP elements alike)"""
    lm = {}
    t = c.i32(); lm['type'] = t
    if t in (1, 2):
        n = c.i32(); lm['guids'] = c.raw(16 * n)
    if t == 1:
        lm['owner'] = c.i32()
        flags, count, size, off = c.i32(), c.i32(), c.i32(), c.i32()
        if size != 12 * count: raise ValueError(f'1D samples: {count} x {size}')
        raw = np.frombuffer(c.raw(size), dtype=np.uint8).reshape(count, 3, 4)[:, :, 1:].astype(np.float32) / 255
        scale = np.array(c.f32(9)).reshape(3, 3)
        lm['samples'] = raw * scale[None]                            # (vertices, coefficient, rgb)
    elif t == 2:
        lm['textures'] = [(c.i32(), np.array(c.f32(3))) for _ in range(3)]
        lm['coord'] = (c.f32(2), c.f32(2))
    elif t != 0:
        raise ValueError(f'light map type {t}')
    return lm


_texture_cache = {}


def sample_lightmap_374(p, lm, uv):
    """(n, 3, 3) coefficients of a type-2 light map at lightmap UVs (n, 2) (before CoordinateScale / Bias)"""
    import texture2d
    (sx, sy), (bx, by) = lm['coord']
    uv = np.asarray(uv, np.float64) * [sx, sy] + [bx, by]
    out = np.zeros((len(uv), 3, 3), np.float32)
    for k, (ref, scale) in enumerate(lm['textures']):
        if ref <= 0: return None
        if (p.filename, ref) not in _texture_cache:
            t = texture2d.Texture(p, ref - 1)
            mip = next(j for j, mm in enumerate(t.mips) if not mm['flags'] & 0x20)
            _texture_cache[(p.filename, ref)] = t.rgba(mip).astype(np.float32) / 255
        img = _texture_cache[(p.filename, ref)]
        h, w = img.shape[:2]
        x = np.clip((uv[:, 0] * w).astype(int), 0, w - 1); y = np.clip((uv[:, 1] * h).astype(int), 0, h - 1)
        out[:, k] = img[y, x, :3] * scale
    return out


def vertex_lighting_374(p, lod, mesh):
    """per-vertex (n, 3, 3) coefficients for LOD 0 from a Gears 1 light map (textures sampled at the vertices'
    lightmap UVs), or None"""
    if lod['type'] == 1: return lod['samples']
    if lod['type'] != 2: return None
    import texture2d
    m = mesh['lods'][0]
    uvs, stride, n, data = m['interleaved']
    ch = int(mesh['props'].get('LightMapCoordinateIndex', 1))
    if ch >= uvs: return None
    a = np.frombuffer(data, dtype=np.uint8).reshape(n, stride)
    uv = np.frombuffer(a[:, 28 + 8 * ch:36 + 8 * ch].tobytes(), dtype='>f4').reshape(n, 2).astype(np.float64)
    (sx, sy), (bx, by) = lod['coord']
    uv = uv * [sx, sy] + [bx, by]
    out = np.zeros((n, 3, 3), np.float32)
    for k, (ref, scale) in enumerate(lod['textures']):
        if ref <= 0: return None
        t = texture2d.Texture(p, ref - 1)
        mip = next(j for j, mm in enumerate(t.mips) if not mm['flags'] & 0x20)
        img = t.rgba(mip).astype(np.float32) / 255
        h, w = img.shape[:2]
        x = np.clip((uv[:, 0] * w).astype(int), 0, w - 1); y = np.clip((uv[:, 1] * h).astype(int), 0, h - 1)
        out[:, k] = img[y, x, :3] * scale
    return out


def gears3_vertex_lightmap(samples, owner, light_guids=()):
    """Gears 3 LODData bytes (with the component's own tail) for per-vertex coefficients (n, 3, rgb); the bulk data's
    file offset is 0 (inline data is read in place)"""
    n = len(samples)
    # range: 8 bits per channel under one scale per component, so a few vertices next to a lamp (values of 10+) would
    # leave everything else near 0; Gears 1 clipped such highs on screen anyway
    samples = np.clip(samples, 0, 2.0)
    avg = samples.mean(1)                                            # (n, rgb)
    peak = np.maximum(avg.max(1, keepdims=True), 1e-6)
    norm = np.clip(avg / peak, 0, 1)                                 # normalized average colour
    direc = samples.max(2)                                           # (n, 3): the largest channel per direction
    top = max(float(np.percentile(direc, 99)), 1e-6)
    out = bytearray(struct.pack('>iiii', 1, 0, 0, 1))                # one LOD, no shadow maps/buffers, type 1D
    # the lights counted as baked into this light map: UE3 lights a primitive dynamically with every light whose
    # GUID its light map does not list (with none listed, every static light of the level is dynamic: 2 fps)
    out += struct.pack('>i', len(light_guids)) + b''.join(light_guids) + struct.pack('>i', owner)
    cols = np.zeros((n, 2, 4), np.uint8)
    cols[:, 0, 1:] = np.round(norm * 255)
    cols[:, 1, 1:] = np.round(np.clip(direc / top, 0, 1) * 255)
    out += struct.pack('>iiii', 0, n, 8 * n, 0) + cols.tobytes()
    out += struct.pack('>9f', 1, 1, 1, top, top, top, 1, 1, 1)
    out += struct.pack('>iiii', 0, 0, 0, 0) + b'\0' + struct.pack('>i', 0)
    return bytes(out)


if __name__ == '__main__':
    import collections, levelinject
    p = upk.Package(sys.argv[1])
    types, bad = collections.Counter(), 0
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'StaticMeshComponent': continue
        d = p.export_data(i)
        try:
            o, pr, end = levelinject.props_at(p, d, (8,))
            lods, stop = read_lod_data_374(d, end)
            rest = len(d) - stop
            types[(tuple(l['type'] for l in lods), rest)] += 1
        except Exception as ex:
            bad += 1
            if bad <= 5: print('fail', p.path(i), ex)
    print(f'{sum(types.values())} parsed, {bad} failed; (light map types, bytes after): {dict(types)}')


# ---- Gears 1's dynamic point lights, baked ----
# Gears 1 bakes only its directional and sky lights into light maps (UseDirectLightMap); its point lights (368 on
# MP_Gridlock) are dynamic and, there, without shadow maps. Gears 3 has none of them, so their light is baked into the
# converted per-vertex light maps: colour x brightness x (1 - d^2 / r^2)^2 (UE3's radial falloff, exponent 2) x N.L.

def g1_point_lights(p):
    """(positions (k, 3), radii (k,), colours x brightness (k, 3)) of a Gears 1 level's PointLights"""
    import levelinject
    pos, rad, col = [], [], []
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'PointLight' or p.path(i).count('.') != 2 or not p.path(i).startswith('TheWorld'): continue
        try:
            o, pr, end = levelinject.props_at(p, p.export_data(i), (32, 4, 8))
            a = {n: v for n, t, k, v in pr}
            comp = a.get('LightComponent', 0)
            if comp <= 0: continue
            o, cpr, end = levelinject.props_at(p, p.export_data(comp - 1), (8, 4, 12))
        except ValueError:
            continue
        c = {n: v for n, t, k, v in cpr}
        if 'CachedParentToWorld' in c: where = struct.unpack('>16f', c['CachedParentToWorld'][1])[12:15]
        elif 'Location' in a: where = struct.unpack('>3f', a['Location'][1])
        else: continue
        argb = c['LightColor'][1] if 'LightColor' in c else b'\xff\xff\xff\x00'
        rgb = np.array([argb[2], argb[1], argb[0]], np.float32) / 255           # FColor stored B G R A
        pos.append(where); rad.append(float(c.get('Radius', 1024.0)))
        col.append(rgb * float(c.get('Brightness', 1.0)))
    return np.array(pos, np.float64).reshape(-1, 3), np.array(rad), np.array(col, np.float32).reshape(-1, 3)


def bake_point_lights(lights, P, N):
    """(n, 3) rgb light at world positions P (n, 3) with normals N (n, 3)"""
    pos, rad, col = lights
    out = np.zeros((len(P), 3), np.float32)
    if len(P) == 0 or len(pos) == 0: return out
    lo, hi = P.min(0), P.max(0)
    near = np.all((pos + rad[:, None] >= lo) & (pos - rad[:, None] <= hi), 1)       # lights reaching the box
    for k in np.nonzero(near)[0]:
        L = pos[k] - P
        d2 = (L * L).sum(1)
        att = np.clip(1 - d2 / rad[k] ** 2, 0, 1) ** 2
        lam = np.clip((L * N).sum(1) / np.sqrt(np.maximum(d2, 1e-6)), 0, 1)
        out += (att * lam)[:, None] * col[k]
    return out


# ---- a chapter's lights not in Gears 1's light maps, baked ----
# Gears 1 bakes most of a level's point and spot lights into its light maps too, not only its directional and sky
# lights: each light map lists the GUIDs of the lights in it (the prison's cell block: 255 of 288 point lights, 73 of
# 83 spot lights). Baking every point light again on top of them lit those surfaces twice; the lights in no light map
# are the ones Gears 1 drew dynamically. And a section's lights are not always in its own package: the cell block's
# meshes (SP_Prison_S02_Meshes) are lit by SP_Prison_S02's. So the chapter's lights are gathered once from all its
# packages (spot lights with their cones), those any light map lists are left out, and every section bakes the rest.

def g1_lights(p, classes=('PointLight', 'SpotLight')):
    """a Gears 1 level's PointLight / SpotLight actors (classes: not the Toggleable / Movable ones, which scripts switch
    and move: copied as lights for characters, not baked into the walls): list of dicts {pos, radius,
    colour (x brightness), dir (spot: its X axis), cos_inner, cos_outer (point: -2), guids}"""
    import levelinject
    out = []
    for i, e in enumerate(p.exports):
        cn = p.class_name(e['class'])
        if cn not in classes: continue
        if p.path(i).count('.') != 2 or not p.path(i).startswith('TheWorld'): continue
        try:
            o, pr, end = levelinject.props_at(p, p.export_data(i), (32, 4, 8))
            a = {n: v for n, t, k, v in pr}
            comp = a.get('LightComponent', 0)
            if comp <= 0: continue
            o, cpr, end = levelinject.props_at(p, p.export_data(comp - 1), (8, 4, 12))
        except ValueError:
            continue
        c = {n: v for n, t, k, v in cpr}
        en = c.get('bEnabled', True)                     # (a bool, or (type, bytes))
        if en is False or (isinstance(en, tuple) and not any(bytes(en[1]))): continue
        m = struct.unpack('>16f', c['CachedParentToWorld'][1]) if 'CachedParentToWorld' in c else None
        if m: where, axis = m[12:15], np.array(m[0:3], np.float64)
        elif 'Location' in a: where, axis = struct.unpack('>3f', a['Location'][1]), np.array([1.0, 0, 0])
        else: continue
        argb = c['LightColor'][1] if 'LightColor' in c else b'\xff\xff\xff\x00'
        rgb = np.array([argb[2], argb[1], argb[0]], np.float32) / 255           # FColor stored B G R A
        L = {'pos': where, 'radius': float(c.get('Radius', 1024.0)), 'colour': rgb * float(c.get('Brightness', 1.0)),
             'dir': axis / max(np.linalg.norm(axis), 1e-6), 'cos_inner': -2.0, 'cos_outer': -2.0,
             'guids': {bytes(c[k][1]) for k in ('LightGuid', 'LightmapGuid') if k in c}}
        if cn.startswith('SpotLight'):
            inner = float(c.get('InnerConeAngle', 0.0)); outer = float(c.get('OuterConeAngle', 44.0))
            L['cos_inner'], L['cos_outer'] = np.cos(np.radians(inner)), np.cos(np.radians(outer))
        out.append(L)
    return out


def baked_light_guids(p):
    """the GUIDs every light map of a Gears 1 level lists (static meshes' LODs and the BSP's ModelComponent elements)"""
    import levelinject
    out = set()
    for i, e in enumerate(p.exports):
        cn = p.class_name(e['class'])
        if cn not in ('StaticMeshComponent', 'ModelComponent'): continue
        d = p.export_data(i)
        try:
            o, pr, end = levelinject.props_at(p, d, (8, 4))
            c = Cursor(d, end)
            if cn == 'StaticMeshComponent':
                for _ in range(c.i32()):
                    c.raw(4 * c.i32()); c.raw(4 * c.i32())
                    lm = read_lightmap_374(c)
                    g = lm.get('guids', b'')
                    out.update(g[k:k + 16] for k in range(0, len(g), 16))
            else:
                c.i32(); c.i32()                                         # model, zone
                for _ in range(c.i32()):
                    lm = read_lightmap_374(c)
                    g = lm.get('guids', b'')
                    out.update(g[k:k + 16] for k in range(0, len(g), 16))
                    c.i32(); c.i32(); c.raw(2 * c.i32()); c.raw(4 * c.i32()); c.raw(16 * c.i32())
        except Exception:
            continue
    return out


def unbaked_lights(packages):
    """the lights of these Gears 1 packages that no light map among them lists, as arrays for bake_lights:
    (positions, radii, colours, dirs, cos_inner, cos_outer), and (total, kept) counts"""
    lights, baked = [], set()
    for p in packages:
        lights += g1_lights(p)
        baked |= baked_light_guids(p)
    kept = [L for L in lights if not (L['guids'] & baked)]
    arr = lambda k, dt=np.float64: np.array([L[k] for L in kept], dt)
    return ((arr('pos').reshape(-1, 3), arr('radius'), arr('colour', np.float32).reshape(-1, 3), arr('dir').reshape(-1, 3),
             arr('cos_inner'), arr('cos_outer')), (len(lights), len(kept)))


LIGHT_CLEARANCE = 24.0


class Occluders:
    """the level's triangles (m, 3, 3) world space, for shadow tests from baked lights: Gears 1 drew the lights its
    light maps leave out dynamically, with shadows; baked per vertex without them every such light shone through the
    walls, pipes and bars round it (the prison's cell block lit flat, "washed out" against Gears 1)"""
    def __init__(self, tris):
        self.tris = np.asarray(tris, np.float64).reshape(-1, 3, 3)
        self.lo, self.hi = self.tris.min(1), self.tris.max(1)

    def visible(self, P, light, cell=256.0):
        """(n,) bool: the segment from each point (moved 2 units off its surface by the caller) to the light meets no
        triangle (Moller-Trumbore, t in (0.001, 0.999)); the points in groups by cell (each group's rays tested against
        the triangles in the box round it and the light)"""
        P = np.asarray(P, np.float64)
        out = np.ones(len(P), bool)
        if not len(P) or not len(self.tris): return out
        key = np.floor(P / cell).astype(np.int64)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        for g in range(inv.max() + 1):
            sel = np.nonzero(inv.ravel() == g)[0]
            out[sel] = self._visible(P[sel], light)
        return out

    def _visible(self, P, light, chunk=200000):
        out = np.ones(len(P), bool)
        lo = np.minimum(P.min(0), light) - 1; hi = np.maximum(P.max(0), light) + 1
        sel = np.nonzero(np.all((self.hi >= lo) & (self.lo <= hi), 1))[0]
        if not len(sel): return out
        T = self.tris[sel]
        v0, e1, e2 = T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
        step = max(1, chunk // len(T))
        for s in range(0, len(P), step):
            O = P[s:s + step]; D = light - O                                   # (k, 3), segment = O + t D
            # (hits within LIGHT_CLEARANCE of the light ignored: Gears 1 put lights inside their lamps' meshes)
            tmax = np.clip(1 - LIGHT_CLEARANCE / np.maximum(np.linalg.norm(D, axis=1), 1e-6), 0, 0.999)[:, None]
            h = np.cross(D[:, None, :], e2[None])                               # (k, m, 3)
            a = (e1[None] * h).sum(2)
            ok = np.abs(a) > 1e-9
            f = np.where(ok, 1 / np.where(ok, a, 1), 0)
            sv = O[:, None, :] - v0[None]
            u = f * (sv * h).sum(2)
            q = np.cross(sv, e1[None])
            v = f * (D[:, None, :] * q).sum(2)
            t = f * (e2[None] * q).sum(2)
            hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-3) & (t < tmax)
            out[s:s + step] = ~hit.any(1)
        return out


def bake_lights(lights, P, N, occluders=None):
    """(n, 3) rgb light at world positions P (n, 3) with normals N (n, 3) from unbaked_lights' arrays: UE3's radial
    falloff (exponent 2) x N.L, spot lights x their cone ((cos - cos_outer) / (cos_inner - cos_outer), squared);
    occluders (Occluders): each light's contribution only where the segment to it is clear"""
    pos, rad, col, dirs, ci, co = lights
    out = np.zeros((len(P), 3), np.float32)
    if len(P) == 0 or len(pos) == 0: return out
    lo, hi = P.min(0), P.max(0)
    near = np.all((pos + rad[:, None] >= lo) & (pos - rad[:, None] <= hi), 1)
    for k in np.nonzero(near)[0]:
        L = pos[k] - P
        d2 = (L * L).sum(1)
        inv = 1 / np.sqrt(np.maximum(d2, 1e-6))
        att = np.clip(1 - d2 / rad[k] ** 2, 0, 1) ** 2
        lam = np.clip((L * N).sum(1) * inv, 0, 1)
        if co[k] > -1.5:
            cosang = -(L * dirs[k]).sum(1) * inv
            att = att * np.clip((cosang - co[k]) / max(ci[k] - co[k], 1e-4), 0, 1) ** 2
        w = att * lam
        if occluders is not None and (w > 0).any():
            lit = np.nonzero(w > 0)[0]
            w[lit] *= occluders.visible(P[lit] + N[lit] * 2.0, pos[k])
        out += w[:, None] * col[k]
    return out
