"""A Gears 3 level package with its Kismet switched off, into a mod (offline).

Converting a Gears 1 level into a Gears 3 campaign map slot leaves that map's own scripts running next to Gears 1's
(the prologue's intro cutscene, tutorials and squad scripting played over the converted opening). This empties the
level's Main_Sequence (SequenceObjects count 0): nothing in it is initialised, so none of its events register and none
of its actions run. The objects stay in the package, unreferenced.
usage: python -P stripkismet.py <mod folder> <gears3 cooked folder> <package> [<package>...]
       [--keep=<name part>]   keeps the top-level objects whose name has it (the converted G1_ subsequences)
       [--drop-class=<class>] keeps the script and only cuts every link into ops of that class (never activated):
                              Gears 3's prologue tutorials (SeqAct_ManageTutorials) without stopping its streaming
       [--set=<object>:<bool property>=0|1[,...]]  sets bool properties of a Kismet object or level actor (by name): the prologue's
                              "start black" cinematic mode (SP_Nightmare_P SeqAct_ToggleCinematicMode_5) locks movement
                              and only the stripped prologue script released it
       [--bypass=<op path end>:<output>[+<output>...]]  links into that op go to those outputs' targets instead (it never runs, what
                              follows it does): Gears 3's intro cutscene (its remake of this opening, played on the
                              player's pawn) skipped while its "Completed" chain still streams the prison in
       [--g1-environment=<Gears 1 package>]  that package's post-process (WorldInfo) into this package's
                              PostProcessVolume, as convert-map --environment does for its target
       [--move=<actor>:x,y,z[,yaw]]  moves a level actor (by name): the campaign's PlayerStart (in the persistent
                              level, SP_Nightmare_P) to the Gears 1 level's own start
       (a package already in the mod is stripped in place: e.g. the converted target itself)"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, upkwrite, levelinject, objcopy


def drop_links(p, w, classes):
    """cuts the output links of every Kismet op into ops of the given classes; returns the count"""
    targets = {i + 1 for i, e in enumerate(p.exports) if p.class_name(e['class']) in classes and p.path(i).startswith('TheWorld')}
    cut = 0
    for i, e in enumerate(p.exports):
        if not p.path(i).startswith('TheWorld.PersistentLevel.Main_Sequence'): continue
        d = p.export_data(i)
        try: recs, end = objcopy.parse828(d, 4, p.fname)
        except Exception: continue
        ol = next((r for r in recs if r[0] == 'OutputLinks'), None)
        if ol is None: continue
        n = struct.unpack_from('>i', ol[4], 0)[0]; o = 4; els = []; changed = 0
        for _ in range(n):
            el, o = objcopy.parse828(ol[4], o, p.fname)
            for r in el:
                if r[0] != 'Links': continue
                m = struct.unpack_from('>i', r[4], 0)[0]; q = 4; keep = []
                for _ in range(m):
                    ll, q = objcopy.parse828(r[4], q, p.fname)
                    op = next((struct.unpack('>i', x[4])[0] for x in ll if x[0] == 'LinkedOp'), 0)
                    if op in targets: changed += 1
                    else: keep.append(ll)
                r[4] = struct.pack('>i', len(keep)) + b''.join(objcopy.encode(w, ll) for ll in keep)
            els.append(el)
        if not changed: continue
        ol[4] = struct.pack('>i', n) + b''.join(objcopy.encode(w, el) for el in els)
        w.replace(i, d[:4] + objcopy.encode(w, recs) + d[end:])
        cut += changed
    return cut


def set_bools(p, w, sets):
    """sets: [(object name, property, 0/1)] on Main_Sequence objects or level actors -> count set"""
    n = 0
    for name, prop, val in sets:
        for i, e in enumerate(p.exports):
            actor = p.path(i) == 'TheWorld.PersistentLevel.' + name
            if e['name'] != name or not (actor or p.path(i).startswith('TheWorld.PersistentLevel.Main_Sequence')): continue
            d = bytearray(w.current(i))
            o, pr, end = levelinject.props_at(p, bytes(d), (26,) if actor else (4,))
            d = levelinject.set_property(p, w, d, o, prop, bytes([val]), 'BoolProperty')
            w.replace(i, bytes(d)); n += 1
    return n


def output_links(p, d):
    """(records, end, [(desc, [(op ref, input idx)])]) of a Kismet op's data"""
    recs, end = objcopy.parse828(d, 4, p.fname)
    ol = next((r for r in recs if r[0] == 'OutputLinks'), None)
    outs = []
    if ol is not None:
        n = struct.unpack_from('>i', ol[4], 0)[0]; o = 4
        for _ in range(n):
            el, o = objcopy.parse828(ol[4], o, p.fname)
            x = {r[0]: r[4] for r in el}
            desc = objcopy.str_value(x['LinkDesc']) if 'LinkDesc' in x else ''
            links = []
            if 'Links' in x:
                m = struct.unpack_from('>i', x['Links'], 0)[0]; q = 4
                for _ in range(m):
                    ll, q = objcopy.parse828(x['Links'], q, p.fname); y = {r[0]: r[4] for r in ll}
                    links.append((struct.unpack('>i', y.get('LinkedOp', b'\x00\x00\x00\x00'))[0], struct.unpack('>i', y.get('InputLinkIdx', b'\x00\x00\x00\x00'))[0]))
            outs.append((desc, links))
    return recs, end, ol, outs


