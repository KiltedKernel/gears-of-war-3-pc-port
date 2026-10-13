"""Jump tables for XenonRecomp, independent of instruction order (Gears of War 3's compiler schedules the table
address as lis / slwi / addi, which XenonAnalyse's fixed patterns never match).

For every `mtctr rX; bctr` in .text: find the bounds check above it (bgt/ble crN ... cmplwi crN, rIdx, N), then
evaluate the instructions from the cmplwi to the bctr symbolically:
  absolute:  ctr = word[T + idx*4]
  computed:  ctr = B + (byte[T + idx] << s)       byte offset: ctr = B + byte[T + idx]
  short:     ctr = B + half[T + idx*2]
The table's `base` is the bctr's own address (the recompiler arms a table when its walk reaches `base`).
usage: find_switches.py default.bin out.toml"""
import struct, sys

B = 0x82000000
img = open(sys.argv[1], "rb").read()


def code_range():
    """first to last code section in the image's PE header (.text, BINK, RADCODE ...): one span, since the
    sections are contiguous apart from padding"""
    pe = struct.unpack_from("<I", img, 0x3C)[0]
    n, opt = struct.unpack_from("<H", img, pe + 6)[0], struct.unpack_from("<H", img, pe + 20)[0]
    spans = []
    for k in range(n):
        h = pe + 24 + opt + 40 * k
        vsize, va = struct.unpack_from("<II", img, h + 8)
        if struct.unpack_from("<I", img, h + 36)[0] & 0x20: spans.append((B + va, B + va + vsize))
    return min(a for a, _ in spans), max(b for _, b in spans) & ~3


TS, TE = code_range()
TL = TE - TS
W = struct.unpack_from(f">{TL // 4}I", img, TS - B)


def word(a): return struct.unpack_from(">I", img, a - B)[0]
def half(a): return struct.unpack_from(">H", img, a - B)[0]
def byte(a): return img[a - B]
def simm(x): return (x & 0xFFFF) - (0x10000 if x & 0x8000 else 0)


def fields(x):
    return x >> 26, (x >> 21) & 31, (x >> 16) & 31, (x >> 11) & 31


def find_bounds(i):
    """walk back from the bctr at index i: (cmplwi index, register, count, default label, bounds-branch index)"""
    cr = None; default = None
    for k in range(1, 40):
        if i - k < 0: return None
        x = W[i - k]; op, d, a, b = fields(x)
        if cr is None:
            # bc with BO "branch if true" on gt (BI%4==1) -> bgt; BO false on gt -> ble
            if op == 16:
                bo, bi = d, a
                if bi % 4 == 1 and bo in (12, 4, 13, 5, 14, 6, 15, 7):
                    cr = bi // 4
                    default = (TS + 4 * (i - k) + (simm(x & 0xFFFC))) & 0xFFFFFFFF
            elif op == 19 and ((x >> 1) & 0x3FF) == 16:   # bgtlr / blelr
                bo, bi = d, a
                if bi % 4 == 1: cr = bi // 4; default = 0
            continue
        if op == 10 and (d >> 2) == cr and (d & 1) == 0:   # cmplwi crN, rA, UIMM (L=0)
            return i - k, a, (x & 0xFFFF) + 1, default
        # the index register can be compared with cmplw against a register holding a constant: not handled
    return None


def spill_slot(start, ridx):
    """unoptimized code: the compared register was just loaded from a stack slot (lwz rIdx, d(r1))"""
    for j in range(start - 1, max(start - 4, -1), -1):
        x = W[j]; op, d, a, b = fields(x)
        if op == 32 and d == ridx and a in (1, 31): return (a, simm(x))
        if d == ridx: return None
    return None


