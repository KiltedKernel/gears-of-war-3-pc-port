"""Put a Gears of War 1/2 level's static mesh actors into a Gears 3 level (offline; output in a mod folder).

Gears 3 cooks level meshes into StaticMeshCollectionActors: the actor's StaticMeshComponents array property, then
one 4x4 float matrix per component (rotation and translation; row 3 is the translation) after the properties. The
component keeps its own Scale3D. A component's serial data is {int 0, an object ref (TemplateOwnerClass?)}, the tagged
properties (StaticMesh, Materials, VisibilityId, Scale3D, lighting...), then native LODData (lightmaps): an empty
array (4 zero bytes) means no static lighting.
Gears 1/2 StaticMeshActor: tagged properties (StaticMeshComponent, Location, Rotation, DrawScale[, DrawScale3D]) after
a state frame (Gears 1: 32 bytes); its component: properties from offset 8 (StaticMesh, Scale3D...).

Each source mesh is converted once (tools/meshconv.py, on a Gears 3 template mesh: its materials), each actor becomes
a new component (a clone of a Gears 3 template component with no lightmap and VisibilityId -1, so precomputed
visibility never culls it), appended to one collection actor with its matrix. tools/upkwrite.py writes the package.
usage: python -P levelinject.py <gears3 package> <source level package> <out folder (mods)> <gears3 cooked folder>
         [--max N] [--offset x,y,z] [--center x,y,z] [--radius r]
  --center/--radius pick source actors near a point (source units); --offset is added to every source location;
  --template <text>: the template component is one whose mesh path contains it (the converted meshes take its
  materials; default: the first component without a lightmap).
Lighting (Gears 1): each component's light map (per vertex, or textures sampled at the vertices' lightmap UVs;
tools/lighting.py) becomes a Gears 3 per-vertex light map; --no-lighting leaves them unlit."""
import argparse, math, os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, staticmesh, meshconv, upkwrite, modpack, lighting, materialconv, texture2d, bspconv, cover


unlit_mats = set()          # source materials drawn unlit (refs)
imported_skel = {}          # (source index, import ref) -> (source index, export index) of the mesh it names
_aux = {}


def g1_import(srcs, si, ref, want_class):
    """an import of source si, as (source index, export index) in the Gears 1 package that has it: the package named
    after the import's outer, else WarfareGame.xxx (Gears 1's always-loaded content); that package joins the sources
    (for meshes and materials only). None when not found"""
    import objcopy
    sp = srcs[si]
    path = objcopy.import_path(sp, ref)
    g1dir = os.path.dirname(os.path.abspath(sp.filename))
    for name in (path.split('.')[0], 'WarfareGame'):
        if name not in _aux:
            f = os.path.join(g1dir, name + '.xxx')
            _aux[name] = upk.Package(f) if os.path.isfile(f) else None
        ap = _aux[name]
        if ap is None: continue
        idx = next((i for i, e in enumerate(ap.exports) if ap.path(i) == path and ap.class_name(e['class']) == want_class), None)
        if idx is None: continue
        if ap not in srcs: srcs.append(ap)
        return srcs.index(ap), idx
    return None
UNLIT_LIGHT = 1.0           # their light map: constant (diffuse x 1 = the texture as it is)


def props_at(p, d, starts=(4, 8, 32, 12, 16, 20, 24, 28, 36, 40)):
    """the tagged properties of export data d, at the first offset where they parse to None with known names"""
    for o in starts:
        try:
            pr, end = p.properties(d, o)
            # an empty list only at a single, known offset (else any None-looking word would match)
            if (pr or len(starts) == 1) and all(n in p.names for n, *_ in pr): return o, pr, end
        except Exception:
            pass
    raise ValueError('no property list found')


def rotation_matrix(pitch, yaw, roll):
    """UE3 FRotationMatrix (rotator units: 65536 = 360 degrees), row vectors"""
    k = 2 * math.pi / 65536
    sp, cp = math.sin(pitch * k), math.cos(pitch * k)
    sy, cy = math.sin(yaw * k), math.cos(yaw * k)
    sr, cr = math.sin(roll * k), math.cos(roll * k)
    return np.array([[cp * cy, cp * sy, sp],
                     [sr * sp * cy - cr * sy, sr * sp * sy + cr * cy, -sr * cp],
                     [-(cr * sp * cy + sr * sy), cy * sr - cr * sp * sy, cr * cp]])


def source_actors(p):
    """[(mesh export index, location, rotator, scale3, component export index)] of a Gears 1/2 level's
    StaticMeshActors"""
    out = []
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'StaticMeshActor': continue
        o, pr, end = props_at(p, p.export_data(i))
        a = {n: v for n, t, k, v in pr}
        comp = a.get('StaticMeshComponent', 0)
        if comp <= 0: continue
        co, cpr, cend = props_at(p, p.export_data(comp - 1), (8, 4, 12))
        c = {n: v for n, t, k, v in cpr}
        mesh = c.get('StaticMesh', 0)
        if mesh <= 0: continue                                     # an import (another package) or none: later
        vec = lambda v, default: struct.unpack('>3f', v[1]) if v else default
        loc = vec(a.get('Location'), (0.0, 0.0, 0.0))
        rot = struct.unpack('>3i', a['Rotation'][1]) if a.get('Rotation') else (0, 0, 0)
        scale = np.array(vec(a.get('DrawScale3D'), (1.0, 1.0, 1.0))) * a.get('DrawScale', 1.0) \
                * np.array(vec(c.get('Scale3D'), (1.0, 1.0, 1.0))) * c.get('Scale', 1.0)
        out.append((mesh - 1, np.array(loc), rot, scale, comp - 1))
    return out


def tag_value_offset(p, d, start, name):
    """offset of property `name`'s value in d (properties from start), and its size"""
    o = start
    while True:
        n = p.fname(*struct.unpack_from('>ii', d, o))
        if n == 'None': return None, 0
        typ = p.fname(*struct.unpack_from('>ii', d, o + 8))
        size = struct.unpack_from('>i', d, o + 16)[0]
        v = o + 24
        if typ == 'StructProperty': v += 8
        elif typ == 'ByteProperty' and p.version >= 633: v += 8
        if typ == 'BoolProperty': size = 1 if p.version >= 673 else 4
        if n == name: return v, size
        o = v + size


def set_property(p, w, d, start, name, value, typ='StructProperty', struct_name=None):
    """d with tagged property `name` (properties from start) set to value bytes; a new tag before None when absent
    (at its default); an array's tag size follows its new length"""
    v, size = tag_value_offset(p, bytes(d), start, name)
    if v is None:
        o = start
        while p.fname(*struct.unpack_from('>ii', d, o)) != 'None':
            v2, sz = tag_value_offset(p, bytes(d), o, p.fname(*struct.unpack_from('>ii', d, o)))
            o = v2 + sz
        size = 0 if typ == 'BoolProperty' and p.version >= 673 else len(value)    # Gears 3 bools: size 0, a value byte
        tag = struct.pack('>ii', *w.name(name)) + struct.pack('>ii', *w.name(typ)) + struct.pack('>ii', size, 0)
        if typ == 'StructProperty': tag += struct.pack('>ii', *w.name(struct_name))
        return bytearray(d[:o]) + tag + value + d[o:]
    out = bytearray(d[:v]) + value + d[v + size:]
    if len(value) != size:                             # an array: the tag's size field is 8 bytes before the value
        struct.pack_into('>i', out, v - 8, len(value))
    return out


