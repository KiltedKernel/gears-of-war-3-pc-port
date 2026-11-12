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


def _aux_package(sp, name):
    """a Gears 1 package beside source sp, by name (cached with g1_import's), or None"""
    if name not in _aux:
        f = os.path.join(os.path.dirname(os.path.abspath(sp.filename)), name + '.xxx')
        _aux[name] = upk.Package(f) if os.path.isfile(f) else None
    return _aux[name]


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
# Gears 1's fog sheets and light cones on the kit's fog material (--fog-material, M_EV_FogSheet_Master_01, its compiled
# permutation): Gears 1's opacity (Brightness / BeamOpacity) times FOG_OPACITY (the master multiplies in its own falloff
# and cloud textures: at Gears 1's value the cell block's sun beams, 0.1, did not show); faded over 1 / DepthBasedDensity
# units of depth behind them (Gears 1's DepthBiasedBlend: bias 0.5 x 500); no edge falloff (FalloffExponent 0: Gears 1's
# cones have none, and with the master's default of 3 the converted beams did not draw at all)
FOG_SCALARS = {'Density': 1.0, 'DepthBasedDensity': 0.004, 'FalloffExponent': 0.0}
FOG_OPACITY = 1.5
# converted skeletal meshes' least bounds extent (no physics asset: their bounds stand for the animated travel)
SKEL_MIN_EXTENT = 1024.0
COPLANAR_NUDGE = 0.4      # units a merged mesh face lying on a BSP face is moved off it
# vector parameters set on the masked template's copies (a pickup material: no glint)
MASKED_VECTORS = {'ShimmerColor': (0.0, 0.0, 0.0, 0.0)}
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
        try: co, cpr, cend = props_at(p, p.export_data(comp - 1), (8, 4, 12))
        except ValueError: cpr = props_at(p, p.export_data(comp - 1), (8,))[1]    # none set (Embry's S05_Detail)
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


_weapon_guids = {}


