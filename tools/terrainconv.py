"""Gears 1 terrain (version 374 Terrain actors) as BSP-style polygons, so the level converter's BSP path (bspconv:
chunked into meshes, materials converted, collision for pawns) carries it. Gears 1's Embry Square floors are terrain.

A Terrain actor: NumVerticesX x NumVerticesY heights (uint16, after its tagged properties), local z = (h - 32768) / 128,
world = Location + DrawScale3D (Engine.Default__Terrain: 256 each) x local, so a patch is 256 units across; per vertex
info bits (1: a hole); Layers (TerrainLayer: Setup = TerrainLayerSetup, AlphaMapIndex), each layer's alpha map (a byte
per vertex). A layer's setup names TerrainMaterials (Material, MappingScale, MappingPanU/V: the texture repeats every
MappingScale patches). Gears 1 draws the layers blended, later ones over earlier; here each patch takes the material of
the last layer covering more than half of it (no blend).

python -P tools/terrainconv.py <Gears 1 package> lists the terrains (size, height range, layers)
"""
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import upk                      # noqa: E402
import levelinject              # noqa: E402
import materialconv             # noqa: E402

TERRAIN_SCALE = 256.0           # Engine.Default__Terrain DrawScale3D (Gears 1)
TID_VISIBILITY_OFF = 1          # FTerrainInfoData: the vertex's quad is a hole
LAYER_COVER = 0.5               # a layer's mean alpha over a patch's corners that makes it the patch's material


def _ref(p, v):
    return struct.unpack('>i', v)[0] if isinstance(v, (bytes, bytearray)) else v


def _props(p, i, starts=(4, 8, 12, 32)):
    try:
        return {n: v for n, t, k, v in levelinject.props_at(p, p.export_data(i), starts)[1]}
    except ValueError:
        return {}


def _array(p, v):
    """the elements (dicts of raw properties: name -> (type, bytes)) of an array of structs"""
    n = struct.unpack_from('>i', v)[0]; q = 4; out = []
    for _ in range(n):
        el, q = materialconv.raw_props(p, v, q)
        out.append({x[0]: (x[1], x[4]) for x in el})
    return out


def layer_material(p, setup):
    """(material ref, mapping scale, pan u, pan v) of a TerrainLayerSetup's first material, else None"""
    if setup <= 0: return None                      # an import: not followed
    a = _props(p, setup - 1)
    mats = _array(p, a['Materials']) if isinstance(a.get('Materials'), (bytes, bytearray)) else []
    for m in mats:
        tm = _ref(p, m.get('Material', ('', b'\0\0\0\0'))[1])
        if tm <= 0: continue
        t = _props(p, tm - 1)
        mat = t.get('Material', 0)
        if not mat: continue
        return mat, float(t.get('MappingScale', 1.0)) or 1.0, float(t.get('MappingPanU', 0.0)), float(t.get('MappingPanV', 0.0))
    return None


def terrains(p):
    """[{name, nx, ny, heights (ny, nx) local z, holes (ny, nx) bool, origin, scale, layers: [(material info, alpha
    (ny, nx) 0..1)]}] of the package's Terrain actors"""
    out = []
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'Terrain' or e['name'].startswith('Default__'): continue
        d = p.export_data(i)
        try: o, pr, end = levelinject.props_at(p, d, (32, 4, 8, 12, 16, 20, 24, 28))
        except ValueError: continue
        a = {n: v for n, t, k, v in pr}
        nx, ny = a.get('NumVerticesX', 0), a.get('NumVerticesY', 0)
        q = end
        nh = struct.unpack_from('>i', d, q)[0]
        if nh != nx * ny or nh == 0: continue
        H = np.frombuffer(d, '>u2', nh, q + 4).astype(np.float64).reshape(ny, nx); q += 4 + 2 * nh
        ni = struct.unpack_from('>i', d, q)[0]
        info = np.frombuffer(d, np.uint8, ni, q + 4).reshape(ny, nx) if ni == nh else np.zeros((ny, nx), np.uint8)
        q += 4 + ni
        na = struct.unpack_from('>i', d, q)[0]; q += 4; alphas = []
        for _ in range(na):
            c = struct.unpack_from('>i', d, q)[0]
            alphas.append(np.frombuffer(d, np.uint8, c, q + 4).astype(np.float64).reshape(ny, nx) / 255.0 if c == nh else None)
            q += 4 + c
        layers = []
        for k, el in enumerate(_array(p, a['Layers']) if isinstance(a.get('Layers'), (bytes, bytearray)) else []):
            if el.get('Hidden', ('', b'\0\0\0\0'))[1][:4] != b'\0\0\0\0': continue
            mi = layer_material(p, _ref(p, el.get('Setup', ('', b'\0\0\0\0'))[1]))
            if mi is None: continue
            ai = struct.unpack('>i', el['AlphaMapIndex'][1])[0] if 'AlphaMapIndex' in el else -1
            alpha = alphas[ai] if 0 <= ai < len(alphas) and alphas[ai] is not None else np.ones((ny, nx))
            layers.append((mi, alpha))
        vec = lambda v, dflt: np.array(struct.unpack('>3f', v[1])) if v else np.array(dflt)
        scale = vec(a.get('DrawScale3D'), (TERRAIN_SCALE,) * 3) * a.get('DrawScale', 1.0)
        rot = struct.unpack('>3i', a['Rotation'][1]) if a.get('Rotation') else (0, 0, 0)
        out.append({'name': e['name'], 'nx': nx, 'ny': ny, 'heights': (H - 32768.0) / 128.0,
                    'holes': (info & TID_VISIBILITY_OFF) != 0, 'origin': vec(a.get('Location'), (0.0, 0.0, 0.0)),
                    'scale': scale, 'rotation': levelinject.rotation_matrix(*rot), 'layers': layers})
    return out


