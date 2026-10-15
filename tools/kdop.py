"""Gears of War 3's compact kDOP collision tree (StaticMesh per-polygon collision): reader and builder.

Format (from sub_826CBBB0, the node test the game runs during collision checks, and checked on 341 shipped trees:
94,772 of 94,777 leaves contain their triangles, the rest are rounding at the tolerance):
  root box       the mesh's kDOP bounds (min float3, max float3)
  nodes          N 6-byte nodes, N a power of two (N = the next power of two >= ceil(triangles / 5)); node k holds
                 the boxes of BOTH its children, relative to its own box (min bytes 0-2, max bytes 3-5):
                   child A (index 2k+1): min = pmin + max(b - 128, 0) / 127 * extent
                                         max = pmax - max(c - 128, 0) / 127 * extent
                   child B (index 2k+2): the same with max(127 - b, 0) and max(127 - c, 0)
                 so one byte moves in one child's bound only (128/127 = neither). Child indices are clamped to
                 N - 1: that last entry is a sentinel (zero bytes) and any index >= N - 1 is a leaf.
  triangles      {u16 v0, v1, v2, material index}; a node's range splits in halves, the first child taking
                 count - count / 2: the triangles are stored in depth-first leaf order.
usage: python -P kdop.py <gears3 package>    (rebuilds every shipped tree from its triangles and checks it)"""
import struct
import numpy as np


def build(positions, triangles, max_per_leaf=5):
    """positions (n, 3) float; triangles [(v0, v1, v2, material)] -> (bounds 6 floats, node bytes, triangle bytes)"""
    pos = np.asarray(positions, dtype=np.float64)
    tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 4)
    t = len(tris)
    if t == 0:
        return (0.0,) * 6, b'', b''
    leaves = -(-t // max_per_leaf)
    n = 1
    while n < leaves: n *= 2
    tv = pos[tris[:, :3]]
    tlo, thi, cen = tv.min(1), tv.max(1), tv.mean(1)
    # every node's bounds are padded by PAD: a flat group of triangles (a floor) would otherwise have a slab of zero
    # thickness, which a line check (zero extent: bullets, the navmesh generator's ground traces) can miss while box
    # checks (pawns) still hit it
    PAD = 0.5
    tlo, thi = tlo - PAD, thi + PAD
    lo, hi = tlo.min(0), thi.max(0)
    # float32 bounds, widened by one ulp so the root contains everything after rounding
    lo32 = np.nextafter(lo.astype(np.float32), np.float32(-np.inf))
    hi32 = np.nextafter(hi.astype(np.float32), np.float32(np.inf))
    nodes = np.zeros((n, 6), np.uint8)
    order = []

    def rec(k, idx, plo, phi):
        if k >= n - 1:
            order.extend(idx.tolist()); return
        count = len(idx)
        first = count - count // 2
        if count > 1:
            axis = int(np.argmax(np.ptp(cen[idx], 0)))
            idx = idx[np.argsort(cen[idx, axis], kind='stable')]
        a_idx, b_idx = idx[:first], idx[first:]
        ext = phi - plo
        def inset(group):
            if len(group) == 0: return np.full(3, 127), np.full(3, 127)
            gl, gh = tlo[group].min(0), thi[group].max(0)
            with np.errstate(divide='ignore', invalid='ignore'):
                qmin = np.where(ext > 0, np.floor((gl - plo) / ext * 127), 0)
                qmax = np.where(ext > 0, np.floor((phi - gh) / ext * 127), 0)
            return np.clip(qmin, 0, 127), np.clip(qmax, 0, 127)
        amin, amax = inset(a_idx)
        bmin, bmax = inset(b_idx)
        byte = np.zeros(6)
        for j in range(3):            # one byte per bound: the child with the larger inset gets it
            byte[j] = 128 + amin[j] if amin[j] >= bmin[j] else 127 - bmin[j]
            byte[3 + j] = 128 + amax[j] if amax[j] >= bmax[j] else 127 - bmax[j]
        # a child that loses its inset is still inside the parent: keep the bytes conservative by recomputing
        nodes[k] = byte.astype(np.uint8)
        A, B = decode(nodes[k], plo, phi)
        rec(min(2 * k + 1, n - 1), a_idx, *A)
        rec(min(2 * k + 2, n - 1), b_idx, *B)

    rec(0, np.arange(t), lo32.astype(np.float64), hi32.astype(np.float64))
    tri_bytes = struct.pack(f'>{4 * t}H', *tris[order].flatten().tolist())
    return tuple(lo32.tolist()) + tuple(hi32.tolist()), nodes.tobytes(), tri_bytes


def decode(node, plo, phi):
    """the two child boxes of a node (as the game computes them, in float32)"""
    q = np.asarray(node, dtype=np.float32)
    plo, phi = np.asarray(plo, np.float32), np.asarray(phi, np.float32)
    ext = phi - plo
    a, c = q[:3], q[3:]
    A = (plo + np.maximum(a - 128, 0) / np.float32(127) * ext, phi - np.maximum(c - 128, 0) / np.float32(127) * ext)
    B = (plo + np.maximum(127 - a, 0) / np.float32(127) * ext, phi - np.maximum(127 - c, 0) / np.float32(127) * ext)
    return A, B


def check(positions, bounds, node_bytes, tri_bytes, tol=0.0):
    """(leaves, leaves whose triangles are inside their box)"""
    pos = np.asarray(positions, dtype=np.float64)
    nodes = np.frombuffer(node_bytes, np.uint8).reshape(-1, 6)
    n = len(nodes)
    t = len(tri_bytes) // 8
    if t == 0: return 0, 0
    tris = np.array(struct.unpack(f'>{4 * t}H', tri_bytes)).reshape(t, 4)
    tv = pos[tris[:, :3]]; tlo, thi = tv.min(1), tv.max(1)
    ok = tot = 0
    stack = [(0, 0, t, np.array(bounds[:3]), np.array(bounds[3:]))]
    while stack:
        k, start, count, plo, phi = stack.pop()
        if k >= n - 1:
            tot += 1
            ok += count == 0 or bool(np.all(tlo[start:start + count] >= plo - tol) and np.all(thi[start:start + count] <= phi + tol))
            continue
        A, B = decode(nodes[k], plo, phi)
        half = count // 2
        stack.append((min(2 * k + 1, n - 1), start, count - half, *A))
        stack.append((min(2 * k + 2, n - 1), start + count - half, half, *B))
    return tot, ok


if __name__ == '__main__':
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import upk, staticmesh
    p = upk.Package(sys.argv[1])
    same_n = trees = leaves = good = 0
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'StaticMesh': continue
        m = staticmesh.read(p, i)
        t = m['kdop_tris'][1]
        if t == 0: continue
        tris = np.array(struct.unpack(f'>{4 * t}H', m['kdop_tris'][2])).reshape(t, 4)
        pos = staticmesh.positions(m['lods'][0])
        bounds, nodes, tb = build(pos, tris.tolist())
        trees += 1
        same_n += len(nodes) // 6 == m['kdop_nodes'][1]
        tot, ok = check(pos, bounds, nodes, tb)
        leaves += tot; good += ok
    print(f'{trees} trees rebuilt: node count as shipped in {same_n}; leaves containing their triangles (no tolerance): {good}/{leaves}')
