"""UE3 reflection over a GoW2 memory dump (GOW3_MEM_DUMP -> build/memdump): names (GNames), objects (GObjObjects),
classes and their properties (name, offset), instances by class. Addresses of the two tables vary by run, so they
are found from a known name entry ("GameViewportClient" = index 1968) and a known object.
usage: python -P ue3dump.py <memdump folder> objects <class> | props <class> | obj <address> [count]"""
import glob, os, struct, sys
import numpy as np

class Dump:
    def __init__(self, folder):
        self.files = [(int(os.path.basename(f)[:8], 16), f) for f in sorted(glob.glob(os.path.join(folder, '*.bin')))]
        self.sizes = {f: os.path.getsize(f) for _, f in self.files}
        self.cache = {}
        self._find_tables()

    def _arr(self, f):
        if f not in self.cache: self.cache[f] = np.fromfile(f, dtype='>u4')
        return self.cache[f]

    def words(self, a, n=1):
        for b, f in self.files:
            if b <= a and a + 4 * n <= b + self.sizes[f]:
                i = (a - b) // 4
                return self._arr(f)[i:i + n].tolist()
        return None

    def u32(self, a):
        w = self.words(a); return w[0] if w else None

    def bytes(self, a, n):
        for b, f in self.files:
            if b <= a and a + n <= b + self.sizes[f]:
                with open(f, 'rb') as h: h.seek(a - b); return h.read(n)
        return None

    def _find_tables(self):
        needle = b'\x00\x00\x0f\x60\x00\x00\x00\x00GameViewportClient\x00'      # entry of name 1968 (Index = 1968*2)
        entry = None
        for b, f in self.files:
            with open(f, 'rb') as h: d = h.read()
            i = d.find(needle)
            if i != -1: entry = b + i; break
        assert entry, 'no GameViewportClient name entry'
        self.names = None
        for b, f in self.files:
            d = self._arr(f)
            for i in np.nonzero(d == entry)[0]:
                cand = b + 4 * int(i) - 4 * 1968
                if self.u32(cand + 4) and self.name_at(cand, 0) == 'None': self.names = cand; break
            if self.names: break
        assert self.names, 'no name table'
        # object table: the object named Default__GameViewportClient (1969) sits at its ObjectInternalInteger
        self.objects = None
        for b, f in self.files:
            if b >= 0xA0000000: continue
            d = self._arr(f)
            for i in np.nonzero((d[:-1] == 1969) & (d[1:] == 0))[0]:
                obj = b + 4 * int(i) - 44
                idx = self.u32(obj + 4)
                if idx is None or idx > 2000000: continue
                for bb, ff in self.files:
                    dd = self._arr(ff)
                    for j in np.nonzero(dd == obj)[0]:
                        start = bb + 4 * int(j) - 4 * idx
                        if self.u32(start + 4 * 2) and self.u32(self.u32(start + 4 * 2) + 4) == 2: self.objects = start; break
                    if self.objects: break
                if self.objects: break
            if self.objects: break
        assert self.objects, 'no object table'
        n = 0
        while True:
            o = self.u32(self.objects + 4 * n)
            if o is None: break
            n += 1
            if n > 3000000: break
        self.count = n

    def name_at(self, table, i):
        p = self.u32(table + 4 * i)
        if not p: return None
        s = self.bytes(p + 8, 96)
        return s.split(b'\0')[0].decode('latin-1') if s else None

    def name(self, i):
        return self.name_at(self.names, i) if 0 <= i < 400000 else None

    # UObject: +4 index, +40 Outer, +44 Name (index, number), +52 Class
    def obj_name(self, o):
        w = self.words(o + 44, 2) if o else None
        if not w: return None
        idx, num = w
        n = self.name(idx)
        return n if num == 0 else f'{n}_{num - 1}'

    def obj_class(self, o): return self.u32(o + 52)

    def full_name(self, o):
        parts, cur = [], o
        while cur:
            parts.append(self.obj_name(cur)); cur = self.u32(cur + 40)
        return '.'.join(reversed(parts))

    def all_objects(self):
        for i in range(self.count):
            o = self.u32(self.objects + 4 * i)
            if o: yield o

    def find_class(self, name):
        for o in self.all_objects():
            c = self.obj_class(o)
            if c and self.obj_name(o) == name and self.obj_name(c) == 'Class': return o
        return None

    def instances(self, cls_name):
        out = []
        cls_set = set()
        for o in self.all_objects():
            c = self.obj_class(o)
            if c and self.obj_name(c) == cls_name: out.append(o)
        return out

    # UStruct: +60 super struct, +76 first child, +80 properties size; UField: +64 next;
    # UProperty: +68 array dim, +72 element size, +100 offset
    def properties(self, cls):
        """(owner class, name, property class, offset, element size, array dim) for cls and its supers"""
        chain = []
        c = cls
        while c:
            chain.append(c); c = self.u32(c + 60)
        out = []
        for c in reversed(chain):
            f = self.u32(c + 76)
            guard = 0
            while f and guard < 5000:
                pc = self.obj_name(self.obj_class(f))
                if pc and pc.endswith('Property'):
                    out.append((self.obj_name(c), self.obj_name(f), pc, self.u32(f + 100), self.u32(f + 72), self.u32(f + 68)))
                f = self.u32(f + 64); guard += 1
        return out

    def prop_offset(self, cls_name, prop):
        for owner, n, pc, off, size, dim in self.properties(self.find_class(cls_name)):
            if n == prop: return off
        return None

if __name__ == '__main__':
    d = Dump(sys.argv[1])
    print(f'names at {d.names:#x}, objects at {d.objects:#x} ({d.count} slots)')
    cmd = sys.argv[2]
    if cmd == 'props':
        c = d.find_class(sys.argv[3])
        for owner, n, pc, off, size, dim in d.properties(c):
            print(f'  {off:#06x} {owner:24} {n:36} {pc} x{dim} ({size})')
    elif cmd == 'objects':
        for o in d.instances(sys.argv[3]): print(f'{o:#010x} {d.full_name(o)}')
    elif cmd == 'obj':
        o = int(sys.argv[3], 16); n = int(sys.argv[4]) if len(sys.argv) > 4 else 40
        print(d.full_name(o), 'class', d.obj_name(d.obj_class(o)))
        w = d.words(o, n)
        for k in range(0, n, 4):
            cells = []
            for x in w[k:k + 4]:
                tag = ''
                if 0x1000 <= x < 0xC0000000 and d.u32(x + 52) and d.u32(x + 4) is not None and d.u32(x + 4) < d.count and d.u32(d.objects + 4 * d.u32(x + 4)) == x:
                    tag = f'<{d.obj_name(x)}>'
                cells.append(f'{x:08X}{tag}')
            print(f'  +{4 * k:3}', ' '.join(cells))
