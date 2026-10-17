"""Gears of War 1 BSP (UModel, package version 374) read and rebuilt as static mesh chunks for Gears 3 (offline).

UModel serial data after the (empty) tagged properties:
  Bounds (7 floats), Vectors (count + float3), Points (count + float3),
  Nodes (count + 68-byte FBspNode: plane (4f), zone mask (u64), iVertPool, iSurf, iVertexIndex, component index,
         component node index, component element index, iBack, iFront, {iZone[2], NumVertices, NodeFlags} bytes,
         iLeaf[2]),
  an int (unknown, 0x2A68 on MP_Gridlock's world model),
  Surfs (count + 52-byte FBspSurf: Material ref, PolyFlags, pBase, vNormal, vTextureU, vTextureV, iBrushPoly,
         Actor ref, plane (4f), ShadowMapScale),
  Verts (count + 24-byte FVert: pVertex, iSide, 16 bytes of shadow map coordinates),
  NumSharedSides, NumZones, ... (zones, polys, leaves: not needed for rendering)
A node with vertices is a convex polygon: Verts[iVertPool .. + NumVertices] -> Points, drawn with Surfs[iSurf]:
material, normal, texture axes. UVs as UE3 builds BSP vertex buffers: ((P - Points[pBase]) . Vectors[vTextureU]) / 128
(and V); each polygon's UVs are shifted by whole tiles toward 0 (half floats keep their precision).
Triangles are wound like the meshes (cross(v1 - v0, v2 - v0) opposite the normal: 99.9% of Gears 1's triangles).
usage: python -P bspconv.py <gears1 level package> [obj out]"""
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, lighting, levelinject

PF_INVISIBLE, PF_PORTAL = 0x00000001, 0x04000000


def read_model_374(p, index):
    d = p.export_data(index)
    pr, o = p.properties(d, 4)
    def cnt():
        nonlocal o
        v = struct.unpack_from('>i', d, o)[0]; o += 4; return v
    o += 28                                                       # bounds
    n = cnt(); vectors = np.frombuffer(d[o:o + 12 * n], '>f4').reshape(n, 3).astype(np.float64); o += 12 * n
    n = cnt(); points = np.frombuffer(d[o:o + 12 * n], '>f4').reshape(n, 3).astype(np.float64); o += 12 * n
    n = cnt(); nodes = []
    for k in range(n):
        b = d[o:o + 68]; o += 68
        vp, surf = struct.unpack_from('>ii', b, 24)
        nodes.append({'plane': struct.unpack_from('>4f', b, 0), 'vert_pool': vp, 'surf': surf,
                      'num_vertices': b[58], 'flags': b[59]})
    o += 4                                                        # (unknown int)
    n = cnt(); surfs = []
    for k in range(n):
        v = struct.unpack_from('>iIiiiiii4ff', d, o); o += 52
        surfs.append({'material': v[0], 'flags': v[1], 'base': v[2], 'normal': v[3], 'u': v[4], 'v': v[5],
                      'brush_poly': v[6], 'actor': v[7]})
    n = cnt(); vr = d[o:o + 24 * n]; o += 24 * n
    verts = np.frombuffer(vr, '>i4').reshape(n, 6)[:, 0].copy()
    shadow_uv = np.frombuffer(vr, '>f4').reshape(n, 6)[:, 2:4].astype(np.float64)   # FVert.ShadowTexCoord
    return {'vectors': vectors, 'points': points, 'nodes': nodes, 'surfs': surfs, 'verts': verts, 'shadow_uv': shadow_uv}


def node_lightmaps(p, model_ref):
    """{node index: light map} from the level's ModelComponents (Gears 1): after the tagged properties, the Model
    ref, ZoneIndex, Elements (count; each: light map (as a static mesh LOD's), Component ref, Material ref, Nodes
    (count + u16), ShadowMaps (count + refs), IrrelevantLights (count + GUIDs)), then the component's own fields"""
    out = {}
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'ModelComponent' or not p.path(i).startswith('TheWorld'): continue
        d = p.export_data(i)
        try:
            o, pr, end = levelinject.props_at(p, d, (8, 4))
            c = lighting.Cursor(d, end)
            if c.i32() != model_ref: continue
            c.i32()                                                   # zone
            for _ in range(c.i32()):
                lm = lighting.read_lightmap_374(c)
                c.i32(); c.i32()                                      # component, material
                n = c.i32(); nodes = struct.unpack(f'>{n}H', c.raw(2 * n))
                c.raw(4 * c.i32()); c.raw(16 * c.i32())
                for nd in nodes: out[nd] = lm
        except Exception as ex:
            print('  ModelComponent', p.path(i), 'not read:', ex)
    return out


def world_model(p):
    """the level's BSP: the largest Model under TheWorld.PersistentLevel"""
    models = [i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'Model' and p.path(i).startswith('TheWorld')]
    return max(models, key=lambda i: p.exports[i]['size'])


