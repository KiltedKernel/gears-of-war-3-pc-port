"""Find a UE3 native class's vtable from its registration (UTF-16 name 'U<Class>' -> GetPrivateStaticClass ->
InternalConstructor, which stores the vtable) and optionally diff it against another class.
usage: class_vtable.py default.bin SkelControlLimb [SkelControlBase] [entries]"""
import struct, sys

BASE = 0x82000000
img = open(sys.argv[1], 'rb').read()
words = struct.unpack(f'>{len(img) // 4}I', img[:len(img) // 4 * 4])
def word(a): return words[(a - BASE) // 4]
def simm(w): return (w & 0xFFFF) - (0x10000 if w & 0x8000 else 0)

def refs(target):
    hi, lo = (target + 0x8000) >> 16 & 0xFFFF, target & 0xFFFF
    out = []
    for k, w in enumerate(words):
        if w >> 26 == 14 and (w & 0xFFFF) == lo:
            ra = (w >> 16) & 31
            for b in range(k - 1, max(k - 13, 0), -1):
                p = words[b]
                if p >> 26 == 15 and (p >> 16) & 31 == 0 and (p >> 21) & 31 == ra:
                    if p & 0xFFFF == hi: out.append(BASE + 4 * k)
                    break
    return out

def lis_addi_values(start, end):
    """all lis/addi constants formed in [start, end)"""
    vals, his = [], {}
    for a in range(start, end, 4):
        w = word(a)
        if w >> 26 == 15 and (w >> 16) & 31 == 0: his[(w >> 21) & 31] = simm(w) << 16
        elif w >> 26 == 14 and (w >> 16) & 31 in his: vals.append((a, (his[(w >> 16) & 31] + simm(w)) & 0xFFFFFFFF))
    return vals

def vtable(cls):
    # the registration's name is a terminated 'U<Class>' string; earlier copies can sit inside debug strings
    # ("UUIInteraction::...") that nothing registers, so take the first copy that code refers to
    name = ('U' + cls).encode('utf-16-be') + b'\0\0'
    i, reg = img.find(name), []
    while i != -1:
        if i % 2 == 0:
            reg = refs(BASE + i)
            if reg: break
        i = img.find(name, i + 1)
    assert reg, f'no registration for {cls}'
    # the registration passes InternalConstructor as one of the function pointers it forms; it is the one that
    # stores a constant (the vtable) at 0(r3)
    def vtable_stores(f, span):
        """constants stored to offset 0 of r3/r31/r30 (the object being constructed) in [f, f + span)"""
        out = []
        for a, v in lis_addi_values(f, f + span):
            nxt = word(a + 4)
            if nxt >> 26 == 36 and (nxt >> 16) & 31 in (3, 31, 30) and nxt & 0xFFFF == 0 and 0x82000000 <= v < 0x82300000:
                out.append(v)
        return out
    candidates = [f for _, f in lis_addi_values(reg[0] - 0x60, reg[0] + 0x80) if 0x82200000 <= f < 0x83000000 and f % 4 == 0]
    for f in candidates:
        try:
            if word(f) != 0x2B030000: continue   # InternalConstructor starts with cmplwi cr6,r3,0
        except IndexError: continue
        stores = vtable_stores(f, 0x40)
        if stores: return stores[-1]
        # it calls the class constructor instead: the last vtable that constructor stores is the most derived one
        for a in range(f, f + 0x40, 4):
            w = word(a)
            if w >> 26 == 18 and w & 1:                                  # bl
                target = (a + ((w & 0x3FFFFFC) - (0x4000000 if w & 0x2000000 else 0))) & 0xFFFFFFFF
                stores = vtable_stores(target, 0x200)
                if stores: return stores[-1]
    raise SystemExit(f'vtable of {cls} not found')

n = int(sys.argv[4]) if len(sys.argv) > 4 else 90
a = vtable(sys.argv[2])
print(f'{sys.argv[2]} vtable {a:08X}')
b = vtable(sys.argv[3]) if len(sys.argv) > 3 else None
if b: print(f'{sys.argv[3]} vtable {b:08X}')
for k in range(n):
    x = word(a + 4 * k)
    y = word(b + 4 * k) if b else None
    if not 0x82000000 <= x < 0x84000000: print(f'[{k}] end ({x:08X})'); break
    print(f'[{k:3}] +{4*k:03X} {x:08X}' + ('' if y is None else ('   same' if x == y else f'   base {y:08X}  <-- override')))
