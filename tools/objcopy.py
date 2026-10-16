"""Copy objects from a Gears of War 1 (374) package into a Gears 3 (828) package (offline): the export, its tagged
properties re-encoded for 828, every object and name reference remapped. The first use is Kismet (sequences and their
objects); the same applies to any actor whose class Gears 3 has.

Property encoding, 374 -> 828:
  BoolProperty   374: a 4-byte value under size 4      828: size 0, one value byte
  ByteProperty   374: no enum name                     828: an enum FName after size/index ('None': the raw value)
  StructProperty both: the struct's FName after size/index; a struct whose value parses as tagged properties is
                 converted recursively, others (Vector, Rotator, Color...) are copied as bytes
  ArrayProperty  element type from the source game's property declarations (an ArrayProperty export's Inner, the
                 property export inside it): object refs and names remapped, structs converted element by element
  Object/Class/Name/Delegate references remapped; Int/Float/Str copied.
Exports: class -> a Gears 3 import of the same class name (War* -> Gear* when Gears 3 has only that); archetype 0 (the
class default); NetIndex (the 4-byte prelude of a non-actor object) -1."""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk

# ops whose links are kept as Gears 1 laid them (as a sequence's). Not the AI factory: Gears 3 lays its links out as
# the class default's fixed ones (Spawn Points, Last Spawned, Spawned, Num Dead, Player Slot, Spawn Destinations)
# then one per spawn set ("Set 1 [1]"...), which is what the rebuild gives (defaults, then Gears 1's extra links
# appended); kept as Gears 1 laid them, the factory never filled the per-set variable (Dom's stayed empty)
DYNAMIC_LINKS = set()
CLASS_RENAME = {} if os.environ.get('OBJCOPY_NO_RENAME') else {
    'SeqEvent_LevelBeginning': 'SeqEvent_LevelLoaded', 'SeqVar_Dom': 'SeqVar_Object',
    # Gears 1 Kismet classes with a Gears 3 class doing the same (links rebuilt from the Gears 3 class; properties
    # Gears 3 does not declare are ignored by its loader); counts: uses in Gears 1's campaign
    'SeqAct_WarCameraShake': 'SeqAct_CameraShake',          # 333
    'SeqAct_WarUseCommLink': 'SeqAct_GearUseCommLink',      # 157
    'SeqAct_WarCheckPoint': 'SeqAct_Checkpoint',            # 128
    'SeqEvt_CheckpointActivated': 'SeqEvent_CheckpointLoaded',  # 130
    'SeqAct_AIAcquireEnemy': 'SeqAct_AISetTarget',          # 177
    'SeqAct_AISetFocus': 'SeqAct_AILookAt',                 # 85
    'SeqAct_WarPlayerAnim': 'SeqAct_GearPlayerAnim',        # 47
    'SeqAct_SetMood': 'SeqAct_AISetMood',                   # 45
    # Gears 3's AI (GearAI) moves through GearGame's SeqAct_AIMove; Engine's AIMoveToActor started but the pawn
    # never moved (the converted opening's Dom stayed at his spawn point and its chain stopped there)
    # GearAI takes move orders from GearGame's SeqAct_AIMove (with Engine's AIMoveToActor the campaign's Dom stayed
    # Action_Idle with no command)
    'SeqAct_AIMoveToActor': 'SeqAct_AIMove',
    'SeqAct_ToggleHammerOfDawn': 'SeqAct_ToggleHOD',        # 68
    'SeqAct_TempHUDFade': 'SeqAct_CameraFade',              # 132
}   # Gears 3 fires "Beginning of Level" on this one

# classes not copied (yet): Gears 1's AI spawner holds Gears 1 spawn sets, and its Gears 3 PostLoad crashed the load
# ("Actor is static" logged through a null output device, sub_8243CDE8 from sub_82A5FBE0): enemies need their own mapping
# ops that act on the player's controller: a SeqVar_Marcus linked only by these becomes Gears 3's GearSeqVar_Player
PLAYER_CONTROL_OPS = {'SeqAct_ToggleCinematicMode', 'SeqAct_ToggleHUD', 'SeqAct_ToggleInput'}
SKIP_CLASSES = set(filter(None, os.environ.get('OBJCOPY_SKIP', '').split(',')))   # (debugging)
EDITOR_COMPONENTS = {'DrawFrustumComponent', 'DrawLightConeComponent', 'DrawLightRadiusComponent', 'ArrowComponent',
                     'SpriteComponent', 'DrawBoxComponent', 'DrawSphereComponent', 'DrawCylinderComponent'}

# Gears 1 AI pawn classes -> Gears 3 content classes (GearGameContent.<name>: loaded with the content packages of the
# game mode / map; levelinject passes the classes available, a factory spawning another is dropped)
PAWN_MAP = {
    'Pawn_GeistLocust': 'GearPawn_LocustDrone', 'Pawn_GeistLocustSniper': 'GearPawn_LocustSniper',
    'Pawn_GeistTroikaGunner': 'GearPawn_LocustDrone', 'Pawn_GeistTroikaSpotter': 'GearPawn_LocustDrone',
    'Pawn_GeistBoomer': 'GearPawn_LocustBoomer', 'Pawn_GeistTheron': 'GearPawn_LocustTheron',
    'Pawn_GeistHunterArmorNoGrenades': 'GearPawn_LocustHunterArmorNoGrenades',
    'Pawn_GeistHunterNoArmorNoGrenades': 'GearPawn_LocustHunterNoArmorNoGrenades',
    'Pawn_GeistBerzerker': 'GearPawn_LocustBerserker', 'Pawn_GeistWretchContent': 'GearPawn_LocustWretch',
    'Pawn_GeistDarkWretch': 'GearPawn_LocustDarkWretch', 'Pawn_GeistDarkWretchSuicider': 'GearPawn_LocustDarkWretch',
    'Pawn_GeistNemacyst': 'GearPawn_LocustNemacyst', 'Pawn_GeistGeneralRaam': 'GearPawn_LocustGeneralRaam',
    'Pawn_COGDom': ['GearPawn_COGDom', 'GearPawn_COGDomG2'], 'Pawn_COGGus': 'GearPawn_COGGus',
    'Pawn_COGGus_Resonator': 'GearPawn_COGGus', 'Pawn_COGBaird': 'GearPawn_COGBaird',
    'Pawn_COGCarmine': ['GearPawn_COGCarmine', 'GearPawn_COGBenjaminCarmine'], 'Pawn_COGMinh': 'GearPawn_COGMinh',
}   # (a list: the first one loaded in the game mode / map)
PAWN_STANDIN = {'COG': ['GearPawn_COGOnyxGuard', 'GearPawn_COGRedShirt', 'GearPawn_COGDomG2'],
                'Locust': ['GearPawn_LocustDrone', 'GearPawn_LocustHunterNoArmorNoGrenades', 'GearPawn_LocustTheron']}
# controllers Gears 3 names differently (the rest: War* -> Gear*)
CONTROLLER_MAP = {'WarAI_Berzerker': 'GearAI_Berserker', 'WarAI_Nemacyst': 'GearAI_NemacystBase', 'WarAI_Raam': 'GearAI_Locust',
                  'WarAI_Drone': 'GearAI_Theron'}

# Gears 1 enum values whose name Gears 3 does not have: the Gears 3 value standing for them (else the property is left
# out: the class default applies)
ENUM_ALIASES = {'AIType_DarkWretchSuicider': 'AIType_DarkWretch', 'AIType_Gus_Resonator': 'AIType_Gus', 'WEAP_HammerOfDawn': 'WEAP_HOD'}

PRIMS = {'IntProperty', 'FloatProperty', 'BoolProperty', 'ByteProperty', 'StrProperty', 'NameProperty',
         'ObjectProperty', 'ClassProperty', 'StructProperty', 'ArrayProperty', 'DelegateProperty',
         'InterfaceProperty', 'ComponentProperty', 'MapProperty'}


def property_types(packages):
    """{property name: (property class, array inner class or None, struct name or None)} from script packages"""
    out = {}
    for p in packages:
        children = {}
        for i, e in enumerate(p.exports):
            children.setdefault(e['outer'], []).append(i)
        for i, e in enumerate(p.exports):
            cn = p.class_name(e['class'])
            if cn not in PRIMS: continue
            # an array's inner property has the array's name: never the declaration itself (taken first, it made
            # SequenceObjects/LinkedVariables plain object properties, and their Gears 1 indices were copied raw)
            if e['outer'] > 0 and p.class_name(p.exports[e['outer'] - 1]['class']) == 'ArrayProperty': continue
            inner = None
            if cn == 'ArrayProperty':
                kids = [k for k in children.get(i + 1, []) if p.class_name(p.exports[k]['class']) in PRIMS]
                if kids: inner = p.class_name(p.exports[kids[0]]['class'])
            out.setdefault(e['name'], (cn, inner, None))
    return out


def class_index(packages):
    """{class name: script package name} for the destination game's script packages"""
    out = {}
    for p in packages:
        pkg = os.path.splitext(os.path.basename(p.filename))[0]
        for e in p.exports:
            if p.class_name(e['class']) == 'Class' and e['outer'] == 0: out.setdefault(e['name'], pkg)
    return out


