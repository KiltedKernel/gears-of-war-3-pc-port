"""A walking route for scripted test runs, over Gears 1's own path network.

A converted chapter's Gears 1 packages hold its navigation points (PathNodes, CoverLinks, CoverSlotMarkers, PlayerStarts:
NavGuid, Location) and ReachSpecs (Start: the navigation point; End: a NavReference whose Guid names the end point, in
any of the level's packages; Distance). The shortest path between the points nearest two locations (Dijkstra; mantle
specs cost --mantle-cost times their length: a scripted walk vaults only by its cover press) is printed as an input script `route` line (runtime/hid/hid.cpp), with
points closer than --spacing to the last one dropped.

python -P tools/g1route.py tools/recipes/gears1_ch1.ini [--at 400] [--leg 20] -- -1199,2600,160 -839,7808,160
"""
import argparse
import configparser
import heapq
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import upk                      # noqa: E402
import levelinject              # noqa: E402
import materialconv             # noqa: E402

SKIP_SPECS = set()
COST = {'MantleReachSpec': float(os.environ.get('G1ROUTE_MANTLE_COST', 8.0))}


def network(packages):
    """(points: {guid: (x, y, z)}, edges: {guid: [(guid, distance)]}) over the packages"""
    points, specs = {}, []
    for p in packages:
        guid_of = {}
        for i, e in enumerate(p.exports):
            if p.path(i).count('.') != 2: continue
            try: o, pr, end = levelinject.props_at(p, p.export_data(i))
            except Exception: continue
            a = {k: v for k, t, kk, v in pr}
            g, loc = a.get('NavGuid'), a.get('Location')
            if g and loc:
                guid_of[i + 1] = bytes(g[1]); points[bytes(g[1])] = struct.unpack('>3f', loc[1])
        for i, e in enumerate(p.exports):
            c = p.class_name(e['class'])
            if not c.endswith('ReachSpec') or c in SKIP_SPECS: continue
            try: o, pr, end = levelinject.props_at(p, p.export_data(i), (4, 8, 12, 32))
            except Exception: continue
            a = {k: v for k, t, kk, v in pr}
            s, en = a.get('Start'), a.get('End')
            if not s or s not in guid_of or not en: continue
            try: el, q = materialconv.raw_props(p, en[1], 0)
            except Exception: continue
            r = {x[0]: x[4] for x in el}
            # the end in this package: its Nav (an object reference); in another: the Guid alone
            actor = struct.unpack('>i', r['Nav'][:4])[0] if 'Nav' in r else 0
            g = guid_of.get(actor) if actor > 0 else (bytes(r['Guid']) if 'Guid' in r else None)
            if g: specs.append((guid_of[s], g, a.get('Distance', 0) * COST.get(c, 1.0)))
    edges = {}
    for s, en, dist in specs:
        if s in points and en in points:
            d = dist or float(np.linalg.norm(np.subtract(points[s], points[en])))
            edges.setdefault(s, []).append((en, d))
    return points, edges


def nearest(points, at, reachable=None):
    best, bd = None, 1e18
    for g, q in points.items():
        if reachable is not None and g not in reachable: continue
        d = (q[0] - at[0]) ** 2 + (q[1] - at[1]) ** 2 + 4 * (q[2] - at[2]) ** 2   # z weighs more: floors
        if d < bd: best, bd = g, d
    return best


def shortest(points, edges, a, b):
    dist, prev, heap = {a: 0.0}, {}, [(0.0, a)]
    while heap:
        d, u = heapq.heappop(heap)
        if u == b: break
        if d > dist.get(u, 1e18): continue
        for v, w in edges.get(u, ()):
            nd = d + w
            if nd < dist.get(v, 1e18): dist[v] = nd; prev[v] = u; heapq.heappush(heap, (nd, v))
    if b not in dist: return None
    path = [b]
    while path[-1] != a: path.append(prev[path[-1]])
    return [points[g] for g in reversed(path)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('recipe')
    ap.add_argument('stops', nargs='+', help='x,y,z: the start, then each place to reach in turn')
    ap.add_argument('--at', type=float, default=0, help='the script time of the first route line')
    ap.add_argument('--leg', type=float, default=20, help="each point's walkto time limit")
    ap.add_argument('--spacing', type=float, default=250)
    ap.add_argument('--g1', default=os.path.join(HERE, '..', '..', 'gow1-decomp', 'disc', 'WarGame', 'CookedXenon'))
    args = ap.parse_args()
    c = configparser.ConfigParser(inline_comment_prefixes=(';',)); c.read(args.recipe, encoding='utf-8')
    ch = c['chapter']
    names = [ch['persistent']] + [s.strip() for s in ch['sections'].replace('\n', ',').split(',') if s.strip()]
    pkgs = [upk.Package(os.path.join(args.g1, n + '.xxx')) for n in names]
    points, edges = network(pkgs)
    print(f'# {len(points)} navigation points, {sum(len(v) for v in edges.values())} reach specs', file=sys.stderr)
    stops = [tuple(float(v) for v in s.split(',')) for s in args.stops]
    t = args.at
    for a, b in zip(stops, stops[1:]):
        path = shortest(points, edges, nearest(points, a), nearest(points, b))
        if path is None:
            print(f'# no path {a} -> {b}', file=sys.stderr); continue
        out = []
        for q in path[1:]:
            if not out or np.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) >= args.spacing: out.append(q)
        if np.hypot(path[-1][0] - out[-1][0], path[-1][1] - out[-1][1]) > 1: out.append(path[-1])
        print(f'{t:g} route ' + ';'.join(f'{q[0]:.0f},{q[1]:.0f}' for q in out) + f' {args.leg:g}')
        length = sum(np.hypot(*np.subtract(u[:2], v[:2])) for u, v in zip(path, path[1:]))
        t += round(length / 300 + 10)        # walking ~400 units/s, with slack


if __name__ == '__main__':
    main()
