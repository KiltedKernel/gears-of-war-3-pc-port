"""More navigation mesh pylons in a Gears 3 level (offline): clones of one of its Pylons.

A Gears 3 level's navigation mesh is built and stored per Pylon: the generator explores the walkable surface from each
pylon (within its expansion sphere), and the meshes live in the pylon's two NavigationMeshBase subobjects (the path
mesh and the obstacle mesh, referenced from the pylon's native data after its properties: NavMeshPtr, ObstacleMesh).
A converted Gears 1 level is too large and too broken up (doors, drops) for the one pylon of the map whose slot it
takes, so more are added, each a clone of that pylon with its own:
  Location / ExpansionSphereCenter / ExpansionRadius, NavGuid (random), CylinderComponent (cloned), the two
  NavigationMeshBase exports (cloned; navinject.py writes the generated meshes into them by the pylon's name)
and linked into the level: Level.Actors, the navigation point list (nextNavigationPoint; NavListEnd) and the pylon list
(NextPylon; PylonListEnd). The list ends are Level refs stored just before CoverLinkRefs (tools/cover.py:
NavListStart/End, CoverListStart/End, PylonListStart/End, an empty array)."""
import os, struct, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, objcopy, cover


def add_pylons(p, w, positions, radius, template=None):
    """new pylons at positions [(x, y, z)]; returns their export indices (0-based)"""
    import levelinject
    pyl = [i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'Pylon' and p.path(i).startswith('TheWorld')]
    template = pyl[0] if template is None else template
    te = p.exports[template]
    kids = [j for j, e in enumerate(p.exports) if e['outer'] == template + 1]
    comp = next(j for j in kids if p.class_name(p.exports[j]['class']) == 'CylinderComponent')
    meshes = [j for j in kids if p.class_name(p.exports[j]['class']) == 'NavigationMeshBase']
    lvl = next(i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'Level' and p.path(i) == 'TheWorld.PersistentLevel')
    used = {e['name'] for e in p.exports} | getattr(w, 'used_export_names', set())
    flags = lambda j: struct.unpack_from('>Q', p.data, p.exports[j]['entry'] + 24)[0]
    arch = lambda j: struct.unpack_from('>i', p.data, p.exports[j]['entry'] + 20)[0]
    def base_num(name):
        b, _, n = name.rpartition('_')
        return (b, int(n) + 1) if n.isdigit() else (name, 0)
    wname = lambda i, n: (w.names[i] if n == 0 else f'{w.names[i]}_{n - 1}')
    new = []
    for (x, y, z) in positions:
        n = 2
        while f'Pylon_{n}' in used: n += 1
        used.add(f'Pylon_{n}')
        ref = w.add_export(te['class'], te['outer'], ('Pylon', n + 1), b'', flags=flags(template), archetype=arch(template))
        cd = bytearray(p.export_data(comp)); cd[4:8] = struct.pack('>i', -1)
        cref = w.add_export(p.exports[comp]['class'], ref, base_num(p.exports[comp]['name']), bytes(cd), flags=flags(comp), archetype=arch(comp))
        mrefs = [w.add_export(p.exports[m]['class'], ref, base_num(p.exports[m]['name']), p.export_data(m), flags=flags(m), archetype=arch(m))
                 for m in meshes]
        d = p.export_data(template)
        recs, end = objcopy.parse828(d, 26, p.fname)
        out = []
        for r in recs:
            name = r[0]
            r = [name, r[1], r[2], (struct.pack('>ii', *w.name(p.fname(*struct.unpack('>ii', r[3])))) if r[3] else b''), r[4]]
            if r[1] == 'NameProperty': r[4] = struct.pack('>ii', *w.name(p.fname(*struct.unpack('>ii', r[4]))))
            if name in ('Location', 'ExpansionSphereCenter'): r[4] = struct.pack('>3f', x, y, z)
            elif name == 'ExpansionRadius': r[4] = struct.pack('>f', radius)
            elif name in ('CylinderComponent', 'CollisionComponent'): r[4] = struct.pack('>i', cref)
            elif name in ('nextNavigationPoint', 'NextPylon'): r[4] = struct.pack('>i', 0)
            elif name == 'NavGuid': r[4] = uuid.uuid4().bytes
            elif name == 'Base': continue
            elif r[1] == 'ObjectProperty' and struct.unpack('>i', r[4])[0] != 0: continue   # (other references: not carried)
            out.append(r)
        tail = bytearray(d[end:])
        old_mesh_refs = [m + 1 for m in meshes]
        for k in range(0, len(tail) - 3, 4):            # the native data's mesh references -> the clones
            v = struct.unpack_from('>i', tail, k)[0]
            if v in old_mesh_refs: struct.pack_into('>i', tail, k, mrefs[old_mesh_refs.index(v)])
        prelude = bytearray(d[:26]); prelude[22:26] = struct.pack('>i', -1)
        w.replace(ref - 1, bytes(prelude) + objcopy.encode(w, out) + bytes(tail))
        new.append(ref - 1)
    # the level: actors, navigation list, pylon list
    ld = bytearray(w.current(lvl))
    L = cover.level_lists(p, bytes(ld), w)
    nav_end_at, pylon_end_at = L['nav_end'], L['pylon_end']
    def relink(at, prop):
        chain = [struct.unpack_from('>i', ld, at)[0] - 1] + new
        for a_, b_ in zip(chain, chain[1:]):
            if a_ < 0: continue
            dd = bytearray(w.current(a_))
            o, pr, end = levelinject.props_at(p, bytes(dd), (26,) + tuple(range(0, 120, 2)))
            v, size = levelinject.tag_value_offset(p, bytes(dd), o, prop)
            if v is None: dd = levelinject.set_property(p, w, dd, o, prop, struct.pack('>i', b_ + 1), 'ObjectProperty')
            else: struct.pack_into('>i', dd, v, b_ + 1)
            w.replace(a_, bytes(dd))
        if chain[0] < 0: struct.pack_into('>i', ld, at - 4, new[0] + 1)   # an empty list: it starts here
        struct.pack_into('>i', ld, at, new[-1] + 1)
    relink(nav_end_at, 'nextNavigationPoint'); relink(pylon_end_at, 'NextPylon')
    w.replace(lvl, bytes(ld))
    objcopy.add_to_level(p, w, lvl, [i + 1 for i in new])
    return new