class Copier:
    def __init__(self, src, dst, w, types, classes=None, script3=None, offset=(0.0, 0.0, 0.0), script1=None):
        self.s, self.p3, self.w, self.types = src, dst, w, types
        self.g1 = script1                                   # Script3 over Gears 1's script: its enums' value names
        self.classes = classes or {}                        # destination class name -> script package
        self.g3 = script3                                   # Script3: actor/component kinds, component templates
        self.offset = offset                                # added to every copied actor's Location
        self.obj_map = {}                                   # source ref -> destination ref (0 when dropped)
        self.class_imports = {}
        self.unconvertible = {}                             # class -> objects dropped because their data did not convert
        self.kinds = {}                                     # destination ref -> (kind, class ref)

    # ---- references ----
    def name(self, index, number):
        return self.w.name(self.s.names[index], number)

    def ref(self, r):
        if r == 0: return 0
        if r in self.obj_map: return self.obj_map[r]
        if r < 0:                                           # an import: a class through the class mapping (War* -> Gear*,
            im = self.s.imports[-r - 1]                     # imported from its script package), else the same name
            if im['class'] == 'Class':
                return self.class_ref(CLASS_RENAME.get(im['name'], im['name']))
            return self.import_like(r)
        if self.s.class_name(self.s.exports[r - 1]['class']) in DEEP_COPY:
            return self.deep_copy(r - 1)
        return 0                                            # an export not copied: None

    def deep_copy(self, i):
        """export i (an AnimSet...) and everything under it, copied when first referenced: a top-level object of the
        destination (its source outer is the asset's package), named Trilogy_<name>"""
        made = self.copy(self.plan([i]), {self.s.exports[i]['outer']: 0}, rename={i: 'Trilogy_' + self.s.exports[i]['name']})
        return made.get(i, 0)

    def import_like(self, r):
        im = self.s.imports[-r - 1]
        want = im['name']
        for k, jm in enumerate(self.p3.imports):
            if jm['name'] == want and jm.get('class') == im.get('class'): return -(k + 1)
        return 0

    def class_ref(self, name):
        if name in self.class_imports: return self.class_imports[name]
        r = 0
        for cand in (name, 'Gear' + name[3:] if name.startswith('War') else None):
            if not cand: continue
            for k, jm in enumerate(self.p3.imports):
                if jm['name'] == cand and jm.get('class') == 'Class': r = -(k + 1); break
            if r: break
        if not r:                                           # not imported yet: the class's script package, then the class
            for cand in (name, 'Gear' + name[3:] if name.startswith('War') else None):
                if cand and cand in self.classes:
                    pkg = self.classes[cand]
                    pk = next((-(k + 1) for k, jm in enumerate(self.w_imports()) if jm == ('Core', 'Package', 0, pkg)), None)
                    if pk is None: pk = self.w.add_import('Core', 'Package', 0, pkg)
                    r = self.w.add_import('Core', 'Class', pk, cand)
                    break
        self.class_imports[name] = r
        return r

    def w_imports(self):
        """the destination's imports as (class package, class, outer, name), including ones added"""
        out = []
        for raw in self.w.imports:
            cp, _, cn, _, outer, on, _n = struct.unpack('>7i', raw)
            out.append((self.w.names[cp], self.w.names[cn], outer, self.w.names[on]))
        return out

    # ---- properties ----
    def props(self, d, o):
        """(828 tagged properties for the 374 ones at d[o:], offset after the source's None)"""
        s = self.s; out = bytearray()
        while True:
            name = s.fname(*struct.unpack_from('>ii', d, o))
            if name == 'None': o += 8; break
            typ = s.fname(*struct.unpack_from('>ii', d, o + 8))
            size, index = struct.unpack_from('>ii', d, o + 16)
            o += 24
            extra = b''
            if typ == 'StructProperty':
                extra = struct.pack('>ii', *self.name(*struct.unpack_from('>ii', d, o))); sname = s.fname(*struct.unpack_from('>ii', d, o)); o += 8
            if typ == 'BoolProperty':
                value = bytes([1 if struct.unpack_from('>i', d, o)[0] else 0]); o += 4; size_out = 0
                out += self.tag(name, typ, size_out, index) + value; continue
            raw = d[o:o + size]; o += size
            if typ == 'ByteProperty':
                # an enum (Gears 3's declaration): the enum's name in the tag, the value's name as the data, as
                # Gears 3 writes them; other bytes as they are
                en = self.g3.enums.get(name) if self.g3 else None
                en1 = self.g1.enums.get(name) if self.g1 else None
                if en and en1 and en1[0] != en[0]: en = None  # (the same property name, different enums: ambiguous)
                if len(raw) == 1 and en:
                    # by name: Gears 1's value name (its own enum: the orders differ, AIType 10 is Minh in Gears 1,
                    # Redshirt in Gears 3), or an alias; else (no Gears 1 enum known) the same index
                    vname = en1[1][raw[0]] if en1 and raw[0] < len(en1[1]) else (en[1][raw[0]] if raw[0] < len(en[1]) else None)
                    vname = vname if vname in en[1] else ENUM_ALIASES.get(vname)
                    if vname is None:
                        self.unconvertible[f'enum {name} value {raw[0]} (not in Gears 3)'] = self.unconvertible.get(f'enum {name} value {raw[0]} (not in Gears 3)', 0) + 1
                        continue
                    value = struct.pack('>ii', *self.w.name(vname))
                    out += self.tag(name, typ, 8, index) + struct.pack('>ii', *self.w.name(en[0])) + value; continue
                value = raw if len(raw) == 1 else struct.pack('>ii', *self.name(*struct.unpack('>ii', raw)))
                out += self.tag(name, typ, len(value), index) + struct.pack('>ii', *self.w.name('None')) + value; continue
            if name == 'WarPawnClass' and typ == 'ObjectProperty':       # AISpawnInfo: Gears 3's GearPawnClass
                name = 'GearPawnClass'
                value = struct.pack('>i', self.pawn_class(struct.unpack('>i', raw)[0]))
            elif name == 'ControllerClass' and typ == 'ObjectProperty' and struct.unpack('>i', raw)[0] < 0:
                cn0 = self.s.imports[-struct.unpack('>i', raw)[0] - 1]['name']
                value = struct.pack('>i', self.class_ref(CONTROLLER_MAP.get(cn0, cn0)))
            elif typ in ('ObjectProperty', 'ClassProperty', 'ComponentProperty', 'InterfaceProperty'):
                value = struct.pack('>i', self.ref(struct.unpack('>i', raw)[0]))
            elif typ == 'NameProperty':
                value = struct.pack('>ii', *self.name(*struct.unpack('>ii', raw)))
            elif typ == 'DelegateProperty':
                value = struct.pack('>i', self.ref(struct.unpack_from('>i', raw, 0)[0])) + struct.pack('>ii', *self.name(*struct.unpack_from('>ii', raw, 4)))
            elif typ == 'StructProperty':
                value = self.struct_value(raw)
            elif typ == 'ArrayProperty':
                value = self.array_value(name, raw)
            else:
                value = raw
            out += self.tag(name, typ, len(value), index) + extra + value
        return bytes(out + struct.pack('>ii', *self.w.name('None'))), o

    def tag(self, name, typ, size, index):
        return struct.pack('>ii', *self.w.name(name)) + struct.pack('>ii', *self.w.name(typ)) + struct.pack('>ii', size, index)

    def tagged(self, raw, o=0):
        """raw[o:] as tagged properties? -> (converted, end) or None"""
        try:
            name = self.s.fname(*struct.unpack_from('>ii', raw, o))
            if name != 'None' and self.s.fname(*struct.unpack_from('>ii', raw, o + 8)) not in PRIMS: return None
            return self.props(raw, o)
        except Exception:
            return None

    def struct_value(self, raw):
        t = self.tagged(raw)
        if t and t[1] == len(raw): return t[0]
        return raw                                          # a native struct (vector, rotator, colour...)

    def array_value(self, name, raw):
        n = struct.unpack_from('>i', raw, 0)[0]
        body = raw[4:]
        inner = self.types.get(name, (None, None, None))[1]
        out = bytearray(struct.pack('>i', n))
        if inner in ('ObjectProperty', 'ClassProperty', 'ComponentProperty', 'InterfaceProperty') and len(body) == 4 * n:
            for k in range(n): out += struct.pack('>i', self.ref(struct.unpack_from('>i', body, 4 * k)[0]))
            return bytes(out)
        if inner == 'NameProperty' and len(body) == 8 * n:
            for k in range(n): out += struct.pack('>ii', *self.name(*struct.unpack_from('>ii', body, 8 * k)))
            return bytes(out)
        if inner == 'ByteProperty' and len(body) == n:
            # an enum array: each element the value's name in Gears 3 (8 bytes), by Gears 1's value name
            en = self.g3.enums.get(name) if self.g3 else None
            en1 = self.g1.enums.get(name) if self.g1 else None
            if en and not (en1 and en1[0] != en[0]):
                vals = []
                for v in body:
                    vn = en1[1][v] if en1 and v < len(en1[1]) else (en[1][v] if v < len(en[1]) else None)
                    vn = vn if vn in en[1] else ENUM_ALIASES.get(vn)
                    if vn is None:
                        self.unconvertible[f'enum array {name} value {v}'] = self.unconvertible.get(f'enum array {name} value {v}', 0) + 1
                        continue
                    vals.append(struct.pack('>ii', *self.w.name(vn)))
                return struct.pack('>i', len(vals)) + b''.join(vals)
        if inner == 'BoolProperty' and len(body) == 4 * n:
            return bytes(out) + bytes(1 if struct.unpack_from('>i', body, 4 * k)[0] else 0 for k in range(n))
        if inner == 'StructProperty' or inner is None:
            o = 0; ok = True; parts = []
            for k in range(n):
                t = self.tagged(body, o)
                if not t: ok = False; break
                parts.append(t[0]); o = t[1]
            if ok and o == len(body): return bytes(out) + b''.join(parts)
        return raw                                          # ints, floats, bytes, native structs, strings

    # ---- exports ----
    def plan(self, roots):
        """the exports to copy: roots and everything under them (outer chain), in source order"""
        want = set(roots)
        changed = True
        while changed:
            changed = False
            for i, e in enumerate(self.s.exports):
                if i in want or e['outer'] - 1 not in want: continue
                cn = self.s.class_name(e['class'])
                owner = self.s.class_name(self.s.exports[e['outer'] - 1]['class'])
                # editor-only components (frustum / light cone drawers, sprites...) and a camera's mesh: not copied
                if cn in EDITOR_COMPONENTS or (owner == 'CameraActor' and cn == 'StaticMeshComponent'): continue
                want.add(i); changed = True
        return sorted(want)

    def g3_class(self, cn):
        cn = CLASS_RENAME.get(cn, cn)
        if self.g3 and cn not in self.g3.classes and cn.startswith('War') and 'Gear' + cn[3:] in self.g3.classes:
            return 'Gear' + cn[3:]
        return cn

    def kind(self, cn):
        """'actor', 'component' or 'object' (the serial prelude: Gears 1 32/8/4 bytes, Gears 3 26/8/4)"""
        c = self.g3_class(cn)
        if self.g3 and self.g3.is_a(c, 'Actor'): return 'actor'
        if self.g3 and self.g3.is_a(c, 'Component'): return 'component'
        return 'object'

    PRELUDE_374 = {'actor': 32, 'component': 8, 'object': 4}

    def prelude_828(self, kind, cls):
        if kind == 'actor':                                 # state frame: Node, StateNode (the class), ProbeMask,
            return struct.pack('>iiIHiii', cls, cls, 0xFFFFFFFF, 0, 0, -1, -1)   # LatentAction, StateStack, Offset, NetIndex
        if kind == 'component': return struct.pack('>ii', 0, -1)           # TemplateOwnerClass, NetIndex
        return struct.pack('>i', -1)                                        # NetIndex

    def pawn_class(self, r):
        """a Gears 1 pawn class reference -> an import of the Gears 3 content class standing for it, when available
        (self.available_pawns: None = assume all); else 0 and the source class is recorded in self.missing_pawns"""
        if r == 0: return 0
        g1 = self.s.class_name(r)
        cands = PAWN_MAP.get(g1)
        cands = [cands] if isinstance(cands, str) else (cands or [])
        avail = getattr(self, 'available_pawns', None)
        g3 = next((c for c in cands if avail is None or c in avail), None)
        if cands and g3 is None:
            # not loaded in this game mode: a stand-in that is (COG: an Onyx Guard, a redshirt...; Locust: a Drone, a
            # Hunter...), or OBJCOPY_PAWN_FALLBACK; none available -> the factory is dropped
            env = os.environ.get('OBJCOPY_PAWN_FALLBACK')
            pool = [env] if env else PAWN_STANDIN['COG' if '_COG' in cands[0] else 'Locust']
            g3 = next((c for c in pool if c in avail), None)
            self.standins = getattr(self, 'standins', {}); self.standins[g1] = g3 or 'none available'
        if not g3:
            self.missing_pawns = getattr(self, 'missing_pawns', set()) | {g1}
            return 0
        pk = self.find_import('Core', 'Package', 0, 'GearGameContent')
        return self.find_import('Core', 'Class', pk, g3)

    def unique_name(self, base, num):
        """(base, number) not yet used by an export of the destination (numbers stored +1, as FNames)"""
        used = getattr(self.w, 'used_export_names', None)
        if used is None:
            used = self.w.used_export_names = {e['name'] for e in self.p3.exports}
        while (f'{base}_{num - 1}' if num else base) in used: num = num + 1 if num else 1
        used.add(f'{base}_{num - 1}' if num else base)
        return base, num

    def find_import(self, class_package, class_name, outer, name):
        for k, im in enumerate(self.w_imports()):
            if im == (class_package, class_name, outer, name): return -(k + 1)
        return self.w.add_import(class_package, class_name, outer, name)

    def component_template(self, i):
        """a component's archetype in Gears 3: <package>.Default__<actor class>.<template name>, when Gears 3's class
        default has a subobject of that name (else None: the component class's own default)"""
        e = self.s.exports[i]
        arch = struct.unpack_from('>i', self.s.data, e['entry'] + 20)[0]
        if arch >= 0 or not self.g3: return 0
        tname = self.s.imports[-arch - 1]['name']
        douter = self.s.imports[-arch - 1]['outer']
        if douter >= 0: return 0
        dname = self.s.imports[-douter - 1]['name']                      # Default__Trigger
        acls = self.g3_class(dname[9:])
        if acls + '' not in self.g3.defaults: return 0
        dp, di = self.g3.defaults[acls]
        kids = [j for j, f in enumerate(dp.exports) if f['outer'] == di + 1 and f['name'] == tname]
        if not kids: return 0
        pkg = os.path.splitext(os.path.basename(dp.filename))[0]
        pk = self.find_import('Core', 'Package', 0, pkg)
        d_imp = self.find_import(pkg, acls, pk, 'Default__' + acls)
        ccls = dp.class_name(dp.exports[kids[0]]['class'])
        return self.find_import(self.classes.get(ccls, pkg), ccls, d_imp, tname)

    def linked_vars(self, i):
        """source export indices of the variables an op (source export i) links"""
        import materialconv
        try: recs, _ = materialconv.raw_props(self.s, self.s.export_data(i), 4)
        except Exception: return []
        v = next((r[4] for r in recs if r[0] == 'VariableLinks'), None)
        if v is None: return []
        out = []
        n = struct.unpack_from('>i', v, 0)[0]; o = 4
        for _ in range(n):
            el, o = materialconv.raw_props(self.s, v, o)
            lv = next((r[4] for r in el if r[0] == 'LinkedVariables'), b'\0\0\0\0')
            m = struct.unpack_from('>i', lv, 0)[0]
            out += [r - 1 for r in (struct.unpack_from(f'>{m}i', lv, 4) if len(lv) == 4 + 4 * m else ()) if r > 0]
        return out

    def player_vars(self, exports):
        """Gears 1's SeqVar_Marcus linked only by player-control ops (cinematic mode): Gears 3's SeqVar_Marcus is a
        SeqVar_Character (Marcus's pawn), and cinematic mode wants the player's controller (Gears 3's cutscenes link
        GearSeqVar_Player): with Marcus the opening's cutscene ran with the player visible and the HUD up"""
        users = {}
        for i in exports:
            for v in self.linked_vars(i): users.setdefault(v, set()).add(self.s.class_name(self.s.exports[i]['class']))
        # (also with a ToggleHidden hiding the player's pawn beside a cinematic mode, whose bHidePlayer does that)
        return {v for v, cs in users.items() if self.s.class_name(self.s.exports[v]['class']) == 'SeqVar_Marcus'
                and (cs <= PLAYER_CONTROL_OPS or ('SeqAct_ToggleCinematicMode' in cs
                                                  and cs <= PLAYER_CONTROL_OPS | {'SeqAct_ToggleHidden'}))}

    def factory_output_var(self, candidates):
        """the variable among candidates (source export indices) that an AI factory's variable links write to"""
        import materialconv
        want = set(candidates)
        for i, e in enumerate(self.s.exports):
            if self.s.class_name(e['class']) != 'SeqAct_AIFactory': continue
            try: recs, _ = materialconv.raw_props(self.s, self.s.export_data(i), 4)
            except Exception: continue
            v = next((r[4] for r in recs if r[0] == 'VariableLinks'), None)
            if v is None: continue
            n = struct.unpack_from('>i', v, 0)[0]; o = 4
            for _ in range(n):
                el, o = materialconv.raw_props(self.s, v, o)
                lv = next((r[4] for r in el if r[0] == 'LinkedVariables'), b'\0\0\0\0')
                m = struct.unpack_from('>i', lv, 0)[0]
                for r in (struct.unpack_from(f'>{m}i', lv, 4) if len(lv) == 4 + 4 * m else ()):
                    if r - 1 in want: return r - 1
        return None

    def copy(self, exports, outer_map, rename=None):
        """create the destination exports (empty), then their data: returns {source index: destination ref}"""
        made = {}
        # Gears 1's SeqVar_Dom is a lookup (every instance = the Dom pawn); as plain object variables only the one an AI
        # factory writes Dom into holds him. So every SeqVar_Dom of the package is merged into that one (the others are
        # not created; references to them go to it): the opening's move order then reaches the Dom it spawned
        dom_vars = [i for i in exports if self.s.class_name(self.s.exports[i]['class']) == 'SeqVar_Dom']
        canonical = self.factory_output_var(dom_vars) if len(dom_vars) > 1 and not SQUAD_SLOTS else None
        aliases = [i for i in dom_vars if canonical is not None and i != canonical]
        as_player = self.player_vars(exports) if not os.environ.get('OBJCOPY_NO_RENAME') else set()
        for i in exports:
            e = self.s.exports[i]
            cn = self.s.class_name(e['class'])
            if i in aliases: continue
            cls = self.class_ref(self.g3_class(cn)) if cn not in SKIP_CLASSES else 0
            if i in as_player and self.class_ref('GearSeqVar_Player'):
                cls = self.class_ref('GearSeqVar_Player')
                self.renamed_player_vars = getattr(self, 'renamed_player_vars', 0) + 1
            if not cls and cn.startswith('SeqAct_') and cn not in SKIP_CLASSES and not os.environ.get('OBJCOPY_NO_PASSTHROUGH'):
                # an action Gears 3 has no class for: a pass-through (SeqAct_Log, screen output off) keeps the chains
                # through it running (dropped, it stopped every chain: the opening's AISetTetherDist left the player
                # in cinematic mode); the link rebuild routes all its inputs to In and all its outputs to Out
                cls = self.class_ref('SeqAct_Log')
                self.passthrough_src = getattr(self, 'passthrough_src', set()) | {i}
                self.unconvertible[f'{cn} (as a pass-through)'] = self.unconvertible.get(f'{cn} (as a pass-through)', 0) + 1
            kind = self.kind(cn)
            if cls:
                # properties that do not convert (a component's prelude is not a NetIndex: Gears 1's distributions,
                # unless known as components): the object is dropped; written with empty properties and its raw
                # native data, the load hung
                try: self.props(self.s.export_data(i), self.PRELUDE_374[kind])
                except Exception: cls = 0; self.unconvertible[cn] = self.unconvertible.get(cn, 0) + 1
            if cls and cn == 'SeqAct_AIFactory':
                before = set(getattr(self, 'missing_pawns', set()))
                self.missing_pawns = set()                  # (the convertibility check above already ran it)
                self.props(self.s.export_data(i), 4)
                lost = self.missing_pawns
                self.missing_pawns = before | lost
                if lost:
                    cls = 0
                    for x in lost: self.unconvertible[f'SeqAct_AIFactory (pawn {x} not available)'] = self.unconvertible.get(f'SeqAct_AIFactory (pawn {x} not available)', 0) + 1
            if not cls: self.obj_map[i + 1] = 0; continue
            nm = (rename or {}).get(i, e['name'])
            base, num = (nm.rsplit('_', 1)[0], int(nm.rsplit('_', 1)[1]) + 1) if '_' in nm and nm.rsplit('_', 1)[1].isdigit() else (nm, 0)
            # object flags as Gears 3's own level objects (0x0007000100000000: RF_LoadForClient/Server/Edit and
            # RF_Public; actors also 0x0200000000000000): without the load flags the loader skips the export
            if kind == 'actor': base, num = self.unique_name(base, num)   # actors of several packages share the level
            flags = 0x0207000100000000 if kind == 'actor' else 0x0007000100000000
            arch = self.component_template(i) if kind == 'component' else 0
            ref = self.w.add_export(cls, 0, (base, num), b'', flags=flags, archetype=arch)
            self.obj_map[i + 1] = made[i] = ref
            self.kinds[ref] = (kind, cls)
        for i in aliases: self.obj_map[i + 1] = self.obj_map.get(canonical + 1, 0)
        # outers once every export exists (a child can come before its parent in the source): entry field +8
        for i, ref in made.items():
            o = self.s.exports[i]['outer']
            outer = outer_map.get(o, self.obj_map.get(o, 0))
            struct.pack_into('>i', self.w.exports[ref - 1], 8, outer)
        for i, ref in made.items():
            d = self.s.export_data(i)
            kind, cls = self.kinds[ref]
            cn = self.s.class_name(self.s.exports[i]['class'])
            props, end = self.props(d, self.PRELUDE_374[kind])
            if kind == 'actor' and any(self.offset):
                props = self.moved(props)
            if kind == 'actor' and os.environ.get('OBJCOPY_UNHIDE'):     # (testing) actors scripts would reveal
                props = self.without(props, {'bHidden'})
            test_anim = os.environ.get('OBJCOPY_TEST_ANIM', '')            # (testing) "AnimSet:Sequence" looping
            if os.environ.get('OBJCOPY_ANIM_ROTATION_ONLY') and cn == 'AnimSet':    # (testing) ref pose translations
                props = self.without(props, {'bAnimRotationOnly'})
            if test_anim and cn in ('SkeletalMeshComponent', 'AnimNodeSequence'):
                props = self.test_anim(cn, props, *test_anim.split(':'))
            place = os.environ.get('OBJCOPY_PLACE', '')                  # (testing) "Class=x,y,z": put them there
            if kind == 'actor' and place.split('=')[0] in (cn, self.s.exports[i]['name']):   # class or actor name
                x, y, z = (float(v) for v in place.split('=')[1].split(','))
                self.placed = getattr(self, 'placed', 0) + 1
                props = self.located(props, (x + 80 * (self.placed - 1), y, z))
            if kind == 'component' and cn in COMPONENT_DROP:
                props = self.without(props, COMPONENT_DROP[cn])
            self.current_src = i
            if cn in FIXUPS: props = FIXUPS[cn](self, props)
            if i in getattr(self, 'passthrough_src', ()):
                props = self.without(props, {'bOutputToScreen'})
                wn = lambda a_, n_: (self.w.names[a_] if n_ == 0 else f'{self.w.names[a_]}_{n_ - 1}')
                recs_, _ = parse828(props, 0, wn)
                props = encode(self.w, recs_ + [['bOutputToScreen', 'BoolProperty', 0, b'', b'\x00']])
            tail = d[end:] if kind == 'object' else COMPONENT_TAIL.get(cn, b'') if kind == 'component' else b''
            self.tail_fixups = []
            if cn in TAIL_FIXUPS: props, tail = TAIL_FIXUPS[cn](self, props, tail)
            head = self.prelude_828(kind, cls) + props
            self.w.replace(ref - 1, head + tail)
            if self.tail_fixups:                                        # bulk data offsets (relative to the tail)
                self.w.fixups[ref - 1] = [(len(head) + f, len(head) + t) for f, t in self.tail_fixups]
        return made

    def without(self, props, names):
        wname = lambda i, n: (self.w.names[i] if n == 0 else f'{self.w.names[i]}_{n - 1}')
        recs, _ = parse828(props, 0, wname)
        return encode(self.w, [r for r in recs if r[0] not in names])

    def test_anim(self, cn, props, set_name, seq):
        wname = lambda i, n: (self.w.names[i] if n == 0 else f'{self.w.names[i]}_{n - 1}')
        recs, _ = parse828(props, 0, wname)
        if cn == 'SkeletalMeshComponent':
            src = next((i for i, e in enumerate(self.s.exports) if e['name'] == set_name
                        and self.s.class_name(e['class']) == 'AnimSet'), None)
            if src is None: return props
            ref = self.ref(src + 1)
            recs = [r for r in recs if r[0] != 'AnimSets'] + [['AnimSets', 'ArrayProperty', 0, b'', struct.pack('>ii', 1, ref)]]
        else:
            recs = [r for r in recs if r[0] not in ('AnimSeqName', 'bPlaying', 'bLooping')] + [
                ['AnimSeqName', 'NameProperty', 0, b'', struct.pack('>ii', *self.w.name(seq))],
                ['bPlaying', 'BoolProperty', 0, b'', b'\x01'], ['bLooping', 'BoolProperty', 0, b'', b'\x01']]
        return encode(self.w, recs)

    def shift_points(self, curve, offset):
        """an InterpCurveVector (tagged: Points of {InVal, OutVal, ...}) with every OutVal moved by offset"""
        wname = lambda i, n: (self.w.names[i] if n == 0 else f'{self.w.names[i]}_{n - 1}')
        sub, _ = parse828(curve, 0, wname)
        for r in sub:
            if r[0] != 'Points': continue
            n = struct.unpack_from('>i', r[4], 0)[0]; o = 4; els = []
            for _ in range(n):
                el, o = parse828(r[4], o, wname)
                for x in el:
                    if x[0] == 'OutVal' and len(x[4]) == 12:
                        v = struct.unpack('>3f', x[4]); x[4] = struct.pack('>3f', *(v[k] + offset[k] for k in range(3)))
                els.append(encode(self.w, el))
            r[4] = struct.pack('>i', n) + b''.join(els)
        return encode(self.w, sub)

    def located(self, props, loc):
        wname = lambda i, n: (self.w.names[i] if n == 0 else f'{self.w.names[i]}_{n - 1}')
        recs, _ = parse828(props, 0, wname)
        for r in recs:
            if r[0] == 'Location': r[4] = struct.pack('>3f', *loc)
        return encode(self.w, recs)

    def moved(self, props):
        wname = lambda i, n: (self.w.names[i] if n == 0 else f'{self.w.names[i]}_{n - 1}')
        recs, _ = parse828(props, 0, wname)
        for r in recs:
            if r[0] == 'Location' and len(r[4]) == 12:
                x, y, z = struct.unpack('>3f', r[4])
                r[4] = struct.pack('>3f', x + self.offset[0], y + self.offset[1], z + self.offset[2])
        return encode(self.w, recs)