def bypass(p, w, specs):
    """specs: [(op path end, output desc)] -> count of links rerouted"""
    n = 0
    for path_end, out in specs:
        op = next(i for i in range(len(p.exports)) if p.path(i).endswith(path_end) and p.path(i).startswith('TheWorld'))
        _, _, _, outs = output_links(p, w.current(op))
        want = [x.lower() for x in out.split('+')]
        targets = [t for d, l in outs if d.lower() in want for t in l]
        for i in range(len(p.exports)):
            if not p.path(i).startswith('TheWorld.PersistentLevel.Main_Sequence') or i == op: continue
            d = w.current(i)
            try: recs, end, ol, outs2 = output_links(p, d)
            except Exception: continue
            if not any(t == op + 1 for _, l in outs2 for t, _ in l): continue
            n_el = struct.unpack_from('>i', ol[4], 0)[0]; o = 4; els = []
            for _ in range(n_el):
                el, o = objcopy.parse828(ol[4], o, p.fname)
                for r in el:
                    if r[0] != 'Links': continue
                    m = struct.unpack_from('>i', r[4], 0)[0]; q = 4; keep = []
                    for _ in range(m):
                        ll, q = objcopy.parse828(r[4], q, p.fname)
                        if next((struct.unpack('>i', x[4])[0] for x in ll if x[0] == 'LinkedOp'), 0) == op + 1:
                            n += 1
                            keep += [[['LinkedOp', 'ObjectProperty', 0, b'', struct.pack('>i', t)],
                                      ['InputLinkIdx', 'IntProperty', 0, b'', struct.pack('>i', k)]] for t, k in targets]
                        else: keep.append(ll)
                    r[4] = struct.pack('>i', len(keep)) + b''.join(objcopy.encode(w, ll) for ll in keep)
                els.append(el)
            ol[4] = struct.pack('>i', n_el) + b''.join(objcopy.encode(w, el) for el in els)
            w.replace(i, d[:4] + objcopy.encode(w, recs) + d[end:])
    return n


def move_actors(p, w, moves):
    """moves: [(actor name, (x, y, z), yaw or None)] in TheWorld.PersistentLevel -> count moved"""
    n = 0
    for name, loc, yaw in moves:
        for i, e in enumerate(p.exports):
            if e['name'] != name or p.path(i) != 'TheWorld.PersistentLevel.' + name: continue
            d = bytearray(w.current(i))
            o, pr, end = levelinject.props_at(p, bytes(d), (26,) + tuple(range(0, 120, 2)))
            d = levelinject.set_property(p, w, d, o, 'Location', struct.pack('>3f', *loc), struct_name='Vector')
            if yaw is not None:
                o, pr, end = levelinject.props_at(p, bytes(d), (o,))
                d = levelinject.set_property(p, w, d, o, 'Rotation', struct.pack('>3i', 0, yaw, 0), struct_name='Rotator')
            w.replace(i, bytes(d)); n += 1
    return n


