"""Navigation mesh edges between pylons of different packages (a converted chapter's sections), offline.

A chapter's navmesh regions are written into several section packages (tools/trilogy.py: each region's pylons in its
`section`), and navinject.py joins only the pylons of one package, and only where they share a border exactly (FNavMeshCrossPylonEdge, the other pylon by object
reference). An AI then could not leave its region: Dom stayed at the hard route's start while the player went on into
the hallway, the next section's pylon. Gears 3's own streaming levels join pylons of different levels with the same
edge class, both polygon references by GUID (object 0, the pylon's NavGuid, 0xFFFF0000 | polygon: SP_Azura2_02_S),
and set bHasCrossLevelPaths on such pylons. This adds those edges (tools/navedges.py cross_edges over each pair of
pylons from different packages whose meshes meet) to both meshes, and the flag to both pylons.

python -P tools/navcross.py <package> <package> [...]   (rewritten in place)"""
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, upkwrite, navmesh, navedges, objcopy, levelinject


def active_pylons(p):
    """{pylon export index: (NavGuid bytes, path mesh export index, obstacle mesh export index)} of a package's pylons
    with a mesh (not disabled)"""
    out = {}
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'Pylon' or not p.path(i).startswith('TheWorld'): continue
        recs, end = objcopy.parse828(p.export_data(i), 26, p.fname)
        a = {r[0]: r[4] for r in recs}
        if a.get('bDisabled', b'\0') != b'\0' or 'NavGuid' not in a: continue
        meshes = [j for j, f in enumerate(p.exports) if f['outer'] == i + 1 and p.class_name(f['class']) == 'NavigationMeshBase']
        if len(meshes) != 2: continue
        parsed = {j: navmesh.read(p, j)[0] for j in meshes}
        path_j = max(meshes, key=lambda j: (len(parsed[j]['edge_info']), len(parsed[j]['segments'])))
        if not parsed[path_j]['polys'] or not parsed[path_j]['edge_info']: continue
        out[i] = (bytes(a['NavGuid']), path_j, next(j for j in meshes if j != path_j))
    return out


