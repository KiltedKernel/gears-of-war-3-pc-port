"""Cover (CoverLink slots) in Gears of War 1 (374) and 3 (828), and Gears 1 cover written as Gears 3 slots (offline).

Gears 1: CoverLink.Slots is an array of tagged structs (CoverType, LocationOffset, RotationOffset, bLeanLeft...).
Gears 3: the CoverSlot struct is serialized natively ("immutable when cooked": every field in declaration order, no
tags; the cooked exports list the fields in reverse). Read from MP_GridlockG3 (checked by parsing every slot array to
its last byte):
  SlotValidAfterTime (f), ForceCoverType, CoverType, LocationDescription (FNames: CT_*, CoverDesc_*),
  LocationOffset (3f), RotationOffset (3i), Actions (count + FNames CA_*),
  FireLinks (count + {Interactions (count + bytes), PackedProperties (i), bFallbackLink, bDynamicIndexInited (bytes)}),
  ExposedCoverPackedProperties (count + i), TurnTargetPackedProperties (i),
  SlipRefs (count + {Poly (24 bytes), Dest (BasedPosition: tagged properties), Direction (i)}),
  OverlapClaimsList (count + {Link ref, SlotIdx}), then 21 one-byte flags in declaration order (FLAGS).
usage: python -P cover.py <gears3 package> check | reencode <mods dir> <gears3 cooked dir>"""
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk

FLAGS = ['bLeanLeft', 'bLeanRight', 'bForceCanPopUp', 'bCanPopUp', 'bCanMantle', 'bCanClimbUp',
         'bForceCanCoverSlip_Left', 'bForceCanCoverSlip_Right', 'bCanCoverSlip_Left', 'bCanCoverSlip_Right',
         'bCanSwatTurn_Left', 'bCanSwatTurn_Right', 'bEnabled', 'bAllowPopup', 'bAllowMantle', 'bAllowCoverSlip',
         'bAllowClimbUp', 'bAllowSwatTurn', 'bForceNoGroundAdjust', 'bPlayerOnly', 'bPreferLeanOverPopup']


class C:
    def __init__(self, d, o=0): self.d, self.o = d, o
    def i(self): v = struct.unpack_from('>i', self.d, self.o)[0]; self.o += 4; return v
    def f(self, n=1): v = struct.unpack_from(f'>{n}f', self.d, self.o); self.o += 4 * n; return v if n > 1 else v[0]
    def raw(self, n): v = self.d[self.o:self.o + n]; self.o += n; return v


def read_slots_828(p, raw):
    """the Gears 3 slots of a CoverLink's Slots array value"""
    c = C(raw)
    slots = []
    for _ in range(c.i()):
        s = {'valid_after': c.f()}
        for k in ('force_type', 'type', 'description'):
            s[k] = p.fname(c.i(), c.i())
        s['location'] = c.f(3); s['rotation'] = (c.i(), c.i(), c.i())
        s['actions'] = [p.fname(c.i(), c.i()) for _ in range(c.i())]
        fl = []
        for _ in range(c.i()):
            inter = c.raw(c.i()); packed = c.i(); flags = c.raw(2)
            fl.append((inter, packed, flags))
        s['fire_links'] = fl
        s['exposed'] = [c.i() for _ in range(c.i())]
        s['turn_target'] = c.i()
        slips = []
        for _ in range(c.i()):
            poly = c.raw(24)
            props, c.o = p.properties(c.d, c.o)
            slips.append((poly, props, c.i()))
        s['slip_refs'] = slips
        s['overlap'] = [(c.i(), c.i()) for _ in range(c.i())]
        s['flags'] = dict(zip(FLAGS, c.raw(len(FLAGS))))
        slots.append(s)
    return slots, c.o


def write_slot_828(w, s):
    """one Gears 3 slot from {'type', 'force_type', 'location', 'rotation', 'actions', flags...}; empty AI data (fire
    links, exposure, slips, overlaps: the AI then evaluates cover without precomputed links)"""
    def fn(name): return struct.pack('>ii', *w.name(name))
    out = struct.pack('>f', 0.0)
    out += fn(s.get('force_type', 'CT_None')) + fn(s['type']) + fn('CoverDesc_None')
    out += struct.pack('>3f', *s['location']) + struct.pack('>3i', *s['rotation'])
    out += struct.pack('>i', len(s['actions'])) + b''.join(fn(a) for a in s['actions'])
    out += struct.pack('>i', 0) + struct.pack('>i', 0) + struct.pack('>i', -1) + struct.pack('>i', 0) + struct.pack('>i', 0)
    out += bytes(1 if s['flags'].get(k) else 0 for k in FLAGS)
    return out


