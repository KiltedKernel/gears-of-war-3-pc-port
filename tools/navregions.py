"""Navmesh regions for a converted chapter's recipe, from Gears 1's own path nodes.

For each script section, its PathNodes are grouped into regions of at most `--radius` around a centre (greedy: the node
with the most nodes in reach, repeatedly); each region gets the node nearest its centre as pylon_at, five spread-out
nodes as pylons_at (farthest-point), and as sources the chapter's geometry sections with BSP or mesh actors in reach.
The blocks printed are recipe [navmesh <name>] sections (seeds_on_path_nodes: Gears 1's path nodes stand 50 units over
their floor). Regions with fewer than --min-nodes nodes are left out.

python -P tools/navregions.py tools/recipes/gears1_ch3.ini [--skip-sections A,B] [--radius 5500]
"""
import argparse
import configparser
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import upk                      # noqa: E402
import levelinject              # noqa: E402
import bspconv                  # noqa: E402


def path_nodes(p):
    out = []
    for i, e in enumerate(p.exports):
        if p.class_name(e['class']) != 'PathNode' or p.path(i).count('.') != 2: continue
        try: a = {k: v for k, t, kk, v in levelinject.props_at(p, p.export_data(i))[1]}
        except ValueError: continue
        if a.get('Location'): out.append(struct.unpack('>3f', a['Location'][1]))
    return np.array(out).reshape(-1, 3)


def geometry_points(p):
    """xy points standing for a package's geometry: its mesh actors' locations and its BSP polygons' centres"""
    pts = []
    try: pts += [a[1][:2] for a in levelinject.source_actors(p)]
    except Exception: pass
    try:
        m = bspconv.read_model_374(p, bspconv.world_model(p))
        pts += [x[1].mean(0)[:2] for x in bspconv.polygons(m)]
    except Exception: pass
    return np.array(pts).reshape(-1, 2)


def regions(P, radius, min_nodes):
    left = np.ones(len(P), bool)
    out = []
    while left.sum() >= min_nodes:
        idx = np.where(left)[0]
        Q = P[idx]
        d = np.linalg.norm(Q[:, None, :2] - Q[None, :, :2], axis=2)
        reach = (d <= radius).sum(1)
        c = int(np.argmax(reach))
        members = idx[d[c] <= radius]
        if len(members) < min_nodes: break
        left[members] = False
        out.append(P[members])
    return out


def spread(P, n):
    centre = P.mean(0)
    pick = [P[np.argmin(np.linalg.norm(P - centre, axis=1))]]
    for _ in range(min(n, len(P) - 1)):
        d = np.min([np.linalg.norm(P - q, axis=1) for q in pick], axis=0)
        pick.append(P[np.argmax(d)])
    return pick


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('recipe')
    ap.add_argument('--radius', type=float, default=5500.0)
    ap.add_argument('--min-nodes', type=int, default=12)
    ap.add_argument('--skip-sections', default='', help='script sections already covered (comma separated)')
    ap.add_argument('--min-geometry', type=int, default=25)
    ap.add_argument('--exclude', default='', help='x,y,r;...: nodes within r of x,y left out (regions already built)')
    ap.add_argument('--g1', default=os.path.join(HERE, '..', '..', 'gow1-decomp', 'disc', 'WarGame', 'CookedXenon'))
    args = ap.parse_args()
    c = configparser.ConfigParser(inline_comment_prefixes=(';',)); c.read(args.recipe, encoding='utf-8')
    ch = c['chapter']
    persistent = ch['persistent']
    sections = [s.strip() for s in ch['sections'].replace('\n', ',').split(',') if s.strip()]
    skip = {s.strip() for s in args.skip_sections.split(',') if s.strip()}
    excl = [np.array([float(v) for v in q.split(',')]) for q in args.exclude.split(';') if q.strip()]
    pkg = lambda n: upk.Package(os.path.join(args.g1, n + '.xxx'))
    geo = {n: geometry_points(pkg(n)) for n in [persistent] + sections if 'cine' not in n.lower() and 'audio' not in n.lower()}
    for s in sections:
        if s in skip or 'cine' in s.lower() or 'audio' in s.lower(): continue
        P = path_nodes(pkg(s))
        for ex in excl: P = P[np.linalg.norm(P[:, :2] - ex[:2], axis=1) > ex[2]]
        if len(P) < args.min_nodes: continue
        for k, R in enumerate(regions(P, args.radius, args.min_nodes)):
            seeds = spread(R, 5)
            centre = seeds[0]
            # sources: sections with real geometry in reach (at least --min-geometry points within the radius: a compact
            # level otherwise brings nearly every section into every region's slot)
            srcs = [n for n, G in geo.items() if len(G) and (np.linalg.norm(G - centre[:2], axis=1) <= args.radius).sum() >= args.min_geometry]
            name = s.split('_', 2)[-1].lower() + (f'_{k + 1}' if k else '')
            fmt = lambda q: ','.join(str(int(round(v))) for v in q)
            print(f'\n[navmesh {name}]\n; {len(R)} of {s}\'s path nodes (tools/navregions.py)\nsection = {s}')
            print(f'pylon_at = {fmt(centre)}\npylon_radius = 6000\npylons_at = ' + ';'.join(fmt(q) for q in seeds[1:]))
            print('slot = MP_GridlockG3\nurl = MP_GridlockG3?game=GearGameContent.GearGameHorde?listen\noffset = 0,0,20000')
            print('sources = ' + ', '.join(srcs))
            print('options = --template GothPillar_Short --materials MI_Locust_Shield --bsp --replace --cover --offset 0,0,20000')
            print(f'          --starts-at={int(centre[0])},{int(centre[1])},{int(centre[2]) + 20000 - 20},0')
            print('seconds = 900\nseeds_on_path_nodes = true')


if __name__ == '__main__':
    main()
