"""Explicit function bounds for every switch XenonRecomp reports jumping outside its function.
1. .pdata (PowerPC RUNTIME_FUNCTION: BeginAddress, packed {PrologLen:8, FuncLen:22 in instructions, ...}) when it covers
   the switch and all its targets.
2. Otherwise (frameless leaf functions have no .pdata): control-flow walk from the function start XenonRecomp chose,
   following conditional/unconditional branches and the switch tables, stopping at blr and at jumps to other known
   function starts (tail calls). The function ends after its highest reached instruction.
usage: fix_boundaries.py default.bin recomp.log switch_tables.toml func_mapping.cpp out.toml [ppc_dir]"""
import bisect, os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ximage
img = open(sys.argv[1], "rb").read()
BASE = ximage.BASE
PDATA, PDATA_SIZE = ximage.pdata(img)
TEXT_END = ximage.code_span(img)[1]
word = lambda a: struct.unpack_from(">I", img, a - BASE)[0]

pd = sorted((b, ((p >> 8) & 0x3FFFFF) * 4) for b, p in
            (struct.unpack_from(">II", img, o) for o in range(PDATA - BASE, PDATA - BASE + PDATA_SIZE, 8)) if b)
pd_starts = [b for b, _ in pd]

# switch tables: base (bctr address) -> labels + default
tables = {}
cur = None
for line in open(sys.argv[3]):
    m = re.match(r"base = 0x([0-9A-F]+)", line.strip())
    if m: cur = int(m.group(1), 16); tables[cur] = set(); continue
    m = re.match(r"default = 0x([0-9A-F]+)", line.strip())
    if m and cur is not None: tables[cur].add(int(m.group(1), 16)); continue
    for v in re.findall(r"0x([0-9A-F]{8})", line):
        if cur is not None and "labels" not in line: tables[cur].add(int(v, 16))
func_starts = sorted({int(v, 16) for v in re.findall(r"\{ 0x([0-9A-F]+),", open(sys.argv[4]).read())})

errs = {}
for m in re.finditer(r"Switch case at ([0-9A-F]+) is trying to jump outside function: ([0-9A-F]+)", open(sys.argv[2]).read()):
    errs.setdefault(int(m.group(1), 16), set()).add(int(m.group(2), 16))
# optional 6th argument, the generated ppc/ folder: a goto to a label the function does not define means the analyzer
# ended the function too early (Gears of War 3: chains of bdzf after a blr); keyed by the function start like a switch
if len(sys.argv) > 6:
    import glob
    missing = 0
    for f in glob.glob(os.path.join(sys.argv[6], "ppc_recomp.*.cpp")):
        fn, defined, used = None, set(), set()
        def flush():
            global missing
            if fn is not None and used - defined:
                errs.setdefault(fn, set()).update(used - defined); missing += 1
        for line in open(f):
            if line.startswith("PPC_FUNC_IMPL(__imp__sub_"):
                flush(); fn = int(line[25:33], 16); defined, used = set(), set()
            elif line.startswith("loc_"):
                defined.add(int(line[4:12], 16))
            elif "goto loc_" in line:
                k = line.index("goto loc_") + 9; used.add(int(line[k:k + 8], 16))
        flush()
    print(f"functions with gotos to missing labels: {missing}")


def walk(start, limit):
    """highest instruction address reached from start, staying below limit"""
    seen, work, hi = set(), [start], start
    while work:
        a = work.pop()
        while start <= a < limit and a not in seen:
            seen.add(a); hi = max(hi, a)
            w = word(a); op = w >> 26
            if a in tables:                                   # bctr of a known switch
                work += [t for t in tables[a] if start <= t < limit]; break
            if op == 18:                                      # b / bl / ba
                tgt = (a + ((w & 0x3FFFFFC) ^ 0x2000000) - 0x2000000) & 0xFFFFFFFF if not (w & 2) else w & 0x3FFFFFC
                if w & 1:                                     # bl: call, falls through
                    a += 4; continue
                if start <= tgt < limit and tgt not in func_starts_set:
                    work.append(tgt)
                break                                         # unconditional: no fall through
            if op == 16:                                      # bc
                bd = w & 0xFFFC; bd -= 0x10000 if bd & 0x8000 else 0
                tgt = (a + bd) & 0xFFFFFFFF
                if not (w & 1) and start <= tgt < limit: work.append(tgt)
                bo = (w >> 21) & 31
                if bo & 0x14 == 0x14: break                   # branch always
                a += 4; continue
            if op == 19 and ((w >> 1) & 0x3FF) in (16, 528):  # bclr / bcctr
                bo = (w >> 21) & 31
                if bo & 0x14 == 0x14 and not (w & 1): break   # blr / bctr (unconditional, not link)
            a += 4
    return hi + 4


func_starts_set = set(func_starts)
out, unresolved = {}, []
for sw, targets in sorted(errs.items()):
    i = bisect.bisect_right(pd_starts, sw) - 1
    if i >= 0:
        b, ln = pd[i]
        if b <= sw < b + ln and max(targets | tables.get(sw, set())) < b + ln:
            out[b] = ln; continue
    j = bisect.bisect_right(func_starts, sw) - 1
    start = func_starts[j]
    k = bisect.bisect_right(pd_starts, sw)
    limit = min(pd_starts[k] if k < len(pd_starts) else TEXT_END, TEXT_END)
    end = walk(start, limit)
    if max(targets) < end:
        out[start] = end - start
    else:
        unresolved.append(sw)
# keep what earlier rounds found (out.toml), then drop ranges nested inside another one (false starts in the middle)
import os
if os.path.exists(sys.argv[5]):
    for m in re.finditer(r"address = 0x([0-9A-F]+), size = 0x([0-9A-F]+)", open(sys.argv[5]).read()):
        b, ln = int(m.group(1), 16), int(m.group(2), 16)
        out[b] = max(out.get(b, 0), ln)
merged = []
for b, ln in sorted(out.items(), key=lambda x: (x[0], -x[1])):
    if merged and b < merged[-1][0] + merged[-1][1]:
        pb, pl = merged[-1]
        merged[-1] = (pb, max(pl, b + ln - pb))        # overlap: one function, covering both
    else:
        merged.append((b, ln))
out = dict(merged)
# functions the analyzer cut short at a conditional return (bdzlr etc.) where the code really falls through:
# memset's byte tail is "stb 0; bdzlr; stb 1; bdzlr; stb 2; blr", so lengths with 2-3 trailing bytes lost them
# (Gears 2: Carmine's 95-entry SkelControlIndex kept two 0 bytes = his left-hand IK on the wrong bone).
# Gears of War 3's memset: the same 0xA0 bytes at 82CC9EA0.
MANUAL = {0x82CC9EA0: 0xA0}
for b, ln in MANUAL.items():
    out[b] = max(out.get(b, 0), ln)
with open(sys.argv[5], "w") as f:
    f.write("# generated by tools/fix_boundaries.py (.pdata, else control-flow walk) for switches that left their function\n")
    f.write("functions = [\n")
    for b, ln in sorted(out.items()):
        f.write(f"    {{ address = 0x{b:08X}, size = 0x{ln:X} }},\n")
    f.write("]\n")
print(f"switches {len(errs)}; functions fixed {len(out)}; unresolved {len(unresolved)} {[hex(x) for x in unresolved[:8]]}")
