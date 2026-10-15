"""Gears of War 3 navigation meshes (UNavigationMeshBase, cooked, engine 828): reader (offline).

Serial data after the (empty) tagged properties, as UNavigationMeshBase::Serialize (sub_825B81B0) writes it:
  NavMeshVersionNum, VersionAtGenerationTime (ints; 43, 43 in MP_GridlockG3)
  Verts (count; each: FVector, PolyIndices: count + u16)                  mesh +0x3C (sub_825DF5C0)
  EdgeStorageInfo (count; each 14 bytes: DataOffset u32, DataSize u16, edge class FName (index, number) -- one of
                   FNavMeshEdgeBase, FNavMeshBasicOneWayEdge, FNavMeshCoverSlipEdge, FNavMeshMantleEdge,
                   FNavMeshOneWayBackRefEdge, FNavMeshPathObjectEdge)    mesh +0x4C (sub_825DF808)
  Polys (count; each (sub_825EDC80): PolyVerts count + u16; second u16 array; PolyCenter FVector; PolyNormal
         FVector; BoxBounds FBox (6 floats + valid byte); PolyCover (version >= 586: count + 24-byte cover
         references, sub_82430E90); PolyHeight float (version >= 588))  mesh +0x9C, 136 bytes each in memory
  ... (the rest: being worked out)
usage: python -P navmesh.py <package> [mesh export name]"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk


class Cur:
    def __init__(self, d, o=0): self.d, self.o = d, o
    def i32(self): v = struct.unpack_from('>i', self.d, self.o)[0]; self.o += 4; return v
    def f32(self, n): v = struct.unpack_from(f'>{n}f', self.d, self.o); self.o += 4 * n; return v
    def u16s(self, n): v = struct.unpack_from(f'>{n}H', self.d, self.o); self.o += 2 * n; return v
    def raw(self, n): v = self.d[self.o:self.o + n]; self.o += n; return v


# edge classes (version 43). FNavMeshEdgeBase / BasicOneWay: Vert0, Vert1 (u16), Poly0, Poly1 (u16), EdgeLength
# float, EdgeCenter FVector, two bytes, EdgePerpDir FVector (38 bytes). The others (vtable slot 24 = sub_825C0010)
# carry four vertices (+12, +14, +112, +114) and then:
#   OneWayBackRef (sub_825BFF78): two polygon references (object ref, GUID, u32 poly id: 24 bytes each), u16 +172
#   DropDown (sub_825BEA18): OneWayBackRef + DropHeight float
#   Mantle, CoverSlip (sub_825BFDB8): OneWayBackRef + actor reference (object ref + GUID) + RelItem u32 + object
#                                      ref + MoveDir FVector + u32
#   PathObject (sub_825C3230): OneWayBackRef + actor reference + u32
FOUR_VERTS = {'FNavMeshOneWayBackRefEdge', 'FNavMeshDropDownEdge', 'FNavMeshMantleEdge', 'FNavMeshCoverSlipEdge',
              'FNavMeshPathObjectEdge', 'FNavMeshCrossPylonEdge'}
# FNavMeshCrossPylonEdge (vtable 8215EAC8) serializes as OneWayBackRef (sub_825BFF78): its two polygon references
# name the two pylons (object refs) and their polygons


def read_edge(c, cls):
    e = {'class': cls}
    nv = 4 if cls in FOUR_VERTS else 2
    vs = c.u16s(nv)
    e['verts'] = vs[:2]; e['verts2'] = vs[2:]
    e['polys'] = c.u16s(2)
    e['length'] = c.f32(1)[0]; e['center'] = c.f32(3); e['b56'], e['b64'] = c.raw(2); e['perp'] = c.f32(3)
    if cls in FOUR_VERTS:
        e['poly_refs'] = [c.raw(24), c.raw(24)]; e['u172'] = c.u16s(1)[0]
        if cls == 'FNavMeshDropDownEdge': e['drop_height'] = c.f32(1)[0]
        elif cls in ('FNavMeshMantleEdge', 'FNavMeshCoverSlipEdge'):
            e['actor'] = c.raw(20); e['rel_item'] = c.i32(); e['object'] = c.i32(); e['move_dir'] = c.f32(3); e['u252'] = c.i32()
        elif cls == 'FNavMeshPathObjectEdge':
            e['actor'] = c.raw(20); e['rel_item'] = c.i32()
    return e


def write_edge(out, e):
    cls = e.get('class', 'FNavMeshEdgeBase')
    out.extend(struct.pack(f'>{2 + len(e.get("verts2", ()))}H', *e['verts'], *e.get('verts2', ())))
    out.extend(struct.pack('>2H', *e['polys'])); out.extend(struct.pack('>4f', e['length'], *e['center']))
    out.append(e['b56']); out.append(e['b64']); out.extend(struct.pack('>3f', *e['perp']))
    if cls in FOUR_VERTS:
        out.extend(e['poly_refs'][0]); out.extend(e['poly_refs'][1]); out.extend(struct.pack('>H', e['u172']))
        if cls == 'FNavMeshDropDownEdge': out.extend(struct.pack('>f', e['drop_height']))
        elif cls in ('FNavMeshMantleEdge', 'FNavMeshCoverSlipEdge'):
            out.extend(e['actor']); out.extend(struct.pack('>ii3fi', e['rel_item'], e['object'], *e['move_dir'], e['u252']))
        elif cls == 'FNavMeshPathObjectEdge':
            out.extend(e['actor']); out.extend(struct.pack('>i', e['rel_item']))


def read(p, index):
    d = p.export_data(index)
    pr, o = p.properties(d, 4)
    c = Cur(d, o)
    m = {'version': c.i32(), 'gen_version': c.i32()}
    m['verts'] = [(c.f32(3), c.u16s(c.i32())) for _ in range(c.i32())]
    m['edge_info'] = [struct.unpack('>IHii', c.raw(14)) for _ in range(c.i32())]
    polys = []
    for _ in range(c.i32()):
        q = {'verts': c.u16s(c.i32()), 'second': c.u16s(c.i32()), 'center': c.f32(3), 'normal': c.f32(3),
             'box': c.f32(6), 'box_valid': c.raw(1)[0]}
        q['cover'] = [c.raw(24) for _ in range(c.i32())]
        q['height'] = c.f32(1)[0]
        polys.append(q)
    m['polys'] = polys
    m['local_to_world'] = c.f32(16); m['world_to_local'] = c.f32(16)
    m['segments'] = [c.u16s(3) for _ in range(c.i32())]        # 6-byte records (border edge segments?)
    m['bounds'] = c.f32(6); m['bounds_valid'] = c.raw(1)[0]
    # edges, in EdgeStorageInfo order, each by its class's Serialize (vtable slot 23); FNavMeshEdgeBase
    # (sub_825BFAA8 after slot 24, sub_825BFCD0): Vert0, Vert1 (u16), Poly0, Poly1 (u16, +106/+108), EdgeLength
    # (float +28), EdgeCenter (FVector +32), byte +56, byte +64 (version >= 30), EdgePerpDir (FVector +44, >= 41)
    edges = []
    for info in m['edge_info']:
        edges.append(read_edge(c, p.fname(info[2], info[3])))
    m['edges'] = edges
    m['edges_raw_tail'] = d[c.o:]                            # (nothing, once every class is read)
    m['props_end'] = o
    m['rest_at'] = c.o
    m['size'] = len(d)
    return m, d


def write(m, head):
    """serial data of a UNavigationMeshBase from read()'s dict (head: the export's bytes up to the end of its tagged
    properties); edge_info entries are (offset, size, name index, name number)"""
    out = bytearray(head)
    i32 = lambda v: out.extend(struct.pack('>i', v))
    f32 = lambda *v: out.extend(struct.pack(f'>{len(v)}f', *v))
    u16 = lambda *v: out.extend(struct.pack(f'>{len(v)}H', *v))
    i32(m['version']); i32(m['gen_version'])
    i32(len(m['verts']))
    for pos, polys in m['verts']:
        f32(*pos); i32(len(polys)); u16(*polys)
    i32(len(m['edge_info']))
    for info in m['edge_info']: out.extend(struct.pack('>IHii', *info))
    i32(len(m['polys']))
    for q in m['polys']:
        i32(len(q['verts'])); u16(*q['verts']); i32(len(q['second'])); u16(*q['second'])
        f32(*q['center']); f32(*q['normal']); f32(*q['box']); out.append(q['box_valid'])
        i32(len(q['cover']))
        for c in q['cover']: out.extend(c)
        f32(q['height'])
    f32(*m['local_to_world']); f32(*m['world_to_local'])
    i32(len(m['segments']))
    for sgm in m['segments']: u16(*sgm)
    f32(*m['bounds']); out.append(m['bounds_valid'])
    for e in m['edges']: write_edge(out, e)
    out.extend(m.get('edges_raw_tail', b''))
    return bytes(out)


def read_dump(path):
    """meshes from the runtime's GOW3_NAVMESH_DUMP file: {name: {'verts': [(pos, poly indices)], 'polys': [{verts,
    center, normal, box (6 floats), box_valid, height}]}}"""
    d = open(path, 'rb').read(); c = Cur(d)
    out = {}
    while c.o < len(d):
        assert c.raw(4) == b'NAVM'
        name = c.raw(c.i32()).decode()
        verts = [(c.f32(3), c.u16s(c.i32())) for _ in range(c.i32())]
        polys = []
        for _ in range(c.i32()):
            q = {'verts': c.u16s(c.i32()), 'center': c.f32(3), 'normal': c.f32(3), 'box': c.f32(6)}
            raw = c.raw(4); q['box_valid'] = raw[0]; q['box_valid_raw'] = raw; q['height'] = c.f32(1)[0]
            polys.append(q)
        out[name] = {'verts': verts, 'polys': polys}
    return out


IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


EDGE_MEMORY_SIZE = {'FNavMeshEdgeBase': 136, 'FNavMeshBasicOneWayEdge': 136, 'FNavMeshOneWayBackRefEdge': 216,
                    'FNavMeshDropDownEdge': 220, 'FNavMeshMantleEdge': 304, 'FNavMeshCoverSlipEdge': 304,
                    'FNavMeshPathObjectEdge': 244, 'FNavMeshCrossPylonEdge': 216}     # EdgeStorageInfo.DataSize (shipped: 136, 216, 304, 244)


def from_dump(path, obstacle, edge_class_name, cover_slots=(), special_slots=(), pylon_ref=0):
    """cooked path and obstacle meshes (dicts for write()) from a runtime dump's meshes (read_dump), with edges
    (tools/navedges.py), border segments (polygon sides not covered by a shared border) and polygon cover:
    cover_slots = [(CoverLink object ref, slot index, world position)], each given to the polygon under it.
    edge_class_name: (index, number) of the FName FNavMeshEdgeBase in the package written to."""
    import numpy as np, navedges
    V = np.array([v[0] for v in path['verts']], np.float64)
    OV = np.array([v[0] for v in obstacle['verts']], np.float64)
    new_verts, edges, poly_edges = navedges.build(V, path['polys'], OV, obstacle['polys'])
    for e in edges: e['class'] = 'FNavMeshEdgeBase'
    if special_slots:
        sv, se, listing = navedges.special_edges(V, path['polys'], special_slots, pylon_ref, len(V) + len(new_verts),
                                                 navedges.Walls(OV, obstacle['polys']))
        base = len(edges)
        new_verts += sv; edges += se
        for k, pi in listing: poly_edges[pi].append(base + k)
    n_special = sum(1 for e in edges if e['class'] != 'FNavMeshEdgeBase')
    def mesh(src, extra_verts, edge_lists, edge_list, segments):
        allv = [(tuple(p), tuple(ix)) for p, ix in src['verts']] + list(extra_verts)
        pts = np.array([v[0] for v in allv]) if allv else np.zeros((1, 3))
        polys = []
        for k, q in enumerate(src['polys']):
            polys.append({'verts': tuple(q['verts']), 'second': tuple(edge_lists[k]) if edge_lists else (),
                          'center': tuple(q['center']), 'normal': tuple(q['normal']), 'box': tuple(q['box']),
                          'box_valid': q['box_valid'], 'cover': [], 'height': q['height']})
        info, off = [], 0
        for e in edge_list:
            cls = e.get('class', 'FNavMeshEdgeBase'); size = EDGE_MEMORY_SIZE[cls]
            name = edge_class_name(cls) if callable(edge_class_name) else edge_class_name
            info.append((off, size, name[0], name[1])); off += size
        return {'version': 43, 'gen_version': 43, 'verts': allv, 'edge_info': info, 'polys': polys,
                'local_to_world': IDENTITY, 'world_to_local': IDENTITY, 'segments': segments,
                'bounds': tuple(pts.min(0)) + tuple(pts.max(0)), 'bounds_valid': 1, 'edges': edge_list}
    # border segments: sides whose length is not covered by shared borders
    covered = {}
    for pa, pb, P0, P1 in navedges.shared_borders(V, path['polys']):
        for pi in (pa, pb):
            covered.setdefault(pi, []).append((P0, P1))
    segments = []
    for pi, q in enumerate(path['polys']):
        vs = q['verts']
        for k in range(len(vs)):
            a, b = vs[k], vs[(k + 1) % len(vs)]
            A, B = V[a][:2], V[b][:2]; L = np.linalg.norm(B - A)
            if L < 1e-3: continue
            u = (B - A) / L; nrm = np.array([-u[1], u[0]]); got = 0.0
            for P0, P1 in covered.get(pi, ()):
                if abs(np.dot(P0[:2] - A, nrm)) < 1.5 and abs(np.dot(P1[:2] - A, nrm)) < 1.5:
                    t0, t1 = sorted((np.dot(P0[:2] - A, u), np.dot(P1[:2] - A, u)))
                    got += max(0.0, min(L, t1) - max(0.0, t0))
            if got < L - 1.0: segments.append((a, b, pi))
    pm = mesh(path, new_verts, poly_edges, edges, segments)
    om = mesh(obstacle, [], None, [], [])
    # cover: each slot to the polygon containing it in XY whose height band holds it (nearest centre otherwise)
    for ref, slot, pos in cover_slots:
        best, bd = None, 1e18
        for k, q in enumerate(pm['polys']):
            bx = q['box']
            if not (bx[0] - 1 <= pos[0] <= bx[3] + 1 and bx[1] - 1 <= pos[1] <= bx[4] + 1): continue
            if not (bx[2] - 80 <= pos[2] <= bx[5] + q['height']): continue
            poly = V[list(q['verts'])][:, :2]
            xs, ys = poly[:, 0], poly[:, 1]; x2, y2 = np.roll(xs, -1), np.roll(ys, -1)
            cr = (ys > pos[1]) != (y2 > pos[1])
            with np.errstate(divide='ignore', invalid='ignore'):
                xi = (x2 - xs) * (pos[1] - ys) / (y2 - ys) + xs
            inside = np.count_nonzero(cr & (pos[0] < xi)) % 2 == 1
            dist = np.linalg.norm(np.array(q['center']) - pos) - (1e6 if inside else 0)
            if dist < bd: best, bd = k, dist
        if best is not None:
            pm['polys'][best]['cover'].append(struct.pack('>i', ref) + bytes(16) + struct.pack('>i', slot))
    return pm, om


def add_cross_edges(path_meshes, obstacle_dumps, pylon_refs, edge_class_name):
    """cross-pylon edges between the cooked path meshes {pylon: dict from from_dump} (tools/navedges.py
    cross_edges): their vertices are appended to both meshes, each edge listed in its own polygon"""
    import numpy as np, navedges
    meshes = {k: (np.array([v[0] for v in m['verts']], np.float64), m['polys']) for k, m in path_meshes.items()}
    walls = {k: navedges.Walls(np.array([v[0] for v in obstacle_dumps[k]['verts']], np.float64), obstacle_dumps[k]['polys'])
             for k in path_meshes}
    out = navedges.cross_edges(meshes, walls)
    base = {k: len(m['verts']) for k, m in path_meshes.items()}
    for k, (new_verts, _) in out.items():
        path_meshes[k]['verts'] = list(path_meshes[k]['verts']) + list(new_verts)
    ref = lambda pylon, poly: struct.pack('>i', pylon_refs[pylon]) + bytes(16) + struct.pack('>I', 0xFFFF0000 | poly)
    added = 0
    for own, (_, edges) in out.items():
        m = path_meshes[own]
        for e, poly in edges:
            other = e['pylons'][1]
            e = dict(e)
            e['verts'] = tuple(base[own] + v[1] for v in e['verts'])
            e['verts2'] = tuple(base[other] + v[2] for v in e['verts2'])
            e['poly_refs'] = [ref(own, e['poly_pair'][0]), ref(other, e['poly_pair'][1])]
            for key in ('pylons', 'poly_pair'): e.pop(key)
            k = len(m['edges'])
            m['edges'].append(e)
            name = edge_class_name(e['class']) if callable(edge_class_name) else edge_class_name
            off = (m['edge_info'][-1][0] + m['edge_info'][-1][1]) if m['edge_info'] else 0
            m['edge_info'].append((off, EDGE_MEMORY_SIZE[e['class']], name[0], name[1]))
            m['polys'][poly]['second'] = tuple(m['polys'][poly]['second']) + (k,)
            added += 1
    for k, m in path_meshes.items():                    # bounds over the added vertices too
        pts = np.array([v[0] for v in m['verts']])
        m['bounds'] = tuple(pts.min(0)) + tuple(pts.max(0))
    return added


if __name__ == '__main__':
    import collections
    p = upk.Package(sys.argv[1])
    name = sys.argv[2] if len(sys.argv) > 2 else 'NavigationMeshBase'
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'NavigationMeshBase' or name not in p.path(i): continue
        m, d = read(p, i)
        print(p.path(i), 'version', m['version'], m['gen_version'], 'verts', len(m['verts']), 'edges', len(m['edge_info']),
              'polys', len(m['polys']), 'rest at', m['rest_at'], 'of', m['size'])
        if m['polys']:
            print('   second array lengths', collections.Counter(len(q['second']) for q in m['polys']).most_common(5))
            print('   cover refs per poly', collections.Counter(len(q['cover']) for q in m['polys']).most_common(5))
            print('   heights', min(q['height'] for q in m['polys']), max(q['height'] for q in m['polys']))
            c = next((q['cover'][0] for q in m['polys'] if q['cover']), None)
            if c: print('   a cover ref', c.hex(' ', 4))


def write_dump(path, meshes):
    """meshes ({name: {'verts', 'polys'}} as read_dump returns) back to a dump file"""
    out = bytearray()
    for name, m in meshes.items():
        nb = name.encode()
        out += b'NAVM' + struct.pack('>i', len(nb)) + nb + struct.pack('>i', len(m['verts']))
        for pos, polys in m['verts']:
            out += struct.pack('>3f', *pos) + struct.pack('>i', len(polys)) + struct.pack(f'>{len(polys)}H', *polys)
        out += struct.pack('>i', len(m['polys']))
        for q in m['polys']:
            out += struct.pack('>i', len(q['verts'])) + struct.pack(f'>{len(q["verts"])}H', *q['verts'])
            out += struct.pack('>3f', *q['center']) + struct.pack('>3f', *q['normal']) + struct.pack('>6f', *q['box'])
            out += q.get('box_valid_raw', bytes([q['box_valid'], 0, 0, 0])) + struct.pack('>f', q['height'])
    open(path, 'wb').write(out)


def shift_dump(meshes, offset, rename=None):
    """the dump's meshes moved by offset (x, y, z) and their pylons renamed ({old: new}): a navmesh generated for a
    level converted at one offset reused for the same level converted at another"""
    ox, oy, oz = offset
    out = {}
    for name, m in meshes.items():
        py, _, kind = name.rpartition('.')
        verts = [((p[0] + ox, p[1] + oy, p[2] + oz), polys) for p, polys in m['verts']]
        polys = []
        for q in m['polys']:
            b = q['box']
            q = dict(q, center=(q['center'][0] + ox, q['center'][1] + oy, q['center'][2] + oz),
                     box=(b[0] + ox, b[1] + oy, b[2] + oz, b[3] + ox, b[4] + oy, b[5] + oz))
            polys.append(q)
        out[f'{(rename or {}).get(py, py)}.{kind}'] = {'verts': verts, 'polys': polys}
    return out
