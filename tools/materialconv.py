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
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, levelinject, texture2d

FORMATS_374 = {2: 'PF_A8R8G8B8', 3: 'PF_G8', 5: 'PF_DXT1', 6: 'PF_DXT3', 7: 'PF_DXT5'}
# converted textures' mips larger than this left out (0: all kept; levelinject --max-texture, a recipe's max_texture). The
# converted textures stay resident (NeverStream) in Gears 3's 145 MB texture pool (Coalesced [TextureStreaming] PoolSize),
# which the streaming textures share: the prison's 105 MB left the classic Marcus and Dom at their low mips (muddy) most
# of the time; at 512 (the persistent package 137 -> 93 MB) they stream in full and the level looks the same at the game's resolution
MAX_TEXTURE_SIZE = 0
# the same for the textures of skeletal meshes' materials (the cutscene's Marcus, Dom, bag, Wretches: a few MB more at
# 1024; at 512 their armour and faces drew muddy beside Gears 1's); None: MAX_TEXTURE_SIZE
MAX_CHARACTER_TEXTURE_SIZE = None


# ---- tagged properties as raw records, for editing and re-encoding ----


def g1_post_process_value(name, value):
    """a Gears 1 post-process setting's bytes as Gears 3 reads the same look. Scene_HighLights: Gears 1 scales the
    scene by it, Gears 3 divides by it (measured: the prison's (0.8, 0.8, 0.7) taken as it is drew the opening 1.5x too
    bright and neutral, blue 33% of the colour where Gears 1's is 27%; as (1/0.8, 1/0.8, 1/0.7), 36.6/33.1/28.4 against
    Gears 1's 42.4/39.3/30.2 on the Xbox 360, Xenia); Scene_Shadows: negated. GEARS1_PP_RAW=1: every value as it is"""
    if name == 'Scene_HighLights' and len(value) == 12 and not os.environ.get('GEARS1_PP_RAW'):
        return struct.pack('>3f', *(1.0 / max(x, 1e-3) for x in struct.unpack('>3f', value)))
    # Scene_Shadows: the opposite sign (measured: the prison's (0, 0, -0.03) taken as it is lifted blue in the dark
    # corridors, 32.7% of the colour against Gears 1's 28.7%; negated, the path choice's corridor drew 29.6/31.2/24.7
    # against the let's play's 29.9/30.8/24.4)
    if name == 'Scene_Shadows' and len(value) == 12 and not os.environ.get('GEARS1_PP_RAW'):
        return struct.pack('>3f', *(-x for x in struct.unpack('>3f', value)))
    return value

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


def g1_uv_transform(p, ref):
    """the UV mapping of a Gears 1 material reference's diffuse sample as (UV set, (scale u, v), (offset u, v)), or None
    when it reads UV set 0 as it is (or through expressions not followed: panners, bump offsets, parameters). Gears 1
    scaled and picked texture coordinates in its materials (TextureCoordinate's CoordinateIndex and Tiling, times and
    plus constants: the prison's trim floors at (1, 0.5), its ruined brick walls on UV set 1, cell concrete 5 times);
    the converted materials sample UV set 0 as it is, and the converted meshes' UVs carry the mapping instead"""
    if ref <= 0: return None
    mat = ref - 1
    try:
        for _ in range(8):
            if p.class_name(p.exports[mat]['class']) != 'MaterialInstanceConstant': break
            recs, _ = raw_props(p, p.export_data(mat), 4)
            par = next((struct.unpack('>i', r[4][:4])[0] for r in recs if r[0] == 'Parent'), 0)
            if par <= 0: return None
            mat = par - 1
        if p.class_name(p.exports[mat]['class']) != 'Material': return None
        tex = g1_textures(p, mat).get('diffuse')
    except Exception:
        return None
    if tex is None: return None
    def props(r):
        return {n: v for n, t, k, v in levelinject.props_at(p, p.export_data(r - 1))[1]}
    def inp(v): return {n: x for n, t, k, x in p.properties(v[1], 0)[0]}.get('Expression', 0)
    def ev(r, depth=0):
        """('uv', set, su, sv, ou, ov) or ('c', x, y), None when not followed"""
        if depth > 12: return None
        if r <= 0: return ('uv', 0, 1.0, 1.0, 0.0, 0.0)
        cn = p.class_name(p.exports[r - 1]['class'])
        a = props(r)
        if cn == 'MaterialExpressionTextureCoordinate':
            t = a.get('Tiling', 1.0)
            return ('uv', a.get('CoordinateIndex', 0), t * a.get('UTiling', 1.0), t * a.get('VTiling', 1.0), 0.0, 0.0)
        if cn == 'MaterialExpressionConstant': return ('c', a.get('R', 0.0), a.get('R', 0.0))
        if cn == 'MaterialExpressionConstant2Vector': return ('c', a.get('R', 0.0), a.get('G', 0.0))
        if cn in ('MaterialExpressionMultiply', 'MaterialExpressionAdd') and 'A' in a and 'B' in a:
            x, y = ev(inp(a['A']), depth + 1), ev(inp(a['B']), depth + 1)
            if x is None or y is None: return None
            if x[0] == 'c' and y[0] == 'uv': x, y = y, x
            if cn == 'MaterialExpressionMultiply':
                if x[0] == 'c' and y[0] == 'c': return ('c', x[1] * y[1], x[2] * y[2])
                if y[0] != 'c': return None
                return ('uv', x[1], x[2] * y[1], x[3] * y[2], x[4] * y[1], x[5] * y[2])
            if x[0] == 'c' and y[0] == 'c': return ('c', x[1] + y[1], x[2] + y[2])
            if y[0] != 'c': return None
            return ('uv', x[1], x[2], x[3], x[4] + y[1], x[5] + y[2])
        return None
    for j, e in enumerate(p.exports):
        if e['outer'] != mat + 1 or p.class_name(e['class']) != 'MaterialExpressionTextureSample': continue
        try:
            a = props(j + 1)
            if a.get('Texture') != tex + 1: continue
            t = ev(inp(a['Coordinates'])) if 'Coordinates' in a else ('uv', 0, 1.0, 1.0, 0.0, 0.0)
        except Exception:
            return None
        if t is None or t[0] != 'uv' or t[1:] == (0, 1.0, 1.0, 0.0, 0.0): return None
        return (t[1], (t[2], t[3]), (t[4], t[5]))
    return None