def remove_property(p, d, start, name):
    """d without tagged property `name` (properties from start)"""
    o = start
    while True:
        n = p.fname(*struct.unpack_from('>ii', d, o))
        if n == 'None': return d
        typ = p.fname(*struct.unpack_from('>ii', d, o + 8))
        size = struct.unpack_from('>i', d, o + 16)[0]
        v = o + 24 + (8 if typ == 'StructProperty' or (typ == 'ByteProperty' and p.version >= 633) else 0)
        if typ == 'BoolProperty': size = 1 if p.version >= 673 else 4
        if n == name: return d[:o] + d[v + size:]
        o = v + size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target'); ap.add_argument('source', help='Gears 1/2 level package(s), comma-separated (a level and its sublevels)'); ap.add_argument('mods'); ap.add_argument('g3cooked')
    ap.add_argument('--max', type=int, default=0)
    ap.add_argument('--offset', default='0,0,0'); ap.add_argument('--center', default=None)
    ap.add_argument('--radius', type=float, default=0)
    ap.add_argument('--template', default=None, help='a component whose StaticMesh path contains this (its materials)')
    ap.add_argument('--no-lighting', action='store_true', help='no light maps (unlit)')
    ap.add_argument('--no-point-lights', action='store_true', help="do not bake the source level's dynamic point lights")
    ap.add_argument('--no-collision', action='store_true', help="keep the template's collision flags")
    ap.add_argument('--bsp', action='store_true', help="also the level's BSP (Gears 1), as static mesh chunks")
    ap.add_argument('--replace', action='store_true', help="the target's own static meshes go: every collection actor "
                    "keeps only the new components (its gameplay actors, volumes, cover and BSP stay)")
    ap.add_argument('--cover', action='store_true', help="the source level's cover: the target's CoverLink actors take "
                    "its links (location, rotation, slots written in Gears 3's format); spare ones get no slots")
    ap.add_argument('--starts-at-cover', action='store_true', help="(test) with --cover: every player start 80 units "
                    "behind a different source cover slot, facing it (spawn, press A: in cover)")
    ap.add_argument('--environment', action='store_true', help="the source level's look: its post-process settings "
                    "in the target's PostProcessVolume (no colour grading table), its fog colour in the target's fog")
    ap.add_argument('--starts-at', default=None, help="(test) x,y,z,yaw: every player start there (e.g. beside a Horde "
                    "command centre flag marker)")
    ap.add_argument('--pylon-at', default=None, help="x,y,z: the level's Pylons there (the navmesh generator's "
                    "exploration starts at a pylon's location, which must be standing room on the new geometry)")
    ap.add_argument('--pylon-radius', type=float, default=6017.0, help="with --pylon-at: the pylon's expansion radius")
    ap.add_argument('--pylons-at', default=None, help="x,y,z;x,y,z...: more pylons (clones of the level's) there, each "
                    "exploring its own region when the navmesh is generated (a large or broken-up level)")
    ap.add_argument('--kismet', action='store_true', help="the sources' Kismet (each Main_Sequence and everything in "
                    "it) copied into the target as subsequences of its Main_Sequence (tools/objcopy.py)")
    ap.add_argument('--fix-starts', action='store_true', help="lift the target's player/enemy starts above the source "
                    "BSP's floors (a start inside the new floor kills the pawn at spawn)")
    ap.add_argument('--materials', default=None, help='a Gears 3 MaterialInstanceConstant (path substring) to clone '
                    'per source material, with its Diffuse_01/Normal_01/Specular_01 set to the converted textures')
    ap.add_argument('--kit', default=None, help="a chapter of its own: the Gears 3 package (the chapter's persistent level, "
                    "loaded first) holding the material instance templates (--materials, the skeletal one): converted "
                    "materials are child instances of them, through imports (tools/levelpkg.py)")
    ap.add_argument('--content', default=None, help="a chapter of its own: the Gears 3 packages (comma-separated) whose "
                    "GearGameContent classes are loaded with it (AI pawn classes; default: by the target's map name)")
    ap.add_argument('--streaming-prefix', default=None, help="a chapter of its own: level names in streaming actions get "
                    "this prefix (the converted packages' names) when in --streaming-built, else are dropped")
    ap.add_argument('--streaming-built', default='', help='with --streaming-prefix: the source package names built')
    ap.add_argument('--chapter-number', type=int, default=None, help="a chapter of its own: its campaign chapter "
                    "number (Gears 1's chapter complete gets ChapterCompleted = number + 1)")
    ap.add_argument('--last-chapter', action='store_true', help="with --chapter-number: no next chapter is built (the "
                    "chapter's map change ends the game instead)")
    ap.add_argument('--player-starts', action='store_true', help="with --kismet: the sources' PlayerStarts copied too (a "
                    "chapter's persistent level: the campaign starts there)")
    args = ap.parse_args()
    offset = np.array([float(x) for x in args.offset.split(',')])
    if args.streaming_prefix is not None:
        import objcopy
        objcopy.STREAMING_PREFIX = args.streaming_prefix
        objcopy.STREAMING_BUILT = {x.strip().lower() for x in args.streaming_built.split(',') if x.strip()}
    if args.chapter_number is not None:
        import objcopy
        objcopy.set_chapter(args.chapter_number, args.last_chapter)

    # one or more source packages (a Gears 1 level and its streamed sublevels share world coordinates): actors,
    # meshes and materials are keyed by (source index, export index)
    srcs = [upk.Package(f) for f in args.source.split(',') if f]
    src = srcs[0]
    actors = []                                        # (source index, mesh, location, rotation, scale, component)
    for si, sp in enumerate(srcs):
        actors += [(si,) + a for a in source_actors(sp)]
    if args.center:
        c = np.array([float(x) for x in args.center.split(',')])
        actors = [a for a in actors if np.linalg.norm(a[2] - c) <= args.radius]
    if args.max: actors = actors[:args.max]
    # meshes drawn only see-through (light shafts, fog sheets): left out until translucent materials convert
    see_through = {}
    for a in actors:
        if a[:2] not in see_through:
            try: see_through[a[:2]] = materialconv.g1_see_through(srcs[a[0]], staticmesh.read(srcs[a[0]], a[1])['lods'][0]['elements'])
            except Exception: see_through[a[:2]] = False
    n_before = len(actors)
    actors = [a for a in actors if not see_through[a[:2]]]
    print(f'{n_before - len(actors)} see-through mesh actors left out ({sum(see_through.values())} meshes)')
    bsp_chunks, polys = [], []                         # chunks: (source index, materials, mesh)
    if args.bsp:
        for si, sp in enumerate(srcs):
            if sp.version != 374: continue
            try:
                wm = bspconv.world_model(sp)
            except ValueError:
                continue
            model = bspconv.read_model_374(sp, wm)
            sp_polys = bspconv.polygons(model, sp, bspconv.node_lightmaps(sp, wm + 1))
            if args.center:
                sp_polys = [x for x in sp_polys if np.linalg.norm(x[1].mean(0) - c) <= args.radius]
            if not sp_polys: continue
            polys += sp_polys
            bsp_chunks += [(si,) + ch for ch in bspconv.chunk_meshes(model, sp_polys)]
        print(f'BSP: {len(polys)} polygons in {len(bsp_chunks)} chunks')
    # with --kismet: the meshes of the sources' InterpActors (doors, movers: copied as actors) are converted too
    mover_meshes, skel_meshes = set(), {}
    if args.kismet and src.version == 374:
        import objcopy, skelmesh
        for si, sp in enumerate(srcs):
            mover_meshes |= {(si, mi) for mi in objcopy.mover_meshes(sp)}
            for mi in objcopy.skeletal_meshes(sp):     # SkeletalMeshActors' meshes: Gears 3 skeletal meshes
                try: skel_meshes[(si, mi)] = skelmesh.read_374(sp, mi)
                except Exception as ex: print(f'skeletal mesh {sp.path(mi)} not converted: {ex}')
        # meshes the level imports (Dom's cutscene mesh COG_Dom.COG_Dom_CamSkel is cooked into WarfareGame.xxx, Gears
        # 1's always-loaded content, not into the level: unconverted, the cutscene's Dom was invisible): read from the
        # Gears 1 package named after the import's outer, else WarfareGame.xxx; extra sources with no actors of their own
        for si in range(len(srcs)):
            sp = srcs[si]
            if getattr(sp, 'aux', False): continue
            for ref in objcopy.skeletal_mesh_imports(sp):
                path = objcopy.import_path(sp, ref)
                found = g1_import(srcs, si, ref, 'SkeletalMesh')
                if found is None: print(f'skeletal mesh {path}: imported, not found in Gears 1 packages'); continue
                srcs[found[0]].aux = True
                try:
                    if found not in skel_meshes: skel_meshes[found] = skelmesh.read_374(srcs[found[0]], found[1])
                    imported_skel[(si, ref)] = found
                    print(f'skeletal mesh {path}: imported, read from {os.path.basename(srcs[found[0]].filename)}')
                except Exception as ex: print(f'skeletal mesh {path} not converted: {ex}')
    mesh_keys = set(a[:2] for a in actors) | mover_meshes
    print(f'{len(actors)} source actors, {len(set(a[:2] for a in actors))} meshes ({len(mover_meshes)} more for movers), from {len(srcs)} packages')

    p3 = upk.Package(args.target)
    upk.E = '>'
    w = upkwrite.Rewriter(p3)
    p3.names = w.names                                 # names the conversion adds read back too (one shared list)
    cls = lambda i: p3.class_name(p3.exports[i]['class'])
    # template component: no lightmap (4-byte native tail), with Scale3D and VisibilityId properties
    tcomp = fallback = None
    for i, e in enumerate(p3.exports):
        if cls(i) != 'StaticMeshComponent' or not p3.path(i).startswith('TheWorld'): continue
        d = p3.export_data(i)
        try: o, pr, end = props_at(p3, d, (8,))
        except ValueError: continue                    # class defaults and templates are laid out differently
        names = [n for n, *_ in pr]
        if 'StaticMesh' not in names: continue
        if 'Scale3D' not in names:                     # (scale 1 is not written: given one below)
            if fallback is None and not args.template and p3.path(i).count('.') == 3 and 'VisibilityId' in names: fallback = i
            continue
        mesh = {n: v for n, t, k, v in pr}['StaticMesh']
        if args.template:                              # its lightmap is dropped below
            if mesh > 0 and args.template.lower() in p3.path(mesh - 1).lower(): tcomp = i; break
        elif d[end:] == bytes(4):
            tcomp = i; break
        elif fallback is None:
            fallback = i
    # a target whose components all have light maps (a lean section template): the first one (each new component
    # gets its own light map tail in place of the template's)
    if tcomp is None: tcomp = fallback
    if tcomp is None: raise SystemExit('no template component without lightmap (with Scale3D) in the target')
    td = p3.export_data(tcomp); to, tpr, tend = props_at(p3, td, (8,))
    if not args.no_collision:                          # pawns collide: BlockNonZeroExtent back to its default (true)
        td = remove_property(p3, td, to, 'BlockNonZeroExtent'); to, tpr, tend = props_at(p3, td, (8,))
        # line traces (bullets, the navmesh generator's ground checks) and physics: explicitly on (the template
        # turns BlockRigidBody off; BlockZeroExtent's default did not block the generator's downward traces)
        for prop in ('BlockZeroExtent', 'BlockRigidBody', 'CollideActors', 'BlockActors'):
            td = remove_property(p3, td, to, prop); to, tpr, tend = props_at(p3, td, (8,))
            td = bytes(set_property(p3, w, bytearray(td), to, prop, b'\x01', 'BoolProperty')); to, tpr, tend = props_at(p3, td, (8,))
    # the mesh's own element materials apply (a Materials array on the component overrides them), and the
    # template's IrrelevantLights (GUIDs of the lights that do not affect the original) do not apply here
    for prop in ('Materials', 'IrrelevantLights'):
        td = remove_property(p3, td, to, prop); to, tpr, tend = props_at(p3, td, (8,))
    if 'Scale3D' not in [n for n, *_ in tpr]:          # every new component's scale is written into this tag
        td = bytes(set_property(p3, w, bytearray(td), to, 'Scale3D', struct.pack('>fff', 1, 1, 1), 'StructProperty', 'Vector'))
        to, tpr, tend = props_at(p3, td, (8,))
    tmesh = {n: v for n, t, k, v in tpr}['StaticMesh'] - 1
    coll = next(i for i in range(len(p3.exports)) if cls(i) == 'StaticMeshCollectionActor')
    print('template component', p3.path(tcomp), 'mesh', p3.path(tmesh), 'collection', p3.path(coll))
    tentry = p3.exports[tcomp]['entry']; tflags = struct.unpack_from('>Q', p3.data, tentry + 24)[0]
    tarch = struct.unpack_from('>i', p3.data, tentry + 20)[0]
    mentry = p3.exports[tmesh]['entry']; mflags = struct.unpack_from('>Q', p3.data, mentry + 24)[0]

    # materials (Gears 1): textures converted once, one material instance per source material
    mic_of, mic_refs = {}, {}                          # (source, material ref) -> converted MIC / its texture refs
    # a chapter of its own (--kit): the material templates are the kit's (the chapter's persistent level); the
    # converted materials are child instances of them, imported (materialconv.child_mic), and the converted textures
    # take a texture of this package with the same compression and LOD group as their property template
    pk = upk.Package(args.kit) if args.kit else None
    upk.E = '>'
    kit_imports = {}
    if args.materials and src.version == 374:
        tp = pk or p3                                  # the package holding the template instance
        tmic = next(i for i in range(len(tp.exports)) if tp.class_name(tp.exports[i]['class']) == 'MaterialInstanceConstant' and args.materials.lower() in tp.path(i).lower())
        md = tp.export_data(tmic); mo, mpr, mend = props_at(tp, md)
        slots = {}
        v, size = tag_value_offset(tp, md, mo, 'TextureParameterValues')
        n_el = struct.unpack_from('>i', md, v)[0]; e_ = v + 4
        for _ in range(n_el):
            recs, e_ = materialconv.raw_props(tp, md, e_)
            r = {x[0]: x[4] for x in recs}
            slots[tp.fname(*struct.unpack('>ii', r['ParameterName']))] = struct.unpack('>i', r['ParameterValue'])[0] - 1
        tex_template = {'diffuse': slots.get('Diffuse_01'), 'normal': slots.get('Normal_01'), 'specular': slots.get('Specular_01')}
        mflags_ = struct.unpack_from('>Q', tp.data, tp.exports[tmic]['entry'] + 24)[0]
        if pk is not None:
            local_tex = [i for i in range(len(p3.exports)) if cls(i) == 'Texture2D' and not p3.exports[i]['name'].startswith('Default__')]
            settings3 = {i: materialconv.texture_settings(p3, i) for i in local_tex}
            for slot, ti in list(tex_template.items()):
                if ti is None or ti < 0: continue
                want = materialconv.texture_settings(pk, ti)
                tex_template[slot] = next((i for i in local_tex if settings3[i] == want), local_tex[0] if local_tex else None)
            mic_cls = next((p3.exports[i]['class'] for i in range(len(p3.exports)) if cls(i) == 'MaterialInstanceConstant'), None)
            kit_parent = materialconv.import_object(w, p3, pk, tmic, kit_imports)
            print(f'kit: material template {pk.path(tmic)} (imported), texture templates '
                  f"{', '.join(f'{k} {p3.path(v)}' for k, v in tex_template.items() if v is not None)}")
        tex_ref = {}
        mats = set()                                   # (source index, material export index)
        for (si, mi), sk in skel_meshes.items():
            mats.update((si, x - 1) for x in sk['materials'] if x > 0)
        import_mats = {}                               # (source, import ref) -> (source, export ref) of the material
        for si, mi in mesh_keys:
            for el in staticmesh.read(srcs[si], mi)['lods'][0]['elements']:
                if el['material'] > 0: mats.add((si, el['material'] - 1))
                elif el['material'] < 0 and (si, el['material']) not in import_mats:
                    # a material imported from Gears 1's content (the cutscene's Hammerbursts: Locust_Hammerburst_Mat)
                    cls_ = srcs[si].imports[-el['material'] - 1].get('class')
                    found = g1_import(srcs, si, el['material'], cls_) if cls_ in ('Material', 'MaterialInstanceConstant') else None
                    if found:
                        srcs[found[0]].aux = True
                        import_mats[(si, el['material'])] = (found[0], found[1] + 1); mats.add(found)
        for si, bm, _ in bsp_chunks:
            mats.update((si, x - 1) for x in bm if x > 0)
        by_name = {}                                   # the same asset in several sublevels: converted once
        for si, gm in sorted(mats):
            sp = srcs[si]
            name = sp.path(gm)
            if name in by_name:
                mic_of[(si, gm + 1)] = by_name[name]
                if (by_name.get(('unlit', name))): unlit_mats.add((si, gm + 1))
                continue
            texs = materialconv.g1_textures(sp, gm)
            if 'diffuse' not in texs: continue
            if materialconv.g1_unlit(sp, gm):             # drawn at full brightness: a constant light map, no specular
                unlit_mats.add((si, gm + 1)); texs = {'diffuse': texs['diffuse']}; by_name[('unlit', name)] = True
            refs = {}
            for slot, ti in texs.items():
                tt = tex_template.get(slot)
                if tt is None or tt < 0: continue
                tkey = sp.path(ti)
                if tkey not in tex_ref:
                    upk.E = '>'
                    data, fix = materialconv.texture_828(w, p3, tt, texture2d.Texture(sp, ti))
                    tflags_ = struct.unpack_from('>Q', p3.data, p3.exports[tt]['entry'] + 24)[0]
                    tex_ref[tkey] = w.add_export(p3.exports[tt]['class'], 0, ('Trilogy_' + tkey.split('.')[-1], 0), data, flags=tflags_, fixups=fix)
                refs[{'diffuse': 'Diffuse_01', 'normal': 'Normal_01', 'specular': 'Specular_01'}[slot]] = tex_ref[tkey]
            if pk is not None:
                data = materialconv.child_mic(w, p3, pk, tmic, kit_parent, refs)
                mic_of[(si, gm + 1)] = by_name[name] = w.add_export(mic_cls, 0, ('Trilogy_' + name.split('.')[-1], 0), data, flags=mflags_)
            else:
                data = materialconv.mic_828(w, p3, tmic, refs)
                mic_of[(si, gm + 1)] = by_name[name] = w.add_export(p3.exports[tmic]['class'], 0, ('Trilogy_' + name.split('.')[-1], 0), data, flags=mflags_)
            mic_refs[(si, gm + 1)] = mic_refs[name] = refs
        for k, v in import_mats.items():
            if v in mic_of: mic_of[k] = mic_of[v]
            if v in unlit_mats: unlit_mats.add(k)
        if import_mats: print(f'{len(import_mats)} imported materials, {sum(1 for k in import_mats if k in mic_of)} converted')
        print(f'{len(mic_of)} materials, {len(tex_ref)} textures converted (template {tp.path(tmic)})')
    # converted meshes have no BodySetup (simple collision): line and box checks must use the triangles (the kDOP
    # tree), or a simple-collision check finds nothing (UseSimpleLineCollision / UseSimpleBoxCollision off)
    def per_poly_collision(data):
        o, pr, end = props_at(p3, data, (4,))
        data = bytearray(data)
        for prop in ('UseSimpleLineCollision', 'UseSimpleBoxCollision'):
            data = set_property(p3, w, data, o, prop, b'\x00', 'BoolProperty')
        return bytes(data)
    # meshes
    new_mesh, parsed, mesh_by_name = {}, {}, {}
    for k, (si, mi) in enumerate(sorted(mesh_keys)):
        sp = srcs[si]
        m = parsed[(si, mi)] = staticmesh.read(sp, mi)
        path = sp.path(mi)
        if path in mesh_by_name:                       # the same mesh in another sublevel package
            new_mesh[(si, mi)] = mesh_by_name[path]; continue
        upk.E = '>'
        data = per_poly_collision(meshconv.to_828(p3, tmesh, m, [mic_of.get((si, el['material'])) for el in m['lods'][0]['elements']]))
        name = 'Trilogy_' + path.split('.')[-1]
        new_mesh[(si, mi)] = mesh_by_name[path] = w.add_export(p3.exports[tmesh]['class'], 0, (name, 0), data, flags=mflags)
    if skel_meshes and not any(cls(i) == 'SkeletalMesh' and not p3.exports[i]['name'].startswith('Default__') for i in range(len(p3.exports))):
        print(f'{len(skel_meshes)} skeletal meshes not converted: the target has no skeletal mesh to take as a template')
        skel_meshes = {}
    if skel_meshes:                                    # template: one of the target's skeletal meshes (its tail)
        import skelmesh
        tsk = next(i for i in range(len(p3.exports)) if cls(i) == 'SkeletalMesh' and not p3.exports[i]['name'].startswith('Default__'))
        ttail = skelmesh.read_828(p3, tsk)['tail']
        tmat = skelmesh.read_828(p3, tsk)['materials'][0]
        # a skeletal mesh needs a material whose parent is compiled for skinning (else the engine draws the default
        # material): the converted textures in a clone of a skeletal-capable MIC of the target (Diffuse / Normal /
        # Specular parameters; OverallEmissive 0: its emissive texture would glow)
        skel_tmic = None
        sp_ = pk or p3                                 # the kit's skeletal material templates in a chapter of its own
        scls = lambda i: sp_.class_name(sp_.exports[i]['class'])
        for i_ in range(len(sp_.exports)):
            if scls(i_) != 'SkeletalMesh' or sp_.exports[i_]['name'].startswith('Default__'): continue
            try: mats__ = skelmesh.read_828(sp_, i_)['materials']
            except Exception: continue
            for r_ in mats__:
                if r_ > 0 and skel_tmic is None and scls(r_ - 1) == 'MaterialInstanceConstant'                         and {'Diffuse', 'Normal', 'Specular'} <= set(materialconv.mic_texture_params(sp_, r_ - 1)):
                    skel_tmic = r_ - 1
        skel_parent = materialconv.import_object(w, p3, pk, skel_tmic, kit_imports) if pk is not None and skel_tmic is not None else None
        skel_cache = {}
        def skel_mic(si_, x_):
            key = srcs[si_].path(x_ - 1)
            if skel_tmic is None or key not in mic_refs: return mic_of.get((si_, x_), tmat)
            if key not in skel_cache:
                refs_ = {{'Diffuse_01': 'Diffuse', 'Normal_01': 'Normal', 'Specular_01': 'Specular'}[k]: v for k, v in mic_refs[key].items()}
                fl_ = struct.unpack_from('>Q', sp_.data, sp_.exports[skel_tmic]['entry'] + 24)[0]
                if pk is not None:
                    data_ = materialconv.child_mic(w, p3, pk, skel_tmic, skel_parent, refs_, {'OverallEmissive': 0.0})
                    skel_cache[key] = w.add_export(mic_cls, 0, ('Trilogy_Skel_' + key.split('.')[-1], 0), data_, flags=fl_)
                else:
                    data_ = materialconv.mic_828(w, p3, skel_tmic, refs_, {'OverallEmissive': 0.0})
                    skel_cache[key] = w.add_export(p3.exports[skel_tmic]['class'], 0, ('Trilogy_Skel_' + key.split('.')[-1], 0), data_, flags=fl_)
            return skel_cache[key]
        print('skeletal material template:', sp_.path(skel_tmic) if skel_tmic is not None else None, '(kit)' if pk is not None else '')
        skflags = struct.unpack_from('>Q', p3.data, p3.exports[tsk]['entry'] + 24)[0]
        for (si, mi), sk in skel_meshes.items():
            path = srcs[si].path(mi)
            if path in mesh_by_name: new_mesh[(si, mi)] = mesh_by_name[path]; continue
            mats_ = [skel_mic(si, x) if x > 0 else tmat for x in sk['materials']]
            data, fix = skelmesh.to_828(w, sk, mats_, ttail)
            new_mesh[(si, mi)] = mesh_by_name[path] = w.add_export(p3.exports[tsk]['class'], 0, ('Trilogy_' + path.split('.')[-1], 0), data, flags=skflags, fixups=fix)
        print(f'{len(skel_meshes)} skeletal meshes converted (template tail: {p3.path(tsk)})')
    # components
    sm_off, _ = tag_value_offset(p3, td, to, 'StaticMesh')
    sc_off, _ = tag_value_offset(p3, td, to, 'Scale3D')
    vis_off, _ = tag_value_offset(p3, td, to, 'VisibilityId')
    # the level's lights (LightGuid and LightmapGuid of every light component): listed as baked in the new light maps
    light_guids = []
    for i, e in enumerate(p3.exports):
        if not cls(i).endswith('LightComponent') or not p3.path(i).startswith('TheWorld'): continue
        ld = p3.export_data(i)
        starts = (8, 4, 12, 16, 20, 24, 28, 32, 36, 40, 44, 48)
        if cls(i) == 'DominantDirectionalLightComponent':        # a u16 shadow depth map (count + data) comes first
            starts = (4 + 2 * struct.unpack_from('>I', ld, 0)[0] + 8,)
        try:
            _, lpr, _ = props_at(p3, ld, starts)
        except ValueError:
            continue
        for n_, t_, k_, v_ in lpr:
            if n_ in ('LightGuid', 'LightmapGuid') and t_ == 'StructProperty': light_guids.append(bytes(v_[1]))
    print(f'{len(light_guids)} light GUIDs listed as baked')
    point_lights = None
    if src.version == 374 and not args.no_point_lights:
        parts = [lighting.g1_point_lights(sp) for sp in srcs]
        parts = [x for x in parts if x is not None and len(x[0])]
        if parts: point_lights = tuple(np.concatenate([x[k] for x in parts]) for k in range(len(parts[0])))
    if point_lights is not None: print(f'{len(point_lights[0])} point lights baked into the light maps')
    def world_vertices(mesh, loc, rot, scale):
        pos, tx, tz, uvh, nv = meshconv.source_vertices(mesh)
        P = np.frombuffer(pos.tobytes(), '>f4').reshape(nv, 3).astype(np.float64)
        Nl = tz[:, [3, 2, 1]].astype(np.float64) / 127.5 - 1
        R = rotation_matrix(*rot)
        Nw = (Nl / np.where(scale == 0, 1, scale)) @ R                  # normals: inverse scale, then rotation
        Nw /= np.linalg.norm(Nw, axis=1, keepdims=True) + 1e-9
        return (P * scale) @ R + loc, Nw
    refs, mats, lit, all_samples = [], [], 0, []
    n_unlit = 0
    for n, (si, mi_, loc, rot, scale, ci) in enumerate(actors):
        sp, mi = srcs[si], (si, mi_)
        tail = bytes(4)                                # no light map
        samples = None
        if not args.no_lighting and sp.version == 374:
            cd_ = sp.export_data(ci)
            try:
                lods, _ = lighting.read_lod_data_374(cd_, props_at(sp, cd_, (8,))[2])
                samples = lighting.vertex_lighting_374(sp, lods[0], parsed[mi]) if lods else None
            except Exception:
                samples = None
            nv = len(staticmesh.positions(parsed[mi]['lods'][0]))
            if samples is not None and len(samples) != nv: samples = None
            els = parsed[mi]['lods'][0]['elements']
            if els and all((si, el['material']) in unlit_mats for el in els):
                samples = np.full((nv, 3, 3), UNLIT_LIGHT, np.float32); n_unlit += 1
            elif point_lights is not None:
                Pw, Nw = world_vertices(parsed[mi], loc, rot, scale)
                pl = lighting.bake_point_lights(point_lights, Pw, Nw)
                base = samples if samples is not None else np.full((nv, 3, 3), 0.05, np.float32)
                samples = base + pl[:, None, :]
            if samples is not None:
                tail = lighting.gears3_vertex_lightmap(samples, len(w.exports) + 1, light_guids)   # owner: this new component
                lit += 1
        d = bytearray(td[:tend]) + tail
        struct.pack_into('>i', d, sm_off, new_mesh[mi])
        struct.pack_into('>3f', d, sc_off, *scale)
        if vis_off: struct.pack_into('>i', d, vis_off, -1)
        refs.append(w.add_export(p3.exports[tcomp]['class'], coll + 1, ('TrilogySMC', n + 1), d, flags=tflags, archetype=tarch))
        m = np.eye(4); m[:3, :3] = rotation_matrix(*rot); m[3, :3] = loc + offset
        mats.append(struct.pack('>16f', *m.flatten()))
        if samples is not None and not (parsed[mi]['lods'][0]['elements'] and all((si, el['material']) in unlit_mats for el in parsed[mi]['lods'][0]['elements'])):
            all_samples.append(samples.mean(0))
    # BSP chunks: a mesh and a component each, in world space (+ offset); lit from the BSP's light maps (sampled at
    # each vertex's lightmap UV), the actors' average lighting where a node had no light map
    ambient = np.mean(all_samples, 0) if all_samples else np.full((3, 3), 0.25)
    for k, (si, bmats, bmesh) in enumerate(bsp_chunks):
        upk.E = '>'
        data = per_poly_collision(meshconv.to_828(p3, tmesh, bmesh, [mic_of.get((si, x)) for x in bmats]))
        mref = w.add_export(p3.exports[tmesh]['class'], 0, ('Trilogy_BSP', k + 1), data, flags=mflags)
        light = bmesh['light'].copy()                  # the BSP's own light maps, a little ambient where a node had none
        light[np.isnan(light[:, 0, 0])] = 0.05 if point_lights is not None else ambient
        if point_lights is not None:
            lod0 = bmesh['lods'][0]
            Pb = np.frombuffer(lod0['positions'][2][2], '>f4').reshape(-1, 3).astype(np.float64)
            vb = np.frombuffer(lod0['vertices'][4][2], np.uint8).reshape(-1, 12)
            Nb = vb[:, [7, 6, 5]].astype(np.float64) / 127.5 - 1      # TangentZ (w z y x)
            light = light + lighting.bake_point_lights(point_lights, Pb, Nb)[:, None, :]
        tail = bytes(4) if args.no_lighting else lighting.gears3_vertex_lightmap(light, len(w.exports) + 1, light_guids)
        d = bytearray(td[:tend]) + tail
        struct.pack_into('>i', d, sm_off, mref)
        struct.pack_into('>3f', d, sc_off, 1.0, 1.0, 1.0)
        if vis_off: struct.pack_into('>i', d, vis_off, -1)
        refs.append(w.add_export(p3.exports[tcomp]['class'], coll + 1, ('TrilogyBSP', k + 1), d, flags=tflags, archetype=tarch))
        m = np.eye(4); m[3, :3] = offset
        mats.append(struct.pack('>16f', *m.flatten()))
    # spawn points: a start whose capsule (half height about 90) would be in a converted floor is lifted above it
    if args.fix_starts and bsp_chunks:
        floors = [(pts, n) for surf, pts, uv, n, light in polys if n[2] > 0.7]
        def inside2d(poly, x, y):
            xs, ys = poly[:, 0], poly[:, 1]; x2, y2 = np.roll(xs, -1), np.roll(ys, -1)
            cross = (ys > y) != (y2 > y)
            with np.errstate(divide='ignore', invalid='ignore'):
                xi = (x2 - xs) * (y - ys) / (y2 - ys) + xs
            return np.count_nonzero(cross & (x < xi)) % 2 == 1
        moved = 0
        for i in range(len(p3.exports)):
            cn = cls(i)
            if not ('PlayerStart' in cn or 'EnemyStart' in cn) or not p3.path(i).startswith('TheWorld'): continue
            d = bytearray(p3.export_data(i))
            try:
                o, pr, end = props_at(p3, bytes(d), (26,) + tuple(range(0, 120, 2)))
            except ValueError:
                continue
            v, size = tag_value_offset(p3, bytes(d), o, 'Location')
            if v is None: continue
            x, y, z = struct.unpack_from('>3f', d, v)
            sx, sy, sz = np.array([x, y, z]) - offset             # in source coordinates
            under = [pts[:, 2].mean() for pts, n in floors if sz - 200 <= pts[:, 2].mean() <= sz + 120 and inside2d(pts, sx, sy)]
            if under and max(under) + 130 > sz:
                struct.pack_into(">f", d, v + 8, float(max(under) + 130 + offset[2]))
                w.replace(i, bytes(d)); moved += 1
        print(f'{moved} spawn points lifted above the new floors')
    if args.starts_at:
        x, y, z, yaw = (float(v) for v in args.starts_at.split(','))
        placed = 0
        for i in range(len(p3.exports)):
            if 'PlayerStart' not in cls(i) or not p3.path(i).startswith('TheWorld'): continue
            d = bytearray(p3.export_data(i))
            try:
                o, pr, end = props_at(p3, bytes(d), (26,) + tuple(range(0, 120, 2)))
            except ValueError:
                continue
            d = set_property(p3, w, d, o, 'Location', struct.pack('>3f', x, y, z), struct_name='Vector')
            d = set_property(p3, w, d, o, 'Rotation', struct.pack('>3i', 0, int(yaw), 0), struct_name='Rotator')
            w.replace(i, bytes(d)); placed += 1
        print(f'starts-at: {placed} player starts at ({x:g}, {y:g}, {z:g}) yaw {int(yaw)}')
    if args.pylon_at:
        x, y, z = (float(v) for v in args.pylon_at.split(','))
        for i in range(len(p3.exports)):
            if cls(i) != 'Pylon' or not p3.path(i).startswith('TheWorld'): continue
            d = bytearray(p3.export_data(i)); o, pr, end = props_at(p3, bytes(d), (26,))
            d = set_property(p3, w, d, o, 'Location', struct.pack('>3f', x, y, z), struct_name='Vector')
            # the exploration is bounded by the pylon's expansion sphere (MP_GridlockG3: an override centred on its
            # own map, radius 6,017): it moves with the pylon, radius --pylon-radius
            o, pr, end = props_at(p3, bytes(d), (26,))
            d = set_property(p3, w, d, o, 'ExpansionSphereCenter', struct.pack('>3f', x, y, z), struct_name='Vector')
            o, pr, end = props_at(p3, bytes(d), (26,))
            d = set_property(p3, w, d, o, 'ExpansionRadius', struct.pack('>f', args.pylon_radius), 'FloatProperty')
            w.replace(i, bytes(d))
            print(f'pylon-at: {p3.path(i)} at ({x:g}, {y:g}, {z:g}), expansion radius {args.pylon_radius:g}')
    if args.pylons_at:
        import pylons
        pts = [tuple(float(v) for v in t.split(',')) for t in args.pylons_at.split(';') if t.strip()]
        made_p = pylons.add_pylons(p3, w, pts, args.pylon_radius)
        print(f'pylons-at: {len(made_p)} more pylons: ' + ', '.join(f'{w.names[struct.unpack_from(">i", w.exports[i], 12)[0]]}' for i in made_p))
    if args.environment and src.version == 374:
        # the source with the level's environment: the first with a HeightFog (a level's persistent package), else the first
        env_src = next((sp for sp in srcs if any(sp.class_name(e['class']) == 'HeightFogComponent' for e in sp.exports)), src)
        environment(env_src, p3, w, cls)
    # cover: Gears 1's links written into the target's own CoverLink actors (no new actors: the level's lists stay).
    # The target's navigation mesh finds cover through Level.CoverIndexPairs (link, slot), so the links are paired by
    # where their slots are, and every pair is pointed at the source slot nearest to where its old slot was.
    cover_map = {}                                     # (source index, source CoverLink export) -> target ref
    if args.cover and src.version == 374:
        from scipy.optimize import linear_sum_assignment
        g1 = [link for sp in srcs for link in cover.read_g1_links(sp)]      # every sublevel's cover
        g1_src = [si for si, sp in enumerate(srcs) for _ in cover.read_g1_links(sp)]
        g3 = cover.read_g3_links(p3)
        A = [cover.slot_positions(loc + offset, rot, slots) for _, loc, rot, slots in g1]
        B = [cover.slot_positions(loc, rot, slots) for _, loc, rot, slots in g3]
        C = np.full((len(g1), len(g3)), 1e6)
        for x, a in enumerate(A):
            for y, b in enumerate(B):
                if len(a) and len(b):
                    C[x, y] = np.linalg.norm(a[:, None] - b[None], axis=2).min(1).mean() + 50 * abs(len(a) - len(b))
        rows, cols = linear_sum_assignment(C)
        source_of = {g3[y][0]: x for x, y in zip(rows, cols)}             # target export -> source link
        for ti, x in source_of.items():                    # (Kismet referring to a source link: the target's link)
            cover_map[(g1_src[x], g1[x][0])] = ti + 1
        set_prop = lambda *a, **k: set_property(p3, w, *a, **k)
        new_slots = {}                                     # target export -> world positions of its new slots
        for ti, _, _, _ in g3:
            d = bytearray(p3.export_data(ti))
            o, pr, end = props_at(p3, bytes(d), (26,))
            if ti in source_of:
                _, loc, rot, slots = g1[source_of[ti]]
                d = set_prop(d, o, 'Location', struct.pack('>3f', *(loc + offset)), struct_name='Vector')
                d = set_prop(d, o, 'Rotation', struct.pack('>3i', *rot), struct_name='Rotator')
                value = struct.pack('>i', len(slots)) + b''.join(cover.write_slot_828(w, sl) for sl in slots)
                new_slots[ti] = A[source_of[ti]]
            else:
                value = struct.pack('>i', 0)                   # a spare link: no slots
            d = set_prop(d, o, 'Slots', value, 'ArrayProperty')
            w.replace(ti, bytes(d))
        lvl = next(i for i in range(len(p3.exports)) if cls(i) == 'Level' and p3.path(i) == 'TheWorld.PersistentLevel')
        ld = bytearray(p3.export_data(lvl))
        L = cover.level_lists(p3, bytes(ld), w)
        link_refs, pairs_at, pairs = L['refs'], L['pairs_at'], L['pairs']
        old_pos = {g[0]: B[k] for k, g in enumerate(g3)}
        flat = [(r, j, q) for r, ti in enumerate(link_refs) for j, q in enumerate(new_slots.get(ti, []))]
        P = np.array([q for r, j, q in flat])
        dist = []; reached = set()
        for k, (r, j) in enumerate(pairs):
            ti = link_refs[r]
            if not len(P): break                       # (no source cover among the target's links: nothing to aim at)
            if j >= len(old_pos.get(ti, [])): continue
            dd = np.linalg.norm(P - old_pos[ti][j], axis=1); m = int(dd.argmin()); dist.append(dd[m])
            struct.pack_into('>iB', ld, pairs_at + 5 * k, flat[m][0], flat[m][1]); reached.add(m)
        w.replace(lvl, bytes(ld))
        # source links beyond the target's own: new CoverLink actors (cover.add_links)
        spare = [x for x in range(len(g1)) if x not in set(source_of.values())]
        if spare:
            new_links = cover.add_links(p3, w, len(spare))
            for ti, x in zip(new_links, spare):
                _, loc, rot, slots = g1[x]
                cover_map[(g1_src[x], g1[x][0])] = ti + 1
                d = bytearray(w.current(ti)); o, pr, end = props_at(p3, bytes(d), (26,))
                d = set_prop(d, o, 'Location', struct.pack('>3f', *(loc + offset)), struct_name='Vector')
                o, pr, end = props_at(p3, bytes(d), (26,))
                d = set_prop(d, o, 'Rotation', struct.pack('>3i', *rot), struct_name='Rotator')
                o, pr, end = props_at(p3, bytes(d), (26,))
                d = set_prop(d, o, 'Slots', struct.pack('>i', len(slots)) + b''.join(cover.write_slot_828(w, sl) for sl in slots), 'ArrayProperty')
                w.replace(ti, bytes(d))
            print(f'cover: {len(spare)} more source links as new CoverLink actors')
        if args.starts_at_cover:
            spots = [(loc + offset, rot, sl) for _, loc, rot, slots in g1 for sl in slots]
            placed = 0
            for i in range(len(p3.exports)):
                if 'PlayerStart' not in cls(i) or not p3.path(i).startswith('TheWorld'): continue
                d = bytearray(p3.export_data(i))
                try:
                    o, pr, end = props_at(p3, bytes(d), (26,) + tuple(range(0, 120, 2)))
                except ValueError:
                    continue
                loc, rot, sl = spots[(placed * 7) % len(spots)]
                pos = cover.slot_positions(loc, rot, [sl])[0]
                yaw = rot[1] + sl['rotation'][1]
                fwd = rotation_matrix(0, yaw, 0)[0]
                at = pos - 80 * fwd + (0, 0, 20)
                d = set_prop(d, o, 'Location', struct.pack('>3f', *at), struct_name='Vector')
                o, pr, end = props_at(p3, bytes(d), (o,))
                d = set_prop(d, o, 'Rotation', struct.pack('>3i', 0, yaw, 0), struct_name='Rotator')
                w.replace(i, bytes(d)); placed += 1
                if placed <= 3: print(f'  start {p3.path(i)} at {at.round()} yaw {yaw}, behind a {sl["type"]} slot at {pos.round()}')
            print(f'starts-at-cover: {placed} player starts placed behind cover slots')
        dist = np.array(dist)
        print(f"cover: {len(source_of)} of {len(g1)} source links ({sum(len(g1[x][3]) for x in source_of.values())} slots) "
              f"in the target's links, {len(g3) - len(source_of)} emptied; {len(dist)} navigation cover references re-aimed, "
              f"median {np.median(dist):.0f} units, {int((dist > 200).sum())} beyond 200; {len(reached)} of {len(flat)} source slots referenced")
    # the collection actor: its array property grows, matrices appended
    def set_components(index, keep_old, new_refs, new_mats):
        cd = p3.export_data(index); co, cpr, cend = props_at(p3, cd)
        v, size = tag_value_offset(p3, cd, co, 'StaticMeshComponents')
        n0 = struct.unpack_from('>i', cd, v)[0]
        old = cd[v + 4:v + size] if keep_old else b''
        n_old = n0 if keep_old else 0
        body = struct.pack('>i', n_old + len(new_refs)) + old + struct.pack(f'>{len(new_refs)}i', *new_refs)
        new = bytearray(cd[:v]) + body
        struct.pack_into('>i', new, v - 8, len(body))              # the tag's size field (before ArrayIndex)
        new += cd[v + size:cend] + (cd[cend:cend + 64 * n0] if keep_old else b'') + b''.join(new_mats) + cd[cend + 64 * n0:]
        w.replace(index, new)
        return n0
    set_components(coll, not args.replace, refs, mats)
    if args.replace:                                   # the other collection actors: emptied
        removed = 0
        for i in range(len(p3.exports)):
            if i != coll and cls(i) == 'StaticMeshCollectionActor' and p3.path(i).startswith('TheWorld'):
                removed += set_components(i, False, [], [])
        print(f'replace: {removed} of the target static mesh components detached from their collection actors')
        # the target's kill zones (PhysicsVolume bPainCausing, 100,000 damage a second on MP_GridlockG3) belong to
        # its own layout: disarmed, or they kill the player in the new geometry
        disarmed = 0
        for i in range(len(p3.exports)):
            if cls(i) != 'PhysicsVolume' or not p3.path(i).startswith('TheWorld'): continue
            d = bytearray(w.data(i) if hasattr(w, 'data') else p3.export_data(i))
            try:
                o, pr, end = props_at(p3, bytes(d), (26,) + tuple(range(0, 80, 2)))
            except ValueError:
                continue
            v, size = tag_value_offset(p3, bytes(d), o, 'bPainCausing')
            if v is None or d[v] == 0: continue
            d[v] = 0
            w.replace(i, bytes(d)); disarmed += 1
        print(f'replace: {disarmed} kill volumes of the target disarmed')
    # Kismet (and the actors it refers to) last: the steps above rewrite the Level from its original data, which
    # dropped the new actors from Level.Actors
    if args.kismet and src.version == 374:
        kismet(srcs, [x for x in args.source.split(',') if x], p3, w, cls, args.offset, new_mesh, cover_map, args.content, args.g3cooked, args.player_starts)
    img = w.build()
    out = os.path.join(args.mods, 'GearGame', 'CookedXbox360')
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, os.path.basename(args.target)), 'wb').write(img)
    modpack.update_toc(args.mods, args.g3cooked)
    # check: the collection actor and new objects read back
    q = upk.Package(os.path.join(out, os.path.basename(args.target)))
    qd = q.export_data(coll); qo, qpr, qend = props_at(q, qd)
    arr = {n: v for n, t, k, v in qpr}['StaticMeshComponents']
    print(f'{lit} of {len(actors)} components lit from the source level ({n_unlit} unlit: a constant light map)')
    print(f'{len(img)} bytes; collection: {struct.unpack_from(">i", arr, 0)[0]} components, '
          f'{(len(qd) - qend) // 64} matrices; {len(q.exports)} exports')


