"""UE3 (Xbox 360) Coalesced_*.bin: every .ini of the game packed in one file.
Layout (big-endian): int32 file count; per file: string name, int32 section count; per section: string name,
int32 key count; per key: string key, string value. String: int32 n; n > 0: n ANSI bytes incl. NUL; n < 0: -n
UTF-16LE code units incl. NUL; n == 0: empty.

usage: coalesced.py dump  Coalesced_int.bin out.txt         (readable ini text, one [file] header per ini)
       coalesced.py check Coalesced_int.bin                 (round trip: parse + write == original)
       coalesced.py merge Coalesced_int.bin out.bin fragment.ini [...]   (mods' config changes, see merge())"""
import struct, sys


class Reader:
    def __init__(self, d): self.d, self.p = d, 0
    def i32(self):
        v = struct.unpack_from('>i', self.d, self.p)[0]; self.p += 4; return v
    def s(self):
        n = self.i32()
        if n == 0: return ''
        if n > 0:
            v = self.d[self.p:self.p + n - 1].decode('latin-1'); self.p += n; return v
        n = -n
        v = self.d[self.p:self.p + 2 * (n - 1)].decode('utf-16-le'); self.p += 2 * n; return v


def parse(d):
    r = Reader(d)
    files = []
    for _ in range(r.i32()):
        name = r.s(); sections = []
        for _ in range(r.i32()):
            sname = r.s(); keys = []
            for _ in range(r.i32()):
                k = r.s(); v = r.s(); keys.append((k, v))
            sections.append((sname, keys))
        files.append((name, sections))
    assert r.p == len(d), (r.p, len(d))
    return files


def wstr(s):
    """Gears 3's file stores every string as UTF-16 (negative length, NUL included); empty as 0"""
    if s == '': return struct.pack('>i', 0)
    b = s.encode('utf-16-le')
    return struct.pack('>i', -(len(b) // 2 + 1)) + b + bytes(2)


def write(files, unicode_names=True):
    out = [struct.pack('>i', len(files))]
    for name, sections in files:
        out.append(wstr(name)); out.append(struct.pack('>i', len(sections)))
        for sname, keys in sections:
            out.append(wstr(sname)); out.append(struct.pack('>i', len(keys)))
            for k, v in keys: out.append(wstr(k)); out.append(wstr(v))
    return b''.join(out)


def merge(files, text, source='fragment'):
    """apply an ini fragment to parsed files (in place). The fragment names its ini with '##### <file name>' lines
    (matched on the end of the packed name: '##### Xbox360-GearGame.ini', '##### GearGame.int'); then [sections]
    and keys, UE3-style:
      Key=v     every value of Key replaced by v (the first such line in a section; later plain lines add values)
      +Key=v    v added (unless present)          -Key=v    v removed
      *Key=v    replaces the value with the same first field as v ("(Identifier=MAINMENUOP_Campaign,...)": a menu
                entry), else added
    A section or file that does not exist is created."""
    target = None; sec = None; replaced = set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(';') or line.startswith('#') and not line.startswith('#####'): continue
        if line.startswith('#####'):
            want = line[5:].strip().lower()
            target = next((f for f in files if f[0].lower().replace('/', '\\').endswith(want)), None)
            if target is None:
                target = ('..\\..\\GearGame\\Config\\' + line[5:].strip(), []); files.append(target)
            sec = None; continue
        if target is None: raise SystemExit(f'{source}:{n}: no ##### <ini file> line before this')
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1]
            sec = next((x for x in target[1] if x[0] == name), None)
            if sec is None: sec = (name, []); target[1].append(sec)
            replaced = set(); continue
        if sec is None or '=' not in line: raise SystemExit(f'{source}:{n}: expected [section] or key=value')
        op = line[0] if line[0] in '+-*' else ''
        k, v = line[len(op):].split('=', 1)
        keys = sec[1]
        if op == '+':
            if (k, v) not in keys: keys.append((k, v))
        elif op == '-':
            keys[:] = [kv for kv in keys if kv != (k, v)]
        elif op == '*':
            first = v.split(',', 1)[0]
            at = next((i for i, kv in enumerate(keys) if kv[0] == k and kv[1].split(',', 1)[0] == first), None)
            if at is None: keys.append((k, v))
            else: keys[at] = (k, v)
        else:
            if k not in replaced:
                keys[:] = [kv for kv in keys if kv[0] != k]; replaced.add(k)
            keys.append((k, v))
    return files


if __name__ == '__main__':
    cmd, path = sys.argv[1], sys.argv[2]
    d = open(path, 'rb').read()
    files = parse(d)
    if cmd == 'dump':
        with open(sys.argv[3], 'w', encoding='utf-8') as f:
            for name, sections in files:
                f.write(f'##### {name}\n')
                for sname, keys in sections:
                    f.write(f'[{sname}]\n')
                    for k, v in keys: f.write(f'{k}={v}\n')
                f.write('\n')
        print(f'{len(files)} files, {sum(len(s) for _, s in files)} sections')
    elif cmd == 'merge':
        for frag in sys.argv[4:]:
            merge(files, open(frag, encoding='utf-8').read(), frag)
        open(sys.argv[3], 'wb').write(write(files))
        print(f'{len(sys.argv) - 4} fragment(s) merged -> {sys.argv[3]}')
    elif cmd == 'check':
        print('files', len(files), 'round trip identical' if write(files) == d else 'round trip DIFFERS (string encoding)')
