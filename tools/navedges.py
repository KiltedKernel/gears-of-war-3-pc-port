"""Edges for a generated Gears 3 path mesh (offline), by the rules read off MP_GridlockG3's shipped navmesh:
  - an edge joins two polygons along a stretch of their shared border (collinear in XY, close in Z); Poly0 < Poly1
  - per entity size S in SIZES: the stretches of that border whose XY distance to the obstacle mesh's walls (those
    overlapping the border's height band) is >= S; a stretch equal to a larger size's is not repeated (one edge, the
    largest size); EdgeLength holds S (34 / 60 / 90 in the shipped mesh)
  - the edge's two vertices are new vertices (no polygon list); EdgeCenter their midpoint; EdgePerpDir the unit
    horizontal perpendicular pointing into Poly1; the byte at +56 is 0, the one at +64 is 1
  - each polygon's edge list (its second array) holds the indices of its edges
On the shipped mesh: every edge's endpoints have clearance >= its size, 243 of 600 sampled endpoints exactly at it."""
import numpy as np

SIZES = (34.0, 60.0, 90.0)


def boundary_segments(V, polys):
    out = []
    for pi, q in enumerate(polys):
        vs = list(q['verts'])
        for k in range(len(vs)):
            out.append((pi, vs[k], vs[(k + 1) % len(vs)]))
    return out


def shared_borders(V, polys, tol_xy=1.0, tol_z=24.0):
    """[(poly a, poly b, p0, p1)] with a < b: overlapping collinear boundary stretches (3D points along a's side)"""
    segs = boundary_segments(V, polys)
    cell = 256.0
    grid = {}
    for si, (pi, a, b) in enumerate(segs):
        lo = np.minimum(V[a][:2], V[b][:2]) // cell; hi = np.maximum(V[a][:2], V[b][:2]) // cell
        for x in range(int(lo[0]), int(hi[0]) + 1):
            for y in range(int(lo[1]), int(hi[1]) + 1):
                grid.setdefault((x, y), []).append(si)
    seen, out = set(), []
    for cellsegs in grid.values():
        for i in cellsegs:
            pa, a0, a1 = segs[i]
            A0, A1 = V[a0], V[a1]
            d = A1[:2] - A0[:2]; L = np.linalg.norm(d)
            if L < 1e-3: continue
            u = d / L; n = np.array([-u[1], u[0]])
            for j in cellsegs:
                pb, b0, b1 = segs[j]
                if pb <= pa or (i, j) in seen: continue
                seen.add((i, j))
                B0, B1 = V[b0], V[b1]
                if abs(np.dot(B0[:2] - A0[:2], n)) > tol_xy or abs(np.dot(B1[:2] - A0[:2], n)) > tol_xy: continue
                t0, t1 = sorted((np.dot(B0[:2] - A0[:2], u), np.dot(B1[:2] - A0[:2], u)))
                lo, hi = max(0.0, t0), min(L, t1)
                if hi - lo < 1.0: continue
                P0 = A0 + (A1 - A0) * (lo / L); P1 = A0 + (A1 - A0) * (hi / L)
                # z agreement: b's side at the same points
                def zb(P):
                    tb = np.dot(P[:2] - B0[:2], B1[:2] - B0[:2]) / max(np.dot(B1[:2] - B0[:2], B1[:2] - B0[:2]), 1e-9)
                    return B0[2] + (B1[2] - B0[2]) * tb
                if abs(zb(P0) - P0[2]) > tol_z or abs(zb(P1) - P1[2]) > tol_z: continue
                out.append((pa, pb, P0, P1))
    return out


