"""The scripted route of a Gears 1/2 level, from its Kismet: every touch event's trigger (where the player has to go,
and the sequence it starts), the AI move orders' destinations (where the squad leads), the teleports and the
checkpoints, with locations, per package. For writing playtests of a converted chapter (tests/g1_chapter1_play.txt)
and for reading a level's flow. usage: python -P tools/levelroute.py <package> [<package>...]"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, materialconv, kismetgraph


def props(p, i, starts=(4,)):
    for st in starts:
        try:
            recs, _ = materialconv.raw_props(p, p.export_data(i), st)
            if all(r[0] in p.names for r in recs): return {r[0]: r for r in recs}
        except Exception: pass
    return {}


def location(p, i):
    a = props(p, i, (32, 26, 4, 8, 36))
    if 'Location' in a:
        v = a['Location'][4]
        return struct.unpack(upk.E + '3f', v[:12])
    return None


def var_targets(p, i, link):
    """the objects (actors) on an op's variable link named `link`"""
    a = props(p, i)
    out = []
    if 'VariableLinks' not in a: return out
    for el in kismetgraph.elements(p, a['VariableLinks'][4]):
        if 'LinkDesc' not in el or kismetgraph.str_of(el['LinkDesc'][4]).lower() != link: continue
        if 'LinkedVariables' not in el: continue
        for v in kismetgraph.refs(el['LinkedVariables'][4]):
            if v <= 0: continue
            va = props(p, v - 1)
            if 'ObjValue' in va:
                o = struct.unpack(upk.E + 'i', va['ObjValue'][4])[0]
                if o > 0: out.append(o - 1)
    return out


def fmt(loc): return '(' + ', '.join(f'{c:.0f}' for c in loc) + ')' if loc else '(?)'


def guid_key(p, raw):
    """a Guid struct's 16 bytes: Gears 1 serializes it as tagged A, B, C, D ints; Gears 3 as the raw ints"""
    try:
        g = {r[0]: r[4] for r in materialconv.raw_props(p, raw, 0)[0]}
        if {'A', 'B', 'C', 'D'} <= set(g): return b''.join(g[k][:4] for k in 'ABCD')
    except Exception: pass
    return bytes(raw[:16])


def path_graph(packages):
    """Gears 1's own path network over a level's packages: navigation points (any actor with a NavGuid: path nodes,
    cover, slot markers, player starts) with their locations, and ReachSpecs (Start: a reference; End: an
    ActorReference {actor, NavGuid}, the guid reaching into other packages) as edges"""
    nodes, by_guid, edges = {}, {}, []
    for path in packages:
        p = upk.Package(path)
        key = os.path.basename(path)
        for i, e in enumerate(p.exports):
            if not p.path(i).startswith('TheWorld.PersistentLevel.') or p.path(i).count('.') != 2: continue
            a = props(p, i, (32, 26))
            if 'NavGuid' not in a or 'Location' not in a: continue
            loc = struct.unpack(upk.E + '3f', a['Location'][4][:12])
            nodes[(key, i)] = loc
            by_guid[guid_key(p, a['NavGuid'][4])] = (key, i)
        for i, e in enumerate(p.exports):
            if not p.class_name(e['class']).endswith('ReachSpec'): continue
            a = props(p, i)
            if 'Start' not in a or 'End' not in a: continue
            s = struct.unpack(upk.E + 'i', a['Start'][4])[0]
            # End: an ActorReference, itself tagged properties in Gears 1 (Nav, Guid)
            try: er_ = {r[0]: r[4] for r in materialconv.raw_props(p, a['End'][4], 0)[0]}
            except Exception: continue
            ref = er_.get('Nav', er_.get('Actor'))          # (Gears 1 names it Nav; a guid only across levels)
            er = struct.unpack(upk.E + 'i', ref)[0] if ref else 0
            edges.append(((key, s - 1), (key, er - 1) if er > 0 else guid_key(p, er_.get('Guid', b''))))
    graph = {}
    for s, t in edges:
        if isinstance(t, bytes): t = by_guid.get(t)
        if s in nodes and t in nodes:
            d = sum((x - y) ** 2 for x, y in zip(nodes[s], nodes[t])) ** 0.5
            graph.setdefault(s, []).append((t, d))
    return nodes, graph


def plan(nodes, graph, points):
    """waypoints along Gears 1's path network through the given points (each snapped to the nearest node)"""
    import heapq
    near = lambda q: min(nodes, key=lambda n: sum((x - y) ** 2 for x, y in zip(nodes[n], q)))
    out = []
    stops = [near(q) for q in points]
    for a, b in zip(stops, stops[1:]):
        dist, prev, todo = {a: 0}, {}, [(0, a)]
        while todo:
            d, n = heapq.heappop(todo)
            if n == b: break
            if d > dist.get(n, 1e18): continue
            for m, w in graph.get(n, ()):
                if d + w < dist.get(m, 1e18):
                    dist[m] = d + w; prev[m] = n; heapq.heappush(todo, (d + w, m))
        if b not in dist:
            print(f'no path from {fmt(nodes[a])} to {fmt(nodes[b])}', file=sys.stderr); out.append(nodes[b]); continue
        leg = [b]
        while leg[-1] != a: leg.append(prev[leg[-1]])
        out += [nodes[n] for n in reversed(leg)][1 if out else 0:]
    return out


if len(sys.argv) > 2 and sys.argv[1] == '--plan':
    # --plan "x,y,z;x,y,z;..." <packages...>: waypoints (one per line) for a playtest's walkto commands
    pts = [tuple(float(v) for v in q.split(',')) for q in sys.argv[2].split(';')]
    nodes, graph = path_graph(sys.argv[3:])
    print(f'# {len(nodes)} navigation points, {sum(len(v) for v in graph.values())} reach specs', file=sys.stderr)
    last = None
    for q in plan(nodes, graph, pts):
        if last is None or sum((x - y) ** 2 for x, y in zip(q, last)) > 300 ** 2:   # (one waypoint per 300 units)
            print(f'{q[0]:.0f} {q[1]:.0f}'); last = q
    sys.exit(0)

for path in sys.argv[1:]:
    p = upk.Package(path)
    print(f'== {os.path.basename(path)}')
    for i, e in enumerate(p.exports):
        cn = p.class_name(e['class'])
        seq = p.path(i).replace('TheWorld.PersistentLevel.Main_Sequence.', '').rsplit('.', 1)[0]
        if cn in ('SeqEvent_Touch', 'SeqEvt_Touch'):
            a = props(p, i)
            o = struct.unpack(upk.E + 'i', a['Originator'][4])[0] if 'Originator' in a else 0
            if o > 0: print(f'  touch   {p.exports[o - 1]["name"]:28} {fmt(location(p, o - 1)):26} -> {seq}')
        elif cn in ('SeqAct_AIMoveToActor', 'SeqAct_AIMove'):
            for t in var_targets(p, i, 'destination') + var_targets(p, i, 'move to'):
                print(f'  move    {p.exports[t]["name"]:28} {fmt(location(p, t)):26} in {seq}')
        elif cn == 'SeqAct_Teleport':
            for t in var_targets(p, i, 'destination'):
                print(f'  teleport {p.exports[t]["name"]:27} {fmt(location(p, t)):26} in {seq}')
        elif cn in ('SeqAct_WarCheckPoint', 'SeqAct_Checkpoint'):
            print(f'  checkpoint {"":25} {"":26} in {seq}')
