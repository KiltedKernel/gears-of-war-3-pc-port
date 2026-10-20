"""Navmesh polygon merging (offline): wider polygons for a generated path mesh.

Gears 3's in-game generator (tools/trilogy.py generate_navmesh) leaves a converted level's path mesh cut into slivers:
in the prison's cell block 163 of 257 polygons are narrower than a pawn (68: twice the 34 radius). The path search
lets an entity through a polygon only when a corridor of its extent fits between the edge it enters by and the edge it
leaves by (FNavMeshEdgeBase::Supports sub_825C25B0 -> sub_8259D540), so a route that turns inside a sliver, or runs
along one, is refused (Dom's easy route stopped at the foot of the stairs: PATHERROR_NOPATHFOUND).

merge() joins neighbouring polygons while the union stays convex (in x, y; dents up to DENT deep filled), close to one
plane and within MAX_VERTS vertices, the narrowest polygons first, in rounds (Hertel-Mehlhorn): T-junctions are split
first so that neighbours share sides, and vertices left in the middle of a straight side are dropped afterwards. The mesh is the runtime dump's
(navmesh.read_dump): {'verts': [(pos, poly indices)], 'polys': [{verts, center, normal, box, box_valid, height}]}.
usage: python -P navmerge.py <dump> [out dump]  (prints the widths before and after)"""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAX_VERTS = 9           # the most a shipped Gears 3 polygon has (MP_GridlockG3)
PLANE_TOL = 10.0        # every vertex of a union this close (z) to its least-squares plane
NORMAL_DOT = 0.9        # and the two polygons' normals this close
DENT = 32.0             # a union's dents up to this deep (x, y) filled: the merged polygon is its convex hull, when
                        # no other polygon lies there (a pawn's centre, 34 from any wall, never goes into them)
BOX_BELOW = 15.0        # a polygon's box: 15 below its lowest vertex to its height above the highest (the generator's)


def width(pts):
    """the polygon's smallest extent over directions in x, y"""
    return min(np.ptp(pts[:, :2] @ np.array([np.cos(t), np.sin(t)])) for t in np.linspace(0, np.pi, 90, endpoint=False))


def _weld(V, polys, tol=0.5):
    """vertices at one position (within tol) made one; polygons' consecutive duplicates dropped"""
    key, remap, out = {}, [], []
    for p in V:
        k = tuple(np.round(p / tol).astype(np.int64))
        if k not in key: key[k] = len(out); out.append(p)
        remap.append(key[k])
    loops = []
    for q in polys:
        loop = [remap[v] for v in q]
        loop = [v for k, v in enumerate(loop) if v != loop[k - 1]]
        loops.append(loop)
    return np.array(out), loops


def _split_tjunctions(V, loops, tol_xy=0.75, tol_z=6.0):
    """each polygon side with another vertex lying on it (x, y within tol_xy, z within tol_z) split there"""
    lo, hi = V[:, :2].min(0), V[:, :2].max(0)
    out = []
    for loop in loops:
        new = []
        for k, a in enumerate(loop):
            b = loop[(k + 1) % len(loop)]
            new.append(a)
            A, B = V[a], V[b]; d = B[:2] - A[:2]; L = np.linalg.norm(d)
            if L < 1e-6: continue
            u = d / L
            rel = V[:, :2] - A[:2]
            t = rel @ u; off = np.abs(rel @ np.array([-u[1], u[0]]))
            zline = A[2] + (B[2] - A[2]) * np.clip(t / L, 0, 1)
            on = np.nonzero((t > tol_xy) & (t < L - tol_xy) & (off < tol_xy) & (np.abs(V[:, 2] - zline) < tol_z))[0]
            on = [v for v in on if v != a and v != b]
            new += sorted(on, key=lambda v: t[v])
        out.append(new)
    return out


def _union(la, lb):
    """the boundary loop of two polygons (vertex loops of one orientation) that share sides, or None when their union
    is not one simple loop"""
    sides = {}
    for loop in (la, lb):
        for k, a in enumerate(loop):
            sides[(a, loop[(k + 1) % len(loop)])] = sides.get((a, loop[(k + 1) % len(loop)]), 0) + 1
    shared = [s for s in sides if (s[1], s[0]) in sides]
    if not shared: return None
    keep = {s for s in sides if (s[1], s[0]) not in sides}
    nxt = {}
    for a, b in keep:
        if a in nxt: return None                      # touching at a vertex twice: not simple
        nxt[a] = b
    start = next(iter(nxt)); loop = [start]
    while True:
        v = nxt[loop[-1]]
        if v == start: break
        if v in loop or v not in nxt: return None
        loop.append(v)
    return loop if len(loop) == len(nxt) else None