def g1_apply_uv_transforms(p, m, transform_of):
    """a Gears 1 static mesh (staticmesh.read_374) with each element's UV set 0 replaced by its material's mapping
    (transform_of(material ref): g1_uv_transform's result or None), in place; returns the number of elements changed"""
    import numpy as np
    lod = m['lods'][0]
    if 'interleaved' not in lod: return 0
    uvs, stride, n, data = lod['interleaved']
    a = np.frombuffer(data, dtype=np.uint8).reshape(n, stride).copy()
    uv = np.frombuffer(a[:, 28:28 + 8 * uvs].tobytes(), dtype='>f4').reshape(n, uvs, 2).astype(np.float64)
    idx = np.frombuffer(lod['indices'][2], dtype='>u2')
    out, done, changed = uv[:, 0].copy(), np.zeros(n, bool), 0
    for el in lod['elements']:
        t = transform_of(el['material'])
        if t is None: continue
        ch, sc, off = t
        if ch >= uvs: continue
        vs = np.unique(idx[el['first_index']:el['first_index'] + 3 * el['triangles']])
        vs = vs[(vs < n) & ~done[vs]]
        out[vs] = uv[vs, ch] * np.array(sc) + np.array(off); done[vs] = True
        changed += 1
    if not changed: return 0
    uv[:, 0] = out
    a[:, 28:28 + 8 * uvs] = np.frombuffer(uv.astype('>f4').tobytes(), dtype=np.uint8).reshape(n, 8 * uvs)
    lod['interleaved'] = (uvs, stride, n, a.tobytes())
    return changed


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