class Walls:
    def __init__(self, OV, opolys):
        w0, w1 = [], []
        for q in opolys:
            vs = OV[list(q['verts'])]
            for k in range(len(vs)):
                w0.append(vs[k]); w1.append(vs[(k + 1) % len(vs)])
        self.W0, self.W1 = np.array(w0), np.array(w1)
        self.zlo = np.minimum(self.W0[:, 2], self.W1[:, 2]); self.zhi = np.maximum(self.W0[:, 2], self.W1[:, 2])

    def clearance(self, pts, ignore_line=None):
        """XY distance from each point (n, 3) to the walls overlapping [z - 20, z + 100]; ignore_line (P0, P1):
        walls lying on that line are left out (a pylon's obstacle mesh lines its own boundary, also where it meets
        the next pylon)"""
        out = np.full(len(pts), 1e9)
        on_line = np.zeros(len(self.W0), bool)
        lines = [] if ignore_line is None else (ignore_line if isinstance(ignore_line, list) else [ignore_line])
        for P0, P1 in lines:
            d = P1[:2] - P0[:2]; n = np.array([-d[1], d[0]]) / (np.linalg.norm(d) + 1e-9)
            u = d / (np.linalg.norm(d) + 1e-9); L = np.linalg.norm(d)
            near = (np.abs((self.W0[:, :2] - P0[:2]) @ n) < 2) & (np.abs((self.W1[:, :2] - P0[:2]) @ n) < 2)
            # (within the border's extent, a little beyond)
            t0, t1 = (self.W0[:, :2] - P0[:2]) @ u, (self.W1[:, :2] - P0[:2]) @ u
            on_line |= near & (np.maximum(t0, t1) >= -64) & (np.minimum(t0, t1) <= L + 64)
        for k, pt in enumerate(pts):
            sel = (self.zhi >= pt[2] - 20) & (self.zlo <= pt[2] + 100) & ~on_line
            if not sel.any(): continue
            a, b = self.W0[sel, :2], self.W1[sel, :2]; d = b - a
            t = np.clip(((pt[:2] - a) * d).sum(1) / ((d ** 2).sum(1) + 1e-9), 0, 1)
            out[k] = np.sqrt(((pt[:2] - (a + d * t[:, None])) ** 2).sum(1)).min()
        return out


def intervals(P0, P1, walls, size, step=4.0):
    """parameter intervals [t0, t1] (0..1) of P0->P1 where the clearance is >= size, ends refined by bisection"""
    L = np.linalg.norm(P1[:2] - P0[:2])
    n = max(2, int(L / step) + 1)
    ts = np.linspace(0, 1, n)
    ok = walls.clearance(P0 + np.outer(ts, P1 - P0)) >= size
    out, k = [], 0
    def refine(t_bad, t_good):
        for _ in range(12):
            tm = (t_bad + t_good) / 2
            if walls.clearance((P0 + (P1 - P0) * tm)[None])[0] >= size: t_good = tm
            else: t_bad = tm
        return t_good
    while k < n:
        if not ok[k]: k += 1; continue
        j = k
        while j + 1 < n and ok[j + 1]: j += 1
        t0 = ts[k] if k == 0 else refine(ts[k - 1], ts[k])
        t1 = ts[j] if j == n - 1 else refine(ts[j + 1], ts[j])
        if (t1 - t0) * L >= 1.0: out.append((t0, t1))
        k = j + 1
    return out


def build(V, polys, OV, opolys, sizes=SIZES):
    """edges for a path mesh: (new vertices [(pos, ())], edges [dict as navmesh.read], per-poly edge lists)"""
    walls = Walls(OV, opolys)
    new_verts, edges = [], []
    poly_edges = [[] for _ in polys]
    base = len(V)
    for pa, pb, P0, P1 in shared_borders(V, polys):
        kept = []                                           # (t0, t1, size), the largest size first
        for s in sorted(sizes, reverse=True):
            for t0, t1 in intervals(P0, P1, walls, s):
                if any(abs(t0 - a) * 1 < 1e-3 and abs(t1 - b) < 1e-3 for a, b, _ in kept): continue
                kept.append((t0, t1, s))
        for t0, t1, s in kept:
            A = P0 + (P1 - P0) * t0; B = P0 + (P1 - P0) * t1
            d = B[:2] - A[:2]; d /= np.linalg.norm(d) + 1e-9
            perp = np.array([-d[1], d[0], 0.0])
            if np.dot(perp[:2], np.array(polys[pb]['center'][:2]) - (A[:2] + B[:2]) / 2) < 0: perp = -perp
            vi = base + len(new_verts)
            new_verts += [(tuple(A), ()), (tuple(B), ())]
            k = len(edges)
            edges.append({'verts': (vi, vi + 1), 'polys': (pa, pb), 'length': float(s), 'center': tuple((A + B) / 2),
                          'b56': 0, 'b64': 1, 'perp': tuple(perp)})
            poly_edges[pa].append(k); poly_edges[pb].append(k)
    return new_verts, edges, poly_edges


