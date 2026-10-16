"""A Kismet sequence as text, for Gears 1 (374), Gears 2 (575) and Gears 3 (828) packages: each op of the sequences
whose path contains the filter, with its output links (output name -> target op [input index]) and the variables on
its variable links. Read with the runtime's GOW3_KISMET_TRACE (which ops ran) to see where a converted script stops.
usage: python -P tools/kismetgraph.py <package> [sequence path filter]"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, materialconv


def short(p, ref):
    if ref <= 0: return str(ref)
    return p.exports[ref - 1]['name']


def str_of(v):
    try:
        n = struct.unpack_from(upk.E + 'i', v, 0)[0]
        if n > 0: return v[4:4 + n - 1].decode('latin-1')
        if n < 0: return v[4:4 - 2 * n - 2].decode('utf-16-le', 'replace')
    except Exception: pass
    return ''


def elements(p, raw):
    n = struct.unpack_from(upk.E + 'i', raw, 0)[0]; o = 4; out = []
    for _ in range(n):
        el, o = materialconv.raw_props(p, raw, o)
        out.append({r[0]: r for r in el})
    return out


def refs(raw):
    n = struct.unpack_from(upk.E + 'i', raw, 0)[0]
    return struct.unpack_from(upk.E + f'{n}i', raw, 4) if len(raw) >= 4 + 4 * n else ()


def describe(p, i):
    """an op's class, outputs and variables"""
    d = p.export_data(i)
    try: recs, _ = materialconv.raw_props(p, d, 4)
    except Exception as ex: return f'  (unreadable: {ex})'
    a = {r[0]: r for r in recs}
    lines = []
    if 'OutputLinks' in a:
        for el in elements(p, a['OutputLinks'][4]):
            desc = str_of(el['LinkDesc'][4]) if 'LinkDesc' in el else '?'
            targets = []
            if 'Links' in el:
                for link in elements(p, el['Links'][4]):
                    op = struct.unpack_from(upk.E + 'i', link['LinkedOp'][4], 0)[0] if 'LinkedOp' in link else 0
                    idx = struct.unpack_from(upk.E + 'i', link['InputLinkIdx'][4], 0)[0] if 'InputLinkIdx' in link else 0
                    targets.append(f'{short(p, op)}[{idx}]')
            if targets: lines.append(f'    {desc} -> {", ".join(targets)}')
    if 'VariableLinks' in a:
        for el in elements(p, a['VariableLinks'][4]):
            desc = str_of(el['LinkDesc'][4]) if 'LinkDesc' in el else '?'
            vs = [short(p, r) for r in refs(el['LinkedVariables'][4])] if 'LinkedVariables' in el else []
            if vs: lines.append(f'    var {desc}: {", ".join(vs)}')
    return '\n'.join(lines)


if __name__ == '__main__':
    p = upk.Package(sys.argv[1])
    flt = sys.argv[2] if len(sys.argv) > 2 else ''
    for i, e in enumerate(p.exports):
        path = p.path(i)
        if '.Main_Sequence' not in path and '.G1_' not in path: continue
        if flt and flt not in path: continue
        cn = p.class_name(e['class'])
        if cn.startswith('SeqVar') or cn in ('InterpData', 'SequenceFrame') or cn.startswith('Interp'): continue
        print(f'{path.split("Main_Sequence.")[-1]} ({cn})')
        text = describe(p, i)
        if text: print(text)
