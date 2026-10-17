"""Gears of War 1 materials as Gears 3 material instances (offline): the Gears 1 material's diffuse, normal and
specular textures become Gears 3 Texture2D exports, and a clone of a Gears 3 MaterialInstanceConstant (one whose
parent takes Diffuse_01 / Normal_01 / Specular_01 texture parameters; its compiled static-permutation shaders stay
valid, as only texture parameters change) points at them.

Gears 1 materials are plain Materials: DiffuseColor / Normal / SpecularColor are tagged structs whose Expression leads
through the expression graph (Add, Multiply... inputs are ExpressionInput structs); the shallowest
MaterialExpressionTextureSample with a texture in this package is the slot's texture (fallback: ReferencedTextures by
name: _Tex / _Diffuse, _Normal, _Spec).
Gears 3 textures are written with every mip inline (LZO-decoded from Gears 1; the same tiled Xbox data) and
NeverStream = True, cloned from a Gears 3 texture of the same slot for the other properties (LODGroup, compression
settings...), TextureFileCacheName removed."""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, levelinject, texture2d

FORMATS_374 = {2: 'PF_A8R8G8B8', 3: 'PF_G8', 5: 'PF_DXT1', 6: 'PF_DXT3', 7: 'PF_DXT5'}


# ---- tagged properties as raw records, for editing and re-encoding ----

def raw_props(p, d, o):
    """[(name, type, array index, extra bytes (struct/enum name), value bytes)], offset after None"""
    out = []
    while True:
        name = p.fname(*struct.unpack_from('>ii', d, o)); o += 8
        if name == 'None': return out, o
        typ = p.fname(*struct.unpack_from('>ii', d, o))
        size, index = struct.unpack_from('>ii', d, o + 8); o += 16
        extra = b''
        if typ == 'StructProperty' or (typ == 'ByteProperty' and p.version >= 633): extra = d[o:o + 8]; o += 8
        if typ == 'BoolProperty': size = 1 if p.version >= 673 else 4
        out.append([name, typ, index, extra, d[o:o + size]]); o += size


def encode_props(w, props):
    """re-encode raw records with the Rewriter's names (Gears 3: bools are one byte, tag size 0)"""
    out = bytearray()
    for name, typ, index, extra, value in props:
        n, t = w.name(name), w.name(typ)
        size = 0 if typ == 'BoolProperty' else len(value)
        out += struct.pack('>iiiiii', n[0], n[1], t[0], t[1], size, index) + extra + value
    n = w.name('None')
    return bytes(out + struct.pack('>ii', *n))


def fname_bytes(w, s):
    return struct.pack('>ii', *w.name(s))


# ---- Gears 1 material -> textures ----

def g1_textures(p, mat):
    """{'diffuse'|'normal'|'specular': texture export index (0-based)} for Gears 1 material export index mat"""
    if mat >= 0 and p.class_name(p.exports[mat]['class']) == 'MaterialInstanceConstant':
        return g1_instance_textures(p, mat)
    if mat < 0 or p.class_name(p.exports[mat]['class']) != 'Material': return {}
    o, pr, end = levelinject.props_at(p, p.export_data(mat))
    a = {n: v for n, t, k, v in pr}
    out = {}
    for slot, prop in (('diffuse', 'DiffuseColor'), ('normal', 'Normal'), ('specular', 'SpecularColor')):
        if prop not in a: continue
        start = {n: v for n, t, k, v in p.properties(a[prop][1], 0)[0]}.get('Expression', 0)
        queue, seen = [start], set()
        while queue and slot not in out:
            ref = queue.pop(0)
            if ref <= 0 or ref in seen: continue
            seen.add(ref)
            try:
                _, epr, _ = levelinject.props_at(p, p.export_data(ref - 1))
            except Exception:
                continue
            for n, t, k, v in epr:
                if n == 'Texture' and t == 'ObjectProperty' and v > 0 and p.class_name(p.exports[v - 1]['class']) == 'Texture2D':
                    out[slot] = v - 1; break
                if t == 'StructProperty' and v[0] == 'ExpressionInput':
                    queue.append({nn: vv for nn, tt, kk, vv in p.properties(v[1], 0)[0]}.get('Expression', 0))
    if len(out) < 3 and 'ReferencedTextures' in a:                       # by name
        raw = a['ReferencedTextures']; n = struct.unpack_from('>i', raw, 0)[0]
        for r in struct.unpack_from(f'>{n}i', raw, 4):
            if r <= 0: continue
            nm = p.path(r - 1).lower()
            for slot, keys in (('diffuse', ('_tex', 'diffuse', '_d')), ('normal', ('normal', '_n')), ('specular', ('spec', '_s'))):
                if slot not in out and any(k in nm.split('.')[-1] for k in keys): out[slot] = r - 1
    return out