# ---- Kismet links rebuilt from Gears 3's classes (the editor's UpdateObject, which cooked data never gets) ----
# A Gears 1 op keeps the links its class had in 2006: a Gears 3 class may have more, fewer or other ones (Gears 3's
# SeqEvent_LevelLoaded: "Loaded and Visible", "Beginning of Level", "Level Reset"; Gears 1's: "Out"). The engine
# activates a link by index, so each op gets its Gears 3 class's link arrays (from the class defaults, nearest in the
# super chain) with the connections (Links, LinkedVariables, LinkedEvents) of the Gears 1 link of the same description
# (else the same index when the counts agree); every output's InputLinkIdx follows its target's new input order.
LINK_ARRAYS = (('InputLinks', None), ('OutputLinks', 'Links'), ('VariableLinks', 'LinkedVariables'),
               ('EventLinks', 'LinkedEvents'))
LINK_ALIAS = {('SeqAct_AIMoveToActor', 'finished'): 'reached goal', ('SeqAct_AIMoveToActor', 'destination'): 'move to',
              ('SeqAct_WarCheckPoint', 'set active checkpoint'): 'save',
              ('SeqEvent_LevelLoaded', 'out'): 'loaded and visible',
              ('SeqEvent_LevelBeginning', 'out'): 'beginning of level'}