def g1_shading(p, ref):
    """(diffuse tint (r, g, b) or None, specular: bool) of a Gears 1 material reference: its DiffuseColor input's mean
    multiplier over its texture (the chain evaluated with texture samples as 1, texture coordinates at their middle
    0.5, vertex colours 1: constants, parameters' defaults, multiply / add / subtract / one-minus / clamp / lerp /
    power / component masks), None when it is about 1, over 1 in any channel, or not followed; specular False when Gears 1 lit it
    NonDirectional or unlit, or wired nothing to SpecularColor. Gears 1 dimmed and tinted texture colours in its
    materials (the prison's hanging grass: the texture x (0.65, 0.5, 0.5) x a root-to-tip gradient, NonDirectional);
    the converted materials draw the texture as it is unless the instance carries these"""
    if ref <= 0: return None, True
    mat = ref - 1
    vec_over = {}
    try:
        for _ in range(8):
            if p.class_name(p.exports[mat]['class']) != 'MaterialInstanceConstant': break
            recs, _ = raw_props(p, p.export_data(mat), 4)
            par = next((struct.unpack('>i', r[4][:4])[0] for r in recs if r[0] == 'Parent'), 0)
            if par <= 0: return None, True
            mat = par - 1
        if p.class_name(p.exports[mat]['class']) != 'Material': return None, True
        a = {n: v for n, t, k, v in levelinject.props_at(p, p.export_data(mat))[1]}
    except Exception:
        return None, True
    lm = a.get('LightingModel', 0)
    specular = 'SpecularColor' in a and lm not in (1, 2)
    def props(r): return {n: v for n, t, k, v in levelinject.props_at(p, p.export_data(r - 1))[1]}
    def inp(v):
        d = {n: x for n, t, k, x in p.properties(v[1], 0)[0]}
        masks = [c for c in ('MaskR', 'MaskG', 'MaskB', 'MaskA') if d.get(c)] if d.get('Mask') else []
        return d.get('Expression', 0), masks
    def pick(x, masks):
        if not masks or x is None: return x
        ch = {'MaskR': 0, 'MaskG': 1, 'MaskB': 2, 'MaskA': 2}
        sel = [x[ch[m]] for m in masks]
        return tuple(sel + [sel[-1]] * (3 - len(sel)))[:3] if len(sel) > 1 else (sel[0],) * 3
    def ev(v, depth=0):
        if v is None: return None
        r, masks = inp(v)
        return pick(node(r, depth), masks)
    def node(r, depth):
        if r <= 0 or depth > 16: return None
        cn = p.class_name(p.exports[r - 1]['class'])[len('MaterialExpression'):]
        b = props(r)
        f = lambda k, d=0.0: float(b.get(k, d))
        if cn == 'Constant': return (f('R'),) * 3
        if cn in ('Constant2Vector', 'Constant3Vector', 'Constant4Vector'): return (f('R'), f('G'), f('B'))
        if cn in ('TextureSample', 'TextureSampleParameter2D', 'VertexColor'): return (1.0, 1.0, 1.0)
        if cn == 'TextureCoordinate': return (0.5, 0.5, 0.5)
        if cn == 'ScalarParameter': return (f('DefaultValue'),) * 3
        if cn == 'VectorParameter':
            dv = b.get('DefaultValue')
            return struct.unpack('>3f', dv[1][:12]) if isinstance(dv, tuple) else None
        two = lambda: (ev(b.get('A'), depth + 1), ev(b.get('B'), depth + 1))
        if cn in ('Multiply', 'Add', 'Subtract'):
            x, y = two()
            if x is None or y is None: return None
            op = {'Multiply': lambda u, w_: u * w_, 'Add': lambda u, w_: u + w_, 'Subtract': lambda u, w_: u - w_}[cn]
            return tuple(op(u, w_) for u, w_ in zip(x, y))
        if cn == 'OneMinus':
            x = ev(b.get('Input'), depth + 1)
            return None if x is None else tuple(1 - u for u in x)
        if cn == 'ComponentMask':
            x = ev(b.get('Input'), depth + 1)
            return pick(x, [m for m, k in (('MaskR', 'R'), ('MaskG', 'G'), ('MaskB', 'B'), ('MaskA', 'A')) if b.get(k)])
        if cn == 'Clamp':
            x = ev(b.get('Input'), depth + 1)
            lo = ev(b.get('Min'), depth + 1) if 'Min' in b else (0.0,) * 3
            hi = ev(b.get('Max'), depth + 1) if 'Max' in b else (1.0,) * 3
            if x is None or lo is None or hi is None: return None
            return tuple(min(max(u, l_), h_) for u, l_, h_ in zip(x, lo, hi))
        if cn == 'LinearInterpolate':
            x, y = two(); t = ev(b.get('Alpha'), depth + 1)
            if x is None or y is None or t is None: return None
            return tuple(u * (1 - k) + w_ * k for u, w_, k in zip(x, y, t))
        if cn == 'Power':
            x = ev(b.get('Base'), depth + 1); e = ev(b.get('Exponent'), depth + 1) if 'Exponent' in b else (f('ConstExponent', 2.0),) * 3
            if x is None or e is None: return None
            return tuple(max(u, 0.0) ** k for u, k in zip(x, e))
        return None
    tint = None
    try:
        if 'DiffuseColor' in a:
            t = ev(a['DiffuseColor'])
            if t is not None:
                t = tuple(min(max(u, 0.0), 2.0) for u in t)
                # dimming only: a multiplier over 1 is mostly a detail texture's x2 (its sample averaging 0.5, not 1)
                if max(t) <= 1.0 and max(abs(u - 1.0) for u in t) > 0.05: tint = t
    except Exception:
        tint = None
    return tint, specular


def g1_emissive(p, mat):
    """(texture export index, (r, g, b) scale) a lit Gears 1 material's EmissiveColor draws: its texture times the
    constants multiplied onto it (Constant, Constant3/4Vector, a VectorParameter's default; the corridor tubes:
    x 1.5 x LightColor (0.8, 1.3, 1.1)); None without a texture"""
    tex = g1_emissive_texture(p, mat)
    if tex is None: return None
    recs = {r[0]: r[4] for r in raw_props(p, p.export_data(mat), 4)[0]}
    def expr_of(v):
        try: sub = {r[0]: r[4] for r in raw_props(p, v, 0)[0]}
        except Exception: return 0
        return struct.unpack('>i', sub['Expression'])[0] if 'Expression' in sub else 0
    def value(e, depth=0):
        """('tex', scale) or ('const', rgb) or None"""
        if e <= 0 or depth > 12: return None
        cn = p.class_name(p.exports[e - 1]['class']).replace('MaterialExpression', '')
        try: er = {r[0]: r for r in raw_props(p, p.export_data(e - 1), 4)[0]}
        except Exception: return None
        f = lambda k, d=0.0: struct.unpack('>f', er[k][4])[0] if k in er else d
        if 'TextureSample' in cn: return ('tex', np.ones(3))
        if cn == 'Constant': return ('const', np.full(3, f('R')))
        if cn in ('Constant3Vector', 'Constant4Vector'): return ('const', np.array([f('R'), f('G'), f('B')]))
        if cn == 'VectorParameter' and 'DefaultValue' in er:
            v = er['DefaultValue'][4]
            try: c = {r[0]: struct.unpack('>f', r[4])[0] for r in raw_props(p, v, 0)[0]}; return ('const', np.array([c.get('R', 0), c.get('G', 0), c.get('B', 0)]))
            except Exception: return ('const', np.array(struct.unpack_from('>3f', v, 0))) if len(v) >= 12 else None
        ins = [value(expr_of(r[4]), depth + 1) for k, r in er.items() if r[1] == 'StructProperty' and k in ('A', 'B', 'Input')]
        ins = [x for x in ins if x is not None]
        if cn == 'Multiply' and len(ins) == 2:
            (ka, va), (kb, vb) = ins
            return ('tex' if 'tex' in (ka, kb) else 'const', va * vb)
        texs = [x for x in ins if x[0] == 'tex']
        return texs[0] if texs else (ins[0] if ins else None)
    v = value(expr_of(recs['EmissiveColor']))
    scale = tuple(float(x) for x in np.clip(v[1], 0, 4)) if v and v[0] == 'tex' else (1.0, 1.0, 1.0)
    return tex, scale


