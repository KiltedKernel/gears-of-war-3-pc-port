"""Every Scaleform movie (SwfMovie / GFxMovieInfo exports) in Gears 3's cooked packages, saved as OUT/<package>/<export
path>.gfx (each movie once), for reading with JPEXS FFDec (`ffdec-cli -export script <dir> <file.gfx>`) and
tools/gfx_input_catalog.py. Reads the player's own disc files; keep the output outside the repository (it is game
data). Packages that do not decompress are skipped (listed on stderr).
usage: python -P tools/gfx_extract.py <disc>/GearGame/CookedXbox360 <out> [package filter]"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk

cooked, out = sys.argv[1], sys.argv[2]
flt = sys.argv[3].lower() if len(sys.argv) > 3 else ''
os.makedirs(out, exist_ok=True)
seen = set()
count = 0
for fn in sorted(os.listdir(cooked)):
    if not fn.lower().endswith('.xxx') or flt not in fn.lower(): continue
    path = os.path.join(cooked, fn)
    try:
        p = upk.Package(path, tables_only=True)
    except Exception as ex:
        print(f'{fn}: unreadable ({ex})', file=sys.stderr); continue
    hits = [i for i, e in enumerate(p.exports)
            if p.class_name(e['class']) in ('SwfMovie', 'GFxMovieInfo') and p.path(i) not in seen]
    if not hits: continue
    try:
        p = upk.Package(path)
    except Exception as ex:
        print(f'{fn}: data unreadable ({ex})', file=sys.stderr); continue
    for i in hits:
        seen.add(p.path(i))
        d = p.export_data(i)
        k = d.find(b'GFX')
        while k >= 0 and d[k + 3] not in range(6, 12):   # "GFX" and the SWF version byte
            k = d.find(b'GFX', k + 1)
        if k < 0: print(f'{fn} {p.path(i)}: no GFX', file=sys.stderr); continue
        size = struct.unpack_from('<I', d, k + 4)[0]
        name = p.path(i).replace('/', '.').replace('\\', '.')
        dst = os.path.join(out, fn[:-4], name + '.gfx')
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, 'wb').write(d[k:k + size])
        count += 1
        print(f'{fn:40} {p.path(i):60} {size}')
print(f'{count} movies', file=sys.stderr)