def kismet(srcs, names, p3, w, cls, offset_text='0,0,0', new_mesh=None, cover_map=None, content_names=None, g3cooked=None, with_starts=False):
    """every source's Main_Sequence (and all under it) into the target, a subsequence of the target's Main_Sequence"""
    import objcopy, collections
    g1dir = os.path.dirname(os.path.abspath(names[0]))
    g3dir = g3cooked or os.path.dirname(os.path.abspath(p3.filename))   # Gears 3's script and content packages
    g1pkgs = [upk.Package(os.path.join(g1dir, f)) for f in ('Core.xxx', 'Engine.xxx', 'GameFramework.xxx', 'WarfareGame.xxx') if os.path.isfile(os.path.join(g1dir, f))]
    types = objcopy.property_types(g1pkgs)
    script1 = objcopy.Script3(g1pkgs)                  # (Gears 1's enums, read the same way)
    # AI pawn classes the game will have loaded: GearGameContent classes in the target and the game mode's startup
    # package (a Horde/multiplayer slot: GEARGAMEHORDE_SF)
    available = set()
    tname = os.path.splitext(os.path.basename(p3.filename))[0]
    if content_names:                                  # a chapter of its own: the packages it loads (levelpkg)
        content = []
        for n_ in content_names.split(','):
            n_ = n_.strip()
            f_ = n_ if os.path.isfile(n_) else os.path.join(g3dir, n_ if n_.lower().endswith('.xxx') else n_ + '.xxx')
            content.append(upk.Package(f_, tables_only=True))
    elif tname.upper().startswith('SP_'):              # a campaign map: the content of all its packages (SP_<Map>_*)
        prefix = '_'.join(tname.split('_')[:2]).upper() + '_'
        content = [upk.Package(os.path.join(g3dir, f), tables_only=True) for f in os.listdir(g3dir)
                   if f.upper().startswith(prefix) and f.lower().endswith('.xxx') and 'LOC_' not in f.upper()]
    else:                                              # multiplayer / Horde slot: the mode's startup package
        content = [upk.Package(os.path.join(g3dir, 'GEARGAMEHORDE_SF.xxx'), tables_only=True)]
    for pk in [p3] + content:
        available |= {e['name'] for e in pk.exports if pk.class_name(e['class']) == 'Class' and e['outer'] > 0
                      and pk.exports[e['outer'] - 1]['name'] == 'GearGameContent'}
    offset = tuple(float(v) for v in offset_text.split(','))
    g3pkgs = [upk.Package(os.path.join(g3dir, f)) for f in ('Core.xxx', 'Engine.xxx', 'GameFramework.xxx', 'GearGame.xxx') if os.path.isfile(os.path.join(g3dir, f))]
    classes = objcopy.class_index(g3pkgs)
    script3 = objcopy.Script3(g3pkgs)
    level3 = next(i for i in range(len(p3.exports)) if cls(i) == 'Level' and p3.path(i) == 'TheWorld.PersistentLevel')
    main3 = next(i for i in range(len(p3.exports)) if p3.path(i) == 'TheWorld.PersistentLevel.Main_Sequence')
    added = []
    stats = collections.Counter()
    only = [x for x in os.environ.get('LEVELINJECT_KISMET_ONLY', '').split(',') if x]   # (debugging) these packages' Kismet only
    for si, (sp, fname) in enumerate(zip(srcs, names)):
        if only and os.path.splitext(os.path.basename(fname))[0] not in only: continue
        main1 = next((i for i in range(len(sp.exports)) if sp.path(i) == 'TheWorld.PersistentLevel.Main_Sequence'), None)
        lvl = next((i for i in range(len(sp.exports)) if sp.path(i) == 'TheWorld.PersistentLevel'), None)
        if lvl is None: continue
        level1 = lvl + 1                                # (a package without Kismet still has actors to copy)
        c = objcopy.Copier(sp, p3, w, types, classes, script3, offset, script1)
        c.available_pawns = available
        for (msi, mi), ref in (new_mesh or {}).items():     # converted meshes stand for the source's
            if msi == si: c.obj_map[mi + 1] = ref
        for (isi, iref), key in imported_skel.items():     # ... and for its imports of them
            if isi == si and key in (new_mesh or {}): c.obj_map[iref] = new_mesh[key]
        for (csi, ci), ref in (cover_map or {}).items():    # cover links: the target's links holding their data
            if csi == si: c.obj_map[ci + 1] = ref
        todo = c.plan([main1]) if main1 is not None else []
        # the actors this Kismet refers to (triggers...): before it, so its references resolve to them
        new_actors = []
        lights_ = [] if os.environ.get('LEVELINJECT_NO_LIGHTS') else objcopy.lights(sp)   # (the level's lights, for characters)
        starts_ = objcopy.player_starts(sp) if with_starts else []
        wanted = objcopy.kismet_actors(sp, todo, objcopy.movers(sp) + objcopy.skeletal_actors(sp) + objcopy.sound_actors(sp) + lights_ + starts_)
        copied = []
        for ai in wanted:
            cn = sp.class_name(sp.exports[ai]['class'])
            stats['actors: ' + cn] += 1
            if cn == 'TriggerVolume':
                r = objcopy.volume_as_trigger(c, ai, level3 + 1)
                if r: new_actors.append(r)
            else:
                copied.append(ai)
        # in one pass: every reference between them resolves (exports are made before any data is written)
        made_a = c.copy(c.plan(copied), {level1: level3 + 1})
        new_actors += [made_a[ai] for ai in copied if made_a.get(ai)]
        for ai in objcopy.collision_enabled_by_script(sp) & set(made_a):    # off until the script turns it on
            if made_a[ai]: objcopy.collision_off(c, made_a[ai]); stats['actors: collision off until scripted'] += 1
        objcopy.add_to_level(p3, w, level3, new_actors)
        # navigation points among them (player starts, AI spawn points) join the level's navigation point list
        nav_new = [made_a[ai] - 1 for ai in copied if made_a.get(ai) and sp.class_name(sp.exports[ai]['class']) in ('PlayerStart', 'PathNode')]
        if nav_new:
            import cover
            stats['navigation points listed'] += cover.append_nav_points(p3, w, nav_new)
        tag = os.path.splitext(os.path.basename(fname))[0]
        made = c.copy(todo, {level1: level3 + 1}, rename={main1: f'G1_{tag}'}) if todo else {}
        stats['objects'] += len(made); stats['dropped (no Gears 3 class)'] += len(todo) - len(made)
        if not os.environ.get('LEVELINJECT_NO_LINK_UPGRADE'):
            up = objcopy.Upgrader(c, script3)
            for k, v in up.run(made).items(): stats[k] += v
            stats['streaming actions emptied'] += objcopy.neutralize_streaming(up, made)
            stats['cutscenes: view restored after'] += objcopy.restore_view_after_cutscenes(up, made)
            stats['squad factories (player slot)'] += objcopy.squad_factories(up, made)
            stats['checkpoint events also on save'] += objcopy.checkpoint_events(up, made)
        for i in todo:
            if i not in made: stats['missing: ' + sp.class_name(sp.exports[i]['class'])] += 1
        for k, v in c.unconvertible.items(): stats['unconvertible: ' + k] += v
        for k, v in getattr(c, 'standins', {}).items(): stats[f'stand-in: {k} -> {v}'] = 1
        top = made.get(main1) if main1 is not None else None
        if not top: continue
        d = bytearray(w.current(top - 1))
        d = set_property(p3, w, d, 4, 'ParentSequence', struct.pack('>i', main3 + 1), 'ObjectProperty')
        w.replace(top - 1, bytes(d))
        added.append(top)
    if added:                                          # the target's Main_Sequence lists them
        d = bytearray(w.current(main3)); o, pr, end = props_at(p3, bytes(d), (4,))
        v, size = tag_value_offset(p3, bytes(d), o, 'SequenceObjects')
        n = struct.unpack_from('>i', d, v)[0]
        value = struct.pack('>i', n + len(added)) + bytes(d[v + 4:v + size]) + b''.join(struct.pack('>i', r) for r in added)
        d = set_property(p3, w, d, o, 'SequenceObjects', value, 'ArrayProperty')
        w.replace(main3, bytes(d))
    si_ = [k[10:] for k in stats if k.startswith('stand-in: ')]
    if si_: print('kismet: AI not loaded in this game mode, spawned as stand-ins:', ', '.join(si_))
    acts = [(k[8:], v) for k, v in stats.items() if k.startswith('actors: ')]
    if acts: print('kismet: actors it refers to, copied:', ', '.join(f'{k} {v}' for k, v in acts), '(TriggerVolume as Trigger)')
    unconv = [(k[15:], v) for k, v in stats.items() if k.startswith('unconvertible: ')]
    if unconv: print('kismet: dropped, data not convertible:', ', '.join(f'{k} {v}' for k, v in unconv))
    top_missing = sorted(((k, v) for k, v in stats.items() if k.startswith('missing')), key=lambda kv: -kv[1])[:8]
    print(f"kismet: links rebuilt from Gears 3's classes on {stats['ops upgraded']} ops, {stats['links kept from Gears 1']} Gears 1 "
          f"links without a Gears 3 counterpart kept; {stats['streaming actions emptied']} level streaming/visibility actions "
          f"{'renamed' if objcopy.STREAMING_PREFIX is not None else 'emptied'}; {stats['squad factories (player slot)']} squad factories")
    print(f"kismet: {len(added)} sequences, {stats['objects']} objects copied, {stats['dropped (no Gears 3 class)']} without a "
          f"Gears 3 class: {', '.join(f'{k[9:]} {v}' for k, v in top_missing)}")


