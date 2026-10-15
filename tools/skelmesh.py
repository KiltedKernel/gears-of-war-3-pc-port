"""Skeletal meshes (USkeletalMesh native data) of Gears of War 1 (package version 374) and Gears 3 (828): read, and
write in Gears 3's format (offline).

After the tagged properties (LODInfo), both games serialize:
  Bounds (FBoxSphereBounds, 7 floats), Materials (count + refs), Origin (3 floats), RotOrigin (3 ints),
  RefSkeleton (count + FMeshBone: Name, Flags, Orientation quat, Position, NumChildren, ParentIndex
               [+ BoneColor in 828]: 48 / 52 bytes), SkeletalDepth,
  LODModels (count + FStaticLODModel), then a name -> bone index map and (828) more.
FStaticLODModel, 374:
  Sections (count + {u16 MaterialIndex, u16 ChunkIndex, u32 BaseIndex, u16 NumTriangles}: 10 bytes),
  IndexBuffer (count + u16), ShadowIndices (count + u16), ActiveBoneIndices (count + u16),
  ShadowTriangleDoubleSided (count + u8), Chunks, Size, NumVertices, Edges (count + 16-byte edges),
  RequiredBones (count + u8), RawPointIndices (bulk: flags, count, size, offset, data),
  vertex buffer (count + 40-byte vertices: position 3f, TangentX/Y/Z packed, UV 2f, 4 bone indices, 4 weights)
FStaticLODModel, 828:
  Sections (count + {u16, u16, u32 BaseIndex, u32 NumTriangles, u8 TriangleSorting}: 13 bytes),
  index container (u32 NeedsCPUAccess, u8 DataTypeSize, bulk array: element size, count, data),
  ActiveBoneIndices, Chunks, Size, NumVertices, RequiredBones, RawPointIndices (bulk), NumTexCoords,
  GPU skin vertex buffer (NumTexCoords, bUseFullPrecisionUVs, bUsePackedPosition, MeshExtension, MeshOrigin,
  bulk array of vertices: TangentX, TangentZ, 4 bone indices, 4 weights, position (packed u32 or 3f),
  UVs (half or float pairs)), then the rest (see read_828).
A chunk: BaseVertexIndex, RigidVertices (count + ...), SoftVertices (count + ...), BoneMap (count + u16),
NumRigidVertices, NumSoftVertices, MaxBoneInfluences (cooked meshes: no rigid/soft vertex arrays).
usage: python -P skelmesh.py <package> [mesh name]"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk


class R:
    def __init__(self, d, o): self.d, self.o = d, o
    def i32(self): v = struct.unpack_from('>i', self.d, self.o)[0]; self.o += 4; return v
    def u16(self): v = struct.unpack_from('>H', self.d, self.o)[0]; self.o += 2; return v
    def u8(self): v = self.d[self.o]; self.o += 1; return v
    def raw(self, n): v = self.d[self.o:self.o + n]; self.o += n; return v
    def floats(self, n): v = struct.unpack_from(f'>{n}f', self.d, self.o); self.o += 4 * n; return v
    def arr(self, size):
        n = self.i32(); return self.raw(size * n), n


def props_end(p, d):
    if p.version >= 828:
        import objcopy
        return objcopy.parse828(d, 4, p.fname)[1]
    return p.properties(d, 4)[1]


def read_common(p, d):
    r = R(d, props_end(p, d))
    m = {'bounds': r.floats(7)}
    n = r.i32(); m['materials'] = [r.i32() for _ in range(n)]
    m['origin'] = r.floats(3); m['rot_origin'] = struct.unpack('>3i', r.raw(12))
    bsize = 52 if p.version >= 828 else 48
    n = r.i32(); bones = []
    for _ in range(n):
        b = r.raw(bsize)
        name = p.fname(*struct.unpack_from('>ii', b, 0))
        bones.append({'name': name, 'flags': struct.unpack_from('>I', b, 8)[0], 'quat': struct.unpack_from('>4f', b, 12),
                      'pos': struct.unpack_from('>3f', b, 28), 'children': struct.unpack_from('>i', b, 40)[0],
                      'parent': struct.unpack_from('>i', b, 44)[0], 'raw': b})
    m['bones'] = bones
    m['depth'] = r.i32()
    return m, r


def read_chunks(r):
    chunks = []
    for _ in range(r.i32()):
        c = {'base': r.i32()}
        if r.i32() or r.i32(): raise ValueError('chunk with rigid/soft vertex arrays (uncooked): not supported')
        n = r.i32(); c['bone_map'] = list(struct.unpack(f'>{n}H', r.raw(2 * n)))
        c['num_rigid'], c['num_soft'], c['max_influences'] = r.i32(), r.i32(), r.i32()
        chunks.append(c)
    return chunks


def bulk(r):
    flags, count, size, off = struct.unpack('>4i', r.raw(16))
    return r.raw(size) if not flags & 1 else b''


def read_374(p, i):
    d = p.export_data(i)
    m, r = read_common(p, d)
    lods = []
    for _ in range(r.i32()):
        L = {}
        L['sections'] = []
        for _ in range(r.i32()):
            mat, chunk, base, ntri = struct.unpack('>HHIH', r.raw(10))
            L['sections'].append({'material': mat, 'chunk': chunk, 'base': base, 'triangles': ntri})
        raw, n = r.arr(2); L['indices'] = list(struct.unpack(f'>{n}H', raw))
        r.arr(2)                                        # shadow indices
        raw, n = r.arr(2); L['active_bones'] = list(struct.unpack(f'>{n}H', raw))
        r.arr(1)                                        # shadow triangle double-sided flags
        L['chunks'] = read_chunks(r)
        L['size'], L['num_vertices'] = r.i32(), r.i32()
        r.arr(16)                                       # edges
        raw, n = r.arr(1); L['required_bones'] = list(raw)
        bulk(r)                                         # raw point indices
        n = r.i32(); vb = r.raw(40 * n)
        verts = []
        for k in range(n):
            v = vb[40 * k:40 * k + 40]
            verts.append({'pos': struct.unpack_from('>3f', v, 0), 'tx': v[12:16], 'ty': v[16:20], 'tz': v[20:24],
                          'uv': struct.unpack_from('>2f', v, 24), 'bones': v[32:36][::-1], 'weights': v[36:40][::-1]})
        L['verts'] = verts
        lods.append(L)
    m['lods'] = lods
    n = r.i32(); r.raw(12 * n)                          # name -> index map
    m['end'], m['size'] = r.o, len(d)
    return m


def read_828(p, i):
    """parses up to the end of the LOD models; the rest (name map, ...) is kept as raw bytes ('tail')"""
    d = p.export_data(i)
    m, r = read_common(p, d)
    lods = []
    m['lods_at'] = None
    for _ in range(r.i32()):
        L = {'sections': []}
        for _ in range(r.i32()):
            mat, chunk, base, ntri, sort = struct.unpack('>HHIIB', r.raw(13))
            L['sections'].append({'material': mat, 'chunk': chunk, 'base': base, 'triangles': ntri, 'sorting': sort})
        L['cpu_access'], L['index_size'] = r.i32(), r.u8()
        es, n = r.i32(), r.i32(); raw = r.raw(es * n)
        L['indices'] = list(struct.unpack(f'>{n}{"H" if es == 2 else "I"}', raw))
        raw, n = r.arr(2); L['active_bones'] = list(struct.unpack(f'>{n}H', raw))
        L['chunks'] = read_chunks(r)
        L['size'], L['num_vertices'] = r.i32(), r.i32()
        raw, n = r.arr(1); L['required_bones'] = list(raw)
        bulk(r)
        L['num_texcoords'] = r.i32()
        vb = {'num_texcoords': r.i32(), 'full_uvs': r.i32(), 'packed_pos': r.i32(),
              'extension': r.floats(3), 'origin': r.floats(3)}
        es, n = r.i32(), r.i32(); vb['stride'], vb['count'] = es, n
        vb['data'] = r.raw(es * n)
        L['vb'] = vb
        L['after_vb'] = r.o
        lods.append(L)
        break                                           # (the rest of a LOD is not decoded yet: one LOD read)
    m['lods'] = lods
    m['tail'] = d[r.o:]
    m['size'] = len(d)
    return m


if __name__ == '__main__':
    p = upk.Package(sys.argv[1])
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'SkeletalMesh' or e['name'].startswith('Default__'): continue
        if len(sys.argv) > 2 and e['name'] != sys.argv[2]: continue
        m = (read_828 if p.version >= 828 else read_374)(p, i)
        L = m['lods'][0]
        print(e['name'], f"{len(m['bones'])} bones, {len(m['lods'])} LODs read, {len(L['sections'])} sections,",
              f"{len(L['indices']) // 3} triangles, {L['num_vertices']} vertices", m.get('end'), m['size'])


# ---- writing (Gears 1 -> Gears 3) ----
def _normal(b):
    """FPackedNormal bytes (as stored: w, z, y, x) -> (x, y, z)"""
    return [(b[3] / 127.5) - 1, (b[2] / 127.5) - 1, (b[1] / 127.5) - 1]


def _det_sign(x, y, z):
    cx = (y[1] * z[2] - y[2] * z[1], y[2] * z[0] - y[0] * z[2], y[0] * z[1] - y[1] * z[0])
    return x[0] * cx[0] + x[1] * cx[1] + x[2] * cx[2]


def lod_info(w, sections):
    """LODInfo (one LOD) for a mesh with that many sections, as Gears 3's own meshes"""
    import objcopy
    none = struct.pack('>ii', *w.name('None'))
    el = objcopy.encode(w, [
        ['DisplayFactor', 'FloatProperty', 0, b'', struct.pack('>f', 0.0)],
        ['LODHysteresis', 'FloatProperty', 0, b'', struct.pack('>f', 0.02)],
        ['LODMaterialMap', 'ArrayProperty', 0, b'', struct.pack('>i', 0)],
        ['bEnableShadowCasting', 'ArrayProperty', 0, b'', struct.pack('>i', sections) + b'\x01' * sections],
        ['TriangleSortSettings', 'ArrayProperty', 0, b'', struct.pack('>i', sections) + none * sections],
        ['bDisableCompressions', 'BoolProperty', 0, b'', b'\x00']])
    return objcopy.encode(w, [['LODInfo', 'ArrayProperty', 0, b'', struct.pack('>i', 1) + el]])


