"""Physics floors for a converted level: boxes under its walkable surfaces, for PhysX.

Gears 3 builds a level mesh's PhysX shape from collision data cooked into the level when the game was made; on the
console nothing is cooked at load time, so the converted meshes (merged chunks, BSP) have no physics shape at all and
ragdolls, gibs and dropped weapons fell through the floors. Box elements of a mesh's RB_BodySetup need no cooking:
PhysX makes a box from its size. floor_boxes turns a level's upward-facing triangles into such boxes:

- flat floors (normal z above FLAT): rasterised on a CELL-unit grid per height (to HEIGHT_STEP units) and merged into
  rectangles, THICK deep under the surface;
- slopes and steps' faces (normal z above WALKABLE): one box per triangle, in the triangle's plane, around it.

body_setup writes the RB_BodySetup (828) holding them."""
import struct

import numpy as np

CELL = 32.0             # grid size for flat floors (units)
HEIGHT_STEP = 4.0       # flat floors at heights within this are merged
THICK = 24.0            # box depth under the surface
FLAT = 0.985            # normal z of a flat floor
WALKABLE = 0.6          # normal z of a surface given boxes at all


def floor_boxes(tris):
    """tris (n, 3, 3) world triangles -> [(centre (3,), axes (3, 3): rows x y z, size (3,): full lengths)]"""
    tris = np.asarray(tris, np.float64)
    if not len(tris): return []
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ok = ln > 1e-6
    tris, n, ln = tris[ok], n[ok], ln[ok]
    n = n / ln[:, None]
    n[n[:, 2] < 0] *= -1                       # (both windings: up is up)
    boxes = []
    flat = n[:, 2] >= FLAT
    cells = {}                                 # height step -> set of (ix, iy)
    for t in tris[flat]:
        lo, hi = t[:, :2].min(0), t[:, :2].max(0)
        ix = np.arange(int(np.floor(lo[0] / CELL)), int(np.floor(hi[0] / CELL)) + 1)
        iy = np.arange(int(np.floor(lo[1] / CELL)), int(np.floor(hi[1] / CELL)) + 1)
        gx, gy = np.meshgrid((ix + 0.5) * CELL, (iy + 0.5) * CELL)
        px, py = gx.ravel(), gy.ravel()
        (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = t
        d = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(d) < 1e-9: continue
        a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / d
        b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / d
        c = 1 - a - b
        inside = (a >= -1e-6) & (b >= -1e-6) & (c >= -1e-6)
        if not inside.any():                   # (a thin sliver: its centre's cell)
            cx, cy = t[:, :2].mean(0)
            px, py, a, b, c, inside = np.array([cx]), np.array([cy]), np.array([1 / 3]), np.array([1 / 3]), np.array([1 / 3]), np.array([True])
        z = a * z0 + b * z1 + c * z2
        for x_, y_, z_ in zip(px[inside], py[inside], z[inside]):
            cells.setdefault(int(round(z_ / HEIGHT_STEP)), set()).add((int(np.floor(x_ / CELL)), int(np.floor(y_ / CELL))))
    for zq, cs in cells.items():
        top = zq * HEIGHT_STEP
        for (x0_, y0_, x1_, y1_) in rectangles(cs):
            w, h = (x1_ - x0_ + 1) * CELL, (y1_ - y0_ + 1) * CELL
            centre = np.array([(x0_ * CELL + w / 2), (y0_ * CELL + h / 2), top - THICK / 2])
            boxes.append((centre, np.eye(3), np.array([w, h, THICK])))
    for t, nz in zip(tris[~flat], n[~flat]):
        if nz[2] < WALKABLE: continue
        e = [t[1] - t[0], t[2] - t[1], t[0] - t[2]]
        u = max(e, key=np.linalg.norm); u = u - nz * np.dot(u, nz); u /= np.linalg.norm(u)
        v = np.cross(nz, u)
        c = t.mean(0)
        q = t - c
        us, vs = q @ u, q @ v
        size = np.array([us.max() - us.min(), vs.max() - vs.min(), THICK])
        centre = c + u * (us.max() + us.min()) / 2 + v * (vs.max() + vs.min()) / 2 - nz * THICK / 2
        boxes.append((centre, np.stack([u, v, nz]), np.maximum(size, 1.0)))
    return boxes


def rectangles(cells):
    """a set of grid cells (ix, iy) -> covering rectangles (x0, y0, x1, y1), inclusive: runs along x per row, then
    equal runs of consecutive rows merged"""
    rows = {}
    for x, y in cells: rows.setdefault(y, []).append(x)
    runs = {}                                  # y -> [(x0, x1)]
    for y, xs in rows.items():
        xs.sort()
        out, s = [], xs[0]
        for a_, b_ in zip(xs, xs[1:] + [None]):
            if b_ != a_ + 1: out.append((s, a_)); s = b_
        runs[y] = out
    open_, done = {}, []                       # (x0, x1) -> (y0, last y) of a rectangle still growing
    for y in sorted(runs):
        cur = set(runs[y])
        for key in list(open_):
            if key not in cur or open_[key][1] != y - 1:
                done.append((key[0], open_[key][0], key[1], open_[key][1])); del open_[key]
        for key in cur:
            open_[key] = (open_[key][0], y) if key in open_ else (y, y)
    done += [(k[0], v[0], k[1], v[1]) for k, v in open_.items()]
    return done


def body_setup(w, name_of, prelude, boxes):
    """RB_BodySetup serial data (828) with the boxes as AggGeom.BoxElems; name_of(s) -> (index, number) FName in w;
    prelude: the object's leading bytes (its NetIndex)"""
    def tag(name, typ, size, extra=b''):
        return struct.pack('>ii', *name_of(name)) + struct.pack('>ii', *name_of(typ)) + struct.pack('>ii', size, 0) + extra
    none = struct.pack('>ii', *name_of('None'))
    elems = b''
    for centre, axes, size in boxes:
        m = np.eye(4); m[:3, :3] = axes; m[3, :3] = centre
        tm = struct.pack('>16f', *m.flatten())
        e = tag('TM', 'StructProperty', 64, struct.pack('>ii', *name_of('Matrix'))) + tm
        for k, v in zip('XYZ', size):
            e += tag(k, 'FloatProperty', 4) + struct.pack('>f', float(v))
        elems += e + none
    arr = struct.pack('>i', len(boxes)) + elems
    agg = tag('BoxElems', 'ArrayProperty', len(arr)) + arr + none
    props = tag('PreCachedPhysDataVersion', 'IntProperty', 4) + struct.pack('>i', 0x02080491)
    props += tag('AggGeom', 'StructProperty', len(agg), struct.pack('>ii', *name_of('KAggregateGeom'))) + agg
    return prelude + props + none + struct.pack('>i', 0)    # (PreCachedPhysData: none)
