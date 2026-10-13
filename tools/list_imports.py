"""List every import of a XEX by name (XenonUtils' ordinal tables), one per line: lib kind name.
usage: list_imports.py default.xex default.bin [names-only]"""
import re, struct, sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = open(sys.argv[1], "rb").read(); img = open(sys.argv[2], "rb").read()
be = lambda f, o: struct.unpack_from(">" + f, d, o)
T = ROOT + r"\tools\XenonRecomp\XenonUtils\xbox"
names = {}
for lib, f in (("xboxkrnl.exe", "xboxkrnl_table.inc"), ("xam.xex", "xam_table.inc")):
    names[lib] = {int(o, 16): (n, k) for o, n, k in re.findall(r"XE_EXPORT\(\w+,\s*(0x[0-9A-Fa-f]+),\s*(\w+),\s*(\w+)\)", open(f"{T}\\{f}").read())}
nopt = be("I", 20)[0]; opts = dict(be("II", 24 + i * 8) for i in range(nopt))
o = opts[0x103FF]; size, strsize, nlib = be("III", o)
libs = []; p = o + 12; end = p + strsize
while p < end and len(libs) < nlib:
    s = d[p:d.index(b"\0", p)].decode(); libs.append(s); p += (len(s) + 1 + 3) & ~3
p = o + 12 + strsize
seen = set()
for i in range(nlib):
    lsize = be("I", p)[0]; nameidx, cnt = be("HH", p + 36)
    for k in range(cnt):
        rec = be("I", p + 40 + k * 4)[0]
        val = struct.unpack_from(">I", img, rec - 0x82000000)[0]
        typ, ordinal = val >> 24, val & 0xFFFF
        n, kind = names.get(libs[nameidx], {}).get(ordinal, (f"{libs[nameidx]}#{ordinal:#x}", "?"))
        if typ == 1 and kind == "kVariable": continue   # the thunk record of a variable does not exist; skip doubles
        if (libs[nameidx], n) in seen: continue
        seen.add((libs[nameidx], n))
        print(n if len(sys.argv) > 3 else f"{libs[nameidx]:13} {'var' if kind == 'kVariable' else 'fn ':3} {n}")
    p += lsize
