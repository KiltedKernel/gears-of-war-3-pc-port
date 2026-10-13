"""Minimal reader for UE3 packages: little-endian PC cooks (e.g. Gears of War: Reloaded: file version 835, licensee
76, LZ4-compressed chunks with compression flag 0x20) and big-endian Xbox 360 cooks once decompressed with
tools/xpack (Gears of War 3: version 828; Gears of War 2: 575; Gears of War 1: 374, mostly uncompressed). The byte order comes from the tag. Decompresses, parses the name/import/export tables and can save an
export's raw data. Offline analysis of the player's own installed files: reads them, never writes next to them.
usage: python -P upk.py <package.upk> [summary|names|imports|exports [filter]|save <export index> <out>]"""
import struct, sys

E = '<'          # byte order of the package being read (set by Package)

def u32(d, o): return struct.unpack_from(E + 'I', d, o)[0]
def i32(d, o): return struct.unpack_from(E + 'i', d, o)[0]

def fstring(d, o):
    n = i32(d, o); o += 4
    if n >= 0: return d[o:o + max(0, n - 1)].decode('latin-1'), o + n
    return d[o:o - 2 * n - 2].decode('utf-16-le', 'replace'), o - 2 * n      # little-endian in Xbox packages too (CJK names)

class Package:
    def __init__(self, path, tables_only=False):
        """tables_only: decompress only the chunks holding the name/import/export tables (export data is then
        missing; fast class censuses). A package compressed as a whole is unpacked with xpack (bad blocks
        zero-filled)."""
        global E
        self.filename = path
        raw = open(path, "rb").read()
        E = '<' if struct.unpack_from('<I', raw, 0)[0] == 0x9E2A83C1 else '>'
        assert u32(raw, 0) == 0x9E2A83C1, 'not a UE3 package'
        if u32(raw, 4) in (0x20000, 0x9E2A83C1):     # compressed as a whole (Gears 1: tag again, little-endian, LZO)
            import os, subprocess, tempfile
            xpack = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xpack', 'xpack.exe')
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, 'whole.upk')
                codec = 'lzo' if E == '<' else 'lzx'          # Gears 1's wrapper is little-endian and LZO
                subprocess.run([xpack, path, out, '0', codec], capture_output=True, env=dict(os.environ, XPACK_SKIP_BAD='1'), check=True)
                raw = open(out, 'rb').read()
            E = '<' if struct.unpack_from('<I', raw, 0)[0] == 0x9E2A83C1 else '>'
        ver = u32(raw, 4)
        self.version, self.licensee = ver & 0xFFFF, ver >> 16
        o = 12
        self.folder, o = fstring(raw, o)
        self.package_flags_offset = o
        self.package_flags = u32(raw, o); o += 4
        (self.name_count, self.name_offset, self.export_count, self.export_offset,
         self.import_count, self.import_offset) = struct.unpack_from(E + '6i', raw, o); o += 24
        # DependsOffset: not in 374 (Gears of War 1); there the header size bounds the tables
        if self.version >= 415: self.depends_offset = i32(raw, o); o += 4
        else: self.depends_offset = u32(raw, 8)
        # 835: ImportExportGuidsOffset, ImportGuidsCount, ExportGuidsCount, ThumbnailTableOffset are absent;
        # Guid, generations, engine/cooker versions, compression
        if 623 <= self.version < 835: o += 16              # 828 (Gears 3): ImportExportGuidsOffset, two counts, ThumbnailTableOffset (not in 575, Gears 2)
        o += 16                                            # guid
        gens = i32(raw, o); o += 4 + 12 * gens
        self.engine_version, self.cooker_version, self.compression = struct.unpack_from(E + '3I', raw, o); o += 12
        self.compression_offset = o - 4                    # CompressionFlags, then the chunk table
        nchunks = i32(raw, o); o += 4
        self.chunk_count = nchunks
        chunks = [struct.unpack_from(E + '4i', raw, o + 16 * i) for i in range(nchunks)]
        if chunks and E == '>':
            # Xbox: LZX chunks (compression flag 4), each a stream like a fully compressed file; tools/xpack decodes one
            import os, subprocess, tempfile
            xpack = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xpack', 'xpack.exe')
            size = max(uo + us for uo, us, co, cs in chunks)
            data = bytearray(size)
            data[:chunks[0][0]] = raw[:chunks[0][0]]
            with tempfile.TemporaryDirectory() as tmp:
                for k, (uo, us, co, cs) in enumerate(chunks):
                    if tables_only and uo >= self.depends_offset: continue
                    out = os.path.join(tmp, f'{k}.bin')
                    r = subprocess.run([xpack, path, out, f'{co:x}', 'lzo' if self.compression == 2 else 'lzx'],
                                       capture_output=True, text=True)
                    if r.returncode != 0: raise ValueError(f'chunk {k}: {r.stderr.strip()}')
                    data[uo:uo + us] = open(out, 'rb').read()[:us]
            self.data = bytes(data)
        elif chunks:
            import lz4.block
            size = max(uo + us for uo, us, co, cs in chunks)
            data = bytearray(size)
            data[:chunks[0][0]] = raw[:chunks[0][0]]
            for uo, us, co, cs in chunks:
                assert u32(raw, co) == 0x9E2A83C1
                total_c, total_u = u32(raw, co + 8), u32(raw, co + 12)
                block_size = u32(raw, co + 4)
                nblocks = (total_u + block_size - 1) // block_size
                bo, src = co + 16, co + 16 + 8 * nblocks
                dst = uo
                for b in range(nblocks):
                    bc, bu = u32(raw, bo + 8 * b), u32(raw, bo + 8 * b + 4)
                    out = lz4.block.decompress(raw[src:src + bc], uncompressed_size=bu)
                    data[dst:dst + bu] = out; src += bc; dst += bu
            self.data = bytes(data)
        else:
            self.data = raw
        self._parse()

    def _parse(self):
        d = self.data
        self.names = []
        o = self.name_offset
        for _ in range(self.name_count):
            s, o = fstring(d, o); o += 8                   # name, u64 flags
            self.names.append(s)
        self.imports = []
        o = self.import_offset
        for _ in range(self.import_count):
            cp, cpn, cn, cnn, outer, on, onn = struct.unpack_from(E + 'iiiiiii', d, o); o += 28
            self.imports.append({'class_package': self.names[cp], 'class': self.names[cn], 'outer': outer,
                                 'name': self.fname(on, onn)})
        # export entries: class, super, outer, name, number, archetype, flags (u64), serial size, serial offset,
        # export flags, NetObjectCount array, package guid, package flags (575 and 828 alike: no ComponentMap; the
        # table then ends exactly at DependsOffset)
        self.exports = []
        o = self.export_offset
        for i in range(self.export_count):
            cls, sup, outer, on, onn, arch = struct.unpack_from(E + '6i', d, o)
            flags, = struct.unpack_from(E + 'Q', d, o + 24)
            size, off = struct.unpack_from(E + 'ii', d, o + 32)
            self.exports.append({'class': cls, 'outer': outer, 'name': self.fname(on, onn), 'size': size,
                                 'offset': off, 'flags': flags, 'entry': o})
            o2 = o + 40
            if self.version < 543:                                 # ComponentMap {FName, index} (Gears 1)
                ncomp = i32(d, o2)
                if not 0 <= ncomp < 10000: raise ValueError(f'export {i}: bad ComponentMap count {ncomp} at {o:#x}')
                o2 += 4 + 12 * ncomp
            nnet = i32(d, o2 + 4)
            if not 0 <= nnet < 100000: raise ValueError(f'export {i}: bad NetObjectCount count {nnet} at {o:#x}')
            o = o2 + 8 + 4 * nnet + 16 + (4 if self.version >= 475 else 0)   # guid; package flags from 475

    def fname(self, index, number):
        n = self.names[index] if 0 <= index < len(self.names) else f'?{index}'
        return n if number == 0 else f'{n}_{number - 1}'

    def class_name(self, ref):
        if ref < 0: return self.imports[-ref - 1]['name']
        if ref > 0: return self.exports[ref - 1]['name']
        return 'Class'

    def path(self, i):
        e = self.exports[i]
        parts = [e['name']]
        outer = e['outer']
        while outer > 0:
            parts.append(self.exports[outer - 1]['name']); outer = self.exports[outer - 1]['outer']
        return '.'.join(reversed(parts))

    def export_data(self, i):
        e = self.exports[i]
        return self.data[e['offset']:e['offset'] + e['size']]

    def properties(self, d, o=4):
        """Tagged properties from offset o (after the int32 NetIndex) up to "None": returns ([(name, type, index,
        value)], offset after them). value: int/float/bool/name, (struct name, raw bytes) or raw bytes for arrays.
        Version differences: BoolProperty's value is a u32 before 673 (Gears 2) and a byte after it (Gears 3);
        ByteProperty carries its enum's name from 633 (Gears 3)."""
        props = []
        while True:
            name = self.fname(i32(d, o), i32(d, o + 4)); o += 8
            if name == 'None': return props, o
            typ = self.fname(i32(d, o), i32(d, o + 4))
            size, index = i32(d, o + 8), i32(d, o + 12); o += 16
            if typ == 'BoolProperty':
                if self.version >= 673: v = d[o] != 0; o += 1
                else: v = u32(d, o) != 0; o += 4
            elif typ == 'StructProperty':
                sname = self.fname(i32(d, o), i32(d, o + 4)); o += 8
                v = (sname, d[o:o + size]); o += size
            elif typ == 'ByteProperty':
                if self.version >= 633: o += 8                                   # enum name
                v = self.fname(i32(d, o), i32(d, o + 4)) if size == 8 else d[o]; o += size
            elif typ in ('IntProperty', 'ObjectProperty', 'ComponentProperty', 'ClassProperty'):
                v = i32(d, o); o += size
            elif typ == 'FloatProperty':
                v = struct.unpack_from(E + 'f', d, o)[0]; o += size
            elif typ == 'NameProperty':
                v = self.fname(i32(d, o), i32(d, o + 4)); o += size
            elif typ in ('StrProperty',):
                v = fstring(d, o)[0]; o += size
            else:
                v = d[o:o + size]; o += size
            props.append((name, typ, index, v))

if __name__ == '__main__':
    p = Package(sys.argv[1])
    cmd = sys.argv[2] if len(sys.argv) > 2 else 'summary'
    if cmd == 'summary':
        print(f'version {p.version}/{p.licensee} engine {p.engine_version} cooker {p.cooker_version} compression {p.compression:#x}')
        print(f'{len(p.names)} names, {len(p.imports)} imports, {len(p.exports)} exports, {len(p.data)} bytes')
    elif cmd == 'names':
        print('\n'.join(p.names))
    elif cmd == 'imports':
        for i, im in enumerate(p.imports): print(-i - 1, im['class'], im['name'])
    elif cmd == 'exports':
        flt = sys.argv[3].lower() if len(sys.argv) > 3 else ''
        for i, e in enumerate(p.exports):
            line = f"{i:5} {p.class_name(e['class']):24} {p.path(i)}  ({e['size']} bytes)"
            if flt in line.lower(): print(line)
    elif cmd == 'save':
        open(sys.argv[4], 'wb').write(p.export_data(int(sys.argv[3])))
