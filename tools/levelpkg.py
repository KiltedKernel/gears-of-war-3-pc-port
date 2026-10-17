"""The packages of a converted chapter of its own (the trilogy; docs/TRILOGY.md, "A chapter's packages").

A Gears 1/2 level becomes Gears 3 packages named after its own with a prefix (Gears 1's SP_Prison_S01 ->
GOW1_SP_Prison_S01), one per source package, so the source's level streaming (its Kismet's LevelStreaming,
LevelVisibility, MultiLevelStreaming, WaitForLevelsVisible) keeps working by name, renamed:
- the persistent level (the chapter's map): a clone of a rich Gears 3 sublevel, the **kit**: its material instances
  and their compiled shaders are the templates the sections' converted materials take as parents (through imports:
  a level package resolves an import of an object that a loaded package carries, checked in game), with the source's
  persistent content and Kismet converted into it, the source's streaming levels (WorldInfo.StreamingLevels), its
  KillZ and its post-process (DefaultPostProcessSettings);
- a **section** per source sublevel: a clone of a lean Gears 3 sublevel, converted into (levelinject).
Clones are **blanked**: the template's actors are left out of Level.Actors (they load, but are never initialised,
drawn, ticked or collided with), except those the conversion reuses (mesh collections, cover links, pylons); its
Kismet is emptied. Every clone gets its own package GUID.
usage: python -P levelpkg.py blank <template.xxx> <out.xxx> [--keep=Class,Class]
       python -P levelpkg.py streaming <package.xxx> <out.xxx> <PackageName>[,...] [--always=PackageName,...]"""
import hashlib, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, upkwrite, levelinject, objcopy, materialconv

# actors a blanked clone keeps: what levelinject reuses (collections: the template component and new components;
# cover links: the cover pass; pylons: the navmesh), and the WorldInfo (Actors[0])
KEEP_SECTION = ('WorldInfo', 'StaticMeshCollectionActor', 'CoverLink', 'Pylon')


def package_guid(name):
    """a package GUID derived from the package's name: stable across builds, distinct between packages"""
    return hashlib.md5(('trilogy:' + name.lower()).encode()).digest()


def fname_of(w):
    return lambda i, n: (w.names[i] if n == 0 else f'{w.names[i]}_{n - 1}')


def level_export(p):
    return next(i for i, e in enumerate(p.exports) if p.path(i) == 'TheWorld.PersistentLevel' and p.class_name(e['class']) == 'Level')


def actors(p, w, li):
    """(level data, offset of the Actors count, actor refs): Level = 4-byte prelude, properties, an int, Actors"""
    d = w.current(li)
    _, o = p.properties(bytes(d), 4)
    n = struct.unpack_from('>i', d, o + 4)[0]
    return d, o + 4, list(struct.unpack_from(f'>{n}i', d, o + 8))


def blank(p, w, keep=KEEP_SECTION):
    """the template's actors out of Level.Actors (but those of the kept classes), its Main_Sequence emptied;
    returns (kept, removed) counts"""
    li = level_export(p)
    d, co, refs = actors(p, w, li)
    out = [r if r > 0 and p.class_name(p.exports[r - 1]['class']) in keep else 0 for r in refs]
    kept = sum(1 for r in out if r)
    removed = sum(1 for r, o_ in zip(refs, out) if r and not o_)
    # the array keeps its length: an emptied slot is None, as a destroyed actor's (Actors[0] stays the WorldInfo)
    w.replace(li, bytes(d[:co + 4]) + b''.join(struct.pack('>i', r) for r in out) + bytes(d[co + 4 + 4 * len(refs):]))
    unlink_navigation(p, w, li, {r for r in out if r})
    m = next((i for i in range(len(p.exports)) if p.path(i) == 'TheWorld.PersistentLevel.Main_Sequence'), None)
    if m is not None:
        sd = bytearray(w.current(m))
        o, pr, end = levelinject.props_at(p, bytes(sd), (4,))
        if levelinject.tag_value_offset(p, bytes(sd), o, 'SequenceObjects')[0] is not None:
            sd = levelinject.set_property(p, w, sd, o, 'SequenceObjects', struct.pack('>i', 0), 'ArrayProperty')
            w.replace(m, bytes(sd))
    return kept, removed