def read_g1_links(p):
    """[(export index, location, rotation, [slot dicts])] for a Gears 1 level's CoverLinks"""
    import levelinject, materialconv
    COVER_TYPES = {0: 'CT_None', 1: 'CT_Standing', 2: 'CT_MidLevel'}
    out = []
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'CoverLink' or not p.path(i).startswith('TheWorld'): continue
        o, pr, end = levelinject.props_at(p, p.export_data(i), (32, 4, 8))
        a = {n: v for n, t, k, v in pr}
        loc = struct.unpack('>3f', a['Location'][1]) if 'Location' in a else (0, 0, 0)
        rot = struct.unpack('>3i', a['Rotation'][1]) if 'Rotation' in a else (0, 0, 0)
        slots = []
        raw = a.get('Slots', b'\0\0\0\0')
        n = struct.unpack_from('>i', raw, 0)[0]; q = 4
        for _ in range(n):
            recs, q = materialconv.raw_props(p, raw, q)
            r = {x[0]: x for x in recs}
            def b(k): return k in r and r[k][4] != b'\0' * len(r[k][4])
            def byte(k, default):
                if k not in r: return default
                v = r[k][4]
                return COVER_TYPES.get(v[0], default) if len(v) == 1 else p.fname(*struct.unpack('>ii', v))
            s = {'type': byte('CoverType', 'CT_Standing'), 'force_type': byte('ForceCoverType', 'CT_None'),
                 'location': struct.unpack('>3f', r['LocationOffset'][4]) if 'LocationOffset' in r else (0.0, 0.0, 0.0),
                 'rotation': struct.unpack('>3i', r['RotationOffset'][4]) if 'RotationOffset' in r else (0, 0, 0)}
            flags = {k: b(k) for k in FLAGS}
            # Gears 1 defaults (properties at their default are not stored): enabled, and every "allow" on
            for k in ('bEnabled', 'bAllowPopup', 'bAllowMantle', 'bAllowCoverSlip', 'bAllowClimbUp', 'bAllowSwatTurn'):
                if k not in r: flags[k] = True
            s['flags'] = flags
            s['actions'] = ([ 'CA_LeanLeft'] if flags['bLeanLeft'] else []) + (['CA_LeanRight'] if flags['bLeanRight'] else []) \
                           + (['CA_PopUp'] if flags['bCanPopUp'] else [])
            slots.append(s)
        out.append((i, np.array(loc), rot, slots))
    return out


def slot_positions(loc, rot, slots):
    """world positions of a link's slots (LocationOffset in the link's rotation frame)"""
    import levelinject
    if not slots: return np.zeros((0, 3))
    R = levelinject.rotation_matrix(*rot)
    return np.array([np.asarray(loc) + np.array(s['location']) @ R for s in slots])


def read_g3_links(p):
    """[(export index, location, rotation, [slot dicts])] for a Gears 3 level's CoverLinks"""
    import levelinject
    out = []
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'CoverLink' or not p.path(i).startswith('TheWorld'): continue
        o, pr, end = levelinject.props_at(p, p.export_data(i), (26,))
        a = {n: v for n, t, k, v in pr}
        loc = struct.unpack('>3f', a['Location'][1]) if 'Location' in a else (0.0, 0.0, 0.0)
        rot = struct.unpack('>3i', a['Rotation'][1]) if 'Rotation' in a else (0, 0, 0)
        out.append((i, np.array(loc), rot, read_slots_828(p, a['Slots'])[0] if 'Slots' in a else []))
    return out


