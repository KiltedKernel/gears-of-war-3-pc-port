"""Find a Gears of War 2 function in Gears of War 3 (or any XEX pair) by shape: the sequence of instruction kinds
(primary opcode + extended opcode, registers and immediates ignored) compared with difflib over every .pdata function
of similar length in the other image.
usage: match_function.py old.bin <old address hex> new.bin [top=5]"""
import difflib, os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ximage

B = ximage.BASE


def funcs(img):
    s, z = ximage.pdata(img)
    out = []
    for o in range(s - B, s - B + z, 8):
        b, p = struct.unpack_from(">II", img, o)
        if b: out.append((b, ((p >> 8) & 0x3FFFFF) * 4))
    return out


def kinds(img, a, n):
    out = []
    for k in range(n // 4):
        w = struct.unpack_from(">I", img, a - B + 4 * k)[0]
        op = w >> 26
        out.append((op << 11) | ((w >> 1) & 0x3FF) if op in (19, 31, 63, 59, 4) else op << 11 | (w & 3 if op in (16, 18) else 0))
    return out


old = open(sys.argv[1], "rb").read(); addr = int(sys.argv[2], 16); new = open(sys.argv[3], "rb").read()
top = int(sys.argv[4]) if len(sys.argv) > 4 else 5
size = dict(funcs(old)).get(addr)
if not size: sys.exit(f"{addr:08X} is not a .pdata function start in the old image")
ref = kinds(old, addr, size)
scores = []
for b, ln in funcs(new):
    if not (0.7 * size <= ln <= 1.4 * size): continue
    sm = difflib.SequenceMatcher(None, ref, kinds(new, b, ln), autojunk=False)
    if sm.real_quick_ratio() < 0.6 or sm.quick_ratio() < 0.6: continue
    scores.append((sm.ratio(), b, ln))
scores.sort(reverse=True)
print(f"old {addr:08X} ({size:#x} bytes)")
for r, b, ln in scores[:top]: print(f"  {b:08X} ({ln:#x} bytes)  similarity {r:.3f}")