def polygons(m, p=None, lightmaps=None):
    """[(surf index, points (k, 3), uvs (k, 2), normal, light (k, 3, 3) or None)] for every visible node polygon;
    light: the node's light map sampled at its vertices (with p and node_lightmaps())"""
    out = []
    for ni, nd in enumerate(m['nodes']):
        k = nd['num_vertices']
        if k < 3: continue
        s = m['surfs'][nd['surf']]
        if s['flags'] & (PF_INVISIBLE | PF_PORTAL): continue
        pts = m['points'][m['verts'][nd['vert_pool']:nd['vert_pool'] + k]]
        base, U, V = m['points'][s['base']], m['vectors'][s['u']], m['vectors'][s['v']]
        uv = np.stack([(pts - base) @ U, (pts - base) @ V], 1) / 128.0
        uv -= np.floor(uv.min(0))
        light = None
        lm = lightmaps.get(ni) if lightmaps else None
        if lm and lm['type'] == 2:
            light = lighting.sample_lightmap_374(p, lm, m['shadow_uv'][nd['vert_pool']:nd['vert_pool'] + k])
        out.append((nd['surf'], pts, uv, m['vectors'][s['normal']], light))
    return out


def pack_normal(v):
    """unit vector -> packed bytes as stored (w z y x), w = 255"""
    q = np.clip(np.round((v + 1) * 127.5), 0, 255).astype(np.uint8)
    return np.stack([np.full(len(v), 255, np.uint8), q[:, 2], q[:, 1], q[:, 0]], 1)


def chunk_meshes(m, polys, cell=4096.0, max_vertices=60000):
    """group polygons into spatial cells -> [(surf indices per element, synthetic mesh dict in staticmesh.read's Gears 3
    form)], ready for meshconv.to_828"""
    cells = {}
    for poly in polys:
        key = tuple(np.floor(poly[1].mean(0) / cell).astype(int))
        cells.setdefault(key, []).append(poly)
    out = []
    for key, group in sorted(cells.items()):
        while group:
            part, nv = [], 0
            while group and nv + len(group[-1][1]) <= max_vertices:
                part.append(group.pop()); nv += len(part[-1][1])
            out.append(build_mesh(m, part))
    return out