def level_cover_tables(p, d):
    """Gears 3 Level native data: (CoverLinkRefs as export indices, offset of the CoverIndexPairs records,
    [(link ref index, slot index)]). Found as the array of every CoverLink (count + refs) right after the
    CoverListStart/End and PylonListStart/End refs; CoverIndexPairs follow: count + 5-byte {int, byte} records
    (MP_GridlockG3: 225 links, 1,010 pairs, ending where the next array starts)."""
    links = sorted(i + 1 for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'CoverLink' and p.path(i).startswith('TheWorld'))
    n = len(links); key = struct.pack('>i', n); want = set(links)
    o = d.find(key)
    while o >= 0:
        refs = struct.unpack_from(f'>{n}i', d, o + 4) if o + 4 + 4 * n <= len(d) else ()
        if set(refs) == want: break
        o = d.find(key, o + 1)
    if o < 0: raise ValueError('CoverLinkRefs not found in the level')
    q = o + 4 + 4 * n
    count = struct.unpack_from('>i', d, q)[0]
    pairs = [struct.unpack_from('>iB', d, q + 4 + 5 * k) for k in range(count)]
    return [r - 1 for r in refs], q + 4, pairs


PYLONS = ('Pylon', 'DynamicPylon')                    # the pylon list holds both (a lift's pylon is dynamic)


def level_lists(p, d, w=None):
    """offsets in a Gears 3 Level's native data of the list ends (NavListEnd, CoverListEnd, PylonListEnd: Level refs)
    and the CoverLinkRefs table: {'nav_end', 'cover_end', 'pylon_end', 'refs_count' (offset or None), 'refs',
    'pairs_at', 'pairs'}. Normally found through CoverLinkRefs (the array of every CoverLink, level_cover_tables); a
    streaming sublevel can have that table empty (SP_Nightmare_02_S: 51 links, none listed), then the list ends are
    found by their contents: CoverListStart/End two CoverLink refs, PylonListStart/End two Pylon refs."""
    try:
        refs, pairs_at, pairs = level_cover_tables(p, d)
        q = pairs_at - 4 - 4 - 4 * len(refs)
        return {'nav_end': q - 24, 'cover_end': q - 16, 'pylon_end': q - 8, 'refs_count': q, 'refs': refs,
                'pairs_at': pairs_at, 'pairs': pairs}
    except ValueError:
        pass
    def kind(r):                                       # (w: exports the rewrite added, e.g. new cover links)
        if 0 < r <= len(p.exports): return p.class_name(p.exports[r - 1]['class'])
        if w is not None and 0 < r <= len(w.exports):
            c = struct.unpack_from('>i', w.exports[r - 1], 0)[0]
            if c < 0 and -c <= len(w.imports):          # (a class import the rewrite may have added: its object name)
                return w.names[struct.unpack('>7i', w.imports[-c - 1])[5]]
            return p.class_name(c) if c > 0 else ''
        return ''
    for o in range(len(d) - 8, 16, -1):               # (every byte offset: the native data before need not align)
        cs, ce, ps, pe = struct.unpack_from('>4i', d, o - 8)
        if kind(cs) == kind(ce) == 'CoverLink' and kind(ps) in PYLONS and kind(pe) in PYLONS:
            return {'nav_end': o - 12, 'cover_end': o - 4, 'pylon_end': o + 4, 'refs_count': None, 'refs': [],
                    'pairs_at': None, 'pairs': []}
    raise ValueError('the level\'s navigation / cover / pylon list ends were not found')


