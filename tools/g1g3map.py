"""Gears of War 1 -> 3 articulation map (offline): what a converted chapter's Gears 1 data holds, set against what
Gears 3's classes can take, so a conversion is planned from the data instead of found in game.

For every object in the chapter's Gears 1 packages (a recipe's sections, or packages given): its class, how many there
are, the Gears 3 class the converter turns it into (objcopy.CLASS_RENAME / PAWN_MAP / CONTROLLER_MAP, else the same
name), whether Gears 3 has that class, and each property the Gears 1 objects set (by count) that the Gears 3 class
(with its parents) does not declare: data a straight copy drops. Classes are read from each game's script packages
(Core, Engine, GameFramework, WarfareGame / GearGame): a property export whose outer is a class is declared there.

usage: python -P g1g3map.py <recipe | package.xxx ...> [--g1 <WarGame\\CookedXenon>] [--g3 <GearGame\\CookedXbox360>]
                            [--out report.md] [--min N]
The report (markdown): classes Gears 3 lacks, then per mapped class the properties it lacks, most-used first."""
import argparse, collections, configparser, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import re
import upk, objcopy

# editor-only data (cooked Gears 3 script does not declare it, and the game never reads it): left out of the report
EDITOR_ONLY = re.compile(r'^(bDirty|ObjPos[XY]|ObjComment|ObjColor|DrawWidth|DrawHeight|.*EditorColor|Editor.*|SourceFile.*|'
                         r'PreviewMesh|3D_Icons|NavOctreeSize|bIsOwnerAStaticMeshActor|Thumbnail.*|Floor(Mesh|Position|'
                         r'Rotation|Scale3D)|ObjName)$')
PROP_CLASSES = {'IntProperty', 'FloatProperty', 'BoolProperty', 'ByteProperty', 'StrProperty', 'NameProperty',
                'ObjectProperty', 'ClassProperty', 'ComponentProperty', 'StructProperty', 'ArrayProperty',
                'MapProperty', 'DelegateProperty', 'InterfaceProperty'}


class Classes:
    """a game's classes from its script packages: {class: (super, {declared properties})}"""
    def __init__(self, packages):
        self.supers, self.props = {}, collections.defaultdict(set)
        for p in packages:
            for i, e in enumerate(p.exports):
                cn = p.class_name(e['class'])
                if cn == 'Class':
                    sup = struct_super(p, i)
                    self.supers.setdefault(e['name'], sup)
                elif cn in PROP_CLASSES and e['outer'] > 0:
                    owner = p.exports[e['outer'] - 1]
                    if p.class_name(owner['class']) in ('Class', 'ScriptStruct'):
                        self.props[owner['name']].add(e['name'])

    def has(self, cls):
        return cls in self.supers or cls in self.native

    native = set()   # classes a stock level imports (native ones with no script export: Model, StaticMesh, Level...)

    def all_props(self, cls):
        out, c, guard = set(), cls, 0
        while c and guard < 64:
            out |= self.props.get(c, set())
            c, guard = self.supers.get(c), guard + 1
        return out

    def chain(self, cls):
        out, c = [], cls
        while c and len(out) < 64:
            out.append(c)
            c = self.supers.get(c)
        return out


def struct_super(p, i):
    """a Class export's SuperStruct (its data's UField/UStruct header: the first object reference that is a class)"""
    import struct as st
    if p.exports[i]['name'] == 'Object': return None
    d = p.export_data(i)
    for off in (8, 12, 16, 4, 20):
        if off + 4 > len(d): continue
        v = st.unpack_from('>i', d, off)[0]
        if v > 0 and v <= len(p.exports) and p.class_name(p.exports[v - 1]['class']) == 'Class':
            return p.exports[v - 1]['name']
        if v < 0 and -v <= len(p.imports) and p.imports[-v - 1].get('class_name', p.imports[-v - 1].get('class')) == 'Class':
            return p.imports[-v - 1]['name']
    return None


def props_of(p, i):
    d = p.export_data(i)
    for o in (4, 8, 0, 12, 16, 20, 24, 28, 32, 36, 40):
        try:
            pr, _ = p.properties(d, o)
            if pr and all(n in p.names for n, *_ in pr): return [n for n, *_ in pr]
        except Exception:
            pass
    return []