def unlink_navigation(p, w, li, kept):
    """the level's navigation point list (NavListStart/End, nextNavigationPoint) without the actors left out of
    Level.Actors: the world's list is every level's chained, and a script's `foreach AllNavigationPoints` reached an
    actor that was never initialised (the template's weapon lockers: GearPickupFactory.CoopPlayerNumberChange, on a
    checkpoint restart, read its WorldInfo, None: a crash). Returns the count dropped."""
    import cover
    ld = bytearray(w.current(li))
    at = cover.level_lists(p, bytes(ld), w)['nav_end'] - 4      # NavListStart, then NavListEnd
    chain, r, seen = [], struct.unpack_from('>i', ld, at)[0], set()
    while 0 < r <= len(p.exports) and r not in seen:
        seen.add(r); chain.append(r)
        d = w.current(r - 1)
        o, pr, end = levelinject.props_at(p, bytes(d), (26,))
        v, size = levelinject.tag_value_offset(p, bytes(d), o, 'nextNavigationPoint')
        r = struct.unpack_from('>i', d, v)[0] if v is not None else 0
    keep = [r for r in chain if r in kept]
    if len(keep) == len(chain): return 0
    for a_, b_ in zip(keep, keep[1:] + [0]):
        d = bytearray(w.current(a_ - 1))
        o, pr, end = levelinject.props_at(p, bytes(d), (26,))
        v, size = levelinject.tag_value_offset(p, bytes(d), o, 'nextNavigationPoint')
        if v is not None: struct.pack_into('>i', d, v, b_)
        elif b_: d = levelinject.set_property(p, w, d, o, 'nextNavigationPoint', struct.pack('>i', b_), 'ObjectProperty')
        w.replace(a_ - 1, bytes(d))
    struct.pack_into('>ii', ld, at, keep[0] if keep else 0, keep[-1] if keep else 0)
    w.replace(li, bytes(ld))
    return len(chain) - len(keep)


def world_info(p):
    return next(i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'WorldInfo' and p.path(i).startswith('TheWorld.PersistentLevel'))


def class_import(p, w, cls, package='Engine'):
    """an import ref of class `cls` (Core.Class in the given script package), added when the package lacks it"""
    for k, im in enumerate(p.imports):
        if im['class'] == 'Class' and im['name'] == cls: return -k - 1
    pkg = next((-k - 1 for k, im in enumerate(p.imports) if im['class'] == 'Package' and im['name'] == package and im['outer'] == 0), None)
    if pkg is None: pkg = w.add_import('Core', 'Package', 0, package)
    return w.add_import('Core', 'Class', pkg, cls)


def set_streaming(p, w, levels, always=()):
    """WorldInfo.StreamingLevels = one LevelStreamingKismet per name in `levels` (loaded and shown by Kismet), one
    LevelStreamingAlwaysLoaded per name in `always`; the objects are new exports in TheWorld"""
    world = next(i for i, e in enumerate(p.exports) if p.path(i) == 'TheWorld' and p.class_name(e['class']) == 'World')
    refs = []
    for kind, names in (('LevelStreamingAlwaysLoaded', always), ('LevelStreamingKismet', levels)):
        if not names: continue
        cls = class_import(p, w, kind)
        for k, name in enumerate(names):
            recs = [['PackageName', 'NameProperty', 0, b'', struct.pack('>ii', *w.name(name))]]
            refs.append(w.add_export(cls, world + 1, (f'{kind}', 1000 + len(refs)),
                                     struct.pack('>i', -1) + objcopy.encode(w, recs), flags=0x0007000000000000))
    wi = world_info(p)
    d = bytearray(w.current(wi))
    o, pr, end = levelinject.props_at(p, bytes(d), (26,))
    value = struct.pack('>i', len(refs)) + b''.join(struct.pack('>i', r) for r in refs)
    d = levelinject.set_property(p, w, d, o, 'StreamingLevels', value, 'ArrayProperty')
    w.replace(wi, bytes(d))
    return len(refs)


def set_kill_z(p, w, z):
    wi = world_info(p)
    d = bytearray(w.current(wi))
    o, pr, end = levelinject.props_at(p, bytes(d), (26,))
    d = levelinject.set_property(p, w, d, o, 'KillZ', struct.pack('>f', z), 'FloatProperty')
    w.replace(wi, bytes(d))


def g1_world_settings(src):
    """a Gears 1 level's WorldInfo: (KillZ, DefaultPostProcessSettings records) (374 properties)"""
    wi = next((i for i, e in enumerate(src.exports) if src.class_name(e['class']) == 'WorldInfo' and src.path(i).startswith('TheWorld')), None)
    if wi is None: return None, {}
    d = src.export_data(wi)
    o, pr, end = levelinject.props_at(src, d, (32, 4, 8))
    a = {n: v for n, t, k, v in pr}
    pp = {}
    gv, gsize = levelinject.tag_value_offset(src, d, o, 'DefaultPostProcessSettings')
    if gv is not None: pp = {r[0]: r for r in materialconv.raw_props(src, d, gv)[0]}
    pp['__package__'] = src
    return a.get('KillZ'), pp