# ---- special edges from cover (read off MP_GridlockG3's shipped navmesh: 73 mantles, 337 cover slips) ----
# For a slot that allows it, a one-way edge from the polygon under the slot to the polygon under a destination point
# in the slot's frame (forward f, the perpendicular r = (-sin yaw, cos yaw), z +23):
#   mantle (bCanMantle)       f * 212                       class FNavMeshMantleEdge,    type byte 1, u252 0
#   slip left (bCanCoverSlip_Left)   f * 76 - r * 131      class FNavMeshCoverSlipEdge, type byte 2, u252 -1
#   slip right (bCanCoverSlip_Right) f * 75 + r * 117      class FNavMeshCoverSlipEdge, type byte 2, u252 +1
# The edge: both vertex pairs one new vertex at the slot, EdgeLength -1, EdgeCenter the slot, type byte, byte 255,
# EdgePerpDir 0, polygon references (pylon, 0xFFFF0000 | source) and (pylon, 0xFFFF0000 | destination), u16 0xFFFF,
# the CoverLink (actor reference), the slot index (RelItem), 0, the destination point (MoveDir), u252. Right after it
# its FNavMeshOneWayBackRefEdge: the same vertices, centre and length, type byte 6, references reversed; the edge is
# listed in the source polygon, the back reference in the destination polygon.
SPECIAL = (('mantle', 'FNavMeshMantleEdge', 1, 212.0, 0.0, 0),
           ('slip_l', 'FNavMeshCoverSlipEdge', 2, 76.0, -131.0, -1),
           ('slip_r', 'FNavMeshCoverSlipEdge', 2, 75.0, 117.0, 1))


def polygon_at(V, polys, pt, below=80.0, above=None):
    """index of the polygon containing pt in XY whose height band holds it (closest in Z), or None"""
    best, bz = None, 1e18
    for k, q in enumerate(polys):
        bx = q['box']
        if not (bx[0] - 1 <= pt[0] <= bx[3] + 1 and bx[1] - 1 <= pt[1] <= bx[4] + 1): continue
        if not (bx[2] - below <= pt[2] <= bx[5] + (q['height'] if above is None else above)): continue
        poly = V[list(q['verts'])][:, :2]
        xs, ys = poly[:, 0], poly[:, 1]; x2, y2 = np.roll(xs, -1), np.roll(ys, -1)
        cr = (ys > pt[1]) != (y2 > pt[1])
        with np.errstate(divide='ignore', invalid='ignore'):
            xi = (x2 - xs) * (pt[1] - ys) / (y2 - ys) + xs
        if np.count_nonzero(cr & (pt[0] < xi)) % 2 != 1: continue
        dz = abs(pt[2] - np.mean(V[list(q['verts'])][:, 2]))
        if dz < bz: best, bz = k, dz
    return best


def special_edges(V, polys, slots, pylon_ref, first_vertex, walls=None, min_clearance=40.0):
    """slots: [{'link': object ref, 'index': slot index, 'pos': world position, 'yaw': rotator units,
    'mantle', 'slip_l', 'slip_r': bools}] -> (new vertices, edges (each followed by its back reference),
    [(edge index, polygon)] list entries)"""
    import struct
    new_verts, edges, listing = [], [], []
    ref = lambda poly: struct.pack('>i', pylon_ref) + bytes(16) + struct.pack('>I', 0xFFFF0000 | poly)
    for sl in slots:
        pos = np.asarray(sl['pos'], np.float64)
        src = polygon_at(V, polys, pos)
        if src is None: continue
        yaw = sl['yaw'] * 2 * np.pi / 65536
        f = np.array([np.cos(yaw), np.sin(yaw), 0.0]); r = np.array([-np.sin(yaw), np.cos(yaw), 0.0])
        for key, cls, kind, fwd, side, u252 in SPECIAL:
            if not sl.get(key): continue
            dest = pos + f * fwd + r * side + np.array([0.0, 0.0, 23.0])
            dst = polygon_at(V, polys, dest)
            if dst is None or dst == src: continue
            # (the shipped mesh's destinations all have >= 46 units of wall clearance; the editor also traced, which
            # is not reproduced: about 1 in 6 generated edges is one the shipped mesh does not have)
            if walls is not None and walls.clearance(dest[None])[0] < min_clearance: continue
            vi = first_vertex + len(new_verts); new_verts.append((tuple(pos), ()))
            common = {'verts': (vi, vi), 'verts2': (vi, vi), 'polys': (0, 0), 'length': -1.0, 'center': tuple(pos),
                      'b64': 255, 'perp': (0.0, 0.0, 0.0), 'u172': 0xFFFF}
            k = len(edges)
            edges.append(dict(common, **{'class': cls, 'b56': kind, 'poly_refs': [ref(src), ref(dst)],
                                         'actor': struct.pack('>i', sl['link']) + bytes(16), 'rel_item': sl['index'],
                                         'object': 0, 'move_dir': tuple(dest), 'u252': u252}))
            edges.append(dict(common, **{'class': 'FNavMeshOneWayBackRefEdge', 'b56': 6, 'poly_refs': [ref(dst), ref(src)]}))
            listing += [(k, src), (k + 1, dst)]
    return new_verts, edges, listing


