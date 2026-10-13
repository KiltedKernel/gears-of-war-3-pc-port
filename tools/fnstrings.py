"""List the strings a recompiled guest function refers to (lis/addi pairs into .rdata), to name it.
usage: python -P fnstrings.py <default.bin> <ppc dir> <function address hex> [...]"""
import glob, os, re, sys


def read_string(d, va):
    o = va - 0x82000000
    if not 0 <= o < len(d) - 2: return None
    if d[o] == 0 and 32 <= d[o + 1] < 127:          # UTF-16BE
        out = ''
        for k in range(o, o + 400, 2):
            c = (d[k] << 8) | d[k + 1]
            if c == 0: break
            if c < 32 or c > 0x7e: return None
            out += chr(c)
        return out if len(out) > 2 else None
    e = o
    while e < len(d) and 32 <= d[e] < 127 and e - o < 200: e += 1
    return d[o:e].decode() if e - o > 3 and d[e] == 0 else None


def body(ppc_dir, addr):
    pat = re.compile(r'^PPC_FUNC_IMPL\(__imp__sub_%X\)' % addr)
    for f in glob.glob(os.path.join(ppc_dir, 'ppc_recomp.*.cpp')):
        lines = open(f, encoding='utf8', errors='replace').read().split('\n')
        for k, l in enumerate(lines):
            if pat.match(l):
                out = []
                for l2 in lines[k + 1:]:
                    if l2.startswith('PPC_FUNC_IMPL') or l2.startswith('PPC_WEAK_FUNC'): break
                    out.append(l2)
                return out
    return []


def main():
    d = open(sys.argv[1], 'rb').read()
    for a in sys.argv[3:]:
        addr = int(a, 16)
        lines = body(sys.argv[2], addr)
        hi = {}
        found, calls = [], []
        for l in lines:
            m = re.search(r'// lis (r\d+),(-?\d+)', l)
            if m: hi[m.group(1)] = (int(m.group(2)) & 0xffff) << 16; continue
            m = re.search(r'// addi (r\d+),(r\d+),(-?\d+)', l)
            if m and m.group(2) in hi:
                s = read_string(d, (hi[m.group(2)] + int(m.group(3))) & 0xffffffff)
                if s and s not in found: found.append(s)
            m = re.search(r'// bl (0x[0-9a-f]+)', l)
            if m: calls.append(m.group(1))
        print(f'sub_{addr:X}: {len(lines)} lines, {len(calls)} calls; strings: {found[:25]}')


if __name__ == '__main__':
    main()