def target_of(cn):
    if cn in objcopy.PAWN_MAP:
        t = objcopy.PAWN_MAP[cn]
        return t[0] if isinstance(t, list) else t
    if cn in objcopy.CONTROLLER_MAP: return objcopy.CONTROLLER_MAP[cn]
    return objcopy.CLASS_RENAME.get(cn, cn)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('inputs', nargs='+')
    ap.add_argument('--g1', default=os.path.join(os.path.dirname(__file__), '..', '..', 'gow1-decomp', 'disc', 'WarGame', 'CookedXenon'))
    ap.add_argument('--g3', default=os.path.join(os.path.dirname(__file__), '..', '..', 'gow3-decomp', 'disc', 'GearGame', 'CookedXbox360'))
    ap.add_argument('--out', default=None)
    ap.add_argument('--min', type=int, default=1, help='properties set fewer times than this are left out')
    a = ap.parse_args()
    files = []
    for x in a.inputs:
        if x.endswith('.ini'):
            c = configparser.ConfigParser(inline_comment_prefixes=(';',)); c.read(x)
            ch = c['chapter']
            names = [ch['persistent']] if ch.get('persistent') else []
            names += [s.strip() for s in ch.get('sections', '').replace('\n', ' ').split(',') if s.strip()]
            files += [os.path.join(a.g1, n + '.xxx') for n in names]
        else:
            files.append(x)
    files = [f for f in files if os.path.isfile(f)]
    load = lambda d, names: [upk.Package(os.path.join(d, n + '.xxx')) for n in names if os.path.isfile(os.path.join(d, n + '.xxx'))]
    g1 = Classes(load(a.g1, ('Core', 'Engine', 'GameFramework', 'WarfareGame')))
    g3 = Classes(load(a.g3, ('Core', 'Engine', 'GameFramework', 'GearGame')))
    g3.native = set()
    for stock in load(a.g3, ('SP_Nightmare_P', 'SP_Nightmare_01_S', 'SP_Prison_P', 'MP_GridlockG3')):
        g3.native |= {im['name'] for im in stock.imports if im.get('class_name', im.get('class')) == 'Class'}
    # content classes (GearGameContent: pawns, weapons, effects) live in content packages, not the script ones
    for f in os.listdir(a.g3):
        u = f.upper()
        if u.endswith('.XXX') and '_LOC_' not in u and (u.startswith('MPCH_') or u.startswith('GEARGAME') or u.endswith('_P.XXX')):
            try:
                pk = upk.Package(os.path.join(a.g3, f), tables_only=True)
            except Exception:
                continue
            g3.native |= {e['name'] for e in pk.exports if pk.class_name(e['class']) == 'Class'}
    count = collections.Counter()
    used = collections.defaultdict(collections.Counter)    # class -> property -> objects setting it
    for f in files:
        p = upk.Package(f)
        for i, e in enumerate(p.exports):
            cn = p.class_name(e['class'])
            if cn in ('Class', 'Package') or cn in PROP_CLASSES: continue
            count[cn] += 1
            for n in set(props_of(p, i)): used[cn][n] += 1
    lines = [f'# Gears 1 -> Gears 3 articulation map', '',
             f'Sources: {", ".join(os.path.basename(f) for f in files)} ({sum(count.values())} objects, {len(count)} classes).', '']
    missing = [(n, cn) for cn, n in count.items() if not g3.has(target_of(cn))]
    lines += ['## Gears 1 classes with no Gears 3 class (objects of them are dropped or need a mapping)', '',
              '| Gears 1 class | objects | Gears 1 parents |', '|---|---|---|']
    for n, cn in sorted(missing, reverse=True):
        lines.append(f'| {cn} | {n} | {" < ".join(g1.chain(cn)[1:4])} |')
    # the converter's source: a property named there is (probably) translated, or dropped on purpose
    here = os.path.dirname(os.path.abspath(__file__))
    source = ''.join(open(os.path.join(here, f), encoding='utf-8').read() for f in
                     ('objcopy.py', 'levelinject.py', 'materialconv.py', 'trilogy.py', 'levelpkg.py', 'lighting.py')
                     if os.path.isfile(os.path.join(here, f)))
    seen = lambda k: re.search(r"['\"]" + re.escape(k) + r"['\"]", source) is not None
    lines += ['', '## Properties Gears 1 sets that the Gears 3 class lacks', '',
              "Editor-only data is left out. A property marked * is named in the converter's source (translated, or",
              'dropped on purpose); the others reach Gears 3 nowhere: each is a candidate for a conversion rule.', '',
              '| Gears 1 class -> Gears 3 | objects | properties Gears 3 does not declare (objects setting them) |', '|---|---|---|']
    for cn, n in count.most_common():
        t = target_of(cn)
        if not g3.has(t): continue
        have = g3.all_props(t)
        lacking = [(k, v) for k, v in used[cn].most_common() if k not in have and v >= a.min and not EDITOR_ONLY.match(k)]
        if lacking:
            lines.append(f'| {cn} -> {t} | {n} | {", ".join(k + ("*" if seen(k) else "") + f" ({v})" for k, v in lacking[:12])}'
                         f'{" ..." if len(lacking) > 12 else ""} |')
    text = '\n'.join(lines) + '\n'
    if a.out: open(a.out, 'w', encoding='utf-8').write(text)
    print(text if not a.out else f'{a.out}: {len(missing)} classes Gears 3 lacks, {len(count)} classes in all')


if __name__ == '__main__':
    main()