# ---- cross-pylon edges (read off SP_Bridge_01_S: 152 in 18 pylons) ----
# Stored in its own pylon's mesh and listed in its own polygon: EdgeStorageInfo class FNavMeshCrossPylonEdge, the
# OneWayBackRef layout: Vert0/Vert1 in its own mesh, the same two points' vertices in the other mesh (second pair),
# polygon references (own pylon, own polygon) and (other pylon, other polygon), type byte 0, byte 1, the size in
# EdgeLength; the other mesh holds the mirror image (146 of 152 shipped edges have one).
def cross_borders(Va, polys_a, Vb, polys_b):
    """[(poly in a, poly in b, P0, P1)]: shared border stretches between two meshes' polygons"""
    V = np.concatenate([Va, Vb]); na = len(Va)
    pb = [dict(q, verts=tuple(v + na for v in q['verts'])) for q in polys_b]
    out = []
    for x, y, P0, P1 in shared_borders(V, list(polys_a) + pb):
        if x < len(polys_a) <= y: out.append((x, y - len(polys_a), P0, P1))
    return out


def cross_edges(meshes, walls_of, sizes=SIZES):
    """meshes: {pylon: (V array, polys)}; walls_of: {pylon: Walls}. -> {pylon: (new vertices [(pos, ())],
    edges [(local edge dict, own poly)])} with vertex indices relative to the end of that pylon's vertex table
    (added by the caller: 'verts' and 'verts2' hold ('new', k) placeholders resolved after appending)"""
    out = {k: ([], []) for k in meshes}
    names = sorted(meshes)
    # every cross-pylon border: the pylons' own boundary walls on them are not walls
    all_lines = []
    for ia, a in enumerate(names):
        for b in names[ia + 1:]:
            all_lines += [(P0, P1) for _, _, P0, P1 in cross_borders(meshes[a][0], meshes[a][1], meshes[b][0], meshes[b][1])]
    for ia, a in enumerate(names):
        Va, Pa = meshes[a]
        lo_a, hi_a = Va.min(0), Va.max(0)
        for b in names[ia + 1:]:
            Vb, Pb = meshes[b]
            if (Vb.min(0) > hi_a + 50).any() or (Vb.max(0) < lo_a - 50).any(): continue
            for pa, pb, P0, P1 in cross_borders(Va, Pa, Vb, Pb):
                class Both:                                   # both pylons' walls, not those on the border
                    def clearance(self, pts):
                        return np.minimum(walls_of[a].clearance(pts, all_lines), walls_of[b].clearance(pts, all_lines))
                kept = []
                for s in sorted(sizes, reverse=True):
                    for t0, t1 in intervals(P0, P1, Both(), s):
                        if any(abs(t0 - x) < 1e-3 and abs(t1 - y) < 1e-3 for x, y, _ in kept): continue
                        kept.append((t0, t1, s))
                for t0, t1, s in kept:
                    A = P0 + (P1 - P0) * t0; B = P0 + (P1 - P0) * t1
                    first = {}                                # the two points' new vertices in each mesh
                    for side in (a, b):
                        first[side] = len(out[side][0]); out[side][0].extend([(tuple(A), ()), (tuple(B), ())])
                    for own, other, po, pt in ((a, b, pa, pb), (b, a, pb, pa)):
                        vo, vt = first[own], first[other]
                        d = B[:2] - A[:2]; d = d / (np.linalg.norm(d) + 1e-9)
                        perp = np.array([-d[1], d[0], 0.0])
                        Pt = meshes[other][1][pt]['center']
                        if np.dot(perp[:2], np.array(Pt[:2]) - (A[:2] + B[:2]) / 2) < 0: perp = -perp
                        out[own][1].append(({'class': 'FNavMeshCrossPylonEdge', 'verts': (('own', vo), ('own', vo + 1)),
                                             'verts2': (('other', other, vt), ('other', other, vt + 1)), 'polys': (0, 0),
                                             'length': float(s), 'center': tuple((A + B) / 2), 'b56': 0, 'b64': 1,
                                             'perp': tuple(perp), 'u172': 0xFFFF, 'pylons': (own, other),
                                             'poly_pair': (po, pt)}, po))
    return out
