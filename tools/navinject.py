"""Replace a Gears 3 level's navigation meshes with ones generated in game (offline step).

The runtime builds the meshes with Gears 3's own generator passes and dumps them (runtime/ue3/navmesh_build.cpp,
GOW3_NAVMESH_BUILD + GOW3_NAVMESH_DUMP); this adds what the passes do not make (edges, border segments, polygon
cover: tools/navmesh.py from_dump, tools/navedges.py) and writes both meshes into the pylon's NavigationMeshBase
exports (the path mesh is the one with edges in the original, the obstacle mesh the other).
usage: python -P navinject.py <package in> <dump> <package out> [pylon name or 'all', default Pylon_1]
       [--starts-at=x,y,z,yaw] [--no-special]"""
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, upkwrite, navmesh, cover


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    opts = dict(a[2:].split('=', 1) for a in sys.argv[1:] if a.startswith('--'))
    src, dump_path, dst = args[:3]
    pylon = args[3] if len(args) > 3 else 'Pylon_1'
    p = upk.Package(src)
    w = upkwrite.Rewriter(p)
    dump = navmesh.read_dump(dump_path)
    pylons = sorted({k.rsplit('.', 1)[0] for k in dump}) if pylon == 'all' else [pylon]
    # a pylon whose exploration found no walkable ground (an empty dump) is disabled: a clone (tools/pylons.py) still
    # holds the copied mesh of the pylon it was cloned from
    empty = [py for py in pylons if not dump.get(f'{py}.path', {}).get('polys')]
    pylons = [py for py in pylons if py not in empty]
    for py in empty:
        import levelinject
        i = next((i for i, e in enumerate(p.exports) if p.path(i) == f'TheWorld.PersistentLevel.{py}'), None)
        if i is None: continue
        d = bytearray(p.export_data(i)); o, pr, end = levelinject.props_at(p, bytes(d), (26,))
        d = levelinject.set_property(p, w, d, o, 'bDisabled', b'', 'BoolProperty')
        w.replace(i, bytes(d))
        print(f'{py}: no walkable ground found from it: disabled')
    # cover slots of the package's own CoverLinks (after levelinject --cover: Gears 1's)
    slots, special = [], []
    for ti, loc, rot, sl in cover.read_g3_links(p):
        for k, pos in enumerate(cover.slot_positions(loc, rot, sl)):
            slots.append((ti + 1, k, np.asarray(pos)))
            fl = sl[k]['flags']
            if fl.get('bEnabled', 1):
                special.append({'link': ti + 1, 'index': k, 'pos': np.asarray(pos), 'yaw': rot[1] + sl[k]['rotation'][1],
                                'mantle': bool(fl.get('bCanMantle')) and sl[k]['type'] == 'CT_MidLevel',
                                'slip_l': bool(fl.get('bCanCoverSlip_Left')), 'slip_r': bool(fl.get('bCanCoverSlip_Right'))})
    built, exports, refs = {}, {}, {}
    for py in pylons:
        meshes = [i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'NavigationMeshBase'
                  and p.path(i).startswith(f'TheWorld.PersistentLevel.{py}.')]
        if len(meshes) != 2 or f'{py}.path' not in dump:
            print(f'{py}: skipped ({len(meshes)} meshes in the package, dump has it: {f"{py}.path" in dump})'); continue
        parsed = {i: navmesh.read(p, i) for i in meshes}
        path_i = max(meshes, key=lambda i: (len(parsed[i][0]['edge_info']), len(parsed[i][0]['segments'])))
        obst_i = next(i for i in meshes if i != path_i)
        refs[py] = next(i for i, e in enumerate(p.exports) if p.path(i) == f'TheWorld.PersistentLevel.{py}') + 1
        pm, om = navmesh.from_dump(dump[f'{py}.path'], dump[f'{py}.obstacle'], w.name, slots,
                                   special if 'no-special' not in opts else (), refs[py])
        built[py] = (pm, om); exports[py] = (path_i, obst_i, parsed)
    if len(built) > 1:
        # a cover slot belongs to one polygon of one pylon (as shipped): one claimed by several pylons stays with
        # a polygon that contains it, else with the first claim
        import navedges
        claims = {}
        for py, (pm, om) in built.items():
            V = np.array([v[0] for v in pm['verts']], np.float64)
            for k, q in enumerate(pm['polys']):
                for c in q['cover']:
                    key = c[:4] + c[20:24]
                    pos = next(s[2] for s in slots if struct.pack('>i', s[0]) == c[:4] and struct.pack('>i', s[1]) == c[20:24])
                    inside = navedges.polygon_at(V, [q], pos) is not None
                    claims.setdefault(key, []).append((not inside, py, k, c))
        dropped = 0
        for key, cl in claims.items():
            cl.sort(key=lambda t: t[0])
            for _, py, k, c in cl[1:]:
                built[py][0]['polys'][k]['cover'].remove(c); dropped += 1
        if dropped: print(f'{dropped} duplicate cover slot claims removed')
        n = navmesh.add_cross_edges({k: v[0] for k, v in built.items()}, {k: dump[f'{k}.obstacle'] for k in built}, refs, w.name)
        print(f'{n} cross-pylon edges between {len(built)} pylons')
    for py, (pm, om) in built.items():
        path_i, obst_i, parsed = exports[py]
        for i, m in ((path_i, pm), (obst_i, om)):
            w.replace(i, navmesh.write(m, parsed[i][1][:parsed[i][0]['props_end']]))
    if 'starts-at' in opts:                            # (test) every player start there: x,y,z,yaw
        import levelinject
        x, y, z, yaw = (float(v) for v in opts['starts-at'].split(','))
        for i, e in enumerate(p.exports):
            if 'PlayerStart' not in p.class_name(e['class']) or not p.path(i).startswith('TheWorld'): continue
            d = bytearray(p.export_data(i))
            try:
                o, pr, end = levelinject.props_at(p, bytes(d), (26,) + tuple(range(0, 120, 2)))
            except ValueError:
                continue
            d = levelinject.set_property(p, w, d, o, 'Location', struct.pack('>3f', x, y, z), struct_name='Vector')
            d = levelinject.set_property(p, w, d, o, 'Rotation', struct.pack('>3i', 0, int(yaw), 0), struct_name='Rotator')
            w.replace(i, bytes(d))
    out = w.build()
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    open(dst, 'wb').write(out)
    for py, (pm, om) in built.items():
        covered = sum(len(q['cover']) for q in pm['polys'])
        print(f"{py}: path mesh {len(pm['verts'])} vertices, {len(pm['polys'])} polygons, {len(pm['edges'])} edges "
              f"({sum(1 for e in pm['edges'] if e['class'] == 'FNavMeshMantleEdge')} mantle, "
              f"{sum(1 for e in pm['edges'] if e['class'] == 'FNavMeshCoverSlipEdge')} cover slip, "
              f"{sum(1 for e in pm['edges'] if e['class'] == 'FNavMeshCrossPylonEdge')} cross-pylon), "
              f"{len(pm['segments'])} border segments, {covered} cover slots on polygons; obstacle mesh "
              f"{len(om['verts'])} vertices, {len(om['polys'])} polygons")
    q = upk.Package(dst)                              # check: every written mesh reads back
    for py in built:
        for i in exports[py][:2]: navmesh.read(q, i)
    print(f'read back: ok -> {dst}')


if __name__ == '__main__':
    main()
