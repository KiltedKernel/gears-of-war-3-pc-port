"""Rewrite a big-endian Xbox UE3 package (Gears of War 3, 828) with objects added: names, imports and exports can be
appended, and export data replaced or appended. Every byte of the original export data stays where it was (bulk
data inside exports records absolute file offsets), so the new tables go at the end of the file and the summary
points there; the old tables remain as dead bytes. The output is uncompressed (as tools/modpack.py writes it).

Gears 3's header: summary, names, imports, exports, depends (one empty TArray<int> per export when cooked), the
import/export GUID table (empty), then export data from TotalHeaderSize. Export entries are kept as raw bytes; a new
entry is built from fields. usage (experiment): python -P upkwrite.py <package> <out>   (rewrites with no change)"""
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk


class Rewriter:
    def __init__(self, pkg):
        assert pkg.version == 828, 'Gears 3 packages only'
        self.p = pkg
        d = pkg.data
        self.names = list(pkg.names)
        self.name_flags = []
        o = pkg.name_offset
        for _ in range(pkg.name_count):
            n = struct.unpack_from('>i', d, o)[0]
            o += 4 + (n if n >= 0 else -2 * n)
            self.name_flags.append(d[o:o + 8]); o += 8
        self.name_bytes = [None] * len(self.names)          # original encoding, kept for unchanged names
        o = pkg.name_offset
        for k in range(pkg.name_count):
            n = struct.unpack_from('>i', d, o)[0]
            ln = 4 + (n if n >= 0 else -2 * n)
            self.name_bytes[k] = d[o:o + ln]; o += ln + 8
        self.imports = [d[pkg.import_offset + 28 * k:pkg.import_offset + 28 * (k + 1)] for k in range(pkg.import_count)]
        self.exports = []
        for k, e in enumerate(pkg.exports):
            end = pkg.exports[k + 1]['entry'] if k + 1 < len(pkg.exports) else pkg.depends_offset
            self.exports.append(bytearray(d[e['entry']:end]))
        self.guid = None                                      # 16 bytes: a new package GUID (a clone under another name)
        self.appended = {}                                    # export index -> data (the last replacement; insertion order)
        self.fixups = {}                                      # export index -> [(field offset, data offset)]

    def name(self, s, number=0):
        """FName (index, number) for s, adding it if needed"""
        if s not in self.names:
            self.names.append(s); self.name_flags.append(bytes.fromhex('0007001000000000')); self.name_bytes.append(None)
        return self.names.index(s), number

    def add_import(self, class_package, class_name, outer, object_name, number=0):
        """a new import (object_name with its FName number: Foo_3 is Foo, number 4); returns its ref"""
        cp, cn, on = self.name(class_package), self.name(class_name), self.name(object_name)
        self.imports.append(struct.pack('>7i', cp[0], 0, cn[0], 0, outer, on[0], number))
        return -len(self.imports)

    def add_export(self, cls, outer, name, data, flags=0x000F0004, archetype=0, super_=0, fixups=()):
        """a new export (class/outer/archetype as object refs); returns its 1-based ref. fixups: [(field, target)]
        offsets within data: the int32 at `field` becomes the absolute file offset of `target` (bulk data headers)"""
        n = self.name(*name) if isinstance(name, tuple) else self.name(name)
        entry = bytearray(struct.pack('>6i', cls, super_, outer, n[0], n[1], archetype))
        entry += struct.pack('>Q', flags) + struct.pack('>ii', len(data), 0)
        entry += struct.pack('>I', 0) + struct.pack('>i', 0) + bytes(16) + struct.pack('>I', 0)   # export flags, net objects, guid, package flags
        self.exports.append(entry)
        self.appended[len(self.exports) - 1] = bytes(data)
        if fixups: self.fixups[len(self.exports) - 1] = list(fixups)
        return len(self.exports)

    def replace(self, index, data):
        """new serial data for export index (0-based)"""
        # only the last version is written: a superseded copy was dead data in the file (a level export replaced once
        # per source package left 7 MB of them)
        self.appended[index] = bytes(data)

    def current(self, index):
        """export index's serial data as it will be written: the last replacement, else the original"""
        if index in self.appended: return self.appended[index]
        return self.p.export_data(index)

    def build(self):
        import modpack
        img = modpack.uncompressed_image(self.p)
        # Net indices: unique among a package's own objects. Gears 3's checkpoints save Kismet, and the objects its
        # variables point at, by them: an object at -1 (or sharing an index) is saved as None. On Continue the
        # chapter's Main_Sequence lost its converted Kismet (G1_<level>, TrilogySharedTextures; 2026-11-03), and a
        # level's object variables lost the converted actors they name (S04's roadie-run door: its matinee played
        # on nothing; 2026-11-05). Objects added here came with -1 or with their source package's indices
        # (duplicates): each gets the next free index, after the highest of the package's own objects. Where it is:
        # an actor's (RF_HasStack) after its state frame (Node, StateNode, ProbeMask 8, LatentAction 2, Offset:
        # 22 bytes; all 317 in a stock level), a component's after its template owner class (and, when that is
        # set, its template name), anything else's first.
        orig = len(self.p.exports)
        def cls_name(k):
            c = struct.unpack_from('>i', self.exports[k], 0)[0]
            if c < 0: return self.names[struct.unpack_from('>i', self.imports[-c - 1], 20)[0]]
            return self.names[struct.unpack_from('>i', self.exports[c - 1], 12)[0]] if c > 0 else 'Class'
        def net_at(k, data):
            if struct.unpack_from('>Q', self.exports[k], 24)[0] & 0x0200000000000000: return 22
            c = cls_name(k)
            if c.endswith('Component') or c.startswith('Distribution'):
                return 4 if struct.unpack_from('>i', data, 0)[0] == 0 else 12
            return 0
        top = -1
        for k in range(orig):
            x = self.p.export_data(k)
            o = net_at(k, x) if len(x) >= 4 else None
            if o is not None and len(x) >= o + 4: top = max(top, struct.unpack_from('>i', x, o)[0])
        next_net = top + 1
        for k in range(orig, len(self.exports)):
            data = self.appended.get(k)
            if data is None or len(data) < 4: continue
            o = net_at(k, data)
            if len(data) < o + 4: continue
            self.appended[k] = data[:o] + struct.pack('>i', next_net) + data[o + 4:]
            next_net += 1
        self.net_objects = next_net
        for k, data in self.appended.items():
            img += bytes((-len(img)) % 16)
            base = len(img)
            img += data
            struct.pack_into('>ii', self.exports[k], 32, len(data), base)
            for field, target in self.fixups.get(k, ()):
                struct.pack_into('>i', img, base + field, base + target)
        img += bytes((-len(img)) % 16)
        so = self.p.package_flags_offset + 4
        def put(o, *v): struct.pack_into(f'>{len(v)}i', img, o, *v)
        name_off = len(img)
        for k, s in enumerate(self.names):
            if self.name_bytes[k] is not None: img += self.name_bytes[k]
            else: img += struct.pack('>i', len(s) + 1) + s.encode('latin-1') + b'\0'
            img += self.name_flags[k]
        imp_off = len(img); img += b''.join(self.imports)
        exp_off = len(img); img += b''.join(bytes(e) for e in self.exports)
        dep_off = len(img); img += bytes(4 * len(self.exports))
        guid_off = len(img)
        put(so, len(self.names), name_off, len(self.exports), exp_off, len(self.imports), imp_off, dep_off)
        put(so + 28, guid_off, 0, 0, 0)                       # ImportExportGuidsOffset, counts, ThumbnailTableOffset
        if self.guid is not None: img[so + 44:so + 60] = self.guid   # the package GUID (then the generations)
        # the newest generation records the export and name counts
        go = so + 28 + 16 + 16
        gens = struct.unpack_from('>i', img, go)[0]
        old_net = struct.unpack_from('>i', img, go + 4 + 12 * (gens - 1) + 8)[0]
        put(go + 4 + 12 * (gens - 1), len(self.exports), len(self.names), max(old_net, self.net_objects))
        return img


if __name__ == '__main__':
    p = upk.Package(sys.argv[1])
    img = Rewriter(p).build()
    open(sys.argv[2], 'wb').write(img)
    q = upk.Package(sys.argv[2])
    same = all(q.export_data(i) == p.export_data(i) for i in range(len(p.exports)))
    print(f'{len(img)} bytes; reread: {len(q.names)} names, {len(q.imports)} imports, {len(q.exports)} exports, '
          f'export data {"identical" if same else "DIFFERENT"}')