def scale_colour(data, fmt, scale):
    """a tiled DXT1/3/5 mip with its colour times scale (r, g, b), block by block in place (the tiling and the packed
    mip tail stay as they are): both colour endpoints scaled, so every texel the block interpolates is; a DXT1 block
    whose endpoint order (its 3/4-colour mode) the scaling would flip is left as it was"""
    import numpy as np
    if fmt not in ('PF_DXT1', 'PF_DXT3', 'PF_DXT5'): return data
    bs, co = (8, 0) if fmt == 'PF_DXT1' else (16, 8)
    a = bytearray(data)
    sr, sg, sb = scale
    for o in range(0, len(a) - bs + 1, bs):
        c0, c1 = struct.unpack_from('>HH', a, o + co)
        def sc(c):
            r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
            r, g, b = min(31, round(r * sr)), min(63, round(g * sg)), min(31, round(b * sb))
            return (r << 11) | (g << 5) | b
        n0, n1 = sc(c0), sc(c1)
        if fmt == 'PF_DXT1' and ((c0 > c1) != (n0 > n1) or (c0 == c1) != (n0 == n1)): continue
        struct.pack_into('>HH', a, o + co, n0, n1)
    return bytes(a)


def g1_emissive_texture(p, mat):
    """the texture (export index) a lit Gears 1 material's EmissiveColor draws from (the first TextureSample down its
    expression chain: the corridor's fluorescent tubes, a hanging lamp), or None (no emissive, unlit, or a constant)"""
    if mat < 0 or p.class_name(p.exports[mat]['class']) != 'Material' or g1_unlit(p, mat): return None
    try: recs = {r[0]: r[4] for r in raw_props(p, p.export_data(mat), 4)[0]}
    except Exception: return None
    if 'EmissiveColor' not in recs: return None
    def expr_of(v):
        try: sub = {r[0]: r[4] for r in raw_props(p, v, 0)[0]}
        except Exception: return 0
        return struct.unpack('>i', sub['Expression'])[0] if 'Expression' in sub else 0
    todo, seen = [expr_of(recs['EmissiveColor'])], set()
    while todo:
        e = todo.pop(0)
        if e <= 0 or e in seen: continue
        seen.add(e)
        try: er = raw_props(p, p.export_data(e - 1), 4)[0]
        except Exception: continue
        if 'TextureSample' in p.class_name(p.exports[e - 1]['class']):
            t = next((struct.unpack('>i', r[4])[0] for r in er if r[0] == 'Texture'), 0)
            if t > 0: return t - 1
        for r in er:
            if r[1] == 'StructProperty': todo.append(expr_of(r[4]))
    return None


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


def g1_mask(p, mat):
    """a masked Gears 1 Material's (BlendMode 1) cutout: (texture export index, 'A' | 'R'), the texture its
    OpacityMask samples and the channel (the diffuse's alpha: the prison's fence, razor wire, grass; a texture of its
    own's red: a tarp's _Alpha), or None"""
    if mat < 0 or p.class_name(p.exports[mat]['class']) != 'Material': return None
    try:
        o, pr, end = levelinject.props_at(p, p.export_data(mat))
    except ValueError:
        return None
    a = {n: v for n, t, k, v in pr}
    # (translucent, lit or not, with a normal map -- the prison's chain-link fences, their opacity the diffuse's alpha
    # faded with distance -- cut by that alpha as well: Gears 3 has no translucent template)
    if a.get('BlendMode') == 1 and 'OpacityMask' in a: src_ = 'OpacityMask'
    elif a.get('BlendMode') == 2 and 'Opacity' in a and not g1_volumetric(p, mat + 1): src_ = 'Opacity'
    else: return None
    inp = {n: v for n, t, k, v in p.properties(a[src_][1], 0)[0]}
    queue, seen, channel = [inp.get('Expression', 0)], set(), 'A' if inp.get('MaskA') else 'R' if inp.get('MaskR') else None
    while queue:
        ref = queue.pop(0)
        if ref <= 0 or ref in seen: continue
        seen.add(ref)
        try:
            _, epr, _ = levelinject.props_at(p, p.export_data(ref - 1))
        except Exception:
            continue
        for n, t, k, v in epr:
            if n == 'Texture' and t == 'ObjectProperty' and v > 0 and p.class_name(p.exports[v - 1]['class']) == 'Texture2D':
                if channel is None:                     # (through Add / Multiply: the diffuse's alpha, or a mask's red)
                    channel = 'A' if v - 1 == g1_textures(p, mat).get('diffuse') else 'R'
                return v - 1, channel
            if t == 'StructProperty' and v[0] == 'ExpressionInput':
                e = {nn: vv for nn, tt, kk, vv in p.properties(v[1], 0)[0]}
                queue.append(e.get('Expression', 0))
                if channel is None and e.get('MaskA'): channel = 'A'
    return None