def g1_instance_textures(p, mic, depth=0):
    """g1_textures for a Gears 1 MaterialInstanceConstant: its texture parameters by name (Gears 1's character
    shaders: COG_Dom_Shader on Dom's gameplay mesh), then its parent's for the slots it leaves (before, its mesh drew
    with the template's material)"""
    out = {}
    try:
        recs, _ = raw_props(p, p.export_data(mic), 4)
    except Exception:
        return out
    a = {r[0]: r for r in recs}
    if 'TextureParameterValues' in a:
        v = a['TextureParameterValues'][4]
        o = 4
        for _ in range(struct.unpack_from('>i', v, 0)[0]):
            el, o = raw_props(p, v, o)
            e = {x[0]: x[4] for x in el}
            if 'ParameterName' not in e or 'ParameterValue' not in e: continue
            name = p.fname(*struct.unpack('>ii', e['ParameterName'][:8])).lower()
            ref = struct.unpack('>i', e['ParameterValue'][:4])[0]
            if ref <= 0 or p.class_name(p.exports[ref - 1]['class']) != 'Texture2D': continue
            slot = 'normal' if 'norm' in name else 'specular' if 'spec' in name else \
                   'diffuse' if any(k in name for k in ('diffuse', 'tex', 'color', 'base')) else None
            if slot and slot not in out: out[slot] = ref - 1
    parent = struct.unpack('>i', a['Parent'][4][:4])[0] if 'Parent' in a else 0
    if parent > 0 and depth < 8 and len(out) < 3:
        cls = p.class_name(p.exports[parent - 1]['class'])
        up = g1_instance_textures(p, parent - 1, depth + 1) if cls == 'MaterialInstanceConstant' else g1_textures(p, parent - 1)
        for k, x in up.items(): out.setdefault(k, x)
    return out


def g1_unlit(p, mat):
    """a Gears 1 material drawn without lighting (LightingModel MLM_Unlit, or only an emissive input): skies,
    backdrops, glows"""
    if mat < 0 or p.class_name(p.exports[mat]['class']) != 'Material': return False
    try:
        o, pr, end = levelinject.props_at(p, p.export_data(mat))
    except ValueError:
        return False
    a = {n: v for n, t, k, v in pr}
    lm = a.get('LightingModel')                    # Gears 1: an int (MLM_Phong 0, NonDirectional 1, Unlit 2, SHPRT 3,
    if lm == 2 or (isinstance(lm, tuple) and lm[1] == 'MLM_Unlit'): return True     # Custom 4); Gears 3: the enum name
    return 'EmissiveColor' in a and 'DiffuseColor' not in a


def g1_blend(p, ref, depth=0):
    """a Gears 1 material reference's BlendMode (0 opaque, 1 masked, 2 translucent, 3 additive, 4 modulate),
    following a material instance's Parent; None when not known (an import, unreadable)"""
    if ref <= 0 or depth > 8: return None
    cn = p.class_name(p.exports[ref - 1]['class'])
    try:
        o, pr, end = levelinject.props_at(p, p.export_data(ref - 1))
    except ValueError:
        return None
    a = {n: v for n, t, k, v in pr}
    if cn in ('Material', 'DecalMaterial'):             # (a decal's material: the prison's moss and grime)
        b = a.get('BlendMode', 0)
        return b if isinstance(b, int) else None
    par = a.get('Parent')
    return g1_blend(p, par, depth + 1) if isinstance(par, int) else None


def g1_see_through(p, mesh_elements):
    """every element of a Gears 1 static mesh drawn translucent / additive / modulated (light shafts, fog sheets):
    the conversion's one material template is opaque, which drew them as solid white cones"""
    modes = [g1_blend(p, el['material']) for el in mesh_elements]
    return bool(modes) and all(m is not None and m >= 2 for m in modes)


# ---- Gears 1 texture -> Gears 3 Texture2D serial data ----