def blocking_volumes(p):
    """a Gears 1 level's BlockingVolumes as collision meshes ({'name', mesh dict like build_mesh's}): their brushes'
    convex elements (BrushComponent.BrushAggGeom.ConvexElems: VertexData, FaceTriData), placed in the world
    (Location, Rotation, DrawScale / DrawScale3D, PrePivot), both windings. Gears 1 moved pawns over BSP and these
    volumes (ramps over stairs, walls round rubble), its meshes mostly not blocking pawns."""
    import levelinject, materialconv
    out = []
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'BlockingVolume' or p.path(i).count('.') != 2: continue
        try:
            o, pr, end = levelinject.props_at(p, p.export_data(i))
            a = {n: v for n, t, k, v in pr}
            bc = a.get('BrushComponent', 0)
            if bc <= 0: continue
            recs, _ = materialconv.raw_props(p, p.export_data(bc - 1), 8)
            agg = next((r[4] for r in recs if r[0] == 'BrushAggGeom'), None)
            if agg is None: continue
            sub, _ = materialconv.raw_props(p, agg, 0)
            ce = next((r[4] for r in sub if r[0] == 'ConvexElems'), None)
            if ce is None: continue
        except Exception:
            continue
        vec = lambda v, default: np.array(struct.unpack('>3f', v[1]) if v else default)
        loc, pivot = vec(a.get('Location'), (0, 0, 0)), vec(a.get('PrePivot'), (0, 0, 0))
        scale = vec(a.get('DrawScale3D'), (1, 1, 1)) * a.get('DrawScale', 1.0)
        rot = struct.unpack('>3i', a['Rotation'][1]) if a.get('Rotation') else (0, 0, 0)
        R = levelinject.rotation_matrix(*rot)
        P, idx = [], []
        o_ = 4
        for _ in range(struct.unpack_from('>i', ce, 0)[0]):
            el, o_ = materialconv.raw_props(p, ce, o_)
            r_ = {x[0]: x[4] for x in el}
            if 'VertexData' not in r_ or 'FaceTriData' not in r_: continue
            nv = struct.unpack_from('>i', r_['VertexData'], 0)[0]
            V = np.array(struct.unpack_from(f'>{3 * nv}f', r_['VertexData'], 4), np.float64).reshape(nv, 3)
            nt = struct.unpack_from('>i', r_['FaceTriData'], 0)[0]
            T = struct.unpack_from(f'>{nt}i', r_['FaceTriData'], 4)
            base = len(P) and sum(len(x) for x in P)
            P.append(((V - pivot) * scale) @ R + loc)
            for t in range(0, nt - 2, 3):
                a_, b_, c_ = base + T[t], base + T[t + 1], base + T[t + 2]
                idx += [a_, b_, c_, a_, c_, b_]
        if not idx: continue
        P = np.concatenate(P); n = len(P)
        # thin horizontal slabs (under 48 units tall) left out: Gears 1 laid them over floors and thresholds the
        # level's geometry already has; as boxes their tops were steps Gears 3's pawns could not climb (the prison
        # cell's doorway: 26 units)
        if P[:, 2].max() - P[:, 2].min() < 48: continue
        tx = pack_normal(np.repeat(np.array([[1.0, 0, 0]]), n, 0)); tz = pack_normal(np.repeat(np.array([[0, 0, 1.0]]), n, 0))
        uvh = np.zeros((n, 1, 2), '>f2')
        verts = np.concatenate([tx, tz, np.frombuffer(uvh.tobytes(), np.uint8).reshape(n, 4)], 1)
        lo, hi = P.min(0), P.max(0)
        centre, ext = (lo + hi) / 2, (hi - lo) / 2
        mesh = {'version': 828, 'props': {}, 'bounds': (*centre, *ext, float(np.linalg.norm(ext))),
                'lods': [{'positions': (12, n, (12, n, P.astype('>f4').tobytes())),
                          'vertices': (1, 12, n, 0, (12, n, verts.tobytes())),
                          'indices': (2, len(idx), struct.pack(f'>{len(idx)}H', *idx)),
                          'wireframe': (2, 0, b''),
                          'elements': [{'first_index': 0, 'triangles': len(idx) // 3, 'min_vertex': 0, 'max_vertex': n - 1}],
                          'materials_374': [0]}]}
        out.append({'name': e['name'], 'mesh': mesh})
    return out


def build_mesh(m, polys):
    by_surf_material = {}
    for poly in polys:
        by_surf_material.setdefault(m['surfs'][poly[0]]['material'], []).append(poly)
    P, TX, TZ, UV, idx, elements, mats, L = [], [], [], [], [], [], [], []
    base = 0
    for mat, group in by_surf_material.items():
        first = len(idx)
        for surf, pts, uv, normal, light in group:
            L.append(light if light is not None else np.full((len(pts), 3, 3), np.nan, np.float32))
            k = len(pts)
            s = m['surfs'][surf]
            U, V = m['vectors'][s['u']], m['vectors'][s['v']]
            tx = U / (np.linalg.norm(U) or 1)
            n_ = normal / (np.linalg.norm(normal) or 1)
            P.append(pts); UV.append(uv)
            TX.append(pack_normal(np.repeat(tx[None], k, 0)))
            tz = pack_normal(np.repeat(n_[None], k, 0))
            if np.cross(n_, tx) @ V < 0: tz[:, 0] = 0          # binormal sign
            TZ.append(tz)
            for j in range(1, k - 1):                             # fan, wound like Gears 1's meshes
                a, b, c = base, base + j, base + j + 1
                if np.cross(pts[j] - pts[0], pts[j + 1] - pts[0]) @ n_ > 0: b, c = c, b
                idx += [a, b, c]
            base += k
        elements.append({'first_index': first, 'triangles': (len(idx) - first) // 3,
                         'min_vertex': 0, 'max_vertex': base - 1})
        mats.append(mat)
    P = np.concatenate(P); n = len(P)
    uvh = np.concatenate(UV).astype('>f2').reshape(n, 1, 2)
    verts = np.concatenate([np.concatenate(TX), np.concatenate(TZ), np.frombuffer(uvh.tobytes(), np.uint8).reshape(n, 4)], 1)
    lo, hi = P.min(0), P.max(0)
    centre, ext = (lo + hi) / 2, (hi - lo) / 2
    mesh = {'version': 828, 'props': {},
            'bounds': (*centre, *ext, float(np.linalg.norm(ext))),
            'lods': [{'positions': (12, n, (12, n, P.astype('>f4').tobytes())),
                      'vertices': (1, 12, n, 0, (12, n, verts.tobytes())),
                      'indices': (2, len(idx), struct.pack(f'>{len(idx)}H', *idx)),
                      'wireframe': (2, 0, b''), 'elements': elements, 'materials_374': mats}],
            'light': np.concatenate(L)}                          # per vertex (NaN where the node had no light map)
    return mats, mesh


if __name__ == '__main__':
    p = upk.Package(sys.argv[1])
    mi = world_model(p)
    m = read_model_374(p, mi)
    lms = node_lightmaps(p, mi + 1)
    polys = polygons(m, p, lms)
    print(f'{len(lms)} nodes with light maps; {sum(1 for x in polys if x[4] is not None)} polygons lit')
    print(f"{p.path(mi)}: {len(m['vectors'])} vectors, {len(m['points'])} points, {len(m['nodes'])} nodes, "
          f"{len(m['surfs'])} surfs, {len(m['verts'])} verts; {len(polys)} visible polygons, "
          f"{sum(len(x[1]) - 2 for x in polys)} triangles, {len(set(m['surfs'][x[0]]['material'] for x in polys))} materials")
    if len(sys.argv) > 2:
        with open(sys.argv[2], 'w') as f:
            base = 1
            for surf, pts, uv, nrm, light in polys:
                for q in pts: f.write('v %g %g %g\n' % tuple(q))
                for j in range(1, len(pts) - 1): f.write(f'f {base} {base + j + 1} {base + j}\n')
                base += len(pts)