def evaluate(start, end, ridx):
    """symbolic values from the cmplwi (start) to the bctr (end); returns (ctr value, a register holding the index)"""
    R = {ridx: ("idx", 0)}
    # copies made just above the compare (mr rX, rIdx or mr rIdx, rX) hold the index too
    for j in range(start - 1, max(start - 12, -1), -1):
        x = W[j]; op, d, a, b = fields(x)
        if op == 31 and ((x >> 1) & 0x3FF) == 444 and d == b:
            if d == ridx: R.setdefault(a, ("idx", 0))
            elif a == ridx: R.setdefault(d, ("idx", 0))
        if op in (16, 18, 19): break
    slot = spill_slot(start, ridx)
    for j in range(start + 1, end):
        x = W[j]; op, d, a, b = fields(x)
        xo = (x >> 1) & 0x3FF
        if x == 0x60000000: continue                                         # nop
        if op == 32 and slot is not None and (a, simm(x)) == slot:           # reload of the spilled index
            R[d] = ("idx", 0)
        elif op == 15:     # lis / addis
            R[d] = ("c", (simm(x) << 16) & 0xFFFFFFFF) if a == 0 else (("c", (R[a][1] + (simm(x) << 16)) & 0xFFFFFFFF) if R.get(a, (0,))[0] == "c" else None)
        elif op == 14:   # li / addi
            if a == 0: R[d] = ("c", simm(x) & 0xFFFFFFFF)
            elif R.get(a) and R[a][0] == "c": R[d] = ("c", (R[a][1] + simm(x)) & 0xFFFFFFFF)
            else: R[d] = None
        elif op == 21:   # rlwinm rA, rS, SH, MB, ME : slwi = SH, 0, 31-SH
            s, ra = d, a
            sh, mb, me = (x >> 11) & 31, (x >> 6) & 31, (x >> 1) & 31
            v = R.get(s)
            if v and v[0] in ("idx", "byte", "half") and mb == 0 and me == 31 - sh:
                R[ra] = (v[0], v[1] + sh) + v[2:]
            elif v and v[0] in ("idx", "byte", "half") and sh == 0:
                R[ra] = v      # clrlwi of a loaded/zero-extended value
            else: R[ra] = None
        elif op == 31 and xo in (23, 87, 279):   # lwzx / lbzx / lhzx rD, rA, rB
            va, vb = R.get(a), R.get(b)
            if va and vb and vb[0] == "c" and va[0] == "idx": va, vb = vb, va
            kind = {23: ("word", 2), 87: ("byte", 0), 279: ("half", 1)}[xo]
            if va and vb and va[0] == "c" and vb[0] == "idx" and vb[1] == kind[1]:
                R[d] = ("abs", va[1]) if kind[0] == "word" else (kind[0], 0, va[1])   # (kind, shift, table)
            else: R[d] = None
        elif op == 31 and xo == 266:   # add rD, rA, rB
            va, vb = R.get(a), R.get(b)
            if va and va[0] == "c": va, vb = vb, va
            if va and vb and va[0] in ("byte", "half") and vb[0] == "c":
                R[d] = ("rel", va[0], va[1], va[2], vb[1])
            else: R[d] = None
        elif op == 31 and xo == 444 and d == b:   # mr rA, rS
            R[a] = R.get(d)
        elif op == 31 and xo == 467:              # mtspr
            spr = ((x >> 16) & 31) | (((x >> 11) & 31) << 5)
            if spr == 9:
                holders = [r for r, v in R.items() if v == ("idx", 0)]
                return (R.get(d), ridx if ridx in holders else (holders[0] if holders else None))
        elif op in (16, 18, 19, 10, 11): pass     # branches and compares do not change GPRs
        elif op in (32, 34, 40, 33, 35, 41, 7, 24, 25, 26, 27, 28, 29, 31, 12, 13, 20, 30):
            # other integer writes: rD (or rA for logical ops) becomes unknown
            if op in (24, 25, 26, 27, 28, 29, 20, 30) or (op == 31 and xo in (28, 60, 124, 316, 412, 444, 476, 24, 536, 792, 824, 922, 954, 986, 26)):
                R[a] = None
            elif op == 31 and xo in (151, 183, 215, 247, 407, 439, 662, 918, 150, 214):   # stores
                pass
            else:
                R[d] = None
    return None


def main():
    out = ["# Generated by tools/find_switches.py (order-independent jump table finder)"]
    counts = {}; skipped = []
    for i, x in enumerate(W):
        if x != 0x4E800420 or i == 0: continue
        mt = i - 1                                                # mtctr rS (nops may sit before the bctr)
        while W[mt] == 0x60000000 and mt > i - 4: mt -= 1
        if (W[mt] & 0xFC1FFFFF) != 0x7C0903A6: continue
        bctr = TS + 4 * i
        bnd = find_bounds(i)
        if not bnd: continue                                  # an indirect call or virtual dispatch
        cmp_i, ridx, n, default = bnd
        v = evaluate(cmp_i, i + 1, ridx)
        if not v or not v[0] or v[1] is None: skipped.append(bctr); continue
        v, ridx = v
        if v[0] == "abs":
            labels = [word(v[1] + 4 * k) for k in range(n)]; kind = "absolute"
        elif v[0] == "rel":
            _, k, shift, table, base = v
            if k == "byte": labels = [(base + (byte(table + m) << shift)) & 0xFFFFFFFF for m in range(n)]
            else: labels = [(base + (half(table + 2 * m) << shift)) & 0xFFFFFFFF for m in range(n)]
            kind = f"{k} offset"
        else:
            skipped.append(bctr); continue
        if not all(TS <= l < TS + TL for l in labels): skipped.append(bctr); continue
        counts[kind] = counts.get(kind, 0) + 1
        out += ["[[switch]]", f"base = 0x{bctr:X}", f"r = {ridx}", f"default = 0x{default:X}", "labels = ["]
        out += [f"    0x{l:X}," for l in labels] + ["]", ""]
    open(sys.argv[2], "w").write("\n".join(out) + "\n")
    print("tables:", sum(counts.values()), counts)
    print(f"bounded bctr not decoded: {len(skipped)}", " ".join(f"{a:08X}" for a in skipped[:40]))


main()
