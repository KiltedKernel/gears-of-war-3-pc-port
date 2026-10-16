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