def overlap_edges(a, b, A, B):
    """cross_edges-style output {a: (new verts, edges), b: ...} where the two meshes overlap instead of meeting (two
    regions generated apart: the cell block's mesh runs 30 units into the hallway's): each outer boundary side of
    one mesh lying over a polygon of the other (its middle, 10 units out from its own polygon, in a polygon of the
    other: the meshes overlap there) becomes an edge between that polygon and its own, over the stretches with
    room for each size (both meshes' walls, without the side's own wall)"""
    out = {a: ([], []), b: ([], [])}
    # each mesh's walls without those lying over the other mesh (where the two overlap, a mesh's own edge is no wall)
    def without_overlap(M, N):
        # (in pieces of at most 16 units: one long boundary wall runs both over the other mesh and beyond it)
        w = M['walls']; W0, W1 = [], []
        for p0, p1 in zip(w.W0, w.W1):
            n = max(1, int(np.ceil(np.linalg.norm(p1[:2] - p0[:2]) / 16)))
            ts = np.linspace(0, 1, n + 1)
            for t0, t1 in zip(ts[:-1], ts[1:]):
                q0, q1 = p0 + (p1 - p0) * t0, p0 + (p1 - p0) * t1
                if navedges.polygon_at(N['V'], N['m']['polys'], (q0 + q1) / 2, below=40, above=40) is None:
                    W0.append(q0); W1.append(q1)
        out_ = object.__new__(navedges.Walls)
        out_.W0, out_.W1 = np.array(W0).reshape(-1, 3), np.array(W1).reshape(-1, 3)
        out_.zlo, out_.zhi = np.minimum(out_.W0[:, 2], out_.W1[:, 2]), np.maximum(out_.W0[:, 2], out_.W1[:, 2])
        return out_
    walls = {a: without_overlap(A, B), b: without_overlap(B, A)}
    for own, other, M, N in ((a, b, A, B), (b, a, B, A)):
        V, P = M['V'], M['m']['polys']
        for pi, v0, v1 in navedges.boundary_segments(V, P):
            P0, P1 = V[v0], V[v1]
            d = P1[:2] - P0[:2]; L = np.linalg.norm(d)
            if L < 20: continue
            u = d / L; mid = (P0 + P1) / 2
            c = np.array(P[pi]['center'])
            nrm = np.array([-u[1], u[0]])
            if np.dot(nrm, c[:2] - mid[:2]) > 0: nrm = -nrm          # pointing out of its own polygon
            probe = mid + np.array([nrm[0], nrm[1], 0.0]) * 10
            if navedges.polygon_at(V, P, probe, below=40, above=40) is not None: continue   # (an inner side)
            q = navedges.polygon_at(N['V'], N['m']['polys'], probe, below=40, above=40)
            if q is None: continue
            class Both:
                def clearance(self, pts):
                    return np.minimum(walls[own].clearance(pts, [(P0, P1)]), walls[other].clearance(pts, [(P0, P1)]))
            kept = []
            for sz in sorted(navedges.SIZES, reverse=True):
                for t0, t1 in navedges.intervals(P0, P1, Both(), sz):
                    if any(abs(t0 - x) < 1e-3 and abs(t1 - y) < 1e-3 for x, y, _ in kept): continue
                    kept.append((t0, t1, sz))
            for t0, t1, sz in kept:
                E0 = P0 + (P1 - P0) * t0; E1 = P0 + (P1 - P0) * t1
                first = {}
                for side in (own, other):
                    first[side] = len(out[side][0]); out[side][0].extend([(tuple(E0), ()), (tuple(E1), ())])
                for o_, t_, po, pt, perp in ((own, other, pi, q, nrm), (other, own, q, pi, -nrm)):
                    out[o_][1].append(({'class': 'FNavMeshCrossPylonEdge', 'verts': (('own', first[o_]), ('own', first[o_] + 1)),
                                        'verts2': (('other', t_, first[t_]), ('other', t_, first[t_] + 1)), 'polys': (0, 0),
                                        'length': float(sz), 'center': tuple((E0 + E1) / 2), 'b56': 0, 'b64': 1,
                                        'perp': (float(perp[0]), float(perp[1]), 0.0), 'u172': 0xFFFF,
                                        'pylons': (o_, t_), 'poly_pair': (po, pt)}, po))
    return out