def parse828(d, o, fname):
    """828 tagged properties at d[o:] -> ([[name, type, index, extra, value]], end); fname(index, number) -> str"""
    out = []
    while True:
        name = fname(*struct.unpack_from('>ii', d, o))
        if name == 'None': return out, o + 8
        typ = fname(*struct.unpack_from('>ii', d, o + 8))
        size, index = struct.unpack_from('>ii', d, o + 16)
        o += 24
        extra = b''
        if typ in ('StructProperty', 'ByteProperty'): extra = d[o:o + 8]; o += 8
        if typ == 'BoolProperty': size = 1
        out.append([name, typ, index, extra, d[o:o + size]]); o += size


def encode(w, recs):
    """records ([name, type, index, extra, value]) -> 828 tagged properties ending in None, names in w"""
    out = bytearray()
    for name, typ, index, extra, value in recs:
        out += struct.pack('>ii', *w.name(name)) + struct.pack('>ii', *w.name(typ))
        out += struct.pack('>ii', 0 if typ == 'BoolProperty' else len(value), index) + extra + value
    return bytes(out + struct.pack('>ii', *w.name('None')))


class Script3:
    """Gears 3's script packages: class defaults and property declarations"""
    def __init__(self, packages):
        self.classes = {}                                   # name -> (package, export index)
        self.defaults = {}
        for p in packages:
            for i, e in enumerate(p.exports):
                if e['outer'] != 0: continue
                if p.class_name(e['class']) == 'Class': self.classes.setdefault(e['name'], (p, i))
                elif e['name'].startswith('Default__'): self.defaults.setdefault(e['name'][9:], (p, i))
        self.types = property_types(packages)
        # enums: {byte property name: (enum name, [value names])}. A ByteProperty export ends with its Enum reference,
        # an Enum export with its value names (count + FNames)
        enum_values = {}
        for p in packages:
            for i, e in enumerate(p.exports):
                if p.class_name(e['class']) != 'Enum': continue
                d = p.export_data(i)
                for n in range(1, 400):
                    o = len(d) - 4 - 8 * n
                    if o < 0: break
                    if struct.unpack_from('>i', d, o)[0] == n:
                        enum_values.setdefault(e['name'], [p.fname(*struct.unpack_from('>ii', d, o + 4 + 8 * k)) for k in range(n)])
                        break
        self.enums = {}
        for p in packages:
            for i, e in enumerate(p.exports):
                if p.class_name(e['class']) != 'ByteProperty': continue
                d = p.export_data(i)
                if len(d) < 4: continue
                r = struct.unpack_from('>i', d, len(d) - 4)[0]
                en = p.exports[r - 1]['name'] if r > 0 else p.imports[-r - 1]['name'] if r < 0 else None
                if en in enum_values: self.enums.setdefault(e['name'], (en, enum_values[en]))

    def super_of(self, name):
        if name not in self.classes: return None
        p, i = self.classes[name]
        sup = struct.unpack_from('>i', p.data, p.exports[i]['entry'] + 4)[0]
        return p.class_name(sup) if sup else None

    def is_a(self, cls, base):
        c = cls
        while c:
            if c == base: return True
            c = self.super_of(c)
        return False

    def default_array(self, cls, prop):
        """(package, raw value) of the nearest default in cls's super chain that stores prop, or None"""
        c = cls
        while c:
            if c in self.defaults:
                p, i = self.defaults[c]
                try:
                    recs, _ = parse828(p.export_data(i), 4, p.fname)
                    for r in recs:
                        if r[0] == prop: return p, r[4]
                except Exception:
                    pass
            c = self.super_of(c)
        return None


def str_value(v):
    n = struct.unpack_from('>i', v, 0)[0]
    return v[4:4 + n - 1].decode('latin-1') if n > 0 else ''


class Upgrader:
    def __init__(self, copier, script3):
        self.c, self.g3, self.w = copier, script3, copier.w
        self.wname = lambda i, n: (self.w.names[i] if n == 0 else f'{self.w.names[i]}_{n - 1}')

    def enc(self, recs):
        return encode(self.w, recs)

    def elements(self, v, fname):
        n = struct.unpack_from('>i', v, 0)[0]; o = 4; els = []
        for _ in range(n):
            recs, o = parse828(v, o, fname); els.append(recs)
        return els

    def retag(self, recs, p):
        """records parsed from Gears 3 script package p -> records valid in the destination (names, refs)"""
        out = []
        for name, typ, index, extra, value in recs:
            if typ in ('StructProperty', 'ByteProperty'):
                extra = struct.pack('>ii', *self.w.name(p.fname(*struct.unpack('>ii', extra))))
            if (typ == 'ByteProperty' and len(value) == 8) or typ == 'NameProperty':
                value = struct.pack('>ii', *self.w.name(p.fname(*struct.unpack('>ii', value))))
            elif typ in ('ObjectProperty', 'ClassProperty'):
                value = struct.pack('>i', self.ref3(p, struct.unpack('>i', value)[0]))
            elif typ == 'StructProperty':
                try:
                    sub, end = parse828(value, 0, p.fname)
                    if end == len(value): value = self.enc(self.retag(sub, p))
                except Exception:
                    pass
            elif typ == 'ArrayProperty':
                inner = self.g3.types.get(name, (None, None, None))[1]
                n = struct.unpack_from('>i', value, 0)[0]
                if inner in ('ObjectProperty', 'ClassProperty'):
                    value = struct.pack('>i', n) + b''.join(struct.pack('>i', self.ref3(p, r)) for r in struct.unpack_from(f'>{n}i', value, 4))
                elif inner == 'StructProperty':
                    value = struct.pack('>i', n) + b''.join(self.enc(self.retag(e, p)) for e in self.elements(value, p.fname))
            out.append([name, typ, index, extra, value])
        return out

    def ref3(self, p, r):
        """a class reference in a script package -> the destination's import of that class; other objects: None"""
        if r == 0: return 0
        is_class = p.class_name(p.exports[r - 1]['class']) == 'Class' if r > 0 else p.imports[-r - 1]['class'] == 'Class'
        return self.c.class_ref(p.class_name(r)) if is_class else 0

    def run(self, made):
        """made: {source index: destination ref} from Copier.copy -> stats"""
        s, w = self.c.s, self.w
        made = {i: ref for i, ref in made.items() if self.c.kinds.get(ref, ('object',))[0] == 'object'}   # (no components)
        cur = {ref: parse828(w.current(ref - 1), 4, self.wname) for ref in made.values()}
        src_class = {ref: s.class_name(s.exports[i]['class']) for i, ref in made.items()}
        plan, input_map, stats = {}, {}, {'ops upgraded': 0, 'links kept from Gears 1': 0}
        desc = lambda e: next((str_value(r[4]).lower() for r in e if r[0] == 'LinkDesc'), '')
        for ref, (recs, end) in cur.items():
            have = {r[0]: r for r in recs}
            arrays = {}
            passthrough = ref in {made_ref for i_, made_ref in made.items() if i_ in getattr(self.c, 'passthrough_src', ())}
            g3c = 'SeqAct_Log' if passthrough else CLASS_RENAME.get(src_class[ref], src_class[ref])
            if self.g3.is_a(g3c, 'Sequence'): continue          # a sequence's links are its own (events/finish actions)
            if g3c in DYNAMIC_LINKS: continue                   # links laid out per instance (an AI factory's per spawn set)
            for prop, conn in LINK_ARRAYS:
                dflt = self.g3.default_array(g3c, prop)
                if dflt is None: continue
                new = [self.retag(e, dflt[0]) for e in self.elements(dflt[1], dflt[0].fname)]
                old = self.elements(have[prop][4], self.wname) if prop in have else []
                nd = [desc(e) for e in new]
                mapping = {}
                for k, e in enumerate(old):
                    d = LINK_ALIAS.get((src_class[ref], desc(e)), desc(e))
                    if passthrough and prop in ('InputLinks', 'OutputLinks') and new: mapping[k] = 0
                    elif d in nd: mapping[k] = nd.index(d)
                    elif len(old) == len(new): mapping[k] = k
                for k, e in enumerate(old):
                    c = next((r for r in e if r[0] == conn), None) if conn else None
                    if k not in mapping:                        # no Gears 3 counterpart (a switch's "Link 3"...): kept
                        new.append(list(e)); mapping[k] = len(new) - 1; stats['links kept from Gears 1'] += 1
                        if (g3c == 'SeqAct_AIFactory' and prop == 'VariableLinks' and c is not None
                                and desc(e).startswith('set ') and 'last spawned' in nd):
                            # Gears 1's per spawn set output ("Set 1 [1]": the pawn it spawned) is never written by
                            # Gears 3's factory (the opening's Dom variable stayed empty, so his "Let's go" never
                            # finished and he never moved): its variables also go on Gears 3's "Last Spawned" (an
                            # object variable; "Spawned" takes a SeqVar_ObjectList and left a SeqVar_Object empty)
                            tgt = new[nd.index('last spawned')]
                            prev = next((r for r in tgt if r[0] == conn), None)
                            n1 = struct.unpack_from('>i', prev[4], 0)[0] if prev is not None else 0
                            n2 = struct.unpack_from('>i', c[4], 0)[0]
                            merged = struct.pack('>i', n1 + n2) + (prev[4][4:] if prev is not None else b'') + c[4][4:]
                            tgt[:] = [r for r in tgt if r[0] != conn] + [[conn, c[1], c[2], c[3], merged]]
                            stats['factory sets also on Last Spawned'] = stats.get('factory sets also on Last Spawned', 0) + 1
                        continue
                    if c is not None:
                        tgt = new[mapping[k]]
                        prev = next((r for r in tgt if r[0] == conn), None)
                        if passthrough and prev is not None and struct.unpack_from('>i', prev[4], 0)[0]:
                            n1, n2 = struct.unpack_from('>i', prev[4], 0)[0], struct.unpack_from('>i', c[4], 0)[0]
                            merged = struct.pack('>i', n1 + n2) + prev[4][4:] + c[4][4:]
                            tgt[:] = [r for r in tgt if r[0] != conn] + [[conn, c[1], c[2], c[3], merged]]
                        else:
                            tgt[:] = [r for r in tgt if r[0] != conn] + [list(c)]
                if prop == 'InputLinks': input_map[ref] = mapping
                arrays[prop] = new
            if arrays: plan[ref] = (recs, end, arrays); stats['ops upgraded'] += 1
        for ref, (recs, end, arrays) in plan.items():
            for el in arrays.get('OutputLinks', []):
                for r in el:
                    if r[0] != 'Links': continue
                    links = self.elements(r[4], self.wname)
                    for ln in links:
                        op = next((struct.unpack('>i', x[4])[0] for x in ln if x[0] == 'LinkedOp'), 0)
                        idx_r = next((x for x in ln if x[0] == 'InputLinkIdx'), None)
                        idx = struct.unpack('>i', idx_r[4])[0] if idx_r else 0
                        new_idx = input_map.get(op, {}).get(idx, idx)
                        if new_idx != idx:
                            if idx_r: idx_r[4] = struct.pack('>i', new_idx)
                            else: ln.append(['InputLinkIdx', 'IntProperty', 0, b'', struct.pack('>i', new_idx)])
                    r[4] = struct.pack('>i', len(links)) + b''.join(self.enc(ln) for ln in links)
            out = [r for r in recs if r[0] not in arrays]
            for prop, els in arrays.items():
                out.append([prop, 'ArrayProperty', 0, b'', struct.pack('>i', len(els)) + b''.join(self.enc(e) for e in els)])
            d = w.current(ref - 1)
            w.replace(ref - 1, d[:4] + self.enc(out) + d[end:])
        return stats