def set_post_process(p, w, g1_pp, base_package):
    """WorldInfo.DefaultPostProcessSettings: a Gears 3 persistent map's settings (base_package's WorldInfo) with
    the source's values on top (as levelinject --environment does for a volume: no colour grading table)"""
    if not [k for k in g1_pp if not k.startswith('__')]: return 0
    b = upk.Package(base_package)
    bwi = world_info(b)
    bd = b.export_data(bwi)
    bo, bpr, bend = levelinject.props_at(b, bd, (26,))
    bv, bsize = levelinject.tag_value_offset(b, bd, bo, 'DefaultPostProcessSettings')
    recs, _ = materialconv.raw_props(b, bd, bv)
    out, have = [], set()
    for r in recs:
        r = list(r)
        name = r[0]
        if name in ('ColorGrading_LookupTable', 'Bloom_Tint'): continue
        if name == 'bOverride_Scene_ColorGradingLUT': r[4] = b'\x00'
        # names inside a record (a struct's or enum's name in the tag's extra; an enum value; a name) are the base
        # package's: re-made in this one
        if r[3]: r[3] = struct.pack('>ii', *w.name(b.fname(*struct.unpack('>ii', r[3]))))
        if r[1] == 'NameProperty' or (r[1] == 'ByteProperty' and len(r[4]) == 8):
            r[4] = struct.pack('>ii', *w.name(b.fname(*struct.unpack('>ii', r[4]))))
        if name in g1_pp and name != 'bEnableDOF':
            g = g1_pp[name]
            if g[1] == 'BoolProperty': r[4] = b'\x01' if g[4] != bytes(len(g[4])) else b'\x00'
            elif g[1] == r[1] and len(g[4]) == len(r[4]) and r[1] in ('FloatProperty', 'IntProperty', 'StructProperty'):
                r[4] = g[4]                            # floats, vectors, colours: the same layout in both games
        out.append(r); have.add(name)
    # the source's settings the base does not set (Gears 1's grading: Scene_*, bloom, depth of field), with their
    # override flags (Gears 3 blends post-process volumes over these per setting)
    src = g1_pp.get('__package__')
    for name, g in g1_pp.items():
        if name.startswith('__') or name in have or name in ('bEnableDOF',): continue
        if g[1] not in ('FloatProperty', 'StructProperty', 'BoolProperty'): continue
        extra = struct.pack('>ii', *w.name(src.fname(*struct.unpack('>ii', g[3])))) if g[3] else b''
        value = (b'\x01' if g[4] != bytes(len(g[4])) else b'\x00') if g[1] == 'BoolProperty' else g[4]
        out.append([name, g[1], 0, extra, value])
        if not name.startswith('b') and 'bOverride_' + name not in have:
            out.append(['bOverride_' + name, 'BoolProperty', 0, b'', b'\x01'])
    body = materialconv.encode_props(w, out)
    wi = world_info(p)
    d = bytearray(w.current(wi))
    o, pr, end = levelinject.props_at(p, bytes(d), (26,))
    d = levelinject.set_property(p, w, d, o, 'DefaultPostProcessSettings', body, 'StructProperty', 'PostProcessSettings')
    w.replace(wi, bytes(d))
    return len(out)


def clone(template, out, name=None, keep=KEEP_SECTION):
    """a blanked clone of a Gears 3 level package with its own GUID, written to out; returns (kept, removed)"""
    p = upk.Package(template)
    upk.E = '>'
    w = upkwrite.Rewriter(p)
    w.guid = package_guid(name or os.path.splitext(os.path.basename(out))[0])
    kept, removed = blank(p, w, keep)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, 'wb').write(w.build())
    return kept, removed


def persistent(path, source, levels, base_package, always=()):
    """the chapter's persistent level (converted already, in place): WorldInfo.StreamingLevels, the source's KillZ
    and post-process on a Gears 3 persistent map's (base_package) settings"""
    src = upk.Package(source)
    kill_z, pp = g1_world_settings(src)
    def edit(fn):                                      # one step per rewrite: each step reads what the last wrote
        p = upk.Package(path)
        upk.E = '>'
        w = upkwrite.Rewriter(p)
        r = fn(p, w)
        open(path, 'wb').write(w.build())
        return r
    n = edit(lambda p, w: set_streaming(p, w, levels, always))
    if kill_z is not None: edit(lambda p, w: set_kill_z(p, w, kill_z))
    npp = edit(lambda p, w: set_post_process(p, w, pp, base_package))
    return n, kill_z, npp


def main():
    a = sys.argv[1:]
    opts = {x.split('=', 1)[0]: x.split('=', 1)[1] for x in a if x.startswith('--') and '=' in x}
    a = [x for x in a if not x.startswith('--')]
    if a[0] == 'persistent':                           # persistent <package> <source persistent> <base> <levels> [--always=]
        n, kz, npp = persistent(a[1], a[2], a[4].split(','), a[3], opts.get('--always', '').split(',') if opts.get('--always') else ())
        print(f'{os.path.basename(a[1])}: {n} streaming levels, KillZ {kz}, {npp} post-process settings')
    if a[0] == 'blank':
        keep = tuple(opts['--keep'].split(',')) if '--keep' in opts else KEEP_SECTION
        kept, removed = clone(a[1], a[2], keep=keep)
        print(f'{os.path.basename(a[2])}: {kept} actors kept, {removed} switched off')
    elif a[0] == 'streaming':
        p = upk.Package(a[1]); upk.E = '>'
        w = upkwrite.Rewriter(p)
        n = set_streaming(p, w, a[3].split(','), opts.get('--always', '').split(',') if opts.get('--always') else ())
        open(a[2], 'wb').write(w.build())
        print(f'{os.path.basename(a[2])}: {n} streaming levels')


if __name__ == '__main__':
    main()