def link(paths, log=print):
    pk = [upk.Package(x) for x in paths]
    ws = [upkwrite.Rewriter(p) for p in pk]
    pyl = {}                                          # (package k, pylon export) -> info
    for k, p in enumerate(pk):
        for i, (guid, pj, oj) in active_pylons(p).items():
            m, d = navmesh.read(p, pj)
            om, _ = navmesh.read(p, oj)
            pyl[(k, i)] = {'guid': guid, 'path_j': pj, 'm': m, 'head': d[:m['props_end']],
                           'V': np.array([v[0] for v in m['verts']], np.float64),
                           'walls': navedges.Walls(np.array([v[0] for v in om['verts']], np.float64), om['polys'])}
    keys = sorted(pyl)
    added, touched = 0, set()
    for ia, a in enumerate(keys):
        for b in keys[ia + 1:]:
            # (same package too: navinject.py's joins need exactly shared borders, and the pylons of one region,
            # explored apart, overlap: none were joined in the prison)
            A, B = pyl[a], pyl[b]
            lo_a, hi_a, lo_b, hi_b = A['V'].min(0), A['V'].max(0), B['V'].min(0), B['V'].max(0)
            if (lo_b > hi_a + 50).any() or (hi_b < lo_a - 50).any(): continue
            out = navedges.cross_edges({a: (A['V'], A['m']['polys']), b: (B['V'], B['m']['polys'])}, {a: A['walls'], b: B['walls']})
            if not any(out[x][1] for x in out): out = overlap_edges(a, b, A, B)     # (meshes overlapping, not meeting)
            if not any(out[x][1] for x in out): continue
            base = {x: len(pyl[x]['m']['verts']) for x in (a, b)}
            for x, (new_verts, _) in out.items():
                pyl[x]['m']['verts'] = list(pyl[x]['m']['verts']) + list(new_verts)
            # a polygon reference: the pylon by object reference when it is in the edge's own package (as shipped
            # meshes join their own pylons, SP_Bridge_01_S), by GUID with no object when in another (SP_Azura2_02_S)
            def ref(x, poly, home):
                if x[0] == home[0]: return struct.pack('>i', x[1] + 1) + bytes(16) + struct.pack('>I', 0xFFFF0000 | poly)
                return struct.pack('>i', 0) + pyl[x]['guid'] + struct.pack('>I', 0xFFFF0000 | poly)
            for own, (_, edges) in out.items():
                m = pyl[own]['m']; w = ws[own[0]]
                for e, poly in edges:
                    other = e['pylons'][1]
                    e = dict(e)
                    e['verts'] = tuple(base[own] + v[1] for v in e['verts'])
                    e['verts2'] = tuple(base[other] + v[2] for v in e['verts2'])
                    e['poly_refs'] = [ref(own, e['poly_pair'][0], own), ref(other, e['poly_pair'][1], own)]
                    for key in ('pylons', 'poly_pair'): e.pop(key)
                    n = len(m['edges'])
                    m['edges'].append(e)
                    name = w.name(e['class'])
                    off = (m['edge_info'][-1][0] + m['edge_info'][-1][1]) if m['edge_info'] else 0
                    m['edge_info'].append((off, navmesh.EDGE_MEMORY_SIZE[e['class']], name[0], name[1]))
                    m['polys'][poly]['second'] = tuple(m['polys'][poly]['second']) + (n,)
                    added += 1
                touched.add(own)
            log(f'  {os.path.basename(paths[a[0]])} {pk[a[0]].exports[a[1]]["name"]} <-> '
                f'{os.path.basename(paths[b[0]])} {pk[b[0]].exports[b[1]]["name"]}: {len(out[a][1])} + {len(out[b][1])} edges')
    # mantles over a border between pylons (the hard hallway's barrier: Pylon_4 one side, Pylon_8 the other): a
    # mantle-able slot's polygon in one pylon, the landing (navedges.SPECIAL's offset) in another
    import cover
    for k, p in enumerate(pk):
        mine = [x for x in keys if x[0] == k]
        if not mine: continue
        for ti, loc, rot, sl in cover.read_g3_links(p):
            for j, pos in enumerate(cover.slot_positions(loc, rot, sl)):
                fl = sl[j]['flags']
                if not (fl.get('bEnabled', 1) and fl.get('bCanMantle') and sl[j]['type'] == 'CT_MidLevel'): continue
                pos = np.asarray(pos, np.float64)
                src = next(((x, q) for x in mine for q in [navedges.polygon_at(pyl[x]['V'], pyl[x]['m']['polys'], pos)] if q is not None), None)
                if src is None: continue
                yaw = (rot[1] + sl[j]['rotation'][1]) * 2 * np.pi / 65536
                _, cls, kind, fwd, side, u252 = navedges.SPECIAL[0]
                dest = pos + np.array([np.cos(yaw), np.sin(yaw), 0.0]) * fwd + np.array([0.0, 0.0, 23.0])
                if navedges.polygon_at(pyl[src[0]]['V'], pyl[src[0]]['m']['polys'], dest) is not None: continue   # (navinject's)
                dst = next(((x, q) for x in keys if x != src[0] for q in [navedges.polygon_at(pyl[x]['V'], pyl[x]['m']['polys'], dest)] if q is not None), None)
                if dst is None: continue
                def ref(x, poly, home):
                    if x[0] == home[0]: return struct.pack('>i', x[1] + 1) + bytes(16) + struct.pack('>I', 0xFFFF0000 | poly)
                    return struct.pack('>i', 0) + pyl[x]['guid'] + struct.pack('>I', 0xFFFF0000 | poly)
                for own, edge in ((src, {'class': cls, 'b56': kind, 'poly_refs': [ref(*src, src[0]), ref(*dst, src[0])],
                                         'actor': struct.pack('>i', ti + 1) + bytes(16), 'rel_item': j, 'object': 0,
                                         'move_dir': tuple(dest), 'u252': u252}),
                                  (dst, {'class': 'FNavMeshOneWayBackRefEdge', 'b56': 6, 'poly_refs': [ref(*dst, dst[0]), ref(*src, dst[0])]})):
                    m = pyl[own[0]]['m']; w = ws[own[0][0]]
                    vi = len(m['verts']); m['verts'] = list(m['verts']) + [(tuple(pos), ())]
                    e = dict(edge, verts=(vi, vi), verts2=(vi, vi), polys=(0, 0), length=-1.0, center=tuple(pos), b64=255,
                             perp=(0.0, 0.0, 0.0), u172=0xFFFF)
                    n = len(m['edges']); m['edges'].append(e)
                    name = w.name(e['class'])
                    off = (m['edge_info'][-1][0] + m['edge_info'][-1][1]) if m['edge_info'] else 0
                    m['edge_info'].append((off, navmesh.EDGE_MEMORY_SIZE[e['class']], name[0], name[1]))
                    m['polys'][own[1]]['second'] = tuple(m['polys'][own[1]]['second']) + (n,)
                    touched.add(own[0])
                added += 2
                log(f'  mantle {os.path.basename(paths[k])} {p.exports[ti]["name"]}#{j}: {pk[src[0][0]].exports[src[0][1]]["name"]} -> '
                    f'{os.path.basename(paths[dst[0][0]])} {pk[dst[0][0]].exports[dst[0][1]]["name"]}')
    for x in touched:
        k, i = x
        m, w, p = pyl[x]['m'], ws[k], pk[k]
        pts = np.array([v[0] for v in m['verts']]); m['bounds'] = tuple(pts.min(0)) + tuple(pts.max(0))
        w.replace(pyl[x]['path_j'], navmesh.write(m, pyl[x]['head']))
        d = bytearray(w.current(i)); o, pr, end = levelinject.props_at(p, bytes(d), (26,))
        d = levelinject.set_property(p, w, d, o, 'bHasCrossLevelPaths', b'\x01', 'BoolProperty')
        w.replace(i, bytes(d))
    # the joined pylons listed in their level's CrossLevelActors: the level's native data after the list ends holds an
    # array (empty), CoverLinkRefs (count + refs), CoverIndexPairs (count + 5-byte records), then CrossLevelActors
    # (count + refs; SP_Azura2_02_S: its pylon and 26 cover links), whose references the engine resolves by GUID as
    # levels stream in. Without it the edges' pylon GUIDs were never resolved: Dom found no path (PATHERROR 3) from the
    # cell block's pylon to the hallway's
    import cover
    for k in sorted({x[0] for x in touched}):
        p, w = pk[k], ws[k]
        lvl = next(i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'Level' and p.path(i) == 'TheWorld.PersistentLevel')
        ld = bytearray(w.current(lvl))
        L = cover.level_lists(p, bytes(ld), w)
        o = L['pylon_end'] + 4
        if struct.unpack_from('>i', ld, o)[0] != 0:
            log(f'  {os.path.basename(paths[k])}: an unknown array before CoverLinkRefs: CrossLevelActors left as they are'); continue
        o += 4
        o += 4 + 4 * struct.unpack_from('>i', ld, o)[0]          # CoverLinkRefs
        o += 4 + 5 * struct.unpack_from('>i', ld, o)[0]          # CoverIndexPairs
        n = struct.unpack_from('>i', ld, o)[0]
        have = set(struct.unpack_from(f'>{n}i', ld, o + 4))
        add = [x[1] + 1 for x in sorted(touched) if x[0] == k and x[1] + 1 not in have]
        ld = ld[:o] + struct.pack('>i', n + len(add)) + ld[o + 4:o + 4 + 4 * n] + b''.join(struct.pack('>i', r) for r in add) + ld[o + 4 + 4 * n:]
        w.replace(lvl, bytes(ld))
        log(f'  {os.path.basename(paths[k])}: CrossLevelActors {n} -> {n + len(add)}')
    for k in sorted({x[0] for x in touched}):
        open(paths[k], 'wb').write(ws[k].build())
    log(f'navcross: {added} edges between pylons of different packages ({len(touched)} pylons)')
    return added


if __name__ == '__main__':
    link(sys.argv[1:])
