"""StaticMesh from big-endian Xbox 360 UE3 packages (Gears of War 3: 828; Gears 2 and 1 to follow): a byte-exact
reader that must end on the export's last byte, and an OBJ/PNG export to look at the result. Offline analysis.

Gears 3 (828) layout after the tagged properties (fields are not aligned: some bools are one byte):
  Bounds        origin (3f), box extent (3f), sphere radius (f)
  BodySetup     object ref
  kDOP tree     bounds min/max (6f), nodes (bulk array: element size, count, data), triangles (bulk array of
                {u16 v0, v1, v2, material})
  InternalVersion i32
  ...           (see read_828: the fields in order, as the reader learns them)
  LODModels     count, each FStaticMeshRenderData:
                RawTriangles bulk header (16 bytes; empty when cooked), Elements (each ending in a 1-byte flag),
                PositionVertexBuffer {stride, count, bulk float3 (big-endian)},
                VertexBuffer {UV sets, stride, count, full-precision UVs, bulk {tangent X, tangent Z, UVs}},
                ColorVertexBuffer {stride, count[, bulk]}, NumVertices, IndexBuffer (bulk u16),
                WireframeIndexBuffer (bulk u16) ...
usage: python -P staticmesh.py <package> list [filter]
       python -P staticmesh.py <package> info <export>
       python -P staticmesh.py <package> obj <export> <out.obj>
       python -P staticmesh.py <package> check          (every StaticMesh must parse to its last byte)"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk


class Cursor:
    def __init__(self, d, o):
        self.d, self.o = d, o
    def i32(self):
        v = struct.unpack_from('>i', self.d, self.o)[0]; self.o += 4; return v
    def u32(self):
        v = struct.unpack_from('>I', self.d, self.o)[0]; self.o += 4; return v
    def u8(self):
        v = self.d[self.o]; self.o += 1; return v
    def f32(self, n=1):
        v = struct.unpack_from(f'>{n}f', self.d, self.o); self.o += 4 * n; return v if n > 1 else v[0]
    def raw(self, n):
        v = self.d[self.o:self.o + n]; self.o += n; return v
    def bulk(self):
        """a bulk-serialized TArray: element size, count, then the elements as they are"""
        size, count = self.i32(), self.i32()
        if not (0 <= size <= 256 and 0 <= count < 1 << 24): raise ValueError(f'bad bulk array {size} x {count} at {self.o - 8:#x}')
        return size, count, self.raw(size * count)
    def array(self, read):
        n = self.i32()
        if not 0 <= n < 1 << 20: raise ValueError(f'bad array count {n} at {self.o - 4:#x}')
        return [read() for _ in range(n)]


def read_mesh(p, d):
    """Gears 3 (828) and Gears 2 (575). 575 differs: no kDOP bounds (its nodes are 32 bytes, with their own
    bounds; 828's are 6-byte compact nodes under one bounding box), 4 bytes after InternalVersion instead of 12,
    no flag byte after each element."""
    v = p.version
    props, o = p.properties(d)
    c = Cursor(d, o)
    m = {'props': {n: v for n, t, i, v in props}, 'version': v}
    m['bounds'] = c.f32(7)
    m['body_setup'] = c.i32()
    m['kdop_bounds'] = c.f32(6) if v >= 828 else None
    m['kdop_nodes'] = c.bulk()
    m['kdop_tris'] = c.bulk()
    m['internal_version'] = c.i32()
    if v >= 828:
        m['name_array'] = c.array(lambda: (c.i32(), c.i32()))       # TArray<FName> (tags?), empty so far
        m['float_array'] = c.array(lambda: c.f32())                 # TArray<float>: empty, or one value (convoy meshes)
        m['unknown_after_version'] = c.i32()                        # 0 so far
    else: m['name_array'] = c.array(lambda: (c.i32(), c.i32()))  # 575: a TArray<FName> (tags?)
    lods = []
    for _ in range(c.i32()):
        lod = {}
        lod['raw_triangles'] = c.raw(16)               # bulk data header, empty when cooked
        def element():
            e = {'material': c.i32(), 'collision': c.i32(), 'old_collision': c.i32(), 'shadow': c.i32(),
                 'first_index': c.i32(), 'triangles': c.i32(), 'min_vertex': c.i32(), 'max_vertex': c.i32(),
                 'material_index': c.i32()}
            e['fragments'] = c.array(lambda: (c.i32(), c.i32()))
            if v >= 828: e['flag'] = c.u8()            # one byte per element (0 so far)
            return e
        lod['elements'] = c.array(element)
        stride, count = c.i32(), c.i32()
        lod['positions'] = (stride, count, c.bulk())
        uvs, vstride, vcount, full = c.i32(), c.i32(), c.i32(), c.i32()
        lod['vertices'] = (uvs, vstride, vcount, full, c.bulk())
        cstride, ccount = c.i32(), c.i32()
        lod['colors'] = (cstride, ccount, c.bulk() if ccount or v < 828 else None)   # 575: the array even when empty
        lod['num_vertices'] = c.i32()
        lod['indices'] = c.bulk()
        lod['wireframe'] = c.bulk()
        if v < 828:                                    # 575: mesh edges (16-byte FMeshEdge) and a per-triangle byte
            lod['edges'] = c.bulk()
            lod['shadow_double_sided'] = c.raw(c.i32())
        lods.append(lod)
    m['lods'] = lods
    m['after_lods'] = c.i32()                          # 1 so far
    m['thumbnail_angle'] = (c.i32(), c.i32(), c.i32())
    m['thumbnail_distance'] = c.f32()
    n = c.i32(); m['high_res_source_name'] = c.raw(n)   # FString, empty when cooked
    m['high_res_source_crc'] = c.u32()
    if v < 828:                                        # 575 ends here
        m['tail'] = d[c.o:]; m['tail_offset'] = c.o
        return m
    m['lighting_guid'] = c.raw(16)
    m['vertex_position_version'] = c.i32()
    m['streaming_texture_factors'] = c.array(lambda: c.f32())
    m['last'] = c.i32()                                # 1 so far
    m['tail'] = d[c.o:]
    m['tail_offset'] = c.o
    return m


def read_374(p, d):
    """Gears 1 (374): kDOP nodes and triangles are plain arrays (36-byte nodes {min, max, leaf, two u32},
    triangles of four u32), no tags, 7-int elements, and one interleaved vertex buffer: UV sets, stride, count,
    then an array (its own count) of {position, 4 bytes, tangent X/Y/Z packed, UVs as float2 per set}; u16 index array."""
    props, o = p.properties(d)
    c = Cursor(d, o)
    m = {'props': {n: v for n, t, i, v in props}, 'version': 374}
    m['bounds'] = c.f32(7)
    m['body_setup'] = c.i32()
    m['kdop_nodes'] = c.array(lambda: c.raw(36))
    m['kdop_tris'] = c.array(lambda: (c.u32(), c.u32(), c.u32(), c.u32()))
    m['internal_version'] = c.i32()
    lods = []
    for _ in range(c.i32()):
        lod = {}
        lod['raw_triangles'] = c.raw(16)
        lod['elements'] = c.array(lambda: dict(zip(('material', 'collision', 'old_collision', 'first_index', 'triangles',
                                                    'min_vertex', 'max_vertex'), (c.i32() for _ in range(7)))))
        uvs, stride, count = c.i32(), c.i32(), c.i32()
        n = c.i32()                                    # the vertex array's own count
        if not 0 <= n < 1 << 20 or stride != 28 + 8 * uvs: raise ValueError(f'vertex buffer {uvs} UVs, stride {stride}, {n} at {c.o:#x}')
        lod['interleaved'] = (uvs, stride, n, c.raw(stride * n))
        lod['num_vertices'] = c.i32()
        n = c.i32(); lod['indices'] = (2, n, c.raw(2 * n))
        n = c.i32(); lod['wireframe'] = (2, n, c.raw(2 * n))
        n = c.i32(); lod['edges'] = c.raw(16 * n)
        n = c.i32(); lod['shadow_double_sided'] = c.raw(n)
        lod['unknown_end'] = c.i32()
        lods.append(lod)
    m['lods'] = lods
    m['after_lods'] = c.i32()
    m['thumbnail_angle'] = (c.i32(), c.i32(), c.i32())
    m['tail'] = d[c.o:]; m['tail_offset'] = c.o
    return m


def positions(lod):
    if 'interleaved' in lod:
        uvs, stride, n, data = lod['interleaved']
        return [struct.unpack_from('>3f', data, stride * k) for k in range(n)]
    size, count, data = lod['positions'][2]
    return [struct.unpack_from('>3f', data, 12 * k) for k in range(count)]


def triangles(lod):
    size, count, data = lod['indices']
    idx = struct.unpack(f'>{count}H', data)
    return [idx[k:k + 3] for k in range(0, count - 2, 3)]


def read(p, i):
    d = p.export_data(i)
    if p.version in (575, 828): return read_mesh(p, d)
    if p.version == 374: return read_374(p, d)
    raise NotImplementedError(f'StaticMesh version {p.version}')


if __name__ == '__main__':
    p = upk.Package(sys.argv[1])
    cmd = sys.argv[2] if len(sys.argv) > 2 else 'list'
    meshes = [i for i, e in enumerate(p.exports) if p.class_name(e['class']) == 'StaticMesh']
    if cmd == 'list':
        flt = sys.argv[3].lower() if len(sys.argv) > 3 else ''
        for i in meshes:
            if flt in p.path(i).lower(): print(f"{i:6} {p.exports[i]['size']:8} {p.path(i)}")
    elif cmd == 'info':
        m = read(p, int(sys.argv[3]))
        for k, v in m.items():
            if k != 'lods': print(f'  {k}: {v if not isinstance(v, bytes) or len(v) < 64 else f"{len(v)} bytes"}')
        for n, lod in enumerate(m['lods']):
            vb = lod.get('vertices') or lod['interleaved']
            print(f"  LOD {n}: {len(positions(lod))} vertices, {lod['indices'][1] // 3} triangles, "
                  f"{len(lod['elements'])} elements, UV sets {vb[0]}, stride {vb[1]}")
    elif cmd == 'obj':
        m = read(p, int(sys.argv[3])); lod = m['lods'][0]
        with open(sys.argv[4], 'w') as f:
            for v in positions(lod): f.write('v %g %g %g\n' % v)
            for t in triangles(lod): f.write('f %d %d %d\n' % (t[0] + 1, t[1] + 1, t[2] + 1))
        print(f"{len(positions(lod))} vertices, {len(triangles(lod))} triangles -> {sys.argv[4]}")
    elif cmd == 'check':
        ok, bad, tails = 0, 0, {}
        for i in meshes:
            try:
                m = read(p, i)
                tails.setdefault(len(m['tail']), []).append(i); ok += 1
            except Exception as e:
                bad += 1
                if bad <= 5: print('  fail', i, p.path(i), e)
        print(f'{ok} parsed, {bad} failed; tail lengths (bytes after the LODs): '
              + ', '.join(f'{n}: {len(v)}' for n, v in sorted(tails.items(), key=lambda x: -len(x[1]))[:12]))