def texture_828(w, p3, template, src):
    """(data, fixups) for a Gears 3 Texture2D export: template = a Gears 3 texture export index (its properties),
    src = texture2d.Texture of a Gears 1 texture"""
    d3 = p3.export_data(template)
    props, end = raw_props(p3, d3, 4)
    fmt = src.format if isinstance(src.format, str) else FORMATS_374.get(src.format, 'PF_DXT1')
    # the cook dropped the top mip(s) (empty entries): the texture starts at the first stored mip, so a texture
    # loaded whole (NeverStream) has its mips where the engine expects them
    first = next(k for k, mm in enumerate(src.mips) if not mm['flags'] & 0x20)
    mips = src.mips[first:]
    sx, sy = mips[0]['w'], mips[0]['h']
    tail = int(src.props.get('MipTailBaseIdx', len(src.mips) - 1)) - first
    keep = []
    for rec in props:
        name = rec[0]
        if name in ('TextureFileCacheName', 'FirstResourceMemMip', 'NeverStream'): continue
        if name in ('SizeX', 'OriginalSizeX'): rec[4] = struct.pack('>i', sx)
        elif name in ('SizeY', 'OriginalSizeY'): rec[4] = struct.pack('>i', sy)
        elif name == 'Format': rec[4] = fname_bytes(w, fmt)
        elif name == 'MipTailBaseIdx': rec[4] = struct.pack('>i', max(tail, 0))
        keep.append(rec)
    keep.append(['NeverStream', 'BoolProperty', 0, b'', b'\x01'])
    out = bytearray(d3[:4]) + encode_props(w, keep)
    fixups = []
    out += struct.pack('>iiii', 0, 0, 0, 0)                               # SourceArt: empty (offset fixed below)
    fixups.append((len(out) - 4, len(out)))
    out += struct.pack('>i', len(mips))
    for m, mip in enumerate(mips):
        data = src.mip_bytes(first + m)
        if data is None:
            out += struct.pack('>Iiii', 0x21, 0, -1, -1)
        else:
            out += struct.pack('>Iiii', 0, len(data), len(data), 0)
            fixups.append((len(out) - 4, len(out)))
            out += data
        out += struct.pack('>ii', mip['w'], mip['h'])
    out += bytes(16) + bytes(4)                                          # cache GUID, trailer (an empty array)
    return bytes(out), fixups


# ---- material instance ----

def mic_828(w, p3, template, textures, scalars=None):
    """serial data for a clone of Gears 3 MaterialInstanceConstant export `template`, with texture parameters
    replaced: textures = {'Diffuse_01': ref, ...} (object refs in the rewritten package), and scalar parameters
    (scalars = {'OverallEmissive': 0.0, ...}) set where the template has them"""
    d3 = bytearray(p3.export_data(template))
    o, pr, end = levelinject.props_at(p3, bytes(d3))
    if scalars:
        v, size = levelinject.tag_value_offset(p3, bytes(d3), o, 'ScalarParameterValues')
        if v is not None:
            e = v + 4
            for _ in range(struct.unpack_from('>i', d3, v)[0]):
                recs, after = raw_props(p3, bytes(d3), e)
                pname = next((p3.fname(*struct.unpack('>ii', r[4])) for r in recs if r[0] == 'ParameterName'), '')
                q = e
                for r in recs:
                    hdr = 24 + len(r[3])
                    if r[0] == 'ParameterValue' and pname in scalars:
                        struct.pack_into('>f', d3, q + hdr, scalars[pname])
                    q += hdr + len(r[4])
                e = after
    v, size = levelinject.tag_value_offset(p3, bytes(d3), o, 'TextureParameterValues')
    n = struct.unpack_from('>i', d3, v)[0]
    e = v + 4
    for _ in range(n):                                  # elements: tagged {ParameterName, ParameterValue, ExpressionGUID}
        recs, after = raw_props(p3, bytes(d3), e)
        pname = next((p3.fname(*struct.unpack('>ii', r[4])) for r in recs if r[0] == 'ParameterName'), '')
        q = e
        for r in recs:
            # walk to this record's value to patch it in place
            hdr = 24 + len(r[3])
            if r[0] == 'ParameterValue' and pname in textures:
                struct.pack_into('>i', d3, q + hdr, textures[pname])
            q += hdr + len(r[4])
        e = after
    return bytes(d3)


def mic_texture_params(p, i):
    """texture parameter names of MaterialInstanceConstant export i (Gears 3 package)"""
    d = p.export_data(i)
    try:
        o, pr, end = levelinject.props_at(p, d)
        v, size = levelinject.tag_value_offset(p, d, o, 'TextureParameterValues')
    except Exception:
        return []
    if v is None: return []
    out, e = [], v + 4
    for _ in range(struct.unpack_from('>i', d, v)[0]):
        recs, e = raw_props(p, d, e)
        out += [p.fname(*struct.unpack('>ii', r[4])) for r in recs if r[0] == 'ParameterName']
    return out


# ---- a chapter of its own: materials as child instances of a kit's (tools/levelpkg.py) ----