def weapon_shader_extra(args, vectors=None):
    """materialconv.child_mic's extra= for an instance of Gears 3's weapon shader (WeaponShader_SP, GearGame.xxx): its
    dropped-weapon glint (ShimmerColor, Weap_DropGlowAlpha / Dist) and environment reflection (envMult) off, by the
    master's ExpressionGUIDs (the HOD instance carries none of them); None without GearGame.xxx"""
    gg_ = os.path.join(args.g3cooked or '', 'GearGame.xxx')
    if gg_ not in _weapon_guids:
        _weapon_guids[gg_] = None
        if os.path.isfile(gg_):
            gp_ = upk.Package(gg_)
            wm_ = next((i for i in range(len(gp_.exports)) if gp_.path(i) == 'ALL_WeaponShaders.SinglePlayer.WeaponShader_SP'
                        and gp_.class_name(gp_.exports[i]['class']) == 'Material'), None)
            if wm_ is not None: _weapon_guids[gg_] = materialconv.material_param_guids(gp_, wm_)
    if _weapon_guids[gg_] is None: return None
    return (_weapon_guids[gg_], {'envMult': 0.0, 'Weap_DropGlowAlpha': 0.0, 'Weap_DropGlowDist': 0.0},
            {'ShimmerColor': (0.0, 0.0, 0.0, 0.0), **(vectors or {})})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target'); ap.add_argument('source', help='Gears 1/2 level package(s), comma-separated (a level and its sublevels)'); ap.add_argument('mods'); ap.add_argument('g3cooked')
    ap.add_argument('--max', type=int, default=0)
    ap.add_argument('--offset', default='0,0,0'); ap.add_argument('--center', default=None)
    ap.add_argument('--radius', type=float, default=0)
    ap.add_argument('--template', default=None, help='a component whose StaticMesh path contains this (its materials)')
    ap.add_argument('--no-lighting', action='store_true', help='no light maps (unlit)')
    ap.add_argument('--shared-textures', default=None, help="a chapter's texture package: converted textures are imports of "
                    "it, recorded in --texture-manifest for trilogy.py to write")
    ap.add_argument('--texture-manifest', default=None)
    ap.add_argument('--facefx-assets', default='', help='"G1 FaceFX asset name=package.xxx:object path;...": the cutscene meshes take these Gears 3 FaceFX actors')
    ap.add_argument('--no-simple-collision', action='store_true', help='Gears 1 meshes block pawns by their triangles, not by their simple collision (BodySetup hulls)')
    ap.add_argument('--no-uv-transforms', action='store_true', help="Gears 1 materials' UV set and tiling not carried into the meshes' UVs")
    ap.add_argument('--merge', action='store_true', help='mesh actors joined into one mesh per 4096-unit cell and collision class (fewer draws)')
    ap.add_argument('--no-point-lights', action='store_true', help="do not bake the source level's dynamic point lights")
    ap.add_argument('--no-collision', action='store_true', help="keep the template's collision flags")
    ap.add_argument('--bsp', action='store_true', help="also the level's BSP (Gears 1), as static mesh chunks")
    ap.add_argument('--replace', action='store_true', help="the target's own static meshes go: every collection actor "
                    "keeps only the new components (its gameplay actors, volumes, cover and BSP stay)")
    ap.add_argument('--cover-from', default='', help="with --cover: more Gears 1 packages (comma separated) whose "
                    "CoverLinks are converted into this one: Gears 3 finds a player's cover through the navigation mesh "
                    "polygons' cover references, object references into the pylon's own package, so cover standing on "
                    "another section's navmesh goes into that section (the prison's cell: S01's cover, S02's pylon)")
    ap.add_argument('--no-own-cover', action='store_true', help="with --cover: the sources' own CoverLinks left out "
                    "(converted into another section with its --cover-from)")
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
    ap.add_argument('--masked-material', default=None, help="a masked Gears 3 MaterialInstanceConstant of the target "
                    "itself (path substring; its parent takes Diffuse_01 / Normal_01 and a mask texture read for its "
                    "alpha): masked Gears 1 materials (fences, razor wire, grass) become child instances of it, their "
                    "cutout the diffuse's alpha, or a DXT1 mask's red as its alpha (materialconv.red_to_alpha)")
    ap.add_argument('--fog-material', default='EngineVolumetrics.Fogsheet.Materials.M_EV_FogSheet_Master_01',
                    help="a translucent Gears 3 Material of the kit (or the target), with its shaders compiled there: Gears 1's "
                    "fog sheets, light cones and light rays (materialconv.g1_volumetric) are drawn with it; 'none': they "
                    "are left out")
    ap.add_argument('--extra-floors', default='', help="hidden world collision boxes 'x0,y0,z0,x1,y1,z1;...' (world units, "
                    "before --offset): floor where a pawn steps off a mover onto world geometry (Gears 3's ledge check "
                    "stopped the player there)")
    ap.add_argument('--kit', default=None, help="a chapter of its own: the Gears 3 package (the chapter's persistent level, "
                    "loaded first) holding the material instance templates (--materials, the skeletal one): converted "
                    "materials are child instances of them, through imports (tools/levelpkg.py)")
    ap.add_argument('--skin-material', default=None, help="<Gears 3 package>:<MIC path>: the skinned material whose "
                    "child instances the converted skeletal meshes' materials become (its Diffuse / Normal / Specular "
                    "set, its other texture parameters neutral); the package must be loaded where the level is (e.g. "
                    "GEARGAMESP_SF in the campaign: COG_Dom_Summer.Materials.M_COG_Dom_Summer_Game)")
    ap.add_argument('--terrain-two-sided', action='store_true', help='terrain triangles in both facings (navmesh slots)')
    ap.add_argument('--bodies-block-traces', action='store_true', help="the hidden collision meshes (Gears 1's blocking "
                    "volumes and simple collision) block line traces too: for a navmesh build (its builder traces down to the "
                    "floor; with them for pawns only, Embry's mesh floors were not found)")
    ap.add_argument('--light-radius-min', type=float, default=0.0, help="copied lights reach at least this far "
                    "(objcopy.LIGHT_RADIUS_MIN: outdoor characters)")
    ap.add_argument('--sun-from', default='', help="Gears 1 packages (comma separated) whose DirectionalLights are copied "
                    "in as the sun (objcopy sun point lights): for the persistent level")
    ap.add_argument('--character-ambient', default=None, help="r,g,b: cutscene characters' light environments' AmbientGlow "
                    "(objcopy.CHARACTER_AMBIENT)")
    ap.add_argument('--light-scale', type=float, default=1.0, help="the copied lights' brightness (and Matinee tracks "
                    "animating it) times this (objcopy.LIGHT_SCALE)")
    ap.add_argument('--chapter-lights', default=None, help="a .npz of the chapter's lights in no Gears 1 light map "
                    "(trilogy: lighting.unbaked_lights over all its packages): baked instead of the sources' point lights")
    ap.add_argument('--g3-effects', default='', help="Gears 1 particle systems / particle materials standing for Gears 3 "
                    "objects the chapter loads (a carrier's): Name=Path,... (objcopy.PARTICLE_SYSTEMS, PARTICLE_MATERIALS)")
    ap.add_argument('--max-character-texture', type=int, default=None, help="the same for skeletal meshes' textures "
                    "(default: --max-texture)")
    ap.add_argument('--max-texture', type=int, default=0, help="converted textures' mips larger than this are left out "
                    "(they stay resident in Gears 3's texture pool, which streaming textures share; 0: all kept)")
    ap.add_argument('--hair-material', default=None, help="<Gears 3 package>:<MIC path>: a masked skinned hair material "
                    "(its texture parameter 'Diffuse And Alpha': colour and cutout) whose copies (materialconv.clone_static_mic) "
                    "masked Gears 1 materials of skeletal meshes become (Dom's and Marcus's hair cards drew solid and light with the skin "
                    "material); the package must be loaded where the level is (a recipe's load_packages)")
    ap.add_argument('--content', default=None, help="a chapter of its own: the Gears 3 packages (comma-separated) whose "
                    "GearGameContent classes are loaded with it (AI pawn classes; default: by the target's map name)")
    ap.add_argument('--streaming-prefix', default=None, help="a chapter of its own: level names in streaming actions get "
                    "this prefix (the converted packages' names) when in --streaming-built, else are dropped")
    ap.add_argument('--streaming-built', default='', help='with --streaming-prefix: the source package names built')
    ap.add_argument('--chapter-number', type=int, default=None, help="a chapter of its own: its campaign chapter "
                    "number (Gears 1's chapter complete gets ChapterCompleted = number + 1)")
    ap.add_argument('--last-chapter', action='store_true', help="with --chapter-number: no next chapter is built (the "
                    "chapter's map change ends the game instead)")
    ap.add_argument('--pawn-classes', default='', help="Gears 1 pawn class=Gears 3 class, comma separated: factories "
                    "spawn that class (its content package loaded by the mod: mod.ini content =)")
    ap.add_argument('--kismet-relink', default='', help="Package/Op.Output>Op.Output:Target;...: a Kismet link moved to another output (in that source package's conversion)")
    ap.add_argument('--actors-no-collision', default='', help="Package/Actor;...: actors that neither collide nor block (when the source is that package)")
    ap.add_argument('--object-vars', default='', help="VarPathSuffix=Actor;...: Kismet object variables pointed at another actor of the level")
    ap.add_argument('--track-offsets', default='', help="Group@time:x,y,z;...: a Matinee group's position keys from that time on moved (a door swinging through a floor)")
    ap.add_argument('--player-spawn-at', default=None, help="with --player-pawn: x,y,z,yaw: where the new pawn spawns (beside the player start, which the old pawn stands on)")
    ap.add_argument('--player-pawn', default=None, help="with --kit: 'Class:op:output': after that op's output (its path "
                    "in the source's Main_Sequence) an AI factory makes the player that Gears 3 class (squad slot 1), "
                    "spawned on the kit's player start")
    ap.add_argument('--named-vars', default=None, help="with --streaming-prefix: the chapter's persistent source package; "
                    "named variables it defines, used by name in this package's Kismet, become imports of its converted "
                    "variables (Gears 3 resolves a name in its own level only)")
    ap.add_argument('--player-starts', action='store_true', help="with --kismet: the sources' PlayerStarts copied too (a "
                    "chapter's persistent level: the campaign starts there)")
    args = ap.parse_args()
    materialconv.MAX_TEXTURE_SIZE = args.max_texture
    materialconv.MAX_CHARACTER_TEXTURE_SIZE = args.max_character_texture
    for pair in [x for x in args.g3_effects.split(',') if '=' in x]:
        g1, g3 = (y.strip() for y in pair.split('=', 1))
        import objcopy as _oc
        _oc.PARTICLE_SYSTEMS[g1] = g3; _oc.PARTICLE_MATERIALS[g1] = g3
    import objcopy
    objcopy.LIGHT_SCALE = args.light_scale
    if args.light_radius_min: objcopy.LIGHT_RADIUS_MIN = args.light_radius_min
    global SUN_FROM
    SUN_FROM = [x for x in args.sun_from.split(',') if x]
    if args.character_ambient: objcopy.CHARACTER_AMBIENT = tuple(float(x) for x in args.character_ambient.split(','))
    offset = np.array([float(x) for x in args.offset.split(',')])
    if args.streaming_prefix is not None:
        import objcopy
        objcopy.STREAMING_PREFIX = args.streaming_prefix
        objcopy.STREAMING_BUILT = {x.strip().lower() for x in args.streaming_built.split(',') if x.strip()}
    if args.chapter_number is not None:
        import objcopy
        objcopy.set_chapter(args.chapter_number, args.last_chapter)
    if args.pawn_classes:
        import objcopy
        objcopy.PAWN_OVERRIDE = dict(x.strip().split('=', 1) for x in args.pawn_classes.split(',') if '=' in x)
    if args.player_pawn and (args.kit or args.player_spawn_at):
        import objcopy
        cls, op, out = (x.strip() for x in args.player_pawn.split(':'))
        # (the persistent level itself: no kit, it is the kit; its player start from the target)
        kit_ = args.kit or args.target
        kp = upk.Package(kit_)
        ps = next((e['name'] for e in kp.exports if kp.class_name(e['class']) == 'PlayerStart'), None)
        if ps or args.player_spawn_at:                 # (with player_spawn_at the player start is not used)
            ps = ps or 'PlayerStart'
            base, num = (ps.rsplit('_', 1)[0], int(ps.rsplit('_', 1)[1]) + 1) if ps.rsplit('_', 1)[-1].isdigit() else (ps, 0)
            objcopy.PLAYER_PAWN = (cls, op, out)
            if args.player_spawn_at: objcopy.PLAYER_SPAWN_AT = tuple(float(x) for x in args.player_spawn_at.split(','))
            objcopy.PLAYER_SPAWN = [('Core', 'Package', os.path.splitext(os.path.basename(kit_))[0], 0), ('Engine', 'World', 'TheWorld', 0),
                                    ('Engine', 'Level', 'PersistentLevel', 0), ('Engine', 'PlayerStart', base, num)]
    if args.kismet_relink:
        import objcopy
        srcbase = os.path.splitext(os.path.basename(args.source.split(',')[0]))[0].lower()
        for item in args.kismet_relink.split(';'):
            if not item.strip() or '/' not in item: continue
            pkg, rest = item.split('/', 1)
            if pkg.strip().lower() != srcbase: continue
            frm, rest = rest.split('>'); to, target = rest.split(':')
            split = lambda x: tuple(v.strip() for v in x.split('.', 1))
            objcopy.KISMET_RELINK.append((split(frm), split(to), target.strip()))
    if args.actors_no_collision:
        import objcopy
        srcbase = os.path.splitext(os.path.basename(args.source.split(',')[0]))[0].lower()
        for item in args.actors_no_collision.split(';'):
            if '/' not in item: continue
            pkg, actor = (x.strip() for x in item.split('/', 1))
            if pkg.lower() == srcbase: objcopy.NO_COLLISION.add(actor)
    if args.object_vars:
        import objcopy
        for item in args.object_vars.split(';'):
            if '=' in item: k, v = item.split('='); objcopy.OBJECT_VAR_OVERRIDES[k.strip()] = v.strip()
    if args.track_offsets:
        import objcopy
        for item in args.track_offsets.split(';'):
            if not item.strip(): continue
            g, rest = item.split('@'); t, v = rest.split(':')
            objcopy.TRACK_OFFSETS[g.strip()] = (float(t), tuple(float(x) for x in v.split(',')))
    if args.named_vars:
        import objcopy
        base = os.path.splitext(os.path.basename(args.named_vars))[0]
        objcopy.NAMED_REMOTE = objcopy.persistent_named_vars(upk.Package(args.named_vars), (args.streaming_prefix or '') + base, f'G1_{base}')

    # one or more source packages (a Gears 1 level and its streamed sublevels share world coordinates): actors,
    # meshes and materials are keyed by (source index, export index)
    srcs = [upk.Package(f) for f in args.source.split(',') if f]
    src = srcs[0]
    actors = []                                        # (source index, mesh, location, rotation, scale, component)
    for si, sp in enumerate(srcs):
        actors += [(si,) + a for a in source_actors(sp)]
    # the same mesh placed twice at the same spot (Gears 1's S02_Meshes and S05_Meshes have 11): drawn once (twice
    # they z-fought)
    seen_, unique_ = set(), []
    for a in actors:
        k_ = (srcs[a[0]].path(a[1]), tuple(np.round(a[2], 1)), tuple(int(r) % 65536 for r in a[3]), tuple(np.round(a[4], 3)))
        if k_ in seen_: continue
        seen_.add(k_); unique_.append(a)
    if len(unique_) != len(actors): print(f'{len(actors) - len(unique_)} mesh actors placed twice at the same spot: once')
    actors = unique_
    if args.center:
        c = np.array([float(x) for x in args.center.split(',')])
        actors = [a for a in actors if np.linalg.norm(a[2] - c) <= args.radius]
    if args.max: actors = actors[:args.max]
    # a component's own materials (Gears 1 StaticMeshComponent.Materials: per element, 0 = the mesh's) override the
    # mesh's: a sixth of the prison's components have them (the sky dome's sky, decal overlays, tinted walls); they
    # drew with their meshes' materials before
    overrides = {}                                     # (source, component) -> [material ref per element]
    for a in actors:
        si, ci = a[0], a[5]
        try:
            co_, cpr_, _ = props_at(srcs[si], srcs[si].export_data(ci), (8, 4, 12))
            raw = {n_: v_ for n_, t_, k_, v_ in cpr_}.get('Materials')
            if raw:
                ov = list(struct.unpack_from(f'>{struct.unpack_from(">i", raw, 0)[0]}i', raw, 4))
                if any(ov): overrides[(si, ci)] = ov
        except Exception:
            pass
    mesh_elements = {}
    def element_materials(si, mi, ci):
        if (si, mi) not in mesh_elements: mesh_elements[(si, mi)] = staticmesh.read(srcs[si], mi)['lods'][0]['elements']
        els = mesh_elements[(si, mi)]
        ov = overrides.get((si, ci), [])
        return [ov[k] if k < len(ov) and ov[k] else el['material'] for k, el in enumerate(els)]
    # meshes drawn only see-through: the fog sheets, light cones and light rays drawn with the kit's translucent
    # fog material (--fog-material) when it has one; the rest (chain-link fences, moss decals) left out until their
    # materials convert
    fog_index = None
    if args.materials and src.version == 374 and args.fog_material.lower() != 'none':
        fp = upk.Package(args.kit or args.target, tables_only=True)
        fog_index = next((i for i, e in enumerate(fp.exports) if fp.path(i) == args.fog_material
                          and fp.class_name(e['class']) == 'Material'), None)
        print(f"fog material: {args.fog_material if fog_index is not None else 'none in ' + (args.kit or args.target)}")
    see_through = {}
    def key_of(a): return a[:2] + tuple(overrides.get((a[0], a[5]), ()))
    for a in actors:
        if key_of(a) not in see_through:
            try:
                els_ = element_materials(a[0], a[1], a[5])
                see_through[key_of(a)] = materialconv.g1_see_through(srcs[a[0]], [{'material': x} for x in els_]) and not all(
                    (fog_index is not None and materialconv.g1_volumetric(srcs[a[0]], x))
                    or (args.masked_material and x > 0 and materialconv.g1_mask(srcs[a[0]], x - 1) is not None) for x in els_)
            except Exception: see_through[key_of(a)] = False
    n_before = len(actors)
    actors = [a for a in actors if not see_through[key_of(a)]]
    print(f'{n_before - len(actors)} see-through mesh actors left out ({sum(see_through.values())} meshes); '
          f'{len(overrides)} components with their own materials')
    bsp_chunks, polys = [], []                         # chunks: (source index, materials, mesh)
    if args.bsp:
        import terrainconv
        for si, sp in enumerate(srcs):
            if sp.version != 374: continue
            try:
                wm = bspconv.world_model(sp)
                model = bspconv.read_model_374(sp, wm)
                sp_polys = bspconv.polygons(model, sp, bspconv.node_lightmaps(sp, wm + 1))
            except ValueError:
                model, sp_polys = terrainconv.empty_model(), []
            # Gears 1's terrain (Embry Square's floors): BSP-style polygons with surfaces of their own (terrainconv)
            if not os.environ.get('LEVELINJECT_NO_TERRAIN'):
                t_polys = terrainconv.polygons(sp, model)
                # (a navmesh slot) both facings: the builder's line checks passed through the converted terrain
                # (pawns stood on it); line checks against the collision tree may be one-sided
                if args.terrain_two_sided: t_polys += [(t[0], t[1][::-1], t[2][::-1], -t[3], t[4]) for t in t_polys]
                if t_polys: print(f'terrain: {len(t_polys)} triangles')
                sp_polys += t_polys
            if args.center:
                sp_polys = [x for x in sp_polys if np.linalg.norm(x[1].mean(0) - c) <= args.radius]
            if not sp_polys: continue
            # the surfaces' materials' texture mapping (materialconv.g1_uv_transform; BSP has UV set 0 only)
            if not args.no_uv_transforms and not os.environ.get('LEVELINJECT_NO_UV'):
                tc_ = {}
                for k_, poly in enumerate(sp_polys):
                    mat_ = model['surfs'][poly[0]]['material']
                    if mat_ not in tc_: tc_[mat_] = materialconv.g1_uv_transform(sp, mat_)
                    t_ = tc_[mat_]
                    if t_ and t_[0] == 0:
                        sp_polys[k_] = (poly[0], poly[1], np.asarray(poly[2]) * np.array(t_[1]) + np.array(t_[2])) + tuple(poly[3:])
            polys += sp_polys
            bsp_chunks += [(si,) + ch for ch in bspconv.chunk_meshes(model, sp_polys)]
        print(f'BSP: {len(polys)} polygons in {len(bsp_chunks)} chunks')
    # with --kismet: the meshes of the sources' InterpActors (doors, movers: copied as actors) are converted too
    mover_meshes, skel_meshes = set(), {}
    scripted_components = set()                        # (source, component) of mesh actors copied with the Kismet
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
        # StaticMeshActors the Kismet refers to (shootable panels): copied as actors with it (objcopy), not drawn here;
        # their pawn collision (Gears 1's hulls) still made here (the copies block shots only: the cell door's sunken
        # pipe, hidden but referenced by the opening, blocked by its triangles stuck the player at 240 fps again)
        scripted = {si: objcopy.scripted_mesh_actors(sp) for si, sp in enumerate(srcs)}
        scripted_components.update((a[0], a[5]) for a in actors if srcs[a[0]].exports[a[5]]['outer'] - 1 in scripted[a[0]])
        if scripted_components: print(f'{len(scripted_components)} mesh actors the Kismet refers to: copied as actors')
    mesh_keys = set(a[:2] for a in actors) | mover_meshes
    print(f'{len(actors)} source actors, {len(set(a[:2] for a in actors))} meshes ({len(mover_meshes)} more for movers), from {len(srcs)} packages')

    p3 = upk.Package(args.target)
    upk.E = '>'
    w = upkwrite.Rewriter(p3)
    p3.names = w.names                                 # names the conversion adds read back too (one shared list)
    cls = lambda i: p3.class_name(p3.exports[i]['class'])
    # template component: no lightmap (4-byte native tail), with Scale3D and VisibilityId properties
    tcomp = fallback = None
    # a collection actor's components first (SP_Azura2_02_S's only light-map-less one is a pickup's: its settings left
    # every converted mesh black)
    in_collection = lambda i: p3.exports[i]['outer'] > 0 and cls(p3.exports[i]['outer'] - 1) == 'StaticMeshCollectionActor'
    order = sorted(range(len(p3.exports)), key=lambda i: not in_collection(i))
    for i in order:
        if fallback is not None and not args.template and not in_collection(i) and in_collection(fallback): break
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
    # template's IrrelevantLights (GUIDs of the lights that do not affect the original) do not apply here; nor its
    # own placement (SP_Azura2_02_S's: Translation, Rotation, Scale 0.625: every converted mesh shrunk and moved)
    for prop in ('Materials', 'IrrelevantLights', 'Translation', 'Rotation', 'Scale'):
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
    emissive_tex = {}                                  # (source, material ref) -> its emissive texture (below)
    # a chapter of its own (--kit): the material templates are the kit's (the chapter's persistent level); the
    # converted materials are child instances of them, imported (materialconv.child_mic), and the converted textures
    # take a texture of this package with the same compression and LOD group as their property template
    pk = upk.Package(args.kit) if args.kit else None
    upk.E = '>'
    kit_imports = {}
    # --shared-textures <package> --texture-manifest <json>: a chapter's converted textures live once, in a package of
    # their own that is always loaded (each section converting its own copies, 213 of the prison's 330 MB of
    # textures were duplicates); a section imports them, and the manifest records what that package must hold
    manifest = None
    if args.shared_textures:
        import json
        manifest = json.load(open(args.texture_manifest)) if os.path.isfile(args.texture_manifest) else {'names': {}, 'entries': {}}
    shared_pkg, kit_tex_template = [], {}
    def shared_texture(sp, ti, tt, tkey, slot, mask=None, character=False, scale=None):
        name = manifest['names'].get(tkey)
        if name is None:
            base = name = 'TrilogyT_' + tkey.split('.')[-1].split('#')[0] + ({'F': '_Fill'}.get(mask, '_Mask') if mask else '') + ('_Emis' if scale else '')
            k = 2
            while name in manifest['entries']: name = f'{base}_v{k}'; k += 1
            manifest['names'][tkey] = name
            kt = kit_tex_template.get(slot)            # (the persistent level is a clone of the kit)
            template = pk.path(kt) if pk is not None and kt is not None and kt >= 0 else p3.path(tt)
            manifest['entries'][name] = {'source': os.path.abspath(sp.filename), 'index': ti, 'template': template, 'slot': slot}
            if mask: manifest['entries'][name]['mask'] = mask
            if character: manifest['entries'][name]['character'] = True
            if scale: manifest['entries'][name]['scale'] = list(scale)
        if not shared_pkg: shared_pkg.append(w.add_import('Core', 'Package', 0, args.shared_textures))
        return w.add_import('Engine', 'Texture2D', shared_pkg[0], name)
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
        kit_tex_template.update(tex_template)          # (the kit's own: the shared textures' templates)
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
        masked_mic = None                              # the masked template: an instance in the target itself
        masked_param = None
        if args.masked_material:
            masked_mic = next((i for i in range(len(p3.exports)) if cls(i) == 'MaterialInstanceConstant'
                               and args.masked_material.lower() in p3.path(i).lower()), None)
            # its mask texture parameter (M_DiscoverPickups_MASTER_MASKED: OpacityMaskTexture, read for its alpha)
            masked_param = next((n for n in materialconv.mic_texture_params(p3, masked_mic) if 'opacity' in n.lower()), None) \
                if masked_mic is not None else None
            print(f'masked material template: {p3.path(masked_mic) if masked_mic is not None else "not in the target"} ({masked_param})')
        n_masked, n_fog, fog_ref = 0, 0, [None, None]
        # lit opaque materials glowing from a texture (the corridors' fluorescent tubes, the prison's light bulbs, lamps):
        # (si, material ref) -> the converted emissive texture; they become instances of the skin material (a weapon
        # shader), whose Weap_Emis draws it: Gears 3 draws it on world meshes with vertex light maps too (in game: the
        # cell block's bulbs glow, dull grey without). LEVELINJECT_NO_EMISSIVE=1: off
        emissive_tex.clear()
        mats = set()                                   # (source index, material export index)
        for (si, mi), sk in skel_meshes.items():
            mats.update((si, x - 1) for x in sk['materials'] if x > 0)
        skel_mats = {(si, x - 1) for (si, mi), sk in skel_meshes.items() for x in sk['materials'] if x > 0}
        import_mats = {}                               # (source, import ref) -> (source, export ref) of the material
        refs_used = [(si, el['material']) for si, mi in mesh_keys for el in staticmesh.read(srcs[si], mi)['lods'][0]['elements']]
        refs_used += [(si, r) for (si, ci), ov in overrides.items() for r in ov if r]
        if args.kismet and src.version == 374:      # movers' (doors') own materials: their components are copied
            import objcopy
            refs_used += [(si, r) for si, sp_ in enumerate(srcs) if not getattr(sp_, 'aux', False) for r in objcopy.mover_materials(sp_)]
        for si, ref in refs_used:
            if ref > 0: mats.add((si, ref - 1))
            elif ref < 0 and (si, ref) not in import_mats:
                # a material imported from Gears 1's content (the cutscene's Hammerbursts: Locust_Hammerburst_Mat)
                cls_ = srcs[si].imports[-ref - 1].get('class')
                found = g1_import(srcs, si, ref, cls_) if cls_ in ('Material', 'MaterialInstanceConstant') else None
                if found:
                    srcs[found[0]].aux = True
                    import_mats[(si, ref)] = (found[0], found[1] + 1); mats.add(found)
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
            if fog_index is not None and materialconv.g1_volumetric(sp, gm + 1):
                # light in the air: an instance of the kit's fog material, Gears 1's colour and opacity on it (unlit:
                # no light map)
                if fog_ref[0] is None:
                    fog_ref[0] = materialconv.import_object(w, p3, pk, fog_index, kit_imports) if pk is not None else fog_index + 1
                    fog_ref[1] = materialconv.material_param_guids(pk if pk is not None else p3, fog_index)
                g1v = materialconv.g1_vector_params(sp, gm + 1)
                colour = g1v.get('FogColor') or g1v.get('LightColor') or (1.0, 1.0, 1.0, 1.0)
                opacity = (g1v.get('Brightness') or g1v.get('BeamOpacity') or (1.0,))[0]
                # (testing, LEVELINJECT_FOG_G1_TEXTURES=1) its shape from Gears 1's own falloff (the beam's gradient)
                # and dust textures in the master's two multiply textures instead of the master's cloud noise. Tried
                # for the hard route's green sheets ("grass blown up"): no change there, so they are not these
                fog_textures = {}
                if os.environ.get('LEVELINJECT_FOG_G1_TEXTURES') and tex_template.get('diffuse') is not None:
                    rt_ = materialconv.g1_referenced_textures(sp, gm + 1)
                    pick = lambda keys: next((t_ for t_ in rt_ if any(k_ in sp.path(t_).lower() for k_ in keys)), None)
                    for pname, ti in (('MultiplyTexture1', pick(('falloff',))), ('MultiplyTexture2', pick(('dust', 'cloud', 'noise')))):
                        if ti is None: continue
                        tkey = sp.path(ti)
                        if tkey not in tex_ref and args.shared_textures:
                            tex_ref[tkey] = shared_texture(sp, ti, tex_template['diffuse'], tkey, 'diffuse')
                        if tkey not in tex_ref:
                            upk.E = '>'
                            tt = tex_template['diffuse']
                            data_, fix_ = materialconv.texture_828(w, p3, tt, texture2d.Texture(sp, ti), lod_bias=0)
                            tflags_ = struct.unpack_from('>Q', p3.data, p3.exports[tt]['entry'] + 24)[0]
                            tex_ref[tkey] = w.add_export(p3.exports[tt]['class'], 0, ('Trilogy_' + tkey.split('.')[-1], 0), data_, flags=tflags_, fixups=fix_)
                        fog_textures[pname] = tex_ref[tkey]
                    if 'MultiplyTexture1' in fog_textures and 'MultiplyTexture2' not in fog_textures:
                        fog_textures['MultiplyTexture2'] = fog_textures['MultiplyTexture1']
                data = materialconv.param_mic(w, md[:mo], fog_ref[0], fog_ref[1],
                                              dict(FOG_SCALARS, Opacity=opacity * FOG_OPACITY),
                                              {'Color': tuple(colour[:3]) + (1.0,)}, fog_textures)
                mic_of[(si, gm + 1)] = by_name[name] = w.add_export(mic_cls if pk is not None else tp.exports[tmic]['class'], 0,
                                                                    ('Trilogy_' + name.split('.')[-1], 0), data, flags=mflags_)
                unlit_mats.add((si, gm + 1)); by_name[('unlit', name)] = True
                mic_refs[(si, gm + 1)] = mic_refs[name] = {}
                n_fog += 1
                continue
            texs = materialconv.g1_textures(sp, gm)
            if 'diffuse' not in texs: continue
            if materialconv.g1_unlit(sp, gm):             # drawn at full brightness: a constant light map, no specular
                unlit_mats.add((si, gm + 1)); texs = {'diffuse': texs['diffuse']}; by_name[('unlit', name)] = True
            refs = {}
            # (a modulate decal's diffuse: its white background filled with stain colour, materialconv.modulate_fill)
            modulate = materialconv.g1_blend(sp, gm + 1) == 4
            for slot, ti in texs.items():
                tt = tex_template.get(slot)
                if tt is None or tt < 0: continue
                fill = 'F' if modulate and slot == 'diffuse' else None
                tkey = sp.path(ti) + ('#fill' if fill else '')
                character = (si, gm) in skel_mats             # (a character's: its own size cap)
                if tkey not in tex_ref and args.shared_textures:
                    # in the chapter's shared texture package (written once all sections are converted): an import
                    tex_ref[tkey] = shared_texture(sp, ti, tt, tkey, slot, mask=fill, character=character)
                if tkey not in tex_ref:
                    upk.E = '>'
                    data, fix = materialconv.texture_828(w, p3, tt, texture2d.Texture(sp, ti), mask=fill, lod_bias=materialconv.SLOT_LOD_BIAS.get(slot),
                                                         max_size=materialconv.MAX_CHARACTER_TEXTURE_SIZE if character else None)
                    tflags_ = struct.unpack_from('>Q', p3.data, p3.exports[tt]['entry'] + 24)[0]
                    tex_ref[tkey] = w.add_export(p3.exports[tt]['class'], 0, ('Trilogy_' + tkey.split('.')[-1], 0), data, flags=tflags_, fixups=fix)
                refs[{'diffuse': 'Diffuse_01', 'normal': 'Normal_01', 'specular': 'Specular_01'}[slot]] = tex_ref[tkey]
            em_ = materialconv.g1_emissive(sp, gm) if not os.environ.get('LEVELINJECT_NO_EMISSIVE') and args.skin_material else None
            etex, escale = em_ if em_ else (None, (1.0, 1.0, 1.0))
            escale = None if os.environ.get('LEVELINJECT_NO_EMISSIVE_SCALE') or tuple(round(x, 3) for x in escale) == (1.0, 1.0, 1.0) else escale
            if etex is not None and sp.class_name(sp.exports[etex]['class']) == 'Texture2D':
                try: texture2d.Texture(sp, etex)
                except Exception: etex = None       # (not a texture texture2d reads: left out)
            else:
                etex = None
            if etex is not None and materialconv.g1_blend(sp, gm + 1) == 0 and tex_template.get('diffuse') is not None:
                # (scaled by Gears 1's emissive multipliers: its own texture entry, the source may be another slot's)
                tkey = sp.path(etex) + ('#emis' + '_'.join(str(round(x * 100)) for x in escale) if escale else '')
                if tkey not in tex_ref and args.shared_textures:
                    tex_ref[tkey] = shared_texture(sp, etex, tex_template['diffuse'], tkey, 'diffuse', scale=escale)
                if tkey in tex_ref: emissive_tex[(si, gm + 1)] = tex_ref[tkey]
            # masked (the fence, razor wire, grass): a child of the masked template, its mask texture the cutout as
            # grey and alpha (the diffuse's alpha: alpha_to_grey; a DXT1 mask's red: red_to_alpha)
            mask = materialconv.g1_mask(sp, gm) if masked_mic is not None and masked_param else None
            if mask is not None and mask[1] in ('R', 'L') and texture2d.Texture(sp, mask[0]).format != 'PF_DXT1': mask = None
            if mask is not None and tex_template.get('diffuse') is not None:
                ti, channel = mask
                tt = tex_template['diffuse']
                tkey = sp.path(ti) + '#mask'
                if tkey not in tex_ref and args.shared_textures:
                    tex_ref[tkey] = shared_texture(sp, ti, tt, tkey, 'diffuse', mask=channel)
                if tkey not in tex_ref:
                    upk.E = '>'
                    data, fix = materialconv.texture_828(w, p3, tt, texture2d.Texture(sp, ti), mask=channel, lod_bias=materialconv.SLOT_LOD_BIAS.get('mask'))
                    tflags_ = struct.unpack_from('>Q', p3.data, p3.exports[tt]['entry'] + 24)[0]
                    tex_ref[tkey] = w.add_export(p3.exports[tt]['class'], 0, ('Trilogy_' + tkey.split('.')[-1].replace('#mask', '_Mask'), 0), data, flags=tflags_, fixups=fix)
                refs[masked_param] = tex_ref[tkey]
                # a copy of the masked instance with its static permutation (a child of it draws the base material's
                # default permutation: the fence drew uncut, or untextured). The template is a pickup's material:
                # its ShimmerColor (70, 50, 40) pulsed a collectible's glint over the prison's paper and rubble, off
                # Gears 1's own shading of it: its diffuse tint (the hanging grass at about a third of its texture's
                # brightness) and no specular where it had none (the pickup master's default 0.5 of the diffuse lit
                # the grass bright green in the dark hallway)
                vectors_ = dict(MASKED_VECTORS)
                if not os.environ.get('LEVELINJECT_NO_G1_SHADING'):
                    tint_, spec_ = materialconv.g1_shading(sp, gm + 1)
                    if tint_: vectors_['Diffuse_01_Color'] = tuple(tint_) + (1.0,)
                    if not spec_: vectors_['Specular_01_Color'] = (0.0, 0.0, 0.0, 1.0)
                if mask[1] == 'L':                     # (a modulate decal: about as dark as the surface it multiplied)
                    t_ = materialconv.MODULATE_TINT
                    vectors_['Diffuse_01_Color'] = (t_, t_, t_, 1.0); vectors_['Specular_01_Color'] = (0.0, 0.0, 0.0, 1.0)
                data = materialconv.clone_static_mic(w, p3, p3, masked_mic, refs, {}, vectors=vectors_)
                mflags_m = struct.unpack_from('>Q', p3.data, p3.exports[masked_mic]['entry'] + 24)[0]
                mic_of[(si, gm + 1)] = by_name[name] = w.add_export(p3.exports[masked_mic]['class'], 0, ('Trilogy_' + name.split('.')[-1], 0), data, flags=mflags_m)
                n_masked += 1
            elif pk is not None:
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
        print(f'{len(mic_of)} materials ({n_masked} masked, {n_fog} fog), {len(tex_ref)} textures converted (template {tp.path(tmic)})')
        if emissive_tex and args.skin_material:
            # lit opaque materials glowing from a texture: instances of the skin material (a
            # weapon shader: Weap_Diffuse / Weap_Norm / Weap_Spec / Weap_Emis), its other textures black or flat
            ef_, epath = args.skin_material.rsplit(':', 1)
            ep_ = upk.Package(ef_)
            etmic = next(i for i in range(len(ep_.exports)) if ep_.path(i).endswith(epath.strip())
                         and ep_.class_name(ep_.exports[i]['class']) == 'MaterialInstanceConstant')
            eimports = {}
            eparent = materialconv.import_object(w, p3, ep_, etmic, eimports)
            def eneutral(name_):
                ref_ = next((-k - 1 for k, im in enumerate(ep_.imports) if im['class'] == 'Texture2D' and im['name'] == name_), None)
                return materialconv.copy_import(w, p3, ep_, ref_, eimports) if ref_ is not None else None
            eblack, eflat = eneutral('AllBlack'), eneutral('DefaultNormal')
            eparams = materialconv.mic_texture_params(ep_, etmic)
            roles_ = {r_: next((n_ for n_ in eparams if part_ in n_.lower()), r_) for r_, part_ in
                      (('Diffuse_01', 'diffuse'), ('Normal_01', 'norm'), ('Specular_01', 'spec'))}
            emis_param = next((n_ for n_ in eparams if 'emis' in n_.lower()), None)
            efl = struct.unpack_from('>Q', ep_.data, ep_.exports[etmic]['entry'] + 24)[0]
            ecls = next((p3.exports[i]['class'] for i in range(len(p3.exports)) if cls(i) == 'MaterialInstanceConstant'), None)
            # the weapon shader's dropped-weapon glint and reflection off and its emissive colour white (the base
            # instance's (0, 4, 12) tinted every glow cyan, twelvefold; Gears 1's colour is in its texture): with the
            # elements' own materials drawn (MaterialIndex), the prison's wall panels shimmered blue and purple
            eextra = weapon_shader_extra(args, {'Weap_EmisColor': (1.0, 1.0, 1.0, 1.0)})
            for key_, eref in emissive_tex.items():
                if emis_param is None or key_ not in mic_refs or ecls is None: continue
                refs_ = {n_: (eflat if 'norm' in n_.lower() else eblack) for n_ in eparams}
                refs_.update({roles_[k_]: v_ for k_, v_ in mic_refs[key_].items() if k_ in roles_})
                refs_[emis_param] = eref
                refs_ = {k_: v_ for k_, v_ in refs_.items() if v_ is not None}
                mic_of[key_] = w.add_export(ecls, 0, ('Trilogy_Emissive_' + srcs[key_[0]].exports[key_[1] - 1]['name'], 0),
                                            materialconv.child_mic(w, p3, ep_, etmic, eparent, refs_, extra=eextra), flags=efl)
            print(f'emissive: {len(emissive_tex)} lit materials glowing from a texture, instances of {ep_.path(etmic)} ({emis_param})')
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
    uv_transform_cache, n_uv_mapped = {}, 0
    for k, (si, mi) in enumerate(sorted(mesh_keys)):
        sp = srcs[si]
        m = parsed[(si, mi)] = staticmesh.read(sp, mi)
        # Gears 1's texture mapping per material (UV set, tiling): into the mesh's UV set 0, which the converted
        # materials sample as it is (not when the light map reads UV set 0: its samples are taken from the same UVs)
        if sp.version == 374 and not args.no_uv_transforms and not os.environ.get('LEVELINJECT_NO_UV') and int(m['props'].get('LightMapCoordinateIndex', 1)) != 0:
            if si not in uv_transform_cache: uv_transform_cache[si] = {}
            tc = uv_transform_cache[si]
            def transform_of(ref, sp=sp, tc=tc):
                if ref not in tc: tc[ref] = materialconv.g1_uv_transform(sp, ref)
                return tc[ref]
            n_uv_mapped += bool(materialconv.g1_apply_uv_transforms(sp, m, transform_of))
        path = sp.path(mi)
        if args.merge and (si, mi) not in set(mover_meshes): continue     # (only merged: no mesh of its own)
        if path in mesh_by_name:                       # the same mesh in another sublevel package
            new_mesh[(si, mi)] = mesh_by_name[path]; continue
        upk.E = '>'
        data = per_poly_collision(meshconv.to_828(p3, tmesh, m, [mic_of.get((si, el['material'])) for el in m['lods'][0]['elements']]))
        name = 'Trilogy_' + path.split('.')[-1]
        new_mesh[(si, mi)] = mesh_by_name[path] = w.add_export(p3.exports[tmesh]['class'], 0, (name, 0), data, flags=mflags)
    if n_uv_mapped: print(f"{n_uv_mapped} meshes with Gears 1's texture mapping (UV set, tiling) in their UVs")
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
        skin_extra = None
        skin_fill = {}                                 # --skin-material: texture parameter -> neutral texture import
        skin_roles = {'Diffuse': 'Diffuse', 'Normal': 'Normal', 'Specular': 'Specular'}
        if args.skin_material:
            # Gears 3's own skinned character shader (its static permutation compiled for skeletal meshes; the kit has
            # no skeletal mesh with a Diffuse / Normal / Specular instance: the cutscene's characters drew the default
            # material, flat white). Its other textures (masks, emissive, gore) black: else the template character's
            # would show through on the converted mesh's UVs
            f_, mpath = args.skin_material.rsplit(':', 1)
            sp_ = upk.Package(f_)
            skel_tmic = next(i for i in range(len(sp_.exports)) if sp_.path(i).endswith(mpath.strip())
                             and sp_.class_name(sp_.exports[i]['class']) == 'MaterialInstanceConstant')
            scls = lambda i: sp_.class_name(sp_.exports[i]['class'])
            skin_imports = {}
            skel_parent = materialconv.import_object(w, p3, sp_, skel_tmic, skin_imports)
            def neutral(name):
                ref = next((-k - 1 for k, im in enumerate(sp_.imports) if im['class'] == 'Texture2D' and im['name'] == name), None)
                return materialconv.copy_import(w, p3, sp_, ref, skin_imports) if ref is not None else None
            black, flat = neutral('AllBlack'), neutral('DefaultNormal')
            # the template's parameter for each converted slot, by name (a character shader's Diffuse / Normal /
            # Specular, a weapon shader's Weap_Diffuse / Weap_Norm / Weap_Spec)
            params = materialconv.mic_texture_params(sp_, skel_tmic)
            for role, part in (('Diffuse', 'diffuse'), ('Normal', 'norm'), ('Specular', 'spec')):
                skin_roles[role] = next((n for n in params if part in n.lower()), role)
            for name in params:
                skin_fill[name] = flat if name == skin_roles['Normal'] else black
            print(f'skin material: {sp_.path(skel_tmic)} ({len(skin_fill)} texture parameters)')
            # the weapon shader's pickup glint (ShimmerColor, Weap_DropGlowAlpha: a dropped weapon's shimmer) and its
            # environment reflection (envMult) off: on the opening's Marcus they drew a white shimmer round him and lit
            # him through the cell's shadow, where Gears 1 kept him dark. Set by the master's ExpressionGUIDs (the HOD
            # instance carries none of them)
            skin_extra = weapon_shader_extra(args)
            if skin_extra: print(f"skin material: glint and reflection off ({', '.join(n for n in ('envMult', 'Weap_DropGlowAlpha', 'ShimmerColor') if n in skin_extra[0])})")
        hair_tmic = None
        if args.hair_material:
            hf_, hpath = args.hair_material.rsplit(':', 1)
            hp_ = upk.Package(hf_)
            hair_tmic = next(i for i in range(len(hp_.exports)) if hp_.path(i).endswith(hpath.strip())
                             and hp_.class_name(hp_.exports[i]['class']) == 'MaterialInstanceConstant')
            hair_imports = {}
            print(f'hair material: {hp_.path(hair_tmic)}')
        skel_cache = {}
        def skel_mic(si_, x_):
            key = srcs[si_].path(x_ - 1)
            if skel_tmic is None or key not in mic_refs: return mic_of.get((si_, x_), tmat)
            if key not in skel_cache and hair_tmic is not None and materialconv.g1_blend(srcs[si_], x_) == 1 \
                    and 'Diffuse_01' in mic_refs[key]:
                # masked (hair cards: the cutout in the diffuse's alpha): a copy of the hair material with its static
                # permutation (a child of it drew the default material), its other values (DiffuseColor 0.2: Gears 1
                # darkens the hair texture, x 0.1) the template's
                data_ = materialconv.clone_static_mic(w, p3, hp_, hair_tmic, {'Diffuse And Alpha': mic_refs[key]['Diffuse_01']}, hair_imports)
                fl_ = struct.unpack_from('>Q', hp_.data, hp_.exports[hair_tmic]['entry'] + 24)[0]
                skel_cache[key] = w.add_export(mic_cls, 0, ('Trilogy_Hair_' + key.split('.')[-1], 0), data_, flags=fl_)
            if key not in skel_cache:
                refs_ = {skin_roles[{'Diffuse_01': 'Diffuse', 'Normal_01': 'Normal', 'Specular_01': 'Specular'}[k]]: v for k, v in mic_refs[key].items()}
                fl_ = struct.unpack_from('>Q', sp_.data, sp_.exports[skel_tmic]['entry'] + 24)[0]
                if skin_fill:
                    refs_ = {**{k: v for k, v in skin_fill.items() if v is not None}, **refs_}
                    # (the weapon shader's specular at a third: Gears 1's characters had no metal sheen)
                    data_ = materialconv.child_mic(w, p3, sp_, skel_tmic, skel_parent, refs_, {'Weap_SpecMult': 0.33}, extra=skin_extra)
                    skel_cache[key] = w.add_export(mic_cls, 0, ('Trilogy_Skel_' + key.split('.')[-1], 0), data_, flags=fl_)
                elif pk is not None:
                    data_ = materialconv.child_mic(w, p3, pk, skel_tmic, skel_parent, refs_, {'OverallEmissive': 0.0})
                    skel_cache[key] = w.add_export(mic_cls, 0, ('Trilogy_Skel_' + key.split('.')[-1], 0), data_, flags=fl_)
                else:
                    data_ = materialconv.mic_828(w, p3, skel_tmic, refs_, {'OverallEmissive': 0.0})
                    skel_cache[key] = w.add_export(p3.exports[skel_tmic]['class'], 0, ('Trilogy_Skel_' + key.split('.')[-1], 0), data_, flags=fl_)
            return skel_cache[key]
        print('skeletal material template:', sp_.path(skel_tmic) if skel_tmic is not None else None, '(kit)' if pk is not None else '')

        skflags = struct.unpack_from('>Q', p3.data, p3.exports[tsk]['entry'] + 24)[0]
        # the mesh's FaceFX actor (facial animation, lip sync): Gears 1's FaceFXAsset copied as it is (both games
        # serialize properties, then the FaceFX archive "FACB" + version, Gears 1 1600, Gears 3 1740, as a byte
        # array and a trailing int). Marcus's and Dom's are cooked into WarfareGame.xxx; the levels' imports of them
        # name a broken outer, so they are found by name
        facefx_cache = {}
        # --facefx-assets "G1 asset name=package.xxx:object path;...": Gears 3's own FaceFX actors for those (Gears 1's
        # copied assets load no FaceFX actor in Gears 3: FaceFXActor stays None, no instance on the component; the
        # Gears 1 animation sets do load, and their curves name the same face graph nodes as Gears 3's classic
        # Marcus and Dom)
        facefx_map = {}
        for part in filter(None, (args.facefx_assets or '').split(';')):
            k_, v_ = part.split('=', 1); f_, o_ = v_.rsplit(':', 1)
            facefx_map[k_.strip()] = (f_.strip(), o_.strip())
        def facefx_asset(si_, mi_):
            sp_ = srcs[si_]
            try: ref_ = {n_: v_ for n_, t_, k_, v_ in props_at(sp_, sp_.export_data(mi_))[1]}.get('FaceFXAsset', 0)
            except ValueError: return 0
            if not ref_: return 0
            if ref_ > 0: fsp, fi = sp_, ref_ - 1
            else:
                name_ = sp_.imports[-ref_ - 1]['name']
                fsp = fi = None
                for cand in [sp_] + [x for x in srcs if x is not sp_] + [_aux_package(sp_, 'WarfareGame')]:
                    if cand is None: continue
                    fi = next((i_ for i_, e_ in enumerate(cand.exports) if e_['name'] == name_ and cand.class_name(e_['class']) == 'FaceFXAsset'), None)
                    if fi is not None: fsp = cand; break
                if fsp is None: print(f'FaceFX asset {name_}: not found'); return 0
            name_ = fsp.exports[fi]['name']
            if name_ in facefx_map:                   # a Gears 3 asset instead (--facefx-assets): an import of it
                if name_ not in facefx_cache:
                    f_, opath = facefx_map[name_]
                    gp = upk.Package(f_)
                    gi = next(k for k in range(len(gp.exports)) if gp.path(k) == opath and gp.class_name(gp.exports[k]['class']) == 'FaceFXAsset')
                    facefx_cache[name_] = materialconv.import_object(w, p3, gp, gi, {})
                    print(f'FaceFX asset for {sp_.path(mi_)}: Gears 3 {opath} ({os.path.basename(f_)})')
                return facefx_cache[name_]
            key_ = (fsp.filename, fi)
            if key_ not in facefx_cache:
                d_ = fsp.export_data(fi)
                end_ = fsp.properties(d_, 4)[1]
                import levelpkg
                facefx_cache[key_] = w.add_export(levelpkg.class_import(p3, w, 'FaceFXAsset'), 0, ('Trilogy_' + fsp.exports[fi]['name'], 0),
                                                  struct.pack('>i', -1) + objcopy.encode(w, []) + d_[end_:], flags=mflags)
                print(f'FaceFX asset {fsp.path(fi)} ({len(d_) - end_} bytes) for {sp_.path(mi_)}')
            return facefx_cache[key_]
        for (si, mi), sk in skel_meshes.items():
            path = srcs[si].path(mi)
            if path in mesh_by_name: new_mesh[(si, mi)] = mesh_by_name[path]; continue
            mats_ = [skel_mic(si, x) if x > 0 else tmat for x in sk['materials']]
            # Gears 1's physics assets are not converted (components get None), and without one a skeletal mesh is
            # culled by its mesh bounds at the actor's origin, not where its bones carry it: the opening's bag
            # vanished as Dom's animation dropped it away from its actor. Bounds cover the animated travel
            b_ = list(sk['bounds'])
            b_[3:6] = [max(x, SKEL_MIN_EXTENT) for x in b_[3:6]]
            b_[6] = max(b_[6], float(np.linalg.norm(b_[3:6])))
            sk = dict(sk, bounds=tuple(b_))
            data, fix = skelmesh.to_828(w, sk, mats_, ttail, facefx_asset(si, mi))
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
    bake = lighting.bake_point_lights
    if src.version == 374 and not args.no_point_lights and args.chapter_lights:
        z = np.load(args.chapter_lights)
        point_lights = tuple(z[k] for k in ('pos', 'radius', 'colour', 'dir', 'cos_inner', 'cos_outer'))
        bake = lighting.bake_lights
        print(f"{len(point_lights[0])} of the chapter's lights (in no Gears 1 light map) baked into the light maps")
    elif src.version == 374 and not args.no_point_lights:
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
    n_unlit = n_noncolliding = n_pawn_through = n_simple = 0
    simple_box_cache, hull_cache, body_parts = {}, {}, []
    # --merge: the level's mesh actors joined into one mesh per 4096-unit cell and collision class (each a component
    # of its own: thousands of components were thousands of draws, the render thread held the frame rate down where
    # the view took in much of a section). Vertex light maps join with them (light per vertex).
    mover_keys = set(mover_meshes)
    buckets = {}                                       # key -> {'P','TX','TZ','UV','L','idx': {mic: [indices]}, 'n'}
    # Gears 1 laid meshes flush on its BSP (pillars and trims against walls, plates on floors): the same plane, facing
    # the same way, each with its own light (the BSP's light map, the mesh's vertex light). Drawn as separate meshes
    # they z-fought, a lit patch flickering in and out of a dark wall as the camera moved (the prison's first room
    # outside the cell, the hard route's hallway). The mesh wins: its faces on a BSP face are moved off it by
    # COPLANAR_NUDGE along their normal (GOW3_NO_COPLANAR_NUDGE=1: as they were)
    bsp_planes = {}
    if polys and not os.environ.get('GOW3_NO_COPLANAR_NUDGE'):
        for poly in polys:
            V_ = np.asarray(poly[1], np.float64)
            if len(V_) < 3: continue
            n_ = np.zeros(3)
            for k_ in range(len(V_)):                  # Newell's normal
                a_, b_ = V_[k_], V_[(k_ + 1) % len(V_)]
                n_ += [(a_[1] - b_[1]) * (a_[2] + b_[2]), (a_[2] - b_[2]) * (a_[0] + b_[0]), (a_[0] - b_[0]) * (a_[1] + b_[1])]
            ln_ = np.linalg.norm(n_)
            if ln_ < 1e-6: continue
            n_ /= ln_
            if np.dot(n_, np.asarray(poly[3], np.float64)) < 0: n_ = -n_      # (the surface's own normal: its front)
            d_ = float(np.dot(n_, V_[0]))
            ax_ = int(np.argmax(np.abs(n_)))
            bsp_planes.setdefault((tuple(np.round(n_ * 20).astype(int)), int(round(d_))), []).append((n_, d_, V_, ax_))
    n_nudged = [0]
    def coplanar_nudge(P, T, VN):
        """P moved where its triangles T (k, 3) lie on a BSP face facing the same way (VN: vertex normals, world)"""
        if not bsp_planes or not len(T): return P
        A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
        N = np.cross(B - A, C - A); L = np.linalg.norm(N, axis=1)
        ok = L > 1e-3
        N[ok] /= L[ok, None]
        flip = np.einsum('ij,ij->i', N, VN[T[:, 0]] + VN[T[:, 1]] + VN[T[:, 2]]) < 0
        N[flip] *= -1
        D = np.einsum('ij,ij->i', N, A)
        move = np.zeros_like(P)
        for t in np.nonzero(ok)[0]:
            nk = tuple(np.round(N[t] * 20).astype(int)); dk = int(round(D[t]))
            hit = False
            for dd in (0, -1, 1):
                for n_, d_, V_, ax_ in bsp_planes.get((nk, dk + dd), ()):
                    if np.dot(n_, N[t]) < 0.999 or abs(d_ - D[t]) > 0.5: continue
                    c_ = (A[t] + B[t] + C[t]) / 3
                    keep = [k for k in range(3) if k != ax_]
                    q = V_[:, keep]; pt = c_[keep]
                    e = np.roll(q, -1, 0) - q
                    cr = e[:, 0] * (pt[1] - q[:, 1]) - e[:, 1] * (pt[0] - q[:, 0])
                    if (cr >= -1e-3).all() or (cr <= 1e-3).all(): hit = True; break
                if hit: break
            if hit:
                move[T[t]] = N[t] * COPLANAR_NUDGE; n_nudged[0] += 1
        return P + move
    MERGE_CELL, MERGE_MAX = 4096.0, 60000
    def merge_add(si, mi, loc, rot, scale, mics, samples, flags_key):
        m_ = parsed[mi]; lod = m_['lods'][0]
        pos, tx, tz, uvh, nv = meshconv.source_vertices(m_)
        P = (np.frombuffer(pos.tobytes(), '>f4').reshape(nv, 3).astype(np.float64) * scale) @ rotation_matrix(*rot) + loc
        R = rotation_matrix(*rot)
        def turn(t, inverse):
            v = t[:, [3, 2, 1]].astype(np.float64) / 127.5 - 1
            v = ((v / np.where(scale == 0, 1, scale)) if inverse else (v * scale)) @ R
            v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
            q = np.clip(np.round((v + 1) * 127.5), 0, 255).astype(np.uint8)
            return np.stack([t[:, 0], q[:, 2], q[:, 1], q[:, 0]], 1)
        TX, TZ = turn(tx, False), turn(tz, True)
        mirrored = np.prod(scale) < 0
        if mirrored: TZ[:, 0] = np.where(TZ[:, 0] > 127, 0, 255)
        if bsp_planes:
            idx_all = np.frombuffer(lod['indices'][2], '>u2').reshape(-1, 3).astype(np.int64)
            VN = TZ[:, [3, 2, 1]].astype(np.float64) / 127.5 - 1
            P = coplanar_nudge(P, idx_all[(idx_all < nv).all(1)], VN)
        centre = P.mean(0)
        # (and by brightness: a vertex light map is 8 bits per vertex scaled to its brightest, so a dim piece in a
        # chunk with a bright one went black, and the bright one white)
        peak = float(np.nanmax(samples)) if samples is not None and np.isfinite(samples).any() else -1.0
        band = int(np.floor(np.log2(max(peak, 1e-3)))) if peak >= 0 else 99
        key = (tuple(np.floor(centre / MERGE_CELL).astype(int)), flags_key, band)
        part = 0
        while key + (part,) in buckets and buckets[key + (part,)]['n'] + nv > MERGE_MAX: part += 1
        b = buckets.setdefault(key + (part,), {'P': [], 'TX': [], 'TZ': [], 'UV': [], 'L': [], 'idx': {}, 'n': 0, 'flags': flags_key})
        base = b['n']
        if samples is not None: samples = np.nan_to_num(samples, nan=0.05)
        b['P'].append(P); b['TX'].append(TX); b['TZ'].append(TZ); b['UV'].append(uvh[:, 0, :]); b['L'].append(samples if samples is not None else nv)
        idx = struct.unpack(f'>{lod["indices"][1]}H', lod['indices'][2])
        for k_, e_ in enumerate(lod['elements']):
            out_ = b['idx'].setdefault(mics[k_] if k_ < len(mics) else None, [])
            f_ = e_['first_index']
            for j in range(e_['triangles']):
                a_, b_, c_ = idx[f_ + 3 * j], idx[f_ + 3 * j + 1], idx[f_ + 3 * j + 2]
                out_ += [base + a_, base + c_, base + b_] if mirrored else [base + a_, base + b_, base + c_]
        b['n'] += nv
    # the lights baked here (in no Gears 1 light map) were dynamic in Gears 1, with shadows: each vertex takes a light
    # only where the segment to it is clear of the section's BSP and solid meshes (cutouts, decals and see-through
    # surfaces let light by). Without it they lit through walls, pipes and bars: the prison flat against Gears 1.
    # LEVELINJECT_NO_LIGHT_SHADOWS=1: as before
    if point_lights is not None and bake is lighting.bake_lights and not os.environ.get('LEVELINJECT_NO_LIGHT_SHADOWS'):
        occ_tris, n_occ_meshes = [], 0
        for poly in polys:
            V_ = np.asarray(poly[1], np.float64)
            occ_tris += [V_[[0, k_, k_ + 1]] for k_ in range(1, len(V_) - 1)]
        n_bsp_tris = len(occ_tris)
        for (si_, mi__, loc_, rot_, scale_, ci_) in actors:
            m_ = parsed.get((si_, mi__))
            if m_ is None: continue
            try:
                els_ = element_materials(si_, mi__, ci_)
                if any(materialconv.g1_blend(srcs[si_], x_) in (1, 2, 3, 4) for x_ in els_ if x_): continue
            except Exception:
                continue
            Pw_, _ = world_vertices(m_, loc_, rot_, scale_)
            T_ = np.asarray(staticmesh.triangles(m_['lods'][0]), np.int64)
            T_ = T_[(T_ < len(Pw_)).all(1)]
            if len(T_): occ_tris.append(Pw_[T_]); n_occ_meshes += 1
        occ_ = lighting.Occluders(np.concatenate([np.asarray(occ_tris[:n_bsp_tris]).reshape(-1, 3, 3)] + occ_tris[n_bsp_tris:])
                                  if len(occ_tris) > n_bsp_tris else np.asarray(occ_tris).reshape(-1, 3, 3))
        bake = lambda L_, P_, N_, o_=occ_: lighting.bake_lights(L_, P_, N_, o_)
        print(f'baked lights shadowed by {len(occ_.tris)} triangles ({n_bsp_tris} BSP, {n_occ_meshes} meshes)')
    for n, (si, mi_, loc, rot, scale, ci) in enumerate(actors):
        sp, mi = srcs[si], (si, mi_)
        tail = bytes(4)                                # no light map
        samples = None
        els = element_materials(si, mi_, ci)
        if not args.no_lighting and sp.version == 374:
            cd_ = sp.export_data(ci)
            try:
                lods, _ = lighting.read_lod_data_374(cd_, props_at(sp, cd_, (8,))[2])
                samples = lighting.vertex_lighting_374(sp, lods[0], parsed[mi]) if lods else None
            except Exception:
                samples = None
            nv = len(staticmesh.positions(parsed[mi]['lods'][0]))
            if samples is not None and len(samples) != nv: samples = None
            if els and all((si, x) in unlit_mats for x in els):
                samples = np.full((nv, 3, 3), UNLIT_LIGHT, np.float32); n_unlit += 1
            elif point_lights is not None:
                Pw, Nw = world_vertices(parsed[mi], loc, rot, scale)
                pl = bake(point_lights, Pw, Nw)
                base = samples if samples is not None else np.full((nv, 3, 3), 0.05, np.float32)
                samples = base + pl[:, None, :]
            if samples is not None:
                tail = lighting.gears3_vertex_lightmap(samples, len(w.exports) + 1, light_guids)   # owner: this new component
                lit += 1
        d = bytearray(td[:tend])
        struct.pack_into('>i', d, sm_off, new_mesh.get(mi, 0))
        struct.pack_into('>3f', d, sc_off, *scale)
        if vis_off: struct.pack_into('>i', d, vis_off, -1)
        # Gears 1's own collision flags: most of its meshes did not block pawns (rubble, railings, stair steps: its
        # BlockingVolumes did), and blocking with every mesh's triangles the player caught on rubble and was pushed
        # back at the top of the prison's stairs
        if not args.no_collision and sp.version == 374:
            try:
                cflags = {n_: v_ for n_, t_, k_, v_ in props_at(sp, sp.export_data(ci), (8, 4, 12))[1]}
            except ValueError:
                cflags = {}
            # a mesh with no simple collision (BodySetup) that keeps UseSimpleBoxCollision (the default) never blocked
            # a pawn in Gears 1: box checks found nothing, the pawn walked on the BSP or volumes under it (bullets
            # hit its triangles). Converted meshes collide by their triangles, and such a mesh blocked as a new edge:
            # the prison's corridor floor tiles, 4 units over the BSP floor at the cell door, stopped the player
            # walking out at high frame rates (UE3's stepUp retries a step only for a move over 12 units a frame)
            if mi not in simple_box_cache:            # (mi: the mesh's (source, export) key)
                try:
                    mp_ = {n_: v_ for n_, t_, k_, v_ in props_at(srcs[mi[0]], srcs[mi[0]].export_data(mi[1]))[1]}
                except ValueError:
                    mp_ = {'BodySetup': 1}
                simple_box_cache[mi] = not mp_.get('BodySetup') and mp_.get('UseSimpleBoxCollision', True) is not False
            if simple_box_cache[mi] and cflags.get('BlockNonZeroExtent') is not False:
                cflags['BlockNonZeroExtent'] = False; n_pawn_through += 1
            # a mesh with simple collision blocked Gears 1's pawns by it (boxes, convex hulls), not by its triangles:
            # pawns collide with a hidden mesh of those hulls (below), the mesh's own triangles stop shots only. By
            # the triangles, the round pipes sunk in the prison's cell threshold were 20-degree slopes 2-4 units high
            # that the player stuck on walking at 240 fps
            elif not args.no_simple_collision and all(cflags.get(k_) is not False for k_ in ('CollideActors', 'BlockActors', 'BlockNonZeroExtent')):
                if mi not in hull_cache: hull_cache[mi] = bspconv.body_hulls(srcs[mi[0]], mi[1])
                if hull_cache[mi]:
                    R_ = rotation_matrix(*rot)
                    for V_, T_ in hull_cache[mi]:
                        body_parts.append(((V_ * scale) @ R_ + loc, T_))
                    cflags['BlockNonZeroExtent'] = False; n_simple += 1
            for prop in ('CollideActors', 'BlockActors', 'BlockNonZeroExtent', 'BlockZeroExtent', 'BlockRigidBody'):
                if cflags.get(prop) is False: d = set_property(p3, w, d, to, prop, b'\x00', 'BoolProperty')
            if cflags.get('CollideActors') is False: n_noncolliding += 1
        if (si, ci) in scripted_components: continue  # (drawn by its copy: objcopy.scripted_mesh_actors)
        if args.merge and mi not in mover_keys:
            # merged into its cell's chunk (below): its triangles in world space, its light, its collision class
            flags_key = tuple(cflags.get(k_) is False for k_ in ('CollideActors', 'BlockActors', 'BlockNonZeroExtent', 'BlockZeroExtent', 'BlockRigidBody')) \
                if not args.no_collision and sp.version == 374 else ()
            merge_add(si, mi, loc, rot, scale, [mic_of.get((si, x)) for x in els], samples, flags_key)
            if samples is not None and not (els and all((si, x) in unlit_mats for x in els)):
                all_samples.append(samples.mean(0))
            continue
        if (si, ci) in overrides:                      # the component's own materials (None: the mesh's)
            ov = [mic_of.get((si, r), 0) if r else 0 for r in overrides[(si, ci)]]
            d = set_property(p3, w, d, to, 'Materials', struct.pack(f'>i{len(ov)}i', len(ov), *ov), 'ArrayProperty')
        d = d + tail
        refs.append(w.add_export(p3.exports[tcomp]['class'], coll + 1, ('TrilogySMC', n + 1), d, flags=tflags, archetype=tarch))
        m = np.eye(4); m[:3, :3] = rotation_matrix(*rot); m[3, :3] = loc + offset
        mats.append(struct.pack('>16f', *m.flatten()))
        if samples is not None and not (els and all((si, x) in unlit_mats for x in els)):
            all_samples.append(samples.mean(0))
    # BSP chunks: a mesh and a component each, in world space (+ offset); lit from the BSP's light maps (sampled at
    # each vertex's lightmap UV), the actors' average lighting where a node had no light map
    ambient = np.mean(all_samples, 0) if all_samples else np.full((3, 3), 0.25)
    # the merged chunks (--merge)
    for k, (key, b) in enumerate(sorted(buckets.items(), key=lambda kv: str(kv[0]))):
        P = np.concatenate(b['P']); n_ = len(P)
        verts = np.concatenate([np.concatenate(b['TX']), np.concatenate(b['TZ']),
                                np.frombuffer(np.concatenate(b['UV']).astype('>f2').tobytes(), np.uint8).reshape(n_, 4)], 1)
        idx, elements, mics = [], [], []
        for mic, tri in b['idx'].items():
            elements.append({'first_index': len(idx), 'triangles': len(tri) // 3, 'min_vertex': 0, 'max_vertex': n_ - 1})
            idx += tri; mics.append(mic)
        lo, hi = P.min(0), P.max(0)
        centre, ext = (lo + hi) / 2, (hi - lo) / 2
        mesh = {'version': 828, 'props': {}, 'bounds': (*centre, *ext, float(np.linalg.norm(ext))),
                'lods': [{'positions': (12, n_, (12, n_, P.astype('>f4').tobytes())),
                          'vertices': (1, 12, n_, 0, (12, n_, verts.tobytes())),
                          'indices': (2, len(idx), struct.pack(f'>{len(idx)}H', *idx)),
                          'wireframe': (2, 0, b''), 'elements': elements}]}
        upk.E = '>'
        data = per_poly_collision(meshconv.to_828(p3, tmesh, mesh, mics))
        mref = w.add_export(p3.exports[tmesh]['class'], 0, ('Trilogy_Merged', k + 1), data, flags=mflags)
        light = np.concatenate([x if not isinstance(x, int) else np.repeat(ambient[None], x, 0) for x in b['L']]).astype(np.float32)
        tail = bytes(4) if args.no_lighting else lighting.gears3_vertex_lightmap(light, len(w.exports) + 1, light_guids)
        d = bytearray(td[:tend])
        struct.pack_into('>i', d, sm_off, mref)
        struct.pack_into('>3f', d, sc_off, 1.0, 1.0, 1.0)
        if vis_off: struct.pack_into('>i', d, vis_off, -1)
        for prop, off_ in zip(('CollideActors', 'BlockActors', 'BlockNonZeroExtent', 'BlockZeroExtent', 'BlockRigidBody'), b['flags']):
            if off_: d = set_property(p3, w, d, to, prop, b'\x00', 'BoolProperty')
        refs.append(w.add_export(p3.exports[tcomp]['class'], coll + 1, ('TrilogyMerged', k + 1), bytes(d) + tail, flags=tflags, archetype=tarch))
        m = np.eye(4); m[3, :3] = offset
        mats.append(struct.pack('>16f', *m.flatten()))
    if n_nudged[0]: print(f'{n_nudged[0]} mesh triangles on BSP faces moved {COPLANAR_NUDGE} off them (no z-fighting)')
    if buckets: print(f'merged: {sum(len(b["P"]) for b in buckets.values())} mesh actors into {len(buckets)} chunks')
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
            light = light + bake(point_lights, Pb, Nb)[:, None, :]
        tail = bytes(4) if args.no_lighting else lighting.gears3_vertex_lightmap(light, len(w.exports) + 1, light_guids)
        d = bytearray(td[:tend]) + tail
        struct.pack_into('>i', d, sm_off, mref)
        struct.pack_into('>3f', d, sc_off, 1.0, 1.0, 1.0)
        if vis_off: struct.pack_into('>i', d, vis_off, -1)
        refs.append(w.add_export(p3.exports[tcomp]['class'], coll + 1, ('TrilogyBSP', k + 1), d, flags=tflags, archetype=tarch))
        m = np.eye(4); m[3, :3] = offset
        mats.append(struct.pack('>16f', *m.flatten()))
    # Gears 1's BlockingVolumes: hidden collision meshes for pawns (not for shots: BlockZeroExtent off), in world space
    n_volumes = 0
    if args.bsp and src.version == 374:
        for si, sp in enumerate(srcs):
            if getattr(sp, 'aux', False): continue
            vols_ = bspconv.blocking_volumes(sp)
            if si == 0 and args.extra_floors:
                # (recipe extra_floors) the S04 roadie-run door: stepping off the catwalk mover (InterpActor_22, its
                # slab ending flush with S05's floor at y 6176) onto world floor failed Gears 3's ledge check; a strip
                # of world floor under the slab's end makes the step world to world
                for k_, box in enumerate(x for x in args.extra_floors.split(';') if x.strip()):
                    x0, y0, z0, x1, y1, z1 = (float(v) for v in box.split(','))
                    Pb_ = np.array([[x, y, z] for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)], np.float64)
                    quads = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3))
                    idx_ = []
                    for a_, b_, c_, d_ in quads:
                        idx_ += [a_, b_, c_, a_, c_, d_, a_, c_, b_, a_, d_, c_]           # both windings
                    vols_.append({'name': f'ExtraFloor_{k_}', 'mesh': bspconv.collision_mesh(Pb_, idx_)})
                print(f"extra floors: {len([x for x in args.extra_floors.split(';') if x.strip()])}")
            for vol in vols_:
                upk.E = '>'
                data = per_poly_collision(meshconv.to_828(p3, tmesh, vol['mesh'], [None]))
                mref = w.add_export(p3.exports[tmesh]['class'], 0, ('Trilogy_Volume', n_volumes + 1), data, flags=mflags)
                d = bytearray(td[:tend])
                struct.pack_into('>i', d, sm_off, mref)
                struct.pack_into('>3f', d, sc_off, 1.0, 1.0, 1.0)
                if vis_off: struct.pack_into('>i', d, vis_off, -1)
                for prop, value in (('HiddenGame', b'\x01'), ('CastShadow', b'\x00'), ('BlockZeroExtent', b'\x01' if args.bodies_block_traces else b'\x00')):
                    d = set_property(p3, w, d, to, prop, value, 'BoolProperty')
                refs.append(w.add_export(p3.exports[tcomp]['class'], coll + 1, ('TrilogyVolume', n_volumes + 1), bytes(d) + bytes(4), flags=tflags, archetype=tarch))
                m = np.eye(4); m[3, :3] = offset
                mats.append(struct.pack('>16f', *m.flatten()))
                n_volumes += 1
        print(f'{n_volumes} blocking volumes as hidden collision meshes')
    # the meshes' simple collision (bspconv.body_hulls, gathered above): hidden collision meshes for pawns, one per
    # 4096-unit cell (up to 30000 vertices each), both windings as the volumes'
    n_bodies = 0
    if body_parts:
        cells = {}
        for P_, T_ in body_parts:
            cells.setdefault(tuple(np.floor(P_.mean(0) / 4096.0).astype(int)), []).append((P_, T_))
        for key in sorted(cells):
            chunk, P_all, idx, nv = [], [], [], 0
            for P_, T_ in cells[key] + [(None, None)]:
                if P_ is None or nv + len(P_) > 30000:
                    if idx: chunk.append(bspconv.collision_mesh(np.concatenate(P_all), idx))
                    P_all, idx, nv = [], [], 0
                    if P_ is None: break
                for t in range(0, len(T_) - 2, 3):
                    a_, b_, c_ = nv + T_[t], nv + T_[t + 1], nv + T_[t + 2]
                    idx += [a_, b_, c_, a_, c_, b_]
                P_all.append(P_); nv += len(P_)
            for mesh_ in chunk:
                upk.E = '>'
                data = per_poly_collision(meshconv.to_828(p3, tmesh, mesh_, [None]))
                mref = w.add_export(p3.exports[tmesh]['class'], 0, ('Trilogy_Body', n_bodies + 1), data, flags=mflags)
                d = bytearray(td[:tend])
                struct.pack_into('>i', d, sm_off, mref)
                struct.pack_into('>3f', d, sc_off, 1.0, 1.0, 1.0)
                if vis_off: struct.pack_into('>i', d, vis_off, -1)
                for prop, value in (('HiddenGame', b'\x01'), ('CastShadow', b'\x00'), ('BlockZeroExtent', b'\x01' if args.bodies_block_traces else b'\x00')):
                    d = set_property(p3, w, d, to, prop, value, 'BoolProperty')
                refs.append(w.add_export(p3.exports[tcomp]['class'], coll + 1, ('TrilogyBody', n_bodies + 1), bytes(d) + bytes(4), flags=tflags, archetype=tarch))
                m = np.eye(4); m[3, :3] = offset
                mats.append(struct.pack('>16f', *m.flatten()))
                n_bodies += 1
        print(f'{n_simple} meshes collide with pawns by their Gears 1 simple collision: {len(body_parts)} hulls in {n_bodies} hidden meshes')
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
            d = bytearray(w.current(i))
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
            d = bytearray(w.current(i))
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
            d = bytearray(w.current(i)); o, pr, end = props_at(p3, bytes(d), (26,))
            d = set_property(p3, w, d, o, 'Location', struct.pack('>3f', x, y, z), struct_name='Vector')
            # the exploration is bounded by the pylon's expansion sphere (MP_GridlockG3: an override centred on its
            # own map, radius 6,017): it moves with the pylon, radius --pylon-radius
            o, pr, end = props_at(p3, bytes(d), (26,))
            d = set_property(p3, w, d, o, 'ExpansionSphereCenter', struct.pack('>3f', x, y, z), struct_name='Vector')
            o, pr, end = props_at(p3, bytes(d), (26,))
            d = set_property(p3, w, d, o, 'ExpansionRadius', struct.pack('>f', args.pylon_radius), 'FloatProperty')
            w.replace(i, bytes(d))
            print(f'pylon-at: {p3.path(i)} at ({x:g}, {y:g}, {z:g}), expansion radius {args.pylon_radius:g}')
    # navigation points' NavGuids unique per converted package: every section is a clone of the same template level,
    # so its pylons and cover links repeated one set of GUIDs in every section, and a reference by GUID (the cross-
    # level navmesh joins, tools/navcross.py) named several pylons at once. (uuid5 of the package and the actor's path:
    # the same on every build)
    import uuid as _uuid
    _pkg = os.path.basename(args.target).lower()
    for i in range(len(p3.exports)):
        if not p3.path(i).startswith('TheWorld.PersistentLevel.') or p3.path(i).count('.') != 2: continue
        d = bytearray(w.current(i))
        try: o, pr, end = props_at(p3, bytes(d), (26,))
        except ValueError: continue
        v, size = tag_value_offset(p3, bytes(d), o, 'NavGuid')
        if v is None or size != 16: continue
        d[v:v + 16] = _uuid.uuid5(_uuid.NAMESPACE_URL, _pkg + '/' + p3.path(i)).bytes
        w.replace(i, bytes(d))
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
        own = [] if args.no_own_cover else srcs
        g1 = [link for sp in own for link in cover.read_g1_links(sp)]       # every sublevel's cover
        g1_src = [si for si, sp in enumerate(own) for _ in cover.read_g1_links(sp)]
        for path_ in [x for x in args.cover_from.split(',') if x.strip()]:   # (no Kismet of this package refers to them)
            extra_ = cover.read_g1_links(upk.Package(path_.strip()))
            g1 += extra_; g1_src += [None] * len(extra_)
        if args.cover_from or args.no_own_cover: print(f'cover: {len(g1)} links ({len(g1) - sum(1 for x in g1_src if x is not None)} from --cover-from)')
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
            if g1_src[x] is not None: cover_map[(g1_src[x], g1[x][0])] = ti + 1
        set_prop = lambda *a, **k: set_property(p3, w, *a, **k)
        new_slots = {}                                     # target export -> world positions of its new slots
        for ti, _, _, _ in g3:
            # (every rewrite here starts from the export as rewritten so far, not the template's original: the pylons
            # (--pylons-at) were appended to the navigation list after the template's last point, a CoverLink, and
            # rewriting that link from its original data cut the list there: the added pylons never reached the
            # navigation octree, the game found no polygons and no cover on them)
            d = bytearray(w.current(ti))
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
        # (the Level as rewritten so far: --pylons-at has already appended its pylons to the level's pylon and
        # navigation lists and Actors; the original's data put the list ends back, and the game, splicing the level's
        # pylon list into the world's at its end, never saw the added pylons: no polygons there, so no cover)
        ld = bytearray(w.current(lvl))
        L = cover.level_lists(p3, bytes(ld), w)
        link_refs, pairs_at, pairs = L['refs'], L['pairs_at'], L['pairs']
        old_pos = {g[0]: B[k] for k, g in enumerate(g3)}
        flat = [(r, j, q) for r, ti in enumerate(link_refs) for j, q in enumerate(new_slots.get(ti, []))]
        P = np.array([q for r, j, q in flat])
        dist = []; reached = set()
        for k, (r, j) in enumerate(pairs):
            if not 0 <= r < len(link_refs): continue   # (a pair outside the level's own links: left as the template has it)
            ti = link_refs[r]
            if not len(P): break                      # (no source cover among the target's links: nothing to aim at)
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
                if g1_src[x] is not None: cover_map[(g1_src[x], g1[x][0])] = ti + 1
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
                d = bytearray(w.current(i))
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
        kismet(srcs, [x for x in args.source.split(',') if x], p3, w, cls, args.offset, new_mesh, cover_map, args.content, args.g3cooked, args.player_starts, mic_map=mic_of)
    if manifest is not None:
        import json
        json.dump(manifest, open(args.texture_manifest, 'w'), indent=0)
        print(f"textures: {len(manifest['entries'])} in the shared package {args.shared_textures} so far")
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
    print(f'{n_noncolliding} components without collision, {n_pawn_through} not blocking pawns (as in Gears 1)')
    print(f'{len(img)} bytes; collection: {struct.unpack_from(">i", arr, 0)[0]} components, '
          f'{(len(qd) - qend) // 64} matrices; {len(q.exports)} exports')