def _simplify(V, loop, tol=0.75):
    """vertices in the middle of a straight side dropped (x, y within tol; z on the line within 2 units)"""
    changed = True
    while changed and len(loop) > 3:
        changed = False
        for k in range(len(loop)):
            p, c, n = V[loop[k - 1]], V[loop[k]], V[loop[(k + 1) % len(loop)]]
            d = n[:2] - p[:2]; L = np.linalg.norm(d)
            if L < 1e-6: continue
            off = abs(np.cross(d / L, c[:2] - p[:2]))
            t = np.dot(c[:2] - p[:2], d / L) / L
            if off < tol and 0 < t < 1 and abs(p[2] + (n[2] - p[2]) * t - c[2]) < 2.0:
                del loop[k]; changed = True; break
    return loop


def _convex(P, sign):
    """x, y convex (turns all of the loop's orientation, straight allowed)"""
    n = len(P)
    for k in range(n):
        a, b, c = P[k - 1][:2], P[k][:2], P[(k + 1) % n][:2]
        cr = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if cr * sign < -1e-3 * max(1.0, np.linalg.norm(b - a) * np.linalg.norm(c - b)): return False
    return True


def _hull(V, loop, sign):
    """the loop's convex hull (x, y; a sub-loop of its vertices, the loop's orientation) when every other vertex is
    within DENT of it, else None"""
    pts = sorted(set(loop), key=lambda v: (V[v][0], V[v][1]))
    cr = lambda o, a, b: (V[a][0] - V[o][0]) * (V[b][1] - V[o][1]) - (V[a][1] - V[o][1]) * (V[b][0] - V[o][0])
    lower, upper = [], []
    for v in pts:
        while len(lower) >= 2 and cr(lower[-2], lower[-1], v) <= 1e-6: lower.pop()
        lower.append(v)
    for v in reversed(pts):
        while len(upper) >= 2 and cr(upper[-2], upper[-1], v) <= 1e-6: upper.pop()
        upper.append(v)
    hull = lower[:-1] + upper[:-1]                     # counter-clockwise
    if sign < 0: hull = hull[::-1]
    H = V[hull][:, :2]
    for v in set(loop) - set(hull):
        p = V[v][:2]; best = 1e18
        for k in range(len(H)):
            a, b = H[k], H[(k + 1) % len(H)]; d = b - a; L2 = float(d @ d) or 1e-9
            t = min(1.0, max(0.0, float((p - a) @ d) / L2))
            best = min(best, float(np.linalg.norm(a + t * d - p)))
        if best > DENT: return None
    return hull


