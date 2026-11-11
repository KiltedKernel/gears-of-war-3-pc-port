"""A campaign pawn class with a multiplayer character's looks: Gears 3 content package cloned with its pawn class
re-parented onto a campaign class (as Gears 3's own flashback Dom, GearGameContent.GearPawn_COGDomG2, is the campaign
Dom with the classic mesh; Gears 3 has no such Marcus).

Why (docs/ENGINEERING_LOG.md 2026-11-11): the multiplayer classic characters (GearPawn_COGMarcusG2MP,
GearPawn_COGDomG2MP) run through GearPawn_HumanBaseMPBase / GearPawn_HumanBaseMP, and in the campaign their arms
misbehave - Dom's pumped when he re-equipped, Marcus's twitched in the radio stance - where the campaign classes'
do not, on the same 93-bone classic skeleton. A class whose super is the campaign class keeps the campaign behaviour.

The clone (its own name and GUID) keeps every object of the source package; the class export is renamed, its
SuperStruct (export entry and class data +8) becomes the campaign class, and its default object and the default's
components take the campaign class's default and components as archetypes (same component names). Properties the
campaign class must not take from its own defaults are cleared on the new default (clear: e.g. the campaign
Marcus's Gears 3 hair materials); properties dropped from it are the multiplayer classes' own (not declared on the
campaign chain) or ones the campaign class should give (its chatter banks). The chapter loads the clone instead of
the source (mod.ini content).

usage: python -P campaignclass.py <source package> <out package> <class> <new class> <Package.SuperClass>
                                  [clear,...] [drop,...]"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, upkwrite, levelpkg, objcopy

# for a multiplayer human character (GearPawn_HumanBaseMP*) on a campaign COG class: the campaign class's Gears 3
# hair materials off (the multiplayer class has none: its mesh keeps its own), and the multiplayer chain's own
# properties (not declared on the campaign chain) and chatter banks (the campaign class's instead) dropped
MP_HUMAN_CLEAR = ('MaskedHairMaterial', 'TranslucentHairMaterial')
MP_HUMAN_DROP = ('bForceStretchToMarcusSkeleton', 'RimShaderMaterialSpecific', 'MasterGUDBankClassNames', 'FAS_ChatterNames')


def make(src, out, cls_name, new_name, super_path, clear=(), drop=()):
    p = upk.Package(src)
    upk.E = '>'
    w = upkwrite.Rewriter(p)
    w.guid = levelpkg.package_guid(os.path.splitext(os.path.basename(out))[0])
    ci = next(i for i, e in enumerate(p.exports) if e['name'] == cls_name and p.class_name(e['class']) == 'Class')
    di = next(i for i, e in enumerate(p.exports) if e['name'] == 'Default__' + cls_name and e['outer'] == p.exports[ci]['outer'])
    comps = [i for i, e in enumerate(p.exports) if e['outer'] == di + 1]
    base_pkg, base_cls = super_path.split('.')
    pk = w.add_import('Core', 'Package', 0, base_pkg)
    sup = w.add_import('Core', 'Class', pk, base_cls)
    base_def = w.add_import(base_pkg, base_cls, pk, 'Default__' + base_cls)

    def set_entry(i, field, value):                      # export entry ints: class, super, outer, name, number, archetype
        struct.pack_into('>i', w.exports[i], 4 * field, value)

    def rename(i, name):
        n = w.name(name)
        set_entry(i, 3, n[0]); set_entry(i, 4, 0)

    # the class: renamed, its super the campaign class (entry and UStruct::SuperStruct at +8 of its data)
    rename(ci, new_name)
    set_entry(ci, 1, sup)
    d = bytearray(w.current(ci))
    struct.pack_into('>i', d, 8, sup)
    w.replace(ci, d)
    # the default object: renamed, the campaign default its archetype; cleared properties as None / 0
    rename(di, 'Default__' + new_name)
    set_entry(di, 5, base_def)
    if clear or drop:
        d = w.current(di)
        recs, end = objcopy.parse828(d, 4, p.fname)
        recs = [r for r in recs if r[0] not in clear and r[0] not in drop]
        for name in clear:
            recs.append([name, 'ObjectProperty', 0, b'', struct.pack('>i', 0)])
        w.replace(di, d[:4] + objcopy.encode(w, recs) + d[end:])
    # its components: the campaign default's same-named components as archetypes
    for i in comps:
        e = p.exports[i]
        cref = e['class']
        if cref < 0:
            imp = p.imports[-cref - 1]
            cpkg = p.imports[-imp['outer'] - 1]['name'] if imp['outer'] < 0 else 'Engine'
            cname = imp['name']
        else:
            cpkg, cname = 'GearGame', p.exports[cref - 1]['name']
        set_entry(i, 5, w.add_import(cpkg, cname, base_def, e['name']))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    open(out, 'wb').write(w.build())
    return [p.exports[i]['name'] for i in comps]


if __name__ == '__main__':
    a = sys.argv[1:]
    lst = lambda k: tuple(x for x in (a[k].split(',') if len(a) > k else []) if x)
    names = make(a[0], a[1], a[2], a[3], a[4], lst(5), lst(6))
    print(f'{os.path.basename(a[1])}: {a[3]} (super {a[4]}), components {", ".join(names)}')