# ---- level streaming in converted Kismet ----
# A converted level merges the chosen Gears 1 sublevels into the target's persistent level, so they are always loaded
# and visible, and Gears 1's other sublevels do not exist in Gears 3's world. Streaming and visibility actions are
# emptied (no level names, not blocking): they complete at once and fire their outputs, so the scripts after them run.
# Left alone, the Prison's opening SeqAct_MultiLevelStreaming (10 Gears 1 sublevels, blocking) hung the match load.
STREAMING = {'SeqAct_LevelStreaming': ('LevelName',), 'SeqAct_LevelVisibility': ('LevelName',),
             'SeqAct_MultiLevelStreaming': ('Levels',), 'SeqAct_WaitForLevelsVisible': ('LevelNames',)}


# A chapter of its own (tools/levelpkg.py) keeps the source's streaming: every source package is converted into a
# package of its own named STREAMING_PREFIX + its name, so a level name of a built package is renamed (in each
# action's LevelName, Levels[].LevelName, LevelNames) and the others are dropped from the action (a level not built:
# Gears 1's next chapter in PrepareMapChange). STREAMING_BUILT: the source package names built (lower case).
STREAMING_PREFIX = None
STREAMING_BUILT = set()


def rename_streaming(up, made):
    s, w = up.c.s, up.w
    n = 0
    def rename(name_bytes):
        old = up.wname(*struct.unpack('>ii', name_bytes))
        return struct.pack('>ii', *w.name(STREAMING_PREFIX + old)) if old.lower() in STREAMING_BUILT else None
    for i, ref in made.items():
        cn = s.class_name(s.exports[i]['class'])
        if cn not in STREAMING: continue
        recs, end = parse828(w.current(ref - 1), 4, up.wname)
        for r in recs:
            if r[0] not in STREAMING[cn]: continue
            if r[1] == 'NameProperty':
                r[4] = rename(r[4]) or struct.pack('>ii', *w.name('None'))
            elif r[0] == 'LevelNames':                 # array of names
                cnt = struct.unpack_from('>i', r[4], 0)[0]
                names = [rename(r[4][4 + 8 * k:12 + 8 * k]) for k in range(cnt)]
                names = [x for x in names if x]
                r[4] = struct.pack('>i', len(names)) + b''.join(names)
            else:                                      # Levels: array of {LevelName, ...} structs
                els = up.elements(r[4], up.wname)
                keep = []
                for e in els:
                    ln = next((x for x in e if x[0] == 'LevelName'), None)
                    if ln is None: continue
                    nb = rename(ln[4])
                    if nb is None: continue
                    ln[4] = nb; keep.append(e)
                r[4] = struct.pack('>i', len(keep)) + b''.join(up.enc(e) for e in keep)
        d = w.current(ref - 1)
        w.replace(ref - 1, d[:4] + up.enc(recs) + d[end:])
        n += 1
    return n


def neutralize_streaming(up, made):
    if STREAMING_PREFIX is not None: return rename_streaming(up, made)
    s, w = up.c.s, up.w
    n = 0
    for i, ref in made.items():
        cn = s.class_name(s.exports[i]['class'])
        if cn not in STREAMING: continue
        recs, end = parse828(w.current(ref - 1), 4, up.wname)
        out = []
        for r in recs:
            if r[0] in STREAMING[cn]:
                r = [r[0], r[1], r[2], r[3], struct.pack('>ii', *w.name('None')) if r[1] == 'NameProperty' else struct.pack('>i', 0)]
            if r[0] != 'bShouldBlockOnLoad': out.append(r)
        if cn != 'SeqAct_LevelVisibility':
            out.append(['bShouldBlockOnLoad', 'BoolProperty', 0, b'', b'\x00'])
        d = w.current(ref - 1)
        w.replace(ref - 1, d[:4] + up.enc(out) + d[end:])
        n += 1
    return n


# ---- the view after a cutscene ----
# A Matinee's director track restores the player's view when the Matinee ends (InterpTrackInstDirector.OldViewTarget).
# In the converted opening it stayed null and the view stayed on the cutscene's last camera, with the player in
# control of a pawn they could not see. Every copied Matinee with a director group gets a SeqAct_SetCameraTarget on
# its "Completed" output (no camera target: the player's own view), on GearSeqVar_Player ("All Players").
def restore_view_after_cutscenes(up, made):
    import levelinject
    c, s, w = up.c, up.c.s, up.w
    n = 0
    for i, ref in made.items():
        if s.class_name(s.exports[i]['class']) != 'SeqAct_Interp': continue
        data = [v for v in c.linked_vars(i) if s.class_name(s.exports[v]['class']) == 'InterpData']
        if not any(s.class_name(e['class']) == 'InterpGroupDirector' and e['outer'] - 1 in data for e in s.exports): continue
        seq = struct.unpack_from('>i', w.exports[ref - 1], 8)[0]
        if seq <= 0: continue
        op_cls, var_cls = c.class_ref('SeqAct_SetCameraTarget'), c.class_ref('GearSeqVar_Player')
        if not op_cls or not var_cls: return n
        parent = ['ParentSequence', 'ObjectProperty', 0, b'', struct.pack('>i', seq)]
        var = w.add_export(var_cls, seq, c.unique_name('G1ViewPlayer', 0), b'', flags=0x0007000100000000)
        w.replace(var - 1, struct.pack('>i', -1) + up.enc([parent]))
        op = w.add_export(op_cls, seq, c.unique_name('G1ViewRestore', 0), b'', flags=0x0007000100000000)
        arrays = []
        for prop, conn in LINK_ARRAYS[:3]:
            dflt = up.g3.default_array('SeqAct_SetCameraTarget', prop)
            if dflt is None: continue
            els = [up.retag(e, dflt[0]) for e in up.elements(dflt[1], dflt[0].fname)]
            if prop == 'VariableLinks':
                for e in els:
                    if next((str_value(r[4]) for r in e if r[0] == 'LinkDesc'), '').lower() == 'target':
                        e[:] = [r for r in e if r[0] != 'LinkedVariables'] + [['LinkedVariables', 'ArrayProperty', 0, b'', struct.pack('>ii', 1, var)]]
            arrays.append([prop, 'ArrayProperty', 0, b'', struct.pack('>i', len(els)) + b''.join(up.enc(e) for e in els)])
        w.replace(op - 1, struct.pack('>i', -1) + up.enc(arrays + [parent]))
        # the Matinee's "Completed" -> the new op
        recs, end = parse828(w.current(ref - 1), 4, up.wname)
        for r in recs:
            if r[0] != 'OutputLinks': continue
            els = up.elements(r[4], up.wname)
            for e in els:
                if next((str_value(x[4]) for x in e if x[0] == 'LinkDesc'), '').lower() != 'completed': continue
                lr = next((x for x in e if x[0] == 'Links'), None)
                link = [['LinkedOp', 'ObjectProperty', 0, b'', struct.pack('>i', op)], ['InputLinkIdx', 'IntProperty', 0, b'', struct.pack('>i', 0)]]
                if lr is None: e.append(['Links', 'ArrayProperty', 0, b'', struct.pack('>i', 1) + up.enc(link)])
                else: lr[4] = struct.pack('>i', struct.unpack_from('>i', lr[4], 0)[0] + 1) + lr[4][4:] + up.enc(link)
            r[4] = struct.pack('>i', len(els)) + b''.join(up.enc(e) for e in els)
        d = w.current(ref - 1)
        w.replace(ref - 1, d[:4] + up.enc(recs) + d[end:])
        # the sequence lists them (initialised with it)
        sd = w.current(seq - 1)
        srecs, send = parse828(sd, 4, up.wname)
        for r in srecs:
            if r[0] == 'SequenceObjects':
                r[4] = struct.pack('>i', struct.unpack_from('>i', r[4], 0)[0] + 2) + r[4][4:] + struct.pack('>ii', var, op)
        w.replace(seq - 1, sd[:4] + up.enc(srecs) + sd[send:])
        n += 1
    return n


# ---- actors the copied Kismet refers to ----
# Copied like any object (actor prelude 32 -> 26 bytes, components with their class default's template as archetype,
# Location + the conversion offset), then listed in Level.Actors. Classes needing data Gears 3 reads differently are
# converted instead: a TriggerVolume (a brush Model + cached physics data) becomes a Trigger whose collision cylinder
# covers the brush's bounds (touch events behave the same; the shape is approximate).
ACTOR_CLASSES = set() if os.environ.get("OBJCOPY_NO_ACTORS") else {"Trigger", "TriggerVolume", "InterpActor", "Trigger_DoorInteraction", "Trigger_ButtonInteraction", "Note", "SkeletalMeshActor", "AmbientSoundSimple", "AmbientSoundNonLoop", "AmbientSound", "PathNode", "CameraActor", "PlayerStart"}
# Gears 1's lights: their light is baked into the converted geometry (tools/lighting.py), but Gears 3 lights
# characters (light environments) and cutscene actors from light actors: copied, lighting dynamic objects only
LIGHT_CLASSES = {"PointLight", "SpotLight", "PointLightToggleable", "SpotLightToggleable", "PointLightMovable", "SpotLightMovable"}
if not os.environ.get("OBJCOPY_NO_ACTORS"): ACTOR_CLASSES |= LIGHT_CLASSES
SOUND_ACTORS = {'AmbientSoundSimple', 'AmbientSoundNonLoop', 'AmbientSound'}
# per component class: properties left out (a StaticMeshComponent's Materials override would point at Gears 1
# materials: the converted mesh carries converted ones) and the native tail written (LODData: empty, no light map)
COMPONENT_DROP = {'StaticMeshComponent': {'Materials'}}
COMPONENT_TAIL = {'StaticMeshComponent': bytes(4),
                  # a light component ends with its InclusionConvexVolumes / ExclusionConvexVolumes (empty arrays): written
                  # without them, Gears 3 read past the component and the campaign load crashed or hung
                  'PointLightComponent': bytes(8), 'SpotLightComponent': bytes(8)}


def movers(src):
    """the level's InterpActors (doors, lifts...): copied whether or not Kismet refers to them"""
    return [i for i, e in enumerate(src.exports) if src.class_name(e['class']) == 'InterpActor'
            and src.path(i).count('.') == 2 and src.path(i).startswith('TheWorld.PersistentLevel')]


def player_starts(src):
    """the level's PlayerStarts (a chapter of its own: the source persistent level's start is the chapter's)"""
    return [i for i, e in enumerate(src.exports) if src.class_name(e['class']) == 'PlayerStart'
            and src.path(i).count('.') == 2 and src.path(i).startswith('TheWorld.PersistentLevel')]


def sound_actors(src):
    """the level's ambient sound actors (copied with their sounds)"""
    return [i for i, e in enumerate(src.exports) if src.class_name(e['class']) in SOUND_ACTORS
            and src.path(i).count('.') == 2 and src.path(i).startswith('TheWorld.PersistentLevel')]


def skeletal_actors(src):
    """the level's SkeletalMeshActors (their meshes converted by tools/skelmesh.py)"""
    return [i for i, e in enumerate(src.exports) if src.class_name(e['class']) == 'SkeletalMeshActor'
            and src.path(i).count('.') == 2 and src.path(i).startswith('TheWorld.PersistentLevel')]