SUN_FROM = []                 # (--sun-from) packages whose DirectionalLights become this package's sun


def kismet(srcs, names, p3, w, cls, offset_text='0,0,0', new_mesh=None, cover_map=None, content_names=None, g3cooked=None, with_starts=False, mic_map=None):
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
    # the chapter's carrier levels too (their content classes and class defaults: a Troika's component templates)
    g3pkgs += [upk.Package(n_.strip()) for n_ in (content_names or '').split(',') if n_.strip() and os.path.isfile(n_.strip())]
    classes = objcopy.class_index(g3pkgs)
    script3 = objcopy.Script3(g3pkgs)
    level3 = next(i for i in range(len(p3.exports)) if cls(i) == 'Level' and p3.path(i) == 'TheWorld.PersistentLevel')
    main3 = next(i for i in range(len(p3.exports)) if p3.path(i) == 'TheWorld.PersistentLevel.Main_Sequence')
    added = []
    stats = collections.Counter()
    only = [x for x in os.environ.get('LEVELINJECT_KISMET_ONLY', '').split(',') if x]   # (debugging) these packages' Kismet only
    # the sun (recipe sun_from: a section holding Gears 1's DirectionalLight): its light as objcopy's sun point light in
    # this package (the persistent level, always visible: lights in a hidden section light nothing; Embry's sun is in
    # its opening's section). Its other contents are not copied
    for fname in SUN_FROM:
        sp = upk.Package(fname)
        lvl = next((i for i in range(len(sp.exports)) if sp.path(i) == 'TheWorld.PersistentLevel'), None)
        suns = [i for i, e in enumerate(sp.exports) if sp.class_name(e['class']) == 'DirectionalLight'
                and sp.path(i).count('.') == 2 and sp.path(i).startswith('TheWorld.PersistentLevel')] if lvl is not None else []
        if not suns: print(f'sun: none in {os.path.basename(fname)}'); continue
        objcopy.install_sun_point()
        c = objcopy.Copier(sp, p3, w, types, classes, script3, offset, script1)
        made_s = c.copy(c.plan(suns), {lvl + 1: level3 + 1})
        objcopy.add_to_level(p3, w, level3, [made_s[ai] for ai in suns if made_s.get(ai)])
        print(f'sun: {len(suns)} from {os.path.basename(fname)} as point lights (radius {objcopy.SUN_RADIUS:.0f}, '
              f'{objcopy.SUN_DISTANCE:.0f} back)')
    for si, (sp, fname) in enumerate(zip(srcs, names)):
        if only and os.path.splitext(os.path.basename(fname))[0] not in only: continue
        main1 = next((i for i in range(len(sp.exports)) if sp.path(i) == 'TheWorld.PersistentLevel.Main_Sequence'), None)
        lvl = next((i for i in range(len(sp.exports)) if sp.path(i) == 'TheWorld.PersistentLevel'), None)
        if lvl is None: continue
        level1 = lvl + 1                                # (a package without Kismet still has actors to copy)
        c = objcopy.Copier(sp, p3, w, types, classes, script3, offset, script1)
        c.available_pawns = available
        loc = os.path.splitext(fname)[0] + '_LOC_int' + os.path.splitext(fname)[1]   # (its spoken lines' waves)
        if os.path.isfile(loc) and not os.environ.get('LEVELINJECT_NO_LOC'): c.loc = upk.Package(loc)
        for (msi, mi), ref in (new_mesh or {}).items():     # converted meshes stand for the source's
            if msi == si: c.obj_map[mi + 1] = ref
        for (msi, mref), mic in (mic_map or {}).items():   # ... converted materials too (a mover's own Materials)
            if msi == si and mic: c.obj_map.setdefault(mref, mic)
        for (isi, iref), key in imported_skel.items():     # ... and for its imports of them
            if isi == si and key in (new_mesh or {}): c.obj_map[iref] = new_mesh[key]
        for (csi, ci), ref in (cover_map or {}).items():    # cover links: the target's links holding their data
            if csi == si: c.obj_map[ci + 1] = ref
        # Gears 1's CoverGroups (AIMove targets: Dom's "into that cover"): Gears 3 drops them ("COVERGROUPS ARE OBSOLETE",
        # GearAI.OnAIMove) and tethers to any other actor; a reference to one becomes its converted link nearest to the
        # group's centre (Dom then moves to that cover)
        if not os.environ.get('LEVELINJECT_NO_COVERGROUPS'):
            for gi, ge in enumerate(sp.exports):
                if sp.class_name(ge['class']) != 'CoverGroup' or sp.path(gi).count('.') != 2: continue
                try: ga = {n: v for n, t, k, v in props_at(sp, sp.export_data(gi))[1]}
                except ValueError: continue
                gloc = np.array(struct.unpack('>3f', ga['Location'][1])) if ga.get('Location') else np.zeros(3)
                raw = ga.get('CoverLinkRefs')
                if not isinstance(raw, (bytes, bytearray)): continue
                best = None
                n_ = struct.unpack_from('>i', raw, 0)[0]; q_ = 4
                for _ in range(n_):
                    el_, q_ = materialconv.raw_props(sp, raw, q_)
                    r_ = {x[0]: x[4] for x in el_}
                    actor_ = r_.get('Nav') or r_.get('Actor')      # (Gears 1: a NavReference {Nav, Guid})
                    lr = struct.unpack('>i', actor_[:4])[0] if actor_ else 0
                    if lr <= 0 or lr not in c.obj_map or not c.obj_map[lr]: continue
                    try: la = {n: v for n, t, k, v in props_at(sp, sp.export_data(lr - 1))[1]}
                    except ValueError: continue
                    lloc = np.array(struct.unpack('>3f', la['Location'][1])) if la.get('Location') else gloc
                    dist = float(np.linalg.norm(lloc - gloc))
                    if best is None or dist < best[0]: best = (dist, c.obj_map[lr])
                if best is None:
                    # its links in another package (by GUID only): this package's converted link nearest to the
                    # group's centre, within 600 units (CoverGroup_5, the prison's doorway: Dom tethered to the
                    # doorway itself and blocked it)
                    for (csi, ci), ref in (cover_map or {}).items():
                        if not ref: continue           # (any of the target's sources: a section's links)
                        try: la = {n: v for n, t, k, v in props_at(srcs[csi], srcs[csi].export_data(ci))[1]}
                        except ValueError: continue
                        if not la.get('Location'): continue
                        dist = float(np.linalg.norm(np.array(struct.unpack('>3f', la['Location'][1])) - gloc))
                        if dist < 600 and (best is None or dist < best[0]): best = (dist, ref)
                if best: c.obj_map[gi + 1] = best[1]; stats['cover groups: their nearest link'] += 1
        todo = c.plan([main1]) if main1 is not None else []
        # the actors this Kismet refers to (triggers...): before it, so its references resolve to them
        new_actors = []
        lights_ = [] if os.environ.get('LEVELINJECT_NO_LIGHTS') else objcopy.lights(sp)   # (the level's lights, for characters)
        starts_ = objcopy.player_starts(sp) if with_starts else []
        routes = objcopy.route_ends(sp)                 # (Routes: their last points, copied, stand for them)
        ends = [n for n in set(routes.values()) if sp.class_name(sp.exports[n]['class']) in objcopy.ACTOR_CLASSES]
        wanted = objcopy.kismet_actors(sp, todo, objcopy.movers(sp) + sorted(objcopy.scripted_mesh_actors(sp)) + objcopy.skeletal_actors(sp) + objcopy.sound_actors(sp) + objcopy.pickups(sp) + lights_ + starts_ + ends)
        copied = []
        for ai in wanted:
            cn = sp.class_name(sp.exports[ai]['class'])
            stats['actors: ' + cn] += 1
            if cn == 'TriggerVolume':
                r = objcopy.volume_as_trigger(c, ai, level3 + 1)
                if r: new_actors.append(r)
            elif cn in objcopy.EHOLE_CLASSES:
                r = objcopy.hole_as_spawner(c, ai, level3 + 1)
                if r: new_actors.append(r)
            else:
                copied.append(ai)
        # in one pass: every reference between them resolves (exports are made before any data is written)
        made_a = c.copy(c.plan(copied), {level1: level3 + 1})
        new_actors += [made_a[ai] for ai in copied if made_a.get(ai)]
        for r, n in routes.items():                     # (a cover link end: the target link holding it)
            ref = made_a.get(n) or c.obj_map.get(n + 1)
            if ref: c.obj_map[r + 1] = ref; stats['routes: their last point'] += 1
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
            stats['held shots resumed after a path choice'] += objcopy.resume_held_matinees(up, made)
            stats['light environments for skeletal mesh actors'] += objcopy.light_environments(up, made)
            stats['squad factories (player slot)'] += objcopy.squad_factories(up, made)
            stats['factory spawn points defaulted'] += objcopy.factory_spawn_fallback(up, made)
            stats['checkpoint events also on save'] += objcopy.checkpoint_events(up, made)
            stats['named variables of the persistent level'] += objcopy.drop_remote_from_sequences(up, made)
            if objcopy.player_pawn(up, made): print(f'kismet: the player becomes {objcopy.PLAYER_PAWN[0]} after {objcopy.PLAYER_PAWN[1]} ({objcopy.PLAYER_PAWN[2]})')
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
        n, old = (struct.unpack_from('>i', d, v)[0], bytes(d[v + 4:v + size])) if v is not None else (0, b'')   # (none yet)
        value = struct.pack('>i', n + len(added)) + old + b''.join(struct.pack('>i', r) for r in added)
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
          f"{'renamed' if objcopy.STREAMING_PREFIX is not None else 'emptied'}; {stats['squad factories (player slot)']} squad factories; "
          f"{stats['factory spawn points defaulted']} empty factory spawn points set to the player start; "
          f"{stats['named variables of the persistent level']} named variables of the persistent level imported")
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
                    r[4] = (materialconv.g1_post_process_value(name, g[4]) if src.version == 374 else g[4])   # same layout
                else:
                    r[4] = g[4]
            out.append(r); have.add(name)
        for name in ('Scene_Desaturation', 'Scene_HighLights', 'Scene_MidTones', 'Scene_Shadows', 'Bloom_Scale',
                     'DOF_FocusDistance', 'DOF_ModulateBlurColor'):
            if name in g1 and name not in have:
                g = g1[name]; extra = struct.pack('>ii', *w.name(src.fname(*struct.unpack('>ii', g[3])))) if g[3] else b''
                out.append([name, g[1], 0, extra, materialconv.g1_post_process_value(name, g[4]) if src.version == 374 else g[4]])
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
        d = bytearray(w.current(ef)); o, pr, end = props_at(p3, bytes(d), (8, 4, 12))
        for name, value in (('OppositeLightColor', color), ('LightInscatteringColor', color),
                            ('OppositeLightBrightness', struct.pack('>f', bright)),
                            ('LightInscatteringBrightness', struct.pack('>f', bright * 0.5))):
            vv, sz = tag_value_offset(p3, bytes(d), o, name)
            if vv is not None and sz == len(value): d[vv:vv + sz] = value
        w.replace(ef, bytes(d))
        print(f'environment: fog colour BGRA {color.hex()} x {bright:.2f}')


if __name__ == '__main__':
    main()