def add_links(p, w, count, template=None):
    """count new CoverLink actors (clones of template, a CoverLink export index; its CylinderComponent cloned too) in
    a Gears 3 level: appended to Level.Actors, to the end of the level's cover list (NextCoverLink) and navigation
    point list (nextNavigationPoint; their ends are Level refs stored just before CoverLinkRefs: NavListStart/End,
    CoverListStart/End, PylonListStart/End, an empty array), and to CoverLinkRefs. NetIndex (the last int of an
    actor's 26-byte prelude, +4 in a component's) -1. Returns the new links' export indices (0-based); their slots
    are written by the caller (CoverIndexPairs get none: polygons reference cover directly)."""
    import levelinject
    links = [i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'CoverLink' and p.path(i).startswith('TheWorld')]
    template = links[0] if template is None else template
    lvl = next(i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'Level' and p.path(i) == 'TheWorld.PersistentLevel')
    comp = next(j for j in range(len(p.exports)) if p.exports[j]['outer'] == template + 1)
    te, ce = p.exports[template], p.exports[comp]
    tflags = struct.unpack_from('>Q', p.data, te['entry'] + 24)[0]; cflags = struct.unpack_from('>Q', p.data, ce['entry'] + 24)[0]
    tarch = struct.unpack_from('>i', p.data, te['entry'] + 20)[0]; carch = struct.unpack_from('>i', p.data, ce['entry'] + 20)[0]
    used = {e['name'] for e in p.exports}
    new = []
    for k in range(count):
        n = 5000 + k
        while f'CoverLink_{n}' in used: n += 1
        used.add(f'CoverLink_{n}')
        ref = w.add_export(te['class'], te['outer'], ('CoverLink', n + 1), b'', flags=tflags, archetype=tarch)
        cd = bytearray(p.export_data(comp)); cd[4:8] = struct.pack('>i', -1)
        cref = w.add_export(ce['class'], ref, (ce['name'].rsplit('_', 1)[0], int(ce['name'].rsplit('_', 1)[1]) + 1) if '_' in ce['name'] else ce['name'], bytes(cd), flags=cflags, archetype=carch)
        d = bytearray(p.export_data(template)); d[22:26] = struct.pack('>i', -1)
        o, pr, end = levelinject.props_at(p, bytes(d), (26,))
        for name in ('CylinderComponent', 'CollisionComponent'):
            v, size = levelinject.tag_value_offset(p, bytes(d), o, name)
            if v is not None: struct.pack_into('>i', d, v, cref)
        for name in ('NextCoverLink', 'nextNavigationPoint'):
            v, size = levelinject.tag_value_offset(p, bytes(d), o, name)
            if v is not None: struct.pack_into('>i', d, v, 0)
        w.replace(ref - 1, bytes(d))
        new.append(ref - 1)
    # the Level: cover and navigation lists, CoverLinkRefs, Actors
    ld = bytearray(w.current(lvl))
    L = level_lists(p, bytes(ld), w)
    refs, q = L['refs'], L['refs_count']                  # CoverLinkRefs (None: the level has no table)
    nav_end_at, cover_end_at = L['nav_end'], L['cover_end']
    def relink(at, prop):
        old_end = struct.unpack_from('>i', ld, at)[0]
        chain = [old_end - 1] + new
        for a_, b_ in zip(chain, chain[1:]):
            if a_ < 0: continue
            d = bytearray(w.current(a_))
            o, pr, end = levelinject.props_at(p, bytes(d), (26,) + tuple(range(0, 120, 2)))
            v, size = levelinject.tag_value_offset(p, bytes(d), o, prop)
            if v is None:
                d = levelinject.set_property(p, w, d, o, prop, struct.pack('>i', b_ + 1), 'ObjectProperty')
            else:
                struct.pack_into('>i', d, v, b_ + 1)
            w.replace(a_, bytes(d))
        if chain[0] < 0: struct.pack_into('>i', ld, at - 4, new[0] + 1)   # an empty list: it starts here
        struct.pack_into('>i', ld, at, new[-1] + 1)
    relink(cover_end_at, 'NextCoverLink'); relink(nav_end_at, 'nextNavigationPoint')
    n_refs = len(refs)
    if q is not None:
        ld = ld[:q] + struct.pack('>i', n_refs + len(new)) + ld[q + 4:q + 4 + 4 * n_refs] + b''.join(struct.pack('>i', x + 1) for x in new) + ld[q + 4 + 4 * n_refs:]
    pr, o = p.properties(bytes(ld), 4)
    n_actors = struct.unpack_from('>i', ld, o + 4)[0]
    a_end = o + 8 + 4 * n_actors
    ld = ld[:o + 4] + struct.pack('>i', n_actors + len(new)) + ld[o + 8:a_end] + b''.join(struct.pack('>i', x + 1) for x in new) + ld[a_end:]
    w.replace(lvl, bytes(ld))
    return new