def polygons(p, model):
    """bspconv.polygons-style tuples (surf index, points (3, 3), uvs (3, 2), normal, None) for the package's terrains,
    two triangles a patch; the surfaces they name are appended to model['surfs'] (one per material, mapping vectors
    appended to model['vectors'])"""
    out = []
    surf_of = {}
    for t in terrains(p):
        if not t['layers']: continue
        nx, ny = t['nx'], t['ny']
        X, Y = np.meshgrid(np.arange(nx, dtype=np.float64), np.arange(ny, dtype=np.float64))
        local = np.stack([X, Y, t['heights']], -1) * t['scale']
        world = local.reshape(-1, 3) @ t['rotation'] + t['origin']
        world = world.reshape(ny, nx, 3)
        for y in range(ny - 1):
            for x in range(nx - 1):
                if t['holes'][y, x]: continue
                layer = 0
                for k, (mi, alpha) in enumerate(t['layers']):
                    if alpha[y:y + 2, x:x + 2].mean() > LAYER_COVER: layer = k
                mat, mscale, pu, pv = t['layers'][layer][0]
                if mat not in surf_of:
                    vu = len(model['vectors'])
                    model['vectors'] = np.concatenate([model['vectors'], np.array([[1.0, 0, 0], [0, 1.0, 0]]) @ t['rotation']])
                    surf_of[mat] = len(model['surfs'])
                    model['surfs'].append({'material': mat, 'flags': 0, 'base': 0, 'normal': 0, 'u': vu, 'v': vu + 1,
                                           'brush_poly': -1, 'actor': 0, 'terrain': True})
                s = surf_of[mat]
                corners = [(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)]
                for tri in ((0, 1, 2), (0, 2, 3)):
                    pts = np.array([world[corners[c][1], corners[c][0]] for c in tri])
                    uv = np.array([[corners[c][0] / mscale + pu, corners[c][1] / mscale + pv] for c in tri])
                    n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
                    if n[2] < 0: n = -n
                    n /= np.linalg.norm(n) or 1.0
                    out.append((s, pts, uv, n, None))
    return out


def empty_model():
    return {'vectors': np.zeros((0, 3)), 'points': np.zeros((0, 3)), 'nodes': [], 'surfs': [], 'verts': np.zeros(0, int),
            'shadow_uv': np.zeros((0, 2))}


if __name__ == '__main__':
    p = upk.Package(sys.argv[1])
    for t in terrains(p):
        z = t['heights'] * t['scale'][2] + t['origin'][2]
        print(f"{t['name']}: {t['nx']}x{t['ny']} at {t['origin'].round()} scale {t['scale']}, z {z.min():.0f}..{z.max():.0f}, "
              f"{int(t['holes'].sum())} holes, layers {[(p.path(m[0][0] - 1) if m[0][0] > 0 else m[0][0], m[0][1:]) for m in t['layers']]}")
    print(len(polygons(p, empty_model())), 'triangles')