def red_to_alpha(data, fmt):
    """a tiled DXT1 mip whose alpha (1-bit: DXT1's punch-through) is its red, white where kept (a Gears 1 mask
    texture's cutout: the masked Gears 3 master reads its mask texture's alpha). Block by block in place, so the
    tiling and the packed mip tail stay as they are: 8-byte blocks, 16-bit words big-endian; both colour endpoints
    white (3-colour mode), index 3 (transparent) where red is under half"""
    import numpy as np
    if fmt != 'PF_DXT1': raise ValueError(f'red_to_alpha: {fmt}')
    pad = (-len(data)) % 8
    blk = np.frombuffer(data + bytes(pad), dtype=np.uint8).reshape(-1, 8)
    le = blk.reshape(-1, 2)[:, ::-1].reshape(-1, 8)                       # little-endian block bytes
    c0 = le[:, 0].astype(np.int32) | (le[:, 1].astype(np.int32) << 8)
    c1 = le[:, 2].astype(np.int32) | (le[:, 3].astype(np.int32) << 8)
    bits = np.ascontiguousarray(le[:, 4:8]).view('<u4')[:, 0]
    idx = ((bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None]) & 3).astype(np.int64)
    r0, r1 = (c0 >> 11) * 255 // 31, (c1 >> 11) * 255 // 31
    four = c0 > c1
    pal = np.stack([r0, r1, np.where(four, (2 * r0 + r1) // 3, (r0 + r1) // 2), np.where(four, (r0 + 2 * r1) // 3, 0)], 1)
    red = np.take_along_axis(pal, idx, 1)
    new = np.where(red >= 128, 0, 3).astype(np.uint32)
    nbits = (new << (2 * np.arange(16, dtype=np.uint32))[None]).sum(1).astype('<u4')
    out = np.concatenate([np.full((len(le), 4), 0xff, np.uint8), nbits.view(np.uint8).reshape(-1, 4)], 1)
    return out.reshape(-1, 2)[:, ::-1].reshape(-1).tobytes()[:len(data)]


def alpha_to_grey(data, fmt):
    """a tiled DXT1/3/5 mip whose colour is grey from its alpha, the alpha kept (a mask read on any channel: the
    masked Gears 3 master's channel switch is its own). Block by block in place, so the tiling and the packed mip
    tail stay as they are: a block is 8 (DXT1) or 16 bytes, 16-bit words big-endian; its colour half white/black
    endpoints (4-colour mode: 255, 0, 170, 85) indexed by each texel's alpha (DXT1: its 1-bit alpha, kept as 3-colour
    mode's transparent index)"""
    import numpy as np
    bpb = 8 if fmt == 'PF_DXT1' else 16
    pad = (-len(data)) % bpb
    blk = np.frombuffer(data + bytes(pad), dtype=np.uint8).reshape(-1, bpb)
    le = blk.reshape(-1, 2)[:, ::-1].reshape(-1, bpb)                     # little-endian block bytes
    if fmt == 'PF_DXT1':
        c0 = le[:, 0].astype(np.int32) | (le[:, 1].astype(np.int32) << 8)
        c1 = le[:, 2].astype(np.int32) | (le[:, 3].astype(np.int32) << 8)
        bits = np.ascontiguousarray(le[:, 4:8]).view('<u4')[:, 0]
        idx = (bits[:, None] >> (2 * np.arange(16, dtype=np.uint32))[None]) & 3
        new = np.where((idx == 3) & (c0 <= c1)[:, None], 3, 0).astype(np.uint32)       # white, or transparent
        nbits = (new << (2 * np.arange(16, dtype=np.uint32))[None]).sum(1).astype('<u4')
        out = np.concatenate([np.full((len(le), 4), 0xff, np.uint8), nbits.view(np.uint8).reshape(-1, 4)], 1)
        return out.reshape(-1, 2)[:, ::-1].reshape(-1).tobytes()[:len(data)]
    if fmt == 'PF_DXT5':
        alpha = texture2d.decode_bc4(np.ascontiguousarray(le[:, :8]))
    else:
        a4 = np.unpackbits(le[:, :8], axis=1, bitorder='little').reshape(-1, 16, 4)
        alpha = (a4 * (1 << np.arange(4))).sum(2) * 17
    levels = np.array([255, 0, 170, 85])
    idx = np.abs(alpha[:, :, None].astype(np.int32) - levels[None, None]).argmin(2).astype(np.uint32)
    bits = (idx << (2 * np.arange(16, dtype=np.uint32))[None]).sum(1).astype('<u4')
    out = le.copy()
    out[:, 8:12] = np.array([0xff, 0xff, 0, 0], np.uint8)
    out[:, 12:16] = bits.view(np.uint8).reshape(-1, 4)
    return out.reshape(-1, 2)[:, ::-1].reshape(-1).tobytes()[:len(data)]


def g1_see_through(p, mesh_elements):
    """every element of a Gears 1 static mesh drawn translucent / additive / modulated (light shafts, fog sheets):
    the conversion's one material template is opaque, which drew them as solid white cones"""
    modes = [g1_blend(p, el['material']) for el in mesh_elements]
    return bool(modes) and all(m is not None and m >= 2 for m in modes)


def g1_volumetric(p, ref):
    """a Gears 1 material reference drawn as light in the air (translucent or additive, with no normal map): the
    prison's fog sheets (RB_Caves_Volumetrics_Distancesheet01), light cones (..._Lightcone01_M) and light rays
    (COG_City_TimgadVolume2_CP_Mat): unlit. Not the see-through chain-link fences (a normal map), the cobwebs (lit)
    nor the decal cards (modulated: moss, dirt, stains)"""
    if g1_blend(p, ref) not in (2, 3): return False
    if ref < 0: return False
    try:
        mat = ref - 1
        while p.class_name(p.exports[mat]['class']) == 'MaterialInstanceConstant':       # (unlit: the parent Material's)
            recs, _ = raw_props(p, p.export_data(mat), 4)
            par = next((struct.unpack('>i', r[4][:4])[0] for r in recs if r[0] == 'Parent'), 0)
            if par <= 0: return False
            mat = par - 1
        return g1_unlit(p, mat) and 'normal' not in g1_textures(p, ref - 1)
    except Exception: return False


# ---- Gears 1 texture -> Gears 3 Texture2D serial data ----

# a converted texture's LODBias by its use (None: the template's). The templates are Gears 3 textures with their own
# memory settings (T_COG_Hanover_shelf_D: 1 or 2), which drew every converted texture at a half or a quarter of its
# size: muddy walls, and the grass's cutout from a 64-texel alpha in blobs (the hard route's outdoor hallway). Gears
# 1's cook already left out the mips its own LODBias dropped (texture_828 starts at the first one stored): colour
# and cutouts at that size, as Gears 1 drew them; normal and specular maps keep the template's (memory: all at 0, the
# prison's resident textures went from about 17 to 65 MB)
SLOT_LOD_BIAS = {'diffuse': 0, 'mask': 0}


def texture_828(w, p3, template, src, mask=None, lod_bias=None, max_size=None, colour_scale=None):
    """(data, fixups) for a Gears 3 Texture2D export: template = a Gears 3 texture export index (its properties),
    src = texture2d.Texture of a Gears 1 texture; mask: 'A' (its alpha as grey too: alpha_to_grey) or 'R' (a DXT1
    mask's red as its alpha: red_to_alpha); lod_bias: its LODBias (None: the template's)"""
    d3 = p3.export_data(template)
    props, end = raw_props(p3, d3, 4)
    fmt = src.format if isinstance(src.format, str) else FORMATS_374.get(src.format, 'PF_DXT1')
    # the cook dropped the top mip(s) (empty entries): the texture starts at the first stored mip, so a texture
    # loaded whole (NeverStream) has its mips where the engine expects them
    first = next(k for k, mm in enumerate(src.mips) if not mm['flags'] & 0x20)
    cap = MAX_TEXTURE_SIZE if max_size is None else max_size
    while cap and max(src.mips[first]['w'], src.mips[first]['h']) > cap and \
            first + 1 < int(src.props.get('MipTailBaseIdx', len(src.mips) - 1)):
        first += 1
    mips = src.mips[first:]
    sx, sy = mips[0]['w'], mips[0]['h']
    tail = int(src.props.get('MipTailBaseIdx', len(src.mips) - 1)) - first
    keep = []
    for rec in props:
        name = rec[0]
        if name in ('TextureFileCacheName', 'FirstResourceMemMip', 'NeverStream'): continue
        if name == 'LODBias' and lod_bias is not None: continue
        if name in ('SizeX', 'OriginalSizeX'): rec[4] = struct.pack('>i', sx)
        elif name in ('SizeY', 'OriginalSizeY'): rec[4] = struct.pack('>i', sy)
        elif name == 'Format': rec[4] = fname_bytes(w, fmt)
        elif name == 'MipTailBaseIdx': rec[4] = struct.pack('>i', max(tail, 0))
        keep.append(rec)
    keep.append(['NeverStream', 'BoolProperty', 0, b'', b'\x01'])
    if lod_bias: keep.append(['LODBias', 'IntProperty', 0, b'', struct.pack('>i', lod_bias)])
    out = bytearray(d3[:4]) + encode_props(w, keep)
    fixups = []
    out += struct.pack('>iiii', 0, 0, 0, 0)                               # SourceArt: empty (offset fixed below)
    fixups.append((len(out) - 4, len(out)))
    out += struct.pack('>i', len(mips))
    for m, mip in enumerate(mips):
        data = src.mip_bytes(first + m)
        if data is not None and mask: data = (red_to_alpha if mask == 'R' else alpha_to_grey)(data, fmt)
        if data is not None and colour_scale and tuple(colour_scale) != (1.0, 1.0, 1.0): data = scale_colour(data, fmt, colour_scale)
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


def clone_static_mic(w, p3, pk, mic, textures, cache, vectors=None):
    """serial data for a copy of MaterialInstanceConstant `mic` of package pk WITH its static permutation (a child
    instance of a static-permutation instance draws with the base material's default permutation instead: Dom's hair
    as a child of the classic Dom's masked hair drew the default material, white): its texture parameters in textures
    ({name: ref in w}) replaced, its names and object references (Parent, texture values; the permutation's
    uniform-expression textures) re-made as imports in w (the game finds the permutation's compiled shaders by its
    id, in pk's shader cache, which must be loaded). The permutation (Gears 3, 828): ... CompileErrors, a map, max
    texture dependency length, Id (GUID), user texcoords, uniform-expression textures (count + refs at 32); then
    the static parameters, whose switch entries (FName, value, override, GUID) name parameters. vectors: vector
    parameters ({name: (r, g, b, a)}) set on the copy (with the base material's ExpressionGUIDs, when pk holds it)"""
    d = pk.export_data(mic)
    recs, end = raw_props(pk, d, 4)
    if pk is p3: ref = lambda r: r                          # (an instance of the package being written: as it is)
    else: ref = lambda r: (import_object(w, p3, pk, r - 1, cache) if r > 0 else copy_import(w, p3, pk, r, cache) if r < 0 else 0)
    nm = lambda b: struct.pack('>ii', *w.name(pk.fname(*struct.unpack('>ii', b))))
    def elements(raw, texture):
        n, o, out = struct.unpack_from('>i', raw, 0)[0], 4, []
        for _ in range(n):
            el, o = raw_props(pk, raw, o)
            pname = next(pk.fname(*struct.unpack('>ii', x[4])) for x in el if x[0] == 'ParameterName')
            new = []
            for name, typ, index, extra, val in el:
                if extra: extra = nm(extra)
                if typ == 'NameProperty': val = nm(val)
                elif name == 'ParameterValue' and texture:
                    val = struct.pack('>i', textures[pname] if pname in textures else ref(struct.unpack('>i', val)[0]))
                new.append([name, typ, index, extra, val])
            out.append(encode_props(w, new))
        return struct.pack('>i', n) + b''.join(out)
    props = []
    for name, typ, index, extra, val in recs:
        if extra: extra = nm(extra)
        if name in ('ScalarParameterValues', 'VectorParameterValues', 'TextureParameterValues'):
            val = elements(val, name == 'TextureParameterValues')
        elif typ == 'ObjectProperty': val = struct.pack('>i', ref(struct.unpack('>i', val)[0]))
        elif typ == 'NameProperty': val = nm(val)
        props.append([name, typ, index, extra, val])
    if vectors:
        base = mic
        while base >= 0 and pk.class_name(pk.exports[base]['class']) == 'MaterialInstanceConstant':
            prs, _ = raw_props(pk, pk.export_data(base), 4)
            base = next((struct.unpack('>i', r[4])[0] for r in prs if r[0] == 'Parent'), 0) - 1
        guids = material_param_guids(pk, base) if base >= 0 else {}
        els = [encode_props(w, [['ParameterName', 'NameProperty', 0, b'', fname_bytes(w, n_)],
                                ['ParameterValue', 'StructProperty', 0, fname_bytes(w, 'LinearColor'), struct.pack('>4f', *v_)],
                                ['ExpressionGUID', 'StructProperty', 0, fname_bytes(w, 'Guid'), guids[n_]]])
               for n_, v_ in vectors.items() if n_ in guids]
        if els:
            old_ = next((r for r in props if r[0] == 'VectorParameterValues'), None)
            if old_ is None: props.insert(0, ['VectorParameterValues', 'ArrayProperty', 0, b'', struct.pack('>i', len(els)) + b''.join(els)])
            else: old_[4] = struct.pack('>i', struct.unpack_from('>i', old_[4], 0)[0] + len(els)) + old_[4][4:] + b''.join(els)
    tail = bytearray(d[end:])
    n = struct.unpack_from('>i', tail, 32)[0]
    for k in range(n):
        struct.pack_into('>i', tail, 36 + 4 * k, ref(struct.unpack_from('>i', tail, 36 + 4 * k)[0]))
    valid = lambda o: 0 < struct.unpack_from('>i', tail, o)[0] < len(pk.names) and struct.unpack_from('>i', tail, o + 4)[0] == 0
    for k in range(36 + 4 * n, len(tail) - 4, 4):                   # the static switch entries: count, then 32 bytes each
        c = struct.unpack_from('>i', tail, k)[0]
        if 0 < c < 32 and k + 4 + 32 * c <= len(tail) and all(valid(k + 4 + 32 * j) for j in range(c)):
            for j in range(c):
                o = k + 4 + 32 * j
                tail[o:o + 8] = nm(bytes(tail[o:o + 8]))
            break
    return d[:4] + encode_props(w, props) + bytes(tail)


def material_param_guids(p, mat):
    """{parameter name: ExpressionGUID bytes} of a Gears 3 Material export's parameter expressions (kept by the cook)"""
    out = {}
    for i, e in enumerate(p.exports):
        if e['outer'] != mat + 1 or 'Parameter' not in p.class_name(e['class']): continue
        try: recs, _ = raw_props(p, p.export_data(i), 4)
        except Exception: continue
        a = {r[0]: r[4] for r in recs}
        if 'ParameterName' in a and 'ExpressionGUID' in a: out[p.fname(*struct.unpack('>ii', a['ParameterName']))] = a['ExpressionGUID']
    return out


def param_mic(w, header, parent_ref, guids, scalars=None, vectors=None, textures=None):
    """serial data for a new MaterialInstanceConstant of a Gears 3 Material (parent_ref) setting scalar ({name: float})
    and vector ({name: (r, g, b, a)}) parameters, each with its ExpressionGUID from guids (material_param_guids);
    header: the bytes before the properties (a MaterialInstanceConstant's NetIndex). Properties only: the game draws
    it with the parent's compiled shaders."""
    def element(name, typ, extra, value):
        return encode_props(w, [['ParameterName', 'NameProperty', 0, b'', fname_bytes(w, name)],
                                ['ParameterValue', typ, 0, extra, value],
                                ['ExpressionGUID', 'StructProperty', 0, fname_bytes(w, 'Guid'), guids[name]]])
    props = [['Parent', 'ObjectProperty', 0, b'', struct.pack('>i', parent_ref)]]
    sc = [element(n, 'FloatProperty', b'', struct.pack('>f', v)) for n, v in (scalars or {}).items() if n in guids]
    vc = [element(n, 'StructProperty', fname_bytes(w, 'LinearColor'), struct.pack('>4f', *v)) for n, v in (vectors or {}).items() if n in guids]
    tc = [element(n, 'ObjectProperty', b'', struct.pack('>i', v)) for n, v in (textures or {}).items() if n in guids and v]
    if sc: props.append(['ScalarParameterValues', 'ArrayProperty', 0, b'', struct.pack('>i', len(sc)) + b''.join(sc)])
    if vc: props.append(['VectorParameterValues', 'ArrayProperty', 0, b'', struct.pack('>i', len(vc)) + b''.join(vc)])
    if tc: props.append(['TextureParameterValues', 'ArrayProperty', 0, b'', struct.pack('>i', len(tc)) + b''.join(tc)])
    return bytes(header) + encode_props(w, props)


def g1_referenced_textures(p, ref):
    """export indices of the textures a Gears 1 material reference's base Material lists (ReferencedTextures)"""
    if ref <= 0: return []
    mat = ref - 1
    try:
        for _ in range(8):
            if p.class_name(p.exports[mat]['class']) != 'MaterialInstanceConstant': break
            recs, _ = raw_props(p, p.export_data(mat), 4)
            par = next((struct.unpack('>i', r[4][:4])[0] for r in recs if r[0] == 'Parent'), 0)
            if par <= 0: return []
            mat = par - 1
        a = {n: v for n, t, k, v in levelinject.props_at(p, p.export_data(mat))[1]}
    except Exception:
        return []
    raw = a.get('ReferencedTextures')
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < 4: return []
    n = struct.unpack_from('>i', raw, 0)[0]
    return [r - 1 for r in struct.unpack_from(f'>{n}i', raw, 4) if r > 0 and p.class_name(p.exports[r - 1]['class']) == 'Texture2D']


def g1_vector_params(p, ref, depth=0):
    """{parameter name: (r, g, b, a)} of a Gears 1 material reference: a material instance's VectorParameterValues,
    then its parent's; a Material's VectorParameter expressions' defaults"""
    if ref <= 0 or depth > 8: return {}
    i = ref - 1
    cn = p.class_name(p.exports[i]['class'])
    out = {}
    if cn == 'Material':
        for k, e in enumerate(p.exports):
            if e['outer'] != ref or p.class_name(e['class']) != 'MaterialExpressionVectorParameter': continue
            try: _, pr, _ = levelinject.props_at(p, p.export_data(k))
            except Exception: continue
            a = {n: v for n, t, x, v in pr}
            if 'ParameterName' in a:
                out[a['ParameterName']] = struct.unpack('>4f', a['DefaultValue'][1][:16]) if 'DefaultValue' in a else (0.0, 0.0, 0.0, 0.0)
        return out
    try: recs, _ = raw_props(p, p.export_data(i), 4)
    except Exception: return out
    a = {r[0]: r[4] for r in recs}
    if 'VectorParameterValues' in a:
        v, o = a['VectorParameterValues'], 4
        for _ in range(struct.unpack_from('>i', v, 0)[0]):
            el, o = raw_props(p, v, o)
            e = {x[0]: x[4] for x in el}
            if 'ParameterName' in e and 'ParameterValue' in e:
                out[p.fname(*struct.unpack('>ii', e['ParameterName'][:8]))] = struct.unpack('>4f', e['ParameterValue'][:16])
    if 'Parent' in a:
        for k, x in g1_vector_params(p, struct.unpack('>i', a['Parent'][:4])[0], depth + 1).items(): out.setdefault(k, x)
    return out


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
