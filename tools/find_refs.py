"""Find code that forms a 32-bit address with lis rX,hi ... addi/ori/lwz/stw rY,rX,lo (within 12 instructions).
usage: find_refs.py default.bin 0x82106080 [0x...]"""
import struct, sys

BASE = 0x82000000
img = open(sys.argv[1], 'rb').read()
targets = [int(a, 16) for a in sys.argv[2:]]
words = struct.unpack(f'>{len(img) // 4}I', img[:len(img) // 4 * 4])

def field(i, lo, n): return (i >> (31 - lo - n + 1)) & ((1 << n) - 1)

for target in targets:
    hi = (target + 0x8000) >> 16 & 0xFFFF
    lo = target & 0xFFFF
    hits = []
    for k, w in enumerate(words):
        op = w >> 26
        if op not in (14, 24, 32, 36, 34, 38, 40, 44, 48, 50, 52, 58, 62) or ((w & 0xFFFF) & (0xFFFC if op in (58, 62) else 0xFFFF)) != lo:   # addi ori lwz stw lbz stb lhz sth lfs lfd stfs
            continue
        ra = field(w, 11, 5)
        for b in range(k - 1, max(k - 13, 0), -1):
            p = words[b]
            if p >> 26 == 15 and field(p, 11, 5) == 0 and field(p, 6, 5) == ra:   # lis ra,hi
                if (p & 0xFFFF) == hi:
                    hits.append(BASE + 4 * k)
                break
    print(f'{target:08X}: {len(hits)} refs', ' '.join(f'{h:08X}' for h in hits[:30]))