def skeletal_meshes(src):
    """export indices of the skeletal meshes the level's SkeletalMeshActors show"""
    import materialconv
    out = set()
    for i in skeletal_actors(src):
        for j, e in enumerate(src.exports):
            if e['outer'] != i + 1 or src.class_name(e['class']) != 'SkeletalMeshComponent': continue
            try: recs, _ = materialconv.raw_props(src, src.export_data(j), 8)
            except Exception: continue
            for r in recs:
                if r[0] == 'SkeletalMesh':
                    m = struct.unpack('>i', r[4])[0]
                    if m > 0: out.add(m - 1)
    return out


def skeletal_mesh_imports(src):
    """import refs (negative) of the skeletal meshes the level's SkeletalMeshActors show from other packages"""
    import materialconv
    out = set()
    for i in skeletal_actors(src):
        for j, e in enumerate(src.exports):
            if e['outer'] != i + 1 or src.class_name(e['class']) != 'SkeletalMeshComponent': continue
            try: recs, _ = materialconv.raw_props(src, src.export_data(j), 8)
            except Exception: continue
            for r in recs:
                if r[0] == 'SkeletalMesh':
                    m = struct.unpack('>i', r[4])[0]
                    if m < 0 and src.imports[-m - 1].get('class') == 'SkeletalMesh': out.add(m)
    return out


def import_path(src, ref):
    """an import's full name (Package.Group.Name)"""
    parts = []
    for _ in range(16):                     # (an outer can be an export: a cooked level's package placeholder)
        if ref == 0: break
        x = src.imports[-ref - 1] if ref < 0 else src.exports[ref - 1]
        parts.append(x['name']); ref = x['outer']
    return '.'.join(reversed(parts))


def mover_meshes(src):
    """export indices of the static meshes the level's InterpActors show"""
    import materialconv
    out = set()
    for i in movers(src):
        for j, e in enumerate(src.exports):
            if e['outer'] != i + 1 or src.class_name(e['class']) != 'StaticMeshComponent': continue
            try: recs, _ = materialconv.raw_props(src, src.export_data(j), 8)
            except Exception: continue
            for r in recs:
                if r[0] == 'StaticMesh':
                    m = struct.unpack('>i', r[4])[0]
                    if m > 0: out.add(m - 1)
    return out


def kismet_actors(src, kismet_exports, extra=()):
    """source export indices of the actors (of ACTOR_CLASSES) that the Kismet exports refer to, with extra (other
    actors to copy), and the actors those refer to in turn (a door trigger's AlignToObj Note...)"""
    import materialconv
    out = set(extra)
    todo = [(i, 4) for i in kismet_exports] + [(i, 32) for i in extra]
    while todo:
        i, start = todo.pop()
        try: recs, _ = materialconv.raw_props(src, src.export_data(i), start)
        except Exception: continue
        for r in recs:
            refs = []
            if r[1] == 'ObjectProperty' and len(r[4]) == 4: refs = [struct.unpack('>i', r[4])[0]]
            elif r[1] == 'ArrayProperty' and len(r[4]) >= 4:
                n = struct.unpack_from('>i', r[4], 0)[0]
                if len(r[4]) == 4 + 4 * n: refs = struct.unpack_from(f'>{n}i', r[4], 4)
            for v in refs:
                if v > 0 and v - 1 not in out and src.class_name(src.exports[v - 1]['class']) in ACTOR_CLASSES:
                    out.add(v - 1); todo.append((v - 1, 32))
    return sorted(out)


def volume_as_trigger(c, i, level_ref):
    """a Gears 3 Trigger standing for Gears 1 volume i (bounds of its brush): returns the new actor's ref"""
    import materialconv
    s = c.s
    d = s.export_data(i)
    recs, _ = materialconv.raw_props(s, d, 32)
    a = {r[0]: r[4] for r in recs}
    loc = struct.unpack('>3f', a['Location']) if 'Location' in a else (0.0, 0.0, 0.0)
    pivot = struct.unpack('>3f', a['PrePivot']) if 'PrePivot' in a else (0.0, 0.0, 0.0)
    scale = struct.unpack('>3f', a['DrawScale3D']) if 'DrawScale3D' in a else (1.0, 1.0, 1.0)
    ds = struct.unpack('>f', a['DrawScale'])[0] if 'DrawScale' in a else 1.0
    brush = struct.unpack('>i', a['Brush'])[0] if 'Brush' in a else 0
    if brush <= 0: return 0
    md = s.export_data(brush - 1)
    _, o = s.properties(md, 4)
    origin = struct.unpack_from('>3f', md, o); extent = struct.unpack_from('>3f', md, o + 12)
    sc = [scale[k] * ds for k in range(3)]
    centre = [loc[k] + (origin[k] - pivot[k]) * sc[k] + c.offset[k] for k in range(3)]   # (rotation ignored)
    radius = max(abs(extent[0] * sc[0]), abs(extent[1] * sc[1]))
    height = abs(extent[2] * sc[2])
    trig = c.class_ref('Trigger'); cyl = c.class_ref('CylinderComponent')
    pk = c.find_import('Core', 'Package', 0, 'Engine')
    d_imp = c.find_import('Engine', 'Trigger', pk, 'Default__Trigger')
    tmpl = c.find_import('Engine', 'CylinderComponent', d_imp, 'CollisionCylinder')
    name = s.exports[i]['name']
    num = int(name.rsplit('_', 1)[1]) + 1 if name.rsplit('_', 1)[-1].isdigit() else 0
    actor = c.w.add_export(trig, level_ref, c.unique_name('G1Volume', num), b'', flags=0x0207000100000000)
    comp = c.w.add_export(cyl, actor, ('CylinderComponent', num), b'', flags=0x0007000100000000, archetype=tmpl)
    f = lambda v: struct.pack('>f', v)
    c.w.replace(comp - 1, c.prelude_828('component', cyl) + encode(c.w, [
        ['CollisionRadius', 'FloatProperty', 0, b'', f(radius)], ['CollisionHeight', 'FloatProperty', 0, b'', f(height)]]))
    vec = lambda v: struct.pack('>3f', *v)
    recs = [['CylinderComponent', 'ObjectProperty', 0, b'', struct.pack('>i', comp)],
            ['Components', 'ArrayProperty', 0, b'', struct.pack('>ii', 1, comp)],
            ['Location', 'StructProperty', 0, struct.pack('>ii', *c.w.name('Vector')), vec(centre)],
            ['CollisionComponent', 'ObjectProperty', 0, b'', struct.pack('>i', comp)]]
    if 'Tag' in a: recs.append(['Tag', 'NameProperty', 0, b'', struct.pack('>ii', *c.name(*struct.unpack('>ii', a['Tag'])))])
    c.w.replace(actor - 1, c.prelude_828('actor', trig) + encode(c.w, recs))
    c.obj_map[i + 1] = actor
    return actor


def add_to_level(p3, w, level_index, actor_refs):
    """append actors (refs) to the level's Actors array (after its properties: an int, then count + refs)"""
    if not actor_refs: return
    ld = bytearray(w.current(level_index))
    _, o = p3.properties(bytes(ld), 4)
    n = struct.unpack_from('>i', ld, o + 4)[0]
    end = o + 8 + 4 * n
    w.replace(level_index, bytes(ld[:o + 4] + struct.pack('>i', n + len(actor_refs)) + ld[o + 8:end]
                                 + b''.join(struct.pack('>i', r) for r in actor_refs) + ld[end:]))


# ---- per-class fix-ups of converted properties ----
def interp_move_lookup(c, props):
    """InterpTrackMove: Gears 3 evaluates a move key through LookupTrack.Points[key] (GroupName, Time: one per
    PosTrack key; Gears 1 has no LookupTrack and Matinee crashed on its empty array, sub_82551778)"""
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    pos = next((r for r in recs if r[0] == 'PosTrack'), None)
    # world-frame keys (MoveFrame IMF_World, the default) are absolute positions: they move with the conversion
    # offset (the opening cutscene's cameras flew at Gears 1's height, 20,000 units under the converted cell)
    mf = next((r for r in recs if r[0] == 'MoveFrame'), None)
    mfname = wname(*struct.unpack('>ii', mf[4])) if mf is not None and len(mf[4]) == 8 else ('IMF_World' if mf is None or mf[4][:1] == b'\x00' else 'relative')
    if pos is not None and mfname == 'IMF_World' and any(c.offset):
        pos[4] = c.shift_points(pos[4], c.offset)
    if any(r[0] == 'LookupTrack' for r in recs): return encode(c.w, recs)
    times = []
    if pos is not None:
        sub, _ = parse828(pos[4], 0, wname)
        pts = next((r for r in sub if r[0] == 'Points'), None)
        if pts is not None:
            n = struct.unpack_from('>i', pts[4], 0)[0]; o = 4
            for _ in range(n):
                el, o = parse828(pts[4], o, wname)
                times.append(next((struct.unpack('>f', r[4])[0] for r in el if r[0] == 'InVal'), 0.0))
    points = b''.join(encode(c.w, [['GroupName', 'NameProperty', 0, b'', struct.pack('>ii', *c.w.name('None'))],
                                   ['Time', 'FloatProperty', 0, b'', struct.pack('>f', t)]]) for t in times)
    track = encode(c.w, [['Points', 'ArrayProperty', 0, b'', struct.pack('>i', len(times)) + points]])
    recs.append(['LookupTrack', 'StructProperty', 0, struct.pack('>ii', *c.w.name('InterpLookupTrack')), track])
    return encode(c.w, recs)


FIXUPS = {'InterpTrackMove': interp_move_lookup}


# Gears 1 assets copied when something copied refers to them (animation sets: Matinee groups, components)
DEEP_COPY = {'AnimSet', 'SoundCue', 'SoundNodeWave'}
# native data after the properties: an AnimSequence's raw animation (empty when cooked: a count of 0) comes before
# the compressed byte stream in Gears 3 (Gears 1 serializes only the byte stream); the keys (ConstantKeyLerp:
# CompressedTrackOffsets of 4 ints per track, translation ACF_None, rotation Float96NoW...) are Gears 3's defaults
ROT_KEY_SIZE = {'ACF_None': 16, 'ACF_Float96NoW': 12, 'ACF_Fixed48NoW': 6, 'ACF_IntervalFixed32NoW': 4,
                'ACF_Fixed32NoW': 4, 'ACF_Float32NoW': 4}