def import_object(w, p3, pk, index, cache):
    """an import ref in the package being written (w, read as p3) of export `index` of package pk (the kit): the
    chain of its outers as Package imports, then the object (its class from pk's import of it). At load the game
    resolves it to the copy the kit (loaded first) carries: cooked objects keep their original paths."""
    chain = []
    i = index
    while i >= 0:
        chain.append(i)
        i = pk.exports[i]['outer'] - 1
    outer = 0
    for k in reversed(chain):
        e = pk.exports[k]
        cref = e['class']
        if cref == 0: cls, cpkg = 'Class', 'Core'
        elif cref < 0:
            im = pk.imports[-cref - 1]
            cls = im['name']
            o = im['outer']
            cpkg = pk.imports[-o - 1]['name'] if o < 0 else 'Engine'
        else: cls, cpkg = pk.exports[cref - 1]['name'], 'Engine'
        key = (cpkg, cls, outer, e['name'])
        if key not in cache:
            existing = next((-n - 1 for n, im in enumerate(p3.imports) if im['class'] == cls and im['outer'] == outer
                             and im['name'] == e['name']), None)
            if existing is None:
                ni, nn = struct.unpack_from('>ii', pk.data, e['entry'] + 12)      # the export's raw FName
                existing = w.add_import(cpkg, cls, outer, pk.names[ni], nn)
            cache[key] = existing
        outer = cache[key]
    return outer


def copy_import(w, p3, pk, ref, cache):
    """an import ref in the package being written (w, read as p3) of the object that import `ref` (negative) of
    package pk names: its chain of outers copied (the engine's own content, e.g. ALL_SoldierShaders.AllBlack)"""
    chain = []
    while ref < 0:
        chain.append(ref)
        ref = pk.imports[-ref - 1]['outer']
    outer = 0
    for r in reversed(chain):
        im = pk.imports[-r - 1]
        key = (im['class_package'], im['class'], outer, im['name'])
        if key not in cache:
            existing = next((-n - 1 for n, x in enumerate(p3.imports) if x['class'] == im['class'] and x['outer'] == outer
                             and x['name'] == im['name']), None)
            if existing is None:
                base, num = (im['name'].rsplit('_', 1) if '_' in im['name'] and im['name'].rsplit('_', 1)[1].isdigit()
                             and not im['name'].rsplit('_', 1)[1].startswith('0') else (im['name'], None))
                existing = w.add_import(im['class_package'], im['class'], outer, base, int(num) + 1 if num is not None else 0)
            cache[key] = existing
        outer = cache[key]
    return outer


def child_mic(w, p3, pk, kit_mic, parent_ref, textures, scalars=None):
    """serial data for a new MaterialInstanceConstant whose Parent is kit_mic (an export of the kit pk, imported as
    parent_ref): its texture parameters set to textures ({'Diffuse_01': ref, ...}, refs in w), scalars where the kit
    instance has them. Written from properties only: no static permutation of its own, so the game uses the
    parent's (compiled into the kit's shader cache); each parameter keeps the kit's ExpressionGUID."""
    d = pk.export_data(kit_mic)
    o, pr, end = levelinject.props_at(pk, d)
    def elements(prop):
        v, size = levelinject.tag_value_offset(pk, d, o, prop)
        if v is None: return []
        out, e = [], v + 4
        for _ in range(struct.unpack_from('>i', d, v)[0]):
            recs, e = raw_props(pk, d, e)
            out.append(recs)
        return out
    def rebuild(recs, value_for):
        """an element re-made with w's names; ParameterValue from value_for(name) (None: the element is left out)"""
        pname = next((pk.fname(*struct.unpack('>ii', r[4])) for r in recs if r[0] == 'ParameterName'), '')
        value = value_for(pname)
        if value is None: return None
        out = []
        for name, typ, index, extra, val in recs:
            if extra: extra = struct.pack('>ii', *w.name(pk.fname(*struct.unpack('>ii', extra))))
            if name == 'ParameterName': val = struct.pack('>ii', *w.name(pname))
            elif name == 'ParameterValue': val = value
            out.append([name, typ, index, extra, val])
        return encode_props(w, out)
    props = [['Parent', 'ObjectProperty', 0, b'', struct.pack('>i', parent_ref)]]
    tex = [x for x in (rebuild(e, lambda n: struct.pack('>i', textures[n]) if n in textures else None)
                       for e in elements('TextureParameterValues')) if x is not None]
    if tex: props.append(['TextureParameterValues', 'ArrayProperty', 0, b'', struct.pack('>i', len(tex)) + b''.join(tex)])
    if scalars:
        sc = [x for x in (rebuild(e, lambda n: struct.pack('>f', scalars[n]) if n in scalars else None)
                          for e in elements('ScalarParameterValues')) if x is not None]
        if sc: props.append(['ScalarParameterValues', 'ArrayProperty', 0, b'', struct.pack('>i', len(sc)) + b''.join(sc)])
    return d[:o] + encode_props(w, props)


def texture_settings(p, i):
    """(CompressionSettings, LODGroup) of a Gears 3 Texture2D export, by name"""
    d = p.export_data(i)
    try: recs, _ = raw_props(p, d, 4)
    except Exception: return None, None
    a = {r[0]: r for r in recs}
    def enum(n):
        r = a.get(n)
        return p.fname(*struct.unpack('>ii', r[4])) if r is not None and len(r[4]) == 8 else None
    return enum('CompressionSettings'), enum('LODGroup')
