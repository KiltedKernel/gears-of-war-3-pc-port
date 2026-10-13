"""List the XEX's variable (data) imports with names from XenonUtils' ordinal tables. usage: list_var_imports.py default.xex default.bin"""
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
for i in range(nlib):
    lsize = be("I", p)[0]; nameidx, cnt = be("HH", p + 36)
    for k in range(cnt):
        rec = be("I", p + 40 + k * 4)[0]
        val = struct.unpack_from(">I", img, rec - 0x82000000)[0]
        typ, ordinal = val >> 24, val & 0xFFFF
        n, kind = names[libs[nameidx]].get(ordinal, ("?", "?"))
        if typ == 0 and kind == "kVariable":
            print(f"{libs[nameidx]:13} var {ordinal:#06x} {n:32} slot {rec:08X}")
    p += lsize