def environment(src, p3, w, cls):
    """Gears 1's post-process settings (WorldInfo.DefaultPostProcessSettings) into the target's PostProcessVolume, its
    colour grading table dropped; Gears 1's HeightFog colour and brightness on the target's ExponentialHeightFog"""
    def find(p, c):
        return next((i for i, e in enumerate(p.exports) if p.class_name(e['class']) == c and p.path(i).startswith('TheWorld')), None)
    def struct_value(p, i, prop, starts):
        d = p.export_data(i); o, pr, end = props_at(p, d, starts)
        v, size = tag_value_offset(p, d, o, prop)
        return d, v, size
    wi = find(src, 'WorldInfo'); ppv = find(p3, 'PostProcessVolume')
    if wi is not None and ppv is not None:
        _, gv, gsize = struct_value(src, wi, 'DefaultPostProcessSettings', (32, 4, 8))
        g1 = {r[0]: r for r in materialconv.raw_props(src, src.export_data(wi), gv)[0]} if gv is not None else {}
        d, v, size = struct_value(p3, ppv, 'Settings', (26,))
        recs, _ = materialconv.raw_props(p3, d, v)
        out, have = [], set()
        for r in recs:
            name = r[0]
            if name in ('ColorGrading_LookupTable', 'Bloom_Tint'): continue        # the night grading, the bloom tint
            if name == 'bOverride_Scene_ColorGradingLUT': r[4] = b'\x00'
            if name in g1 and name not in ('bEnableDOF',):
                g = g1[name]
                if g[1] == 'BoolProperty': r[4] = b'\x01' if g[4] != bytes(len(g[4])) else b'\x00'
                elif g[1] == 'StructProperty' and g[3] != b'':
                    r[4] = g[4]                                                   # Vector, Color: same layout
                else:
                    r[4] = g[4]
            out.append(r); have.add(name)
        for name in ('Scene_Desaturation', 'Scene_HighLights', 'Scene_MidTones', 'Scene_Shadows', 'Bloom_Scale',
                     'DOF_FocusDistance', 'DOF_ModulateBlurColor'):
            if name in g1 and name not in have:
                g = g1[name]; extra = struct.pack('>ii', *w.name(src.fname(*struct.unpack('>ii', g[3])))) if g[3] else b''
                out.append([name, g[1], 0, extra, g[4]])
        body = materialconv.encode_props(w, out)
        nd = bytearray(d[:v]) + body + d[v + size:]
        struct.pack_into('>i', nd, v - 16, len(body))                # the tag's size (struct name follows it)
        w.replace(ppv, bytes(nd))
        print(f"environment: post-process from the source's WorldInfo ({', '.join(sorted(n for n in g1 if n in have or n.startswith('Scene')))}), no grading table")
    hf = find(src, 'HeightFogComponent'); ef = find(p3, 'ExponentialHeightFogComponent')
    if hf is not None and ef is not None:
        o, pr, end = props_at(src, src.export_data(hf), (8, 4, 12))
        a = {n: v for n, t, k, v in pr}
        color = a['LightColor'][1] if 'LightColor' in a else bytes((255, 255, 255, 0))
        bright = a.get('LightBrightness', 1.0)
        d = bytearray(p3.export_data(ef)); o, pr, end = props_at(p3, bytes(d), (8, 4, 12))
        for name, value in (('OppositeLightColor', color), ('LightInscatteringColor', color),
                            ('OppositeLightBrightness', struct.pack('>f', bright)),
                            ('LightInscatteringBrightness', struct.pack('>f', bright * 0.5))):
            vv, sz = tag_value_offset(p3, bytes(d), o, name)
            if vv is not None and sz == len(value): d[vv:vv + sz] = value
        w.replace(ef, bytes(d))
        print(f'environment: fog colour BGRA {color.hex()} x {bright:.2f}')


if __name__ == '__main__':
    main()