def reencode(target, mods, g3cooked, shuffle=False):
    """experiment: a Gears 3 level with its own slots rewritten by write_slot_828 (the AI data dropped); shuffle: each
    link takes the next link's Location, Rotation and slots (cover in the same places, other actor identities)"""
    import levelinject, upkwrite, modpack
    p = upk.Package(target); w = upkwrite.Rewriter(p); n = 0
    links = [i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'CoverLink' and p.path(i).startswith('TheWorld')]
    def get(i):
        d = p.export_data(i); o, pr, end = levelinject.props_at(p, d, (26,)); a = {n: v for n, t, k, v in pr}
        v, size = levelinject.tag_value_offset(p, d, o, 'Slots')
        return a['Location'][1], a['Rotation'][1], read_slots_828(p, d[v:v + size])[0]
    def has_both(i):
        o, pr, end = levelinject.props_at(p, p.export_data(i), (26,)); names = {x[0] for x in pr}
        return 'Location' in names and 'Rotation' in names
    links = [i for i in links if has_both(i)]
    data = [get(i) for i in links]
    if shuffle: data = data[1:] + data[:1]
    for i, (loc, rot, slots) in zip(links, data):
        d = bytearray(p.export_data(i))
        for name, value in (('Location', loc), ('Rotation', rot),
                            ('Slots', struct.pack('>i', len(slots)) + b''.join(write_slot_828(w, s) for s in slots))):
            o, pr, end = levelinject.props_at(p, bytes(d), (26,))
            v, size = levelinject.tag_value_offset(p, bytes(d), o, name)
            d = bytearray(d[:v]) + value + d[v + size:]
            if len(value) != size: struct.pack_into('>i', d, v - 8, len(value))
        w.replace(i, bytes(d)); n += 1
    dst = os.path.join(mods, 'GearGame', 'CookedXbox360'); os.makedirs(dst, exist_ok=True)
    open(os.path.join(dst, os.path.basename(target)), 'wb').write(w.build())
    modpack.update_toc(mods, g3cooked)
    print(f'{n} cover links re-encoded')


if __name__ == '__main__' and sys.argv[2] == 'reencode':
    reencode(sys.argv[1], sys.argv[3], sys.argv[4], 'shuffle' in sys.argv[5:])
elif __name__ == '__main__':
    import collections, levelinject
    p = upk.Package(sys.argv[1])
    ok = bad = 0; types = collections.Counter(); flags = collections.Counter()
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'CoverLink' or not p.path(i).startswith('TheWorld'): continue
        o, pr, end = levelinject.props_at(p, p.export_data(i), (26,))
        raw = {n: v for n, t, k, v in pr}['Slots']
        try:
            slots, stop = read_slots_828(p, raw)
            if stop != len(raw): raise ValueError(f'{len(raw) - stop} bytes left')
            ok += 1
            for s in slots:
                types[s['type']] += 1
                for k, v in s['flags'].items(): flags[(k, v)] += 1
        except Exception as ex:
            bad += 1
            if bad <= 3: print('fail', p.path(i), ex)
    print(f'{ok} cover links parsed exactly, {bad} failed; slot types {dict(types)}')
    print('flag values:', {k: (flags[(k, 0)], flags[(k, 1)]) for k in FLAGS})


def append_nav_points(p, w, new):
    """navigation points copied into a Gears 3 level (PlayerStarts, PathNodes: export indices, 0-based) appended to
    its navigation point list (nextNavigationPoint from NavListEnd; NavListEnd = the last): the game finds player
    starts through that list (a copied PlayerStart outside it was never used: the player spawned at the origin and
    fell to KillZ). Returns the count."""
    import levelinject
    if not new: return 0
    lvl = next(i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'Level' and p.path(i) == 'TheWorld.PersistentLevel')
    ld = bytearray(w.current(lvl))
    at = level_lists(p, bytes(ld), w)['nav_end']
    old_end = struct.unpack_from('>i', ld, at)[0]
    # the last one ends the list: a copied point keeps the source's nextNavigationPoint, which can lead back into the
    # chain (a loop: the level load never finished)
    chain = [old_end - 1] + list(new) + [-1]
    for a_, b_ in zip(chain, chain[1:]):
        if a_ < 0: continue
        d = bytearray(w.current(a_))
        o, pr, end = levelinject.props_at(p, bytes(d), (26,) + tuple(range(0, 120, 2)))
        v, size = levelinject.tag_value_offset(p, bytes(d), o, 'nextNavigationPoint')
        if v is None: d = levelinject.set_property(p, w, d, o, 'nextNavigationPoint', struct.pack('>i', b_ + 1), 'ObjectProperty')
        else: struct.pack_into('>i', d, v, b_ + 1)
        w.replace(a_, bytes(d))
    if old_end == 0: struct.pack_into('>i', ld, at - 4, new[0] + 1)      # an empty list (levelpkg.blank): it starts here
    struct.pack_into('>i', ld, at, new[-1] + 1)
    w.replace(lvl, bytes(ld))
    return len(new)