def _clip_area(P, Q):
    """area (x, y) of two convex polygons' intersection (Sutherland-Hodgman)"""
    out = [tuple(p) for p in P[:, :2]]
    if _area2(Q) < 0: Q = Q[::-1]
    for k in range(len(Q)):
        a, b = Q[k][:2], Q[(k + 1) % len(Q)][:2]
        side = lambda p: (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        inp, out = out, []
        for i in range(len(inp)):
            cur, prev = inp[i], inp[i - 1]
            sc, sp = side(cur), side(prev)
            if sc >= 0:
                if sp < 0: out.append(_cut(prev, cur, sp, sc))
                out.append(cur)
            elif sp >= 0: out.append(_cut(prev, cur, sp, sc))
        if not out: return 0.0
    return abs(_area2(np.array(out))) / 2


def _cut(p, q, sp, sq):
    t = sp / (sp - sq)
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def _area2(P):
    x, y = P[:, 0], P[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def _overlaps(Q, H, lo, hi):
    qlo, qhi = Q.min(0), Q.max(0)
    if qlo[0] >= hi[0] or qlo[1] >= hi[1] or qhi[0] <= lo[0] or qhi[1] <= lo[1]: return False
    if qlo[2] > hi[2] + 60 or qhi[2] < lo[2] - 60: return False
    return _clip_area(H, Q) > 1.0


def _planar(P):
    A = np.c_[P[:, 0], P[:, 1], np.ones(len(P))]
    coef, *_ = np.linalg.lstsq(A, P[:, 2], rcond=None)
    return np.abs(A @ coef - P[:, 2]).max() <= PLANE_TOL


def _normal(P):
    """Newell's normal, facing up"""
    n = np.zeros(3)
    for k in range(len(P)):
        a, b = P[k], P[(k + 1) % len(P)]
        n += ((a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]), (a[0] - b[0]) * (a[1] + b[1]))
    n /= np.linalg.norm(n) or 1.0
    return -n if n[2] < 0 else n


def merge(mesh, log=None):
    """the mesh (a dump's path mesh) with neighbouring polygons merged; a new dict of the same form"""
    if not mesh['polys']: return mesh
    V = np.array([v[0] for v in mesh['verts']], np.float64)
    V, loops = _weld(V, [q['verts'] for q in mesh['polys']])
    loops = _split_tjunctions(V, loops)
    sign = np.sign(sum(np.sign(_area2(V[l])) for l in loops)) or 1.0
    height = [q['height'] for q in mesh['polys']]
    normal = [np.array(q['normal'], np.float64) for q in mesh['polys']]
    alive = [True] * len(loops)
    wid = [width(V[l]) for l in loops]
    BL = np.array([V[l].min(0) for l in loops]); BH = np.array([V[l].max(0) for l in loops])
    merges = 0
    while True:
        # a round: the candidate pairs (sharing a side), the narrowest polygon's first, then the longest shared
        # length; a pair whose polygon changed earlier in the round waits for the next
        owner = {}
        for i, l in enumerate(loops):
            if not alive[i]: continue
            for k, a in enumerate(l): owner[(a, l[(k + 1) % len(l)])] = i
        cand = {}
        for (a, b), i in owner.items():
            j = owner.get((b, a))
            if j is None or j <= i: continue
            cand[(i, j)] = cand.get((i, j), 0.0) + float(np.linalg.norm(V[a][:2] - V[b][:2]))
        changed = set()
        for (i, j), _ in sorted(cand.items(), key=lambda kv: (min(wid[kv[0][0]], wid[kv[0][1]]), -kv[1])):
            if i in changed or j in changed: continue
            if np.dot(normal[i], normal[j]) < NORMAL_DOT: continue
            u = _union(loops[i], loops[j])
            if u is None: continue
            u = _simplify(V, u)
            if not _convex(V[u], sign):
                u = _hull(V, u, sign)
                if u is None or len(u) > MAX_VERTS: continue
                # nothing else under the filled dents (a polygon at about this height overlapping the hull)
                H = V[u]; lo, hi = H.min(0), H.max(0)
                near = np.nonzero((BL[:, 0] < hi[0]) & (BL[:, 1] < hi[1]) & (BH[:, 0] > lo[0]) & (BH[:, 1] > lo[1]) &
                                  (BL[:, 2] <= hi[2] + 60) & (BH[:, 2] >= lo[2] - 60))[0]
                if any(alive[k] and k not in (i, j) and _overlaps(V[loops[k]], H, lo, hi) for k in near): continue
            P = V[u]
            if len(u) > MAX_VERTS or not _planar(P): continue
            loops[i] = u; alive[j] = False; wid[i] = width(P); BL[i], BH[i] = P.min(0), P.max(0)
            height[i] = min(height[i], height[j]); normal[i] = _normal(P)
            merges += 1; changed.update((i, j))
        if not changed: break
    # the merged mesh: vertices used, each with its polygons
    keep = [i for i in range(len(loops)) if alive[i]]
    used = sorted({v for i in keep for v in loops[i]})
    new_index = {v: k for k, v in enumerate(used)}
    vpolys = [[] for _ in used]
    polys = []
    for n, i in enumerate(keep):
        loop = [new_index[v] for v in loops[i]]
        for v in loop: vpolys[v].append(n)
        P = V[loops[i]]
        lo, hi = P.min(0), P.max(0)
        old = mesh['polys'][i]
        polys.append({'verts': tuple(loop), 'center': tuple(P.mean(0)), 'normal': tuple(normal[i]),
                      'box': (lo[0], lo[1], lo[2] - BOX_BELOW, hi[0], hi[1], hi[2] + height[i]),
                      'box_valid': old['box_valid'], 'box_valid_raw': old.get('box_valid_raw', bytes([old['box_valid'], 0, 0, 0])),
                      'height': height[i]})
    if log: log(f'navmerge: {len(mesh["polys"])} polygons -> {len(polys)} ({merges} merges)')
    return {'verts': [(tuple(V[v]), tuple(vpolys[k])) for k, v in enumerate(used)], 'polys': polys}


if __name__ == '__main__':
    import navmesh
    meshes = navmesh.read_dump(sys.argv[1])
    for name, m in list(meshes.items()):
        if not name.endswith('.path') or not m['polys']: continue
        out = merge(m, print)
        for label, mm in (('before', m), ('after', out)):
            V = np.array([v[0] for v in mm['verts']])
            W = np.array([width(V[list(q['verts'])]) for q in mm['polys']])
            print(f'  {name} {label}: {len(W)} polygons, narrower than 68: {(W < 68).sum()}, than 34: {(W < 34).sum()}')
        meshes[name] = out
    if len(sys.argv) > 2: navmesh.write_dump(sys.argv[2], meshes)
