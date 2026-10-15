"""Convert a StaticMesh from Gears of War 1 (374) or 2 (575) into Gears of War 3's serialization (828), on a Gears 3
template mesh that supplies what belongs to Gears 3's side: the tagged properties, the materials (by element), the
body setup and the thumbnail/lighting/streaming fields. Offline, from the player's own discs.

What is converted (LOD 0):
  positions        float3, as they are (all versions big-endian)
  tangents         packed normals are stored byte-reversed (w z y x). Gears 3 keeps TangentX and TangentZ, with the
                   binormal sign in TangentZ.w (255 = +1, 0 = -1). Gears 2 stores the same two plus a constant
                   FFFFFFFF word (dropped). Gears 1 stores X, Y and Z: the sign is computed from Y.
  UVs              half floats (Gears 2/3); Gears 1's float UVs are converted. At least as many UV sets as the
                   template's LightMapCoordinateIndex needs (UV 0 repeated), so a lightmap channel exists.
  indices          u16 triangle list and wireframe list, as they are
  elements         first index, triangle count, vertex range from the source; material and collision flags from the
                   template's element of the same index (its last one when the source has more)
  collision        a compact kDOP tree built over the triangles (tools/kdop.py); no body setup (the template's simple
                   collision would replace the triangles)
usage: python -P meshconv.py <gears3 package> <template mesh path> <source package> <source mesh path> [out.bin]
       (checks the result by reading it back with staticmesh.py and comparing the geometry)"""
import os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, staticmesh, kdop