def anim_sequence_tail(c, props, tail):
    """Gears 1 AnimSequence (key encoding ConstantKeyLerp: per track TransOffset, NumTransKeys, RotOffset,
    NumRotKeys into the byte stream; translation keys 12-byte vectors, rotation keys in RotationCompressionFormat)
    -> Gears 3: the same keys re-laid as AKF_VariableKeyLerp, the encoding Gears 3's own Float96NoW sequences use
    (with ConstantKeyLerp, which none of Gears 3's sequences use, the crow's animation came out as spikes).
    VariableKeyLerp per track: translation keys, then for more than one key a frame index per key (u8 below 256
    frames, else u16), padded to 4 bytes with 0x55; the same for the rotation keys (IntervalFixed32: its 24-byte
    range header first). Gears 1's keys are evenly spaced: key k at frame round(k * (frames - 1) / (keys - 1)).
    The native data gains the raw animation count (0) before the byte stream. Returns (props, tail)."""
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    r = {x[0]: x for x in recs}
    fmt = 'ACF_None'
    if 'RotationCompressionFormat' in r:
        v = r['RotationCompressionFormat'][4]
        fmt = wname(*struct.unpack('>ii', v)) if len(v) == 8 else ['ACF_None', 'ACF_Float96NoW', 'ACF_Fixed48NoW',
              'ACF_IntervalFixed32NoW', 'ACF_Fixed32NoW', 'ACF_Float32NoW'][v[0]]
    frames = struct.unpack('>i', r['NumFrames'][4])[0] if 'NumFrames' in r else 1
    bs_len = struct.unpack_from('>i', tail, 0)[0]
    bs = tail[4:4 + bs_len]
    if 'CompressedTrackOffsets' not in r or fmt not in ROT_KEY_SIZE:
        c.unconvertible[f'AnimSequence ({fmt}): keys left as Gears 1 laid them'] = 1
        return props, struct.pack('>i', 0) + tail
    v = r['CompressedTrackOffsets'][4]
    n = struct.unpack_from('>i', v, 0)[0]
    offs = struct.unpack_from(f'>{n}i', v, 4)
    out, new_offs = bytearray(), []
    def table(keys):
        if keys <= 1: return
        for k in range(keys):
            f = round(k * (frames - 1) / (keys - 1))
            out.extend(struct.pack('>B', f) if frames < 256 else struct.pack('>H', f))
        while len(out) % 4: out.append(0x55)
    for t in range(n // 4):
        to, tn, ro, rn = offs[4 * t:4 * t + 4]
        new_offs += [len(out), tn]
        out.extend(bs[to:to + 12 * tn]); table(tn)
        size = ROT_KEY_SIZE[fmt] * rn + (24 if fmt == 'ACF_IntervalFixed32NoW' and rn > 1 else 0)
        if rn == 1: size = 12                           # a single rotation key is always Float96NoW
        new_offs += [len(out), rn]
        out.extend(bs[ro:ro + size]); table(rn)
    recs = [x for x in recs if x[0] not in ('CompressedTrackOffsets', 'KeyEncodingFormat', 'TranslationCompressionFormat')]
    recs.append(['CompressedTrackOffsets', 'ArrayProperty', 0, b'', struct.pack(f'>i{len(new_offs)}i', len(new_offs), *new_offs)])
    enum = lambda e, v: [struct.pack('>ii', *c.w.name(e)), struct.pack('>ii', *c.w.name(v))]
    recs.append(['TranslationCompressionFormat', 'ByteProperty', 0] + enum('AnimationCompressionFormat', 'ACF_None'))
    recs.append(['KeyEncodingFormat', 'ByteProperty', 0] + enum('AnimationKeyFormat', 'AKF_VariableKeyLerp'))
    return encode(c.w, recs), struct.pack('>ii', 0, len(out)) + bytes(out) + tail[4 + bs_len:]


TAIL_FIXUPS = {'AnimSequence': anim_sequence_tail}


# ---- sounds ----
def _distribution_range(c, ref):
    """(min, max) of a Gears 1 RawDistributionFloat's distribution (source export ref): uniform or constant"""
    import materialconv
    if ref <= 0: return None
    cls = c.s.class_name(c.s.exports[ref - 1]['class'])
    try: recs, _ = materialconv.raw_props(c.s, c.s.export_data(ref - 1), 8)
    except Exception: return None
    a = {r[0]: r[4] for r in recs}
    f = lambda k, d: struct.unpack('>f', a[k])[0] if k in a else d
    if cls == 'DistributionFloatUniform': return f('Min', 0.0), f('Max', 0.0)
    if cls == 'DistributionFloatConstant': return f('Constant', 0.0), f('Constant', 0.0)
    return None


def sound_node_ambient(c, props):
    """Gears 1 SoundNodeAmbient(NonLoop): radii and modulation as distributions (RawDistributionFloat structs), one
    Wave (looping node) -> Gears 3: RadiusMin/Max, VolumeMin/Max, PitchMin/Max (DelayMin/Max) floats and SoundSlots"""
    import materialconv
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    src_recs, _ = materialconv.raw_props(c.s, c.s.export_data(c.current_src), 4)
    src = {r[0]: r[4] for r in src_recs}
    def dist(name):
        if name not in src: return None
        sub, _ = materialconv.raw_props(c.s, src[name], 0)
        d = next((r[4] for r in sub if r[0] == 'Distribution'), None)
        return _distribution_range(c, struct.unpack('>i', d)[0]) if d else None
    f = lambda v: struct.pack('>f', v)
    out = [r for r in recs if r[0] not in ('MinRadius', 'MaxRadius', 'VolumeModulation', 'PitchModulation', 'DelayTime', 'Wave')]
    for g1, lo, hi, use in (('MinRadius', 'RadiusMin', None, 0), ('MaxRadius', 'RadiusMax', None, 1),
                            ('VolumeModulation', 'VolumeMin', 'VolumeMax', None), ('PitchModulation', 'PitchMin', 'PitchMax', None),
                            ('DelayTime', 'DelayMin', 'DelayMax', None)):
        rng = dist(g1)
        if rng is None: continue
        if hi is None: out.append([lo, 'FloatProperty', 0, b'', f(rng[0] if use == 0 else rng[1])])
        else: out += [[lo, 'FloatProperty', 0, b'', f(rng[0])], [hi, 'FloatProperty', 0, b'', f(rng[1])]]
    wave = next((r for r in recs if r[0] == 'Wave'), None)
    if wave is not None and not any(r[0] == 'SoundSlots' for r in out):
        slot = encode(c.w, [['Wave', 'ObjectProperty', 0, b'', wave[4]], ['PitchScale', 'FloatProperty', 0, b'', f(1.0)],
                            ['VolumeScale', 'FloatProperty', 0, b'', f(1.0)], ['Weight', 'FloatProperty', 0, b'', f(1.0)]])
        out.append(['SoundSlots', 'ArrayProperty', 0, b'', struct.pack('>i', 1) + slot])
    return encode(c.w, out)


def sound_cue(c, props):
    """Gears 1 SoundCue.SoundGroup -> Gears 3's SoundClass (the same names: Ambient, SFX, Dialog...)"""
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    for r in recs:
        if r[0] == 'SoundGroup': r[0] = 'SoundClass'
    return encode(c.w, recs)


def sound_node_wave_tail(c, props, tail):
    """Gears 1 XMA1 sound data -> Gears 3's four bulk blocks with XMA2 data (tools/xma.py); NumChannels, SampleRate
    and RawPCMDataSize added to the properties"""
    import xma
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    try:
        data, (ch, rate, samples) = xma.to_g3_xbox_data(xma.read_g1_wave(tail))
    except Exception as ex:
        c.unconvertible[f'SoundNodeWave ({ex})'] = c.unconvertible.get(f'SoundNodeWave ({ex})', 0) + 1
        data, ch, rate, samples = b'', 1, 22050, 0
    recs, _ = parse828(props, 0, wname)
    recs = [r for r in recs if r[0] not in ('NumChannels', 'SampleRate', 'RawPCMDataSize')]
    recs += [['NumChannels', 'IntProperty', 0, b'', struct.pack('>i', ch)], ['SampleRate', 'IntProperty', 0, b'', struct.pack('>i', rate)],
             ['RawPCMDataSize', 'IntProperty', 0, b'', struct.pack('>i', samples * ch * 2)]]
    out = bytearray()
    for block in (b'', b'', data, b''):                 # RawData, CompressedPCData, CompressedXbox360Data, PS3
        c.tail_fixups.append((len(out) + 12, len(out) + 16))
        out += struct.pack('>4i', 0, len(block), len(block), 0) + block
    return encode(c.w, recs), bytes(out)


FIXUPS.update({'SoundNodeAmbient': sound_node_ambient, 'SoundNodeAmbientNonLoop': sound_node_ambient, 'SoundCue': sound_cue})
TAIL_FIXUPS['SoundNodeWave'] = sound_node_wave_tail


# ---- music ----
def set_music_track(c, props):
    """Gears 1 SeqAct_SetMusicTrack (assigns SeqVar_MusicTrack tracks {TrackType, TheSoundCue} to the music manager,
    which plays them by combat mood) -> Gears 3 SeqAct_PlayMusicTrack {MusicTrack: TheSoundCue, fades}: plays the
    linked track (its "Ambient" one when it sets several)"""
    import materialconv
    src_recs, _ = materialconv.raw_props(c.s, c.s.export_data(c.current_src), 4)
    a = {r[0]: r[4] for r in src_recs}
    tracks = []
    if 'VariableLinks' in a:
        n = struct.unpack_from('>i', a['VariableLinks'], 0)[0]; o = 4
        for _ in range(n):
            el, o = materialconv.raw_props(c.s, a['VariableLinks'], o); x = {r[0]: r[4] for r in el}
            lv = x.get('LinkedVariables', b'\0\0\0\0'); m = struct.unpack_from('>i', lv, 0)[0]
            for r in struct.unpack_from(f'>{m}i', lv, 4) if len(lv) == 4 + 4 * m else ():
                if r <= 0 or c.s.class_name(c.s.exports[r - 1]['class']) != 'SeqVar_MusicTrack': continue
                vr, _ = materialconv.raw_props(c.s, c.s.export_data(r - 1), 4)
                mt = next((y[4] for y in vr if y[0] == 'MusicTrack'), None)
                if mt is None: continue
                sub, _ = materialconv.raw_props(c.s, mt, 0); s = {y[0]: y[4] for y in sub}
                ttype = c.s.fname(*struct.unpack('>ii', s['TrackType'])) if 'TrackType' in s else ''
                cue = struct.unpack('>i', s['TheSoundCue'])[0] if 'TheSoundCue' in s else 0
                if cue: tracks.append((ttype, cue))
    if not tracks: return props
    ttype, cue = next((t for t in tracks if t[0].lower() == 'ambient'), tracks[0])
    f = lambda v: struct.pack('>f', v)
    music = encode(c.w, [['TheSoundCue', 'ObjectProperty', 0, b'', struct.pack('>i', c.ref(cue))],
                         ['FadeInTime', 'FloatProperty', 0, b'', f(2.0)], ['FadeInVolumeLevel', 'FloatProperty', 0, b'', f(1.0)],
                         ['FadeOutTime', 'FloatProperty', 0, b'', f(2.0)], ['FadeOutVolumeLevel', 'FloatProperty', 0, b'', f(0.0)]])
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    recs = [r for r in recs if r[0] != 'MusicTrack'] + [['MusicTrack', 'StructProperty', 0, struct.pack('>ii', *c.w.name('MusicTrackStruct')), music]]
    return encode(c.w, recs)


FIXUPS['SeqAct_SetMusicTrack'] = set_music_track
CLASS_RENAME['SeqAct_SetMusicTrack'] = 'SeqAct_PlayMusicTrack'


# ---- character variables ----
# Gears 1's SeqVar_Dom (and its kind) is a lookup: every instance means "the Dom pawn". Gears 3's equivalent is
# SeqVar_Character {PawnClass}: the pawn of that class (the Gears 3 class standing for the Gears 1 character in this
# game mode, as the AI factories use). As a plain SeqVar_Object only the instance an AI factory wrote to held Dom: the
# opening's move order went to an empty variable and Dom stood still.
CHARACTER_VARS = {'SeqVar_Dom': 'GearPawn_COGDomBase'}   # a GearGame script class: always loaded, matches subclasses
# (a GearGameContent class there hung the campaign: the main thread waiting in sub_822F7F00's sleep loop)


def character_var(c, props):
    cn = c.s.class_name(c.s.exports[c.current_src]['class'])
    ref = c.class_ref(CHARACTER_VARS[cn])
    if not ref: return props
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    recs = [r for r in recs if r[0] != 'PawnClass'] + [['PawnClass', 'ClassProperty', 0, b'', struct.pack('>i', ref)]]
    return encode(c.w, recs)


if os.environ.get('OBJCOPY_CHARACTER_VARS'):          # (experiment: with SeqVar_Character the campaign hung at load)
    for _v in CHARACTER_VARS:
        CLASS_RENAME[_v] = 'SeqVar_Character'
        FIXUPS[_v] = character_var


# ---- checkpoints ----
# Gears 1's SeqAct_WarCheckPoint ("Set Active Checkpoint") has no outputs: what follows a checkpoint hangs on the
# SeqEvt_CheckpointActivated of the same checkpoint actor (its Originator), which fires when the checkpoint is
# activated, saved as well as loaded. Gears 3's SeqAct_Checkpoint ("Save") fires "Out" after saving, and
# SeqEvent_CheckpointLoaded (the event's Gears 3 class) only on a load. So each event's targets are linked from the
# "Out" of the checkpoint actions that activate its checkpoint as well (else, on a fresh start, the prison's
# initialPlayerStuff never teleported the player to the cell: the player stayed at the stub map's start, the origin).
def checkpoint_events(up, made):
    import materialconv
    c, s, w = up.c, up.c.s, up.w
    def g1_props(i):
        try: return {r[0]: r for r in materialconv.raw_props(s, s.export_data(i), 4)[0]}
        except Exception: return {}
    def var_values(i, link):
        out = set()
        a = g1_props(i)
        if 'VariableLinks' not in a: return out
        n = struct.unpack_from('>i', a['VariableLinks'][4], 0)[0]; o = 4
        for _ in range(n):
            el, o = materialconv.raw_props(s, a['VariableLinks'][4], o)
            e = {x[0]: x for x in el}
            if 'LinkedVariables' not in e: continue
            m = struct.unpack_from('>i', e['LinkedVariables'][4], 0)[0]
            for v in struct.unpack_from(f'>{m}i', e['LinkedVariables'][4], 4):
                if v > 0:
                    va = g1_props(v - 1)
                    if 'ObjValue' in va: out.add(struct.unpack('>i', va['ObjValue'][4])[0])
        return out
    actions = [(i, ref) for i, ref in made.items() if s.class_name(s.exports[i]['class']) == 'SeqAct_WarCheckPoint']
    events = [(i, ref) for i, ref in made.items() if s.class_name(s.exports[i]['class']) == 'SeqEvt_CheckpointActivated']
    if not actions or not events: return 0
    n = 0
    for ei, eref in events:
        ea = g1_props(ei)
        origin = struct.unpack('>i', ea['Originator'][4])[0] if 'Originator' in ea else 0
        mine = [(ai, aref) for ai, aref in actions if origin and origin in var_values(ai, 'Checkpoint')]
        if not mine: mine = [(ai, aref) for ai, aref in actions if s.exports[ai]['outer'] == s.exports[ei]['outer']]
        # the event's converted output links
        erecs, _ = parse828(w.current(eref - 1), 4, up.wname)
        links, k = b'', 0
        for r_ in erecs:
            if r_[0] != 'OutputLinks': continue
            for e in up.elements(r_[4], up.wname):
                lr = next((x for x in e if x[0] == 'Links'), None)
                if lr is not None: links += lr[4][4:]; k += struct.unpack_from('>i', lr[4], 0)[0]
        if not k: continue
        for ai, aref in mine:
            recs, end = parse828(w.current(aref - 1), 4, up.wname)
            for r_ in recs:
                if r_[0] != 'OutputLinks': continue
                els = up.elements(r_[4], up.wname)
                for e in els:
                    if next((str_value(x[4]) for x in e if x[0] == 'LinkDesc'), '').lower() != 'out': continue
                    lr = next((x for x in e if x[0] == 'Links'), None)
                    if lr is None: e.append(['Links', 'ArrayProperty', 0, b'', struct.pack('>i', k) + links])
                    else: lr[4] = struct.pack('>i', struct.unpack_from('>i', lr[4], 0)[0] + k) + lr[4][4:] + links
                    n += 1
                r_[4] = struct.pack('>i', len(els)) + b''.join(up.enc(e) for e in els)
            d = w.current(aref - 1)
            w.replace(aref - 1, d[:4] + up.enc(recs) + d[end:])
    return n


# ---- the squad: Gears 3's player slots ----
# Gears 3's campaign squad is four player slots (GearGameSP_Base.PlayerSlots), each a human or an AI: its Kismet
# reaches a squad member with GearSeqVar_Player {PlayerIdx} (slot N's pawn; Gears 3's levels name them "Marcus",
# "Dom", "P3", "P4" through SeqVar_Named), and spawns an AI squad member with an AI factory whose "Player Slot" is
# the slot (SP_Nightmare_P's Squads: Dom's factory, Player Slot 2, Last Spawned -> "Dom"); a co-op partner takes that
# slot instead. Gears 1's co-op squad is Marcus and Dom: its SeqVar_Dom (a lookup: the Dom pawn, in any package)
# becomes GearSeqVar_Player slot 2, and a factory spawning Dom gets Player Slot 2. Unlike merging every Dom variable
# into the one a factory fills (OBJCOPY_NO_SQUAD_SLOTS=1, the old way), this works across packages: Dom is spawned in
# one sublevel and scripted in others.
SQUAD_VARS = {'SeqVar_Dom': ('Dom', 2)}                 # Gears 1 variable class -> (name, player slot)
SQUAD_PAWNS = {'Pawn_COGDom': 2}                        # a factory spawning this pawn class fills that slot


def squad_var(c, props):
    name, slot = SQUAD_VARS[c.s.class_name(c.s.exports[c.current_src]['class'])]
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    recs = [r for r in recs if r[0] not in ('ObjValue', 'PlayerIdx', 'bAllPlayers', 'VarName')]
    recs += [['bAllPlayers', 'BoolProperty', 0, b'', b'\x00'], ['PlayerIdx', 'IntProperty', 0, b'', struct.pack('>i', slot)],
             ['VarName', 'NameProperty', 0, b'', struct.pack('>ii', *c.w.name(name))]]
    return encode(c.w, recs)


SQUAD_SLOTS = not os.environ.get('OBJCOPY_NO_SQUAD_SLOTS') and not os.environ.get('OBJCOPY_NO_RENAME')
if SQUAD_SLOTS:
    for _v in SQUAD_VARS:
        CLASS_RENAME[_v] = 'GearSeqVar_Player'
        FIXUPS[_v] = squad_var


def squad_factories(up, made):
    """every copied AI factory spawning a squad member (SQUAD_PAWNS) gets that player slot: a new SeqVar_Int on its
    "Player Slot" variable link; returns the count"""
    if not SQUAD_SLOTS: return 0
    c, s, w = up.c, up.c.s, up.w
    n = 0
    for i, ref in made.items():
        if s.class_name(s.exports[i]['class']) != 'SeqAct_AIFactory': continue
        d = s.export_data(i)
        slot = None
        for k in range(0, len(d) - 3):
            v = struct.unpack_from('>i', d, k)[0]
            if -len(s.imports) <= v < 0 and s.imports[-v - 1]['class'] == 'Class' and s.imports[-v - 1]['name'] in SQUAD_PAWNS:
                slot = SQUAD_PAWNS[s.imports[-v - 1]['name']]; break
        if slot is None: continue
        seq = struct.unpack_from('>i', w.exports[ref - 1], 8)[0]
        var_cls = c.class_ref('SeqVar_Int')
        if seq <= 0 or not var_cls: continue
        parent = ['ParentSequence', 'ObjectProperty', 0, b'', struct.pack('>i', seq)]
        var = w.add_export(var_cls, seq, c.unique_name('G1SquadSlot', 0), b'', flags=0x0007000100000000)
        w.replace(var - 1, struct.pack('>i', -1) + up.enc([['IntValue', 'IntProperty', 0, b'', struct.pack('>i', slot)], parent]))
        recs, end = parse828(w.current(ref - 1), 4, up.wname)
        linked = False
        for r in recs:
            if r[0] != 'VariableLinks': continue
            els = up.elements(r[4], up.wname)
            for e in els:
                if next((str_value(x[4]) for x in e if x[0] == 'LinkDesc'), '').lower() != 'player slot': continue
                lv = next((x for x in e if x[0] == 'LinkedVariables'), None)
                if lv is None: e.append(['LinkedVariables', 'ArrayProperty', 0, b'', struct.pack('>ii', 1, var)])
                else: lv[4] = struct.pack('>ii', 1, var)
                linked = True
            r[4] = struct.pack('>i', len(els)) + b''.join(up.enc(e) for e in els)
        if not linked: continue
        dd = w.current(ref - 1)
        w.replace(ref - 1, dd[:4] + up.enc(recs) + dd[end:])
        sd = w.current(seq - 1)
        srecs, send = parse828(sd, 4, up.wname)
        for r in srecs:
            if r[0] == 'SequenceObjects':
                r[4] = struct.pack('>i', struct.unpack_from('>i', r[4], 0)[0] + 1) + r[4][4:] + struct.pack('>i', var)
        w.replace(seq - 1, sd[:4] + up.enc(srecs) + sd[send:])
        n += 1
    return n


# ---- collision changes ----
def change_collision(c, props):
    """Gears 1 SeqAct_ChangeCollision {bCollideActors, bBlockActors} -> Gears 3's {CollisionType}: Gears 3's action
    applies CollisionType only (default COLLIDE_CustomDefault: the actor's own defaults), so the opening's cell door
    did not get the collision Gears 1's script set on it"""
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    a = {r[0]: r[4] for r in recs}
    collide, block = a.get('bCollideActors', b'\0') != b'\0', a.get('bBlockActors', b'\0') != b'\0'
    t = 'COLLIDE_BlockAll' if collide and block else 'COLLIDE_TouchAll' if collide else 'COLLIDE_NoCollision'
    recs = [r for r in recs if r[0] != 'CollisionType'] + [
        ['CollisionType', 'ByteProperty', 0, struct.pack('>ii', *c.w.name('ECollisionType')), struct.pack('>ii', *c.w.name(t))]]
    return encode(c.w, recs)


FIXUPS['SeqAct_ChangeCollision'] = change_collision


def collision_enabled_by_script(src):
    """source actors (export indices) a SeqAct_ChangeCollision switches collision on for: Gears 1 kept them without
    collision until then (the opening's cell door swings through the cell while the hidden player stands there; with
    collision in Gears 3 it pushed the player out of the cell, through the wall)"""
    import materialconv
    c = Copier.__new__(Copier); c.s = src
    out = set()
    for i, e in enumerate(src.exports):
        if src.class_name(e['class']) != 'SeqAct_ChangeCollision': continue
        try: recs, _ = materialconv.raw_props(src, src.export_data(i), 4)
        except Exception: continue
        if not any(r[0] == 'bCollideActors' and r[4] != b'\0' * len(r[4]) for r in recs): continue
        for v in c.linked_vars(i):
            if src.class_name(src.exports[v]['class']) != 'SeqVar_Object': continue
            vr, _ = materialconv.raw_props(src, src.export_data(v), 4)
            ov = next((struct.unpack('>i', y[4])[0] for y in vr if y[0] == 'ObjValue'), 0)
            if ov > 0: out.add(ov - 1)
    return out


def collision_off(c, ref):
    """a copied actor (destination ref) without collision until a script turns it on"""
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    d = c.w.current(ref - 1)
    recs, end = parse828(d, 26, wname)
    recs = [r for r in recs if r[0] not in ('bCollideActors', 'bBlockActors')] + [
        ['bCollideActors', 'BoolProperty', 0, b'', b'\x00'], ['bBlockActors', 'BoolProperty', 0, b'', b'\x00']]
    c.w.replace(ref - 1, d[:26] + encode(c.w, recs) + d[end:])


# ---- lights ----
def lights(src):
    """the level's light actors (LIGHT_CLASSES): copied for the characters' lighting"""
    return [i for i, e in enumerate(src.exports) if src.class_name(e['class']) in LIGHT_CLASSES
            and src.path(i).count('.') == 2 and src.path(i).startswith('TheWorld.PersistentLevel')]


def light_component(c, props):
    """a copied light lights dynamic objects only (characters, movers, cutscene actors): the converted geometry
    already has this light baked in (LightingChannels: Dynamic, CompositeDynamic; not BSP / Static), and casts no
    shadows (hundreds of Gears 1 lights in a level)"""
    wname = lambda i, n: (c.w.names[i] if n == 0 else f'{c.w.names[i]}_{n - 1}')
    recs, _ = parse828(props, 0, wname)
    drop = {'LightingChannels', 'CastShadows', 'CastDynamicShadows', 'CastStaticShadows', 'bHasLightEverBeenBuiltIntoLightMap'}
    ch = encode(c.w, [[k, 'BoolProperty', 0, b'', b'\x01'] for k in ('bInitialized', 'Dynamic', 'CompositeDynamic')])
    if os.environ.get('OBJCOPY_LIGHTS_MINIMAL'):       # (debugging the load crash: only the light's look)
        recs = [r for r in recs if r[0] in ('Radius', 'Brightness', 'LightColor', 'FalloffExponent', 'InnerConeAngle', 'OuterConeAngle')]
    recs = [r for r in recs if r[0] not in drop] + [
        ['LightingChannels', 'StructProperty', 0, struct.pack('>ii', *c.w.name('LightingChannelContainer')), ch],
        ['CastShadows', 'BoolProperty', 0, b'', b'\x00'], ['CastDynamicShadows', 'BoolProperty', 0, b'', b'\x00']]
    return encode(c.w, recs)


if not os.environ.get('OBJCOPY_LIGHTS_RAW'):                  # (debugging: the components as Gears 1 had them)
    for _lc in ('PointLightComponent', 'SpotLightComponent'):
        FIXUPS[_lc] = light_component