def strip(src, dst, keep_names=(), drop_classes=(), sets=(), moves=(), bypasses=(), g1_env=None):
    p = upk.Package(src)
    w = upkwrite.Rewriter(p)
    if drop_classes or sets or moves or bypasses or g1_env:
        msg = []
        if g1_env:                                     # (levelinject's --environment, into this package's volume)
            levelinject.environment(upk.Package(g1_env), p, w, None)
            msg.append(f'post-process from {os.path.basename(g1_env)}')
        if bypasses: msg.append(f'{bypass(p, w, bypasses)} links rerouted past {", ".join(b for b, _ in bypasses)}')
        if moves: msg.append(f'{move_actors(p, w, moves)} actors moved')
        if drop_classes: msg.append(f'{drop_links(p, w, set(drop_classes))} links into {", ".join(drop_classes)} cut')
        if sets: msg.append(f'{set_bools(p, w, sets)} properties set')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, 'wb').write(w.build())
        print(f'{os.path.basename(src)}: {"; ".join(msg)} -> {dst}')
        return
    m = next((i for i in range(len(p.exports)) if p.path(i) == 'TheWorld.PersistentLevel.Main_Sequence'), None)
    if m is None:
        print(f'{os.path.basename(src)}: no Kismet'); return
    d = bytearray(p.export_data(m))
    o, pr, end = levelinject.props_at(p, bytes(d), (4,))
    v, size = levelinject.tag_value_offset(p, bytes(d), o, 'SequenceObjects')
    n = struct.unpack_from('>i', d, v)[0]
    refs = struct.unpack_from(f'>{n}i', d, v + 4)
    kept = [r for r in refs if r > 0 and any(k in p.exports[r - 1]['name'] for k in keep_names)]
    value = struct.pack('>i', len(kept)) + b''.join(struct.pack('>i', r) for r in kept)
    d = levelinject.set_property(p, w, d, o, 'SequenceObjects', value, 'ArrayProperty')
    w.replace(m, bytes(d))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    open(dst, 'wb').write(w.build())
    print(f'{os.path.basename(src)}: {n} top-level Kismet objects off ({len(kept)} kept) -> {dst}')


def main():
    mod, cooked, pkgs = sys.argv[1], sys.argv[2], sys.argv[3:]
    keep = [a[7:] for a in pkgs if a.startswith('--keep=')]
    drop = [a[13:] for a in pkgs if a.startswith('--drop-class=')]
    sets = []
    for a in (a[6:] for a in pkgs if a.startswith('--set=')):
        for item in a.split(','):
            obj, _, kv = item.partition(':'); k, _, v = kv.partition('=')
            sets.append((obj, k, int(v)))
    bypasses = [tuple(a[9:].rsplit(':', 1)) for a in pkgs if a.startswith('--bypass=')]
    g1_env = next((a[17:] for a in pkgs if a.startswith('--g1-environment=')), None)
    moves = []
    for a in (a[7:] for a in pkgs if a.startswith('--move=')):
        obj, _, v = a.partition(':'); f = [float(x) for x in v.split(',')]
        moves.append((obj, tuple(f[:3]), int(f[3]) if len(f) > 3 else None))
    for name in (a for a in pkgs if not a.startswith('--')):
        f = name if name.lower().endswith('.xxx') else name + '.xxx'
        dst = os.path.join(mod, 'GearGame', 'CookedXbox360', f)
        src = dst if os.path.isfile(dst) else os.path.join(cooked, f)
        strip(src, dst + '.tmp' if src == dst else dst, keep, drop, sets, moves, bypasses, g1_env)
        if src == dst: os.replace(dst + '.tmp', dst)


if __name__ == '__main__':
    main()