def to_828(w, m, materials, template_tail):
    """Gears 3 SkeletalMesh serial data for a Gears 1 mesh (read_374; its first LOD), materials: destination refs
    (one per source material slot), template_tail: a Gears 3 mesh's read_828 'tail' (its constant part after the
    name map is reused). Returns (data, fixups) for upkwrite.add_export."""
    import numpy as np
    L = m['lods'][0]
    out = bytearray(struct.pack('>i', -1))              # NetIndex
    out += lod_info(w, len(L['sections']))
    out += struct.pack('>7f', *m['bounds'])
    out += struct.pack('>i', len(materials)) + b''.join(struct.pack('>i', r) for r in materials)
    out += struct.pack('>3f', *m['origin']) + struct.pack('>3i', *m['rot_origin'])
    out += struct.pack('>i', len(m['bones']))
    for b in m['bones']:
        out += struct.pack('>ii', *w.name(b['name'])) + struct.pack('>I', b['flags']) + struct.pack('>4f', *b['quat'])
        out += struct.pack('>3f', *b['pos']) + struct.pack('>ii', b['children'], b['parent']) + b'\xff\xff\xff\xff'
    out += struct.pack('>i', m['depth'])
    out += struct.pack('>i', 1)                         # LODs: the first
    out += struct.pack('>i', len(L['sections']))
    for s in L['sections']:
        out += struct.pack('>HHIIB', s['material'], s['chunk'], s['base'], s['triangles'], 0)
    idx = L['indices']
    wide = max(idx, default=0) > 0xFFFF
    out += struct.pack('>iB', 1, 4 if wide else 2)
    out += struct.pack('>ii', 4 if wide else 2, len(idx)) + struct.pack(f'>{len(idx)}{"I" if wide else "H"}', *idx)
    out += struct.pack('>i', len(L['active_bones'])) + struct.pack(f'>{len(L["active_bones"])}H', *L['active_bones'])
    out += struct.pack('>i', len(L['chunks']))
    for c in L['chunks']:
        out += struct.pack('>iii', c['base'], 0, 0) + struct.pack('>i', len(c['bone_map']))
        out += struct.pack(f'>{len(c["bone_map"])}H', *c['bone_map'])
        out += struct.pack('>iii', c['num_rigid'], c['num_soft'], c['max_influences'])
    out += struct.pack('>ii', 0, L['num_vertices'])
    out += struct.pack('>i', len(L['required_bones'])) + bytes(L['required_bones'])
    fixups = [(len(out) + 12, len(out) + 16)]           # raw point indices: empty, offset = where its data would be
    out += struct.pack('>4i', 0, 0, 0, 0)
    out += struct.pack('>i', 1)                         # NumTexCoords
    out += struct.pack('>iii', 1, 0, 0)                 # 1 UV set, half-float UVs, unpacked positions
    # MeshExtension 1, MeshOrigin 0 (as Gears 3's unpacked meshes): the shader still applies pos * ext + origin,
    # and the mesh's half size there blew a bird up 45 times
    out += struct.pack('>3f', 1.0, 1.0, 1.0) + struct.pack('>3f', 0.0, 0.0, 0.0)
    out += struct.pack('>ii', 32, len(L['verts']))
    for v in L['verts']:
        x, y, z = _normal(v['tx']), _normal(v['ty']), _normal(v['tz'])
        tz = bytes([255 if _det_sign(x, y, z) >= 0 else 0]) + v['tz'][1:]
        uv = np.array(v['uv'], dtype='>f2').tobytes()
        out += v['tx'] + tz + v['bones'][::-1] + v['weights'][::-1] + struct.pack('>3f', *v['pos']) + uv
    out += template_tail[:4]                            # (end of the LOD)
    names = [b['name'] for b in m['bones']]
    out += struct.pack('>i', len(names)) + b''.join(struct.pack('>ii', *w.name(n)) + struct.pack('>i', k) for k, n in enumerate(names))
    n = struct.unpack_from('>i', template_tail, 4)[0]
    out += template_tail[8 + 12 * n:]
    return bytes(out), fixups