def be_i32(*v): return struct.pack(f'>{len(v)}i', *v)
def be_f32(*v): return struct.pack(f'>{len(v)}f', *v)
def bulk(size, data): return be_i32(size, len(data) // size if size else 0) + data


def source_vertices(m):
    """(positions float3 BE bytes, TangentX bytes (n,4), TangentZ bytes with sign (n,4), UVs half BE (n, uvs, 2), n)"""
    lod = m['lods'][0]
    if 'interleaved' in lod:                                       # Gears 1
        uvs, stride, n, data = lod['interleaved']
        a = np.frombuffer(data, dtype=np.uint8).reshape(n, stride)
        pos = a[:, 0:12].copy()
        tx, ty, tz = a[:, 16:20].copy(), a[:, 20:24], a[:, 24:28].copy()
        def vec(t): return t[:, [3, 2, 1]].astype(np.float32) / 127.5 - 1      # bytes w z y x -> x y z
        sign = np.einsum('ij,ij->i', np.cross(vec(tz), vec(tx)), vec(ty)) >= 0
        tz[:, 0] = np.where(sign, 255, 0)
        uv = np.frombuffer(a[:, 28:28 + 8 * uvs].tobytes(), dtype='>f4').reshape(n, uvs, 2)
        uvh = np.clip(np.nan_to_num(uv), -65504, 65504).astype('>f2')   # (a few source light map UVs are garbage, 1e13)
        return pos, tx, tz, uvh, n
    stride, count, (bs, bc, bd) = lod['positions']
    pos = np.frombuffer(bd, dtype=np.uint8).reshape(count, 12).copy()
    uvs, vstride, vcount, full, (es, ec, ed) = lod['vertices']
    if full: raise NotImplementedError('full-precision UVs')
    a = np.frombuffer(ed, dtype=np.uint8).reshape(ec, es)
    nt = 8 if m['version'] >= 828 else 12                          # Gears 2: TangentX, TangentZ, FFFFFFFF
    tx, tz = a[:, 0:4].copy(), a[:, 4:8].copy()
    uvh = np.frombuffer(a[:, nt:nt + 4 * uvs].tobytes(), dtype='>f2').reshape(ec, uvs, 2)
    return pos, tx, tz, uvh, count


def to_828(p3, i3, src, materials=None):
    """serial data for Gears 3 export i3 (the template) holding mesh src (parsed by staticmesh.read); materials:
    an object ref per source element (default: the template's)"""
    d3 = p3.export_data(i3)
    tpl = staticmesh.read(p3, i3)
    props_end = p3.properties(d3)[1]
    pos, tx, tz, uvh, n = source_vertices(src)
    need_uvs = int(tpl['props'].get('LightMapCoordinateIndex', 0)) + 1
    if uvh.shape[1] < need_uvs:
        uvh = np.concatenate([uvh] + [uvh[:, :1]] * (need_uvs - uvh.shape[1]), 1)
    uvs = uvh.shape[1]
    lod = src['lods'][0]
    out = bytearray(d3[:props_end])
    out += be_f32(*src['bounds'])
    # no body setup: the template's is ITS simple collision (a box for a pillar), which UE3 prefers over the
    # triangles; without one, collision uses the kDOP tree built below (per polygon)
    out += be_i32(0)
    # collision: a compact kDOP tree over the triangles (tools/kdop.py), material index = element
    idx = struct.unpack(f'>{lod["indices"][1]}H', lod['indices'][2])
    tris = []
    for k, e in enumerate(lod['elements']):
        f = e['first_index']
        tris += [(idx[f + 3 * j], idx[f + 3 * j + 1], idx[f + 3 * j + 2], k) for j in range(e['triangles'])]
    p = np.frombuffer(pos.tobytes(), dtype='>f4').reshape(n, 3)
    bounds, nodes, tri_bytes = kdop.build(p, tris)
    if not tris: bounds = (*p.min(0), *p.max(0))
    out += be_f32(*bounds)
    out += be_i32(6, len(nodes) // 6) + nodes + be_i32(8, len(tri_bytes) // 8) + tri_bytes
    out += be_i32(18)                                              # InternalVersion
    out += be_i32(0) + be_i32(0) + be_i32(0)                       # name array, float array, int
    out += be_i32(1)                                               # one LOD
    out += tpl['lods'][0]['raw_triangles']
    tels = tpl['lods'][0]['elements']
    out += be_i32(len(lod['elements']))
    for k, e in enumerate(lod['elements']):
        t = tels[min(k, len(tels) - 1)]
        mat = materials[k] if materials and materials[k] else t['material']
        out += be_i32(mat, t['collision'], t['old_collision'], t['shadow'], e['first_index'], e['triangles'],
                      e['min_vertex'], e['max_vertex'], min(k, len(tels) - 1))
        out += be_i32(1, e['first_index'], e['triangles']) + bytes([0])          # one fragment, flag
    out += be_i32(12, n) + bulk(12, pos.tobytes())
    vstride = 8 + 4 * uvs
    verts = np.concatenate([tx, tz, np.frombuffer(uvh.tobytes(), dtype=np.uint8).reshape(n, 4 * uvs)], 1)
    out += be_i32(uvs, vstride, n, 0) + bulk(vstride, verts.tobytes())
    out += be_i32(0, 0)                                            # no vertex colours
    out += be_i32(n)
    out += bulk(2, lod['indices'][2]) + bulk(2, lod['wireframe'][2])
    t = tpl
    out += be_i32(t['after_lods'], *t['thumbnail_angle']) + be_f32(t['thumbnail_distance'])
    out += be_i32(len(t['high_res_source_name'])) + t['high_res_source_name'] + struct.pack('>I', t['high_res_source_crc'])
    out += t['lighting_guid'] + be_i32(t['vertex_position_version'], len(t['streaming_texture_factors']))
    out += be_f32(*t['streaming_texture_factors']) + be_i32(t['last'])
    return bytes(out)


def find(p, path):
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) == 'StaticMesh' and p.path(i).lower() == path.lower(): return i
    raise KeyError(path)


if __name__ == '__main__':
    p3 = upk.Package(sys.argv[1]); i3 = find(p3, sys.argv[2])
    ps = upk.Package(sys.argv[3]); src = staticmesh.read(ps, find(ps, sys.argv[4]))
    upk.E = '>'
    data = to_828(p3, i3, src)
    # read it back as Gears 3 data
    class One:
        version, names = 828, p3.names
        fname, properties = p3.fname, p3.properties
        def export_data(self, i): return data
    back = staticmesh.read_mesh(One(), data)
    assert not back['tail'], f"{len(back['tail'])} bytes left"
    a, b = staticmesh.positions(src['lods'][0]), staticmesh.positions(back['lods'][0])
    ta, tb = staticmesh.triangles(src['lods'][0]), staticmesh.triangles(back['lods'][0])
    print(f'{len(data)} bytes; read back: {len(b)} vertices ({"same" if a == b else "DIFFERENT"}), '
          f'{len(tb)} triangles ({"same" if ta == tb else "DIFFERENT"}), {len(back["lods"][0]["elements"])} elements, '
          f'UV sets {back["lods"][0]["vertices"][0]}')
    if len(sys.argv) > 5: open(sys.argv[5], 'wb').write(data)
