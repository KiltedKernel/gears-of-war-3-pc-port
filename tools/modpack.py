"""Build a Gears of War 3 mod folder (build/mods, which the runtime lays over the disc) that replaces textures with
Gears of War 2 ones. Offline, from the player's own discs; the output is derived game data and is never shipped.

For each replacement, the Gears 3 package holding the texture is rewritten:
  - uncompressed: PKG_StoreCompressed, the summary's CompressionFlags and chunk table are cleared (the summary keeps its size, padded,
    so every absolute offset in the package stays valid), the rest is the decompressed image unchanged;
  - the Texture2D export gets new serial data appended at the end of the file, and its export-table entry is
    pointed there: the Gears 3 properties (with TextureFileCacheName renamed to the mod's cache), then Gears 2's
    mips. Mips stored in Gears 2's Textures.tfc are copied as they are (LZX chunks; both games use the same
    XMemCompress framing and the same tiled Xbox formats) into the mod's texture cache, <name>_<n>.tfc, which the
    engine opens by name like its own caches (it tries <name>.xxx, then <name>.tfc). Inline mips are copied with
    their BulkDataOffsetInFile set to their new place.
Xbox360TOC.txt (sizes and MD5s; the engine reads it) is copied with the new sizes and the cache added.

Meshes (tools/meshconv.py) are converted from Gears 1 or 2 onto the Gears 3 mesh they replace, appended the same way.
usage: python -P modpack.py <mods folder> <gears3 cooked folder> <gears2 cooked folder> <replacements.txt> [gears1 cooked folder]
replacements.txt, one per line:
  texture <gears3 package> <gears3 texture path> g2:<package> <texture path>
  mesh    <gears3 package> <gears3 mesh path, or * for all> g1|g2|g3:<package> <mesh path, or = for the same path>
  (a 4-field line is a texture from Gears 2)"""
import hashlib, os, struct, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import upk, texture2d, staticmesh, meshconv

TFC_NUMBER = 2            # FName number: <name>_1.tfc


def load(path):
    p = upk.Package(path)                          # (a package compressed as a whole is unpacked by upk)
    p.cooked = os.path.dirname(path)
    return p


def find(p, path, cls='Texture2D'):
    for i in range(len(p.exports)):
        if p.class_name(p.exports[i]['class']) == cls and p.path(i).lower() == path.lower(): return i
    raise KeyError(f'{cls} {path} not in {p.filename}')


def uncompressed_image(p):
    """the package as an uncompressed file, every offset unchanged"""
    d = bytearray(p.data)
    E = upk.E
    co, n = p.compression_offset, p.chunk_count
    tail = bytes(d[co + 8 + 16 * n:p.name_offset])
    struct.pack_into(E + 'Ii', d, co, 0, 0)
    struct.pack_into(E + 'I', d, p.package_flags_offset, p.package_flags & ~0x02000000)   # PKG_StoreCompressed: the
    # loader would otherwise set up its chunk map (sub_822E9E78 -> 822B92D8 reads chunks[count - 1])
    d[co + 8:p.name_offset] = tail + bytes(16 * n)
    return d


def texture_data(p3, i3, t3, t2, base, tfc_name, tfc):
    """new serial data for Gears 3 texture i3 with Gears 2 texture t2's mips; base = its file offset"""
    E = upk.E
    d3 = p3.export_data(i3)
    props, o = p3.properties(d3)
    head = bytearray(d3[:o])
    if t3.tfc:                                     # rename the cache: the value follows the 24-byte tag
        idx = p3.names.index('TextureFileCacheName')
        at = bytes(head).find(struct.pack(E + 'ii', idx, 0))
        name_idx = p3.names.index(tfc_name)
        struct.pack_into(E + 'ii', head, at + 24, name_idx, TFC_NUMBER)
    out = bytearray(head)
    out += struct.pack(E + 'Iiii', 0, 0, 0, base + len(out) + 16)              # SourceArt: empty
    out += struct.pack(E + 'i', len(t2.mips))
    for m, mip in enumerate(t2.mips):
        if mip['flags'] & 0x20:
            out += struct.pack(E + 'Iiii', mip['flags'], 0, -1, -1)
        elif mip['inline'] is None:                                            # in Gears 2's cache: copy the chunk
            with open(os.path.join(t2.pkg.cooked, t2.tfc + '.tfc'), 'rb') as f:
                f.seek(mip['offset']); chunk = f.read(mip['size'])
            out += struct.pack(E + 'Iiii', mip['flags'], mip['count'], len(chunk), tfc.tell())
            tfc.write(chunk)
        else:
            out += struct.pack(E + 'Iiii', mip['flags'], mip['count'], len(mip['inline']), base + len(out) + 16)
            out += mip['inline']
        out += struct.pack(E + 'ii', mip['w'], mip['h'])
    out += t3.guid + t3.trailer
    return bytes(out)


def main():
    mods, g3dir, g2dir, spec = sys.argv[1:5]
    sources = {'g3': g3dir, 'g2': g2dir, 'g1': sys.argv[5] if len(sys.argv) > 5 else None}
    jobs = {}
    for line in open(spec):
        if line.strip() and not line.startswith('#'):
            f = line.split()
            if len(f) == 4: f = ['texture'] + f[:2] + ['g2:' + f[2], f[3]]        # the original texture-only lines
            kind, pkg3, path3, src, path2 = f
            game, pkg2 = src.split(':', 1)
            jobs.setdefault(pkg3, []).append((kind, path3, game, pkg2, path2))
    cooked = os.path.join(mods, 'GearGame', 'CookedXbox360')
    os.makedirs(cooked, exist_ok=True)
    tfc_name = 'Textures'
    tfc_file = f'{tfc_name}_{TFC_NUMBER - 1}.tfc'
    g2cache = {}
    written = {}
    with open(os.path.join(cooked, tfc_file), 'wb') as tfc:
        for pkg3, items in jobs.items():
            p3 = load(os.path.join(g3dir, pkg3))
            img = uncompressed_image(p3)
            for kind, path3, game, pkg2, path2 in items:
                if (game, pkg2) not in g2cache: g2cache[(game, pkg2)] = load(os.path.join(sources[game], pkg2))
                p2 = g2cache[(game, pkg2)]
                upk.E = '>'
                if kind == 'mesh':
                    targets = ([i for i, e in enumerate(p3.exports) if p3.class_name(e['class']) == 'StaticMesh']
                               if path3 == '*' else [find(p3, path3, 'StaticMesh')])
                    for i3 in targets:                 # '*': every static mesh of the package (a visibility test)
                        # source '=': the object of the same path (with g3:, the writer's round trip on Gears 3's own meshes)
                        src = staticmesh.read(p2, find(p2, p3.path(i3) if path2 == '=' else path2, 'StaticMesh'))
                        img += bytes((-len(img)) % 16)
                        base = len(img)
                        data = meshconv.to_828(p3, i3, src)
                        img += data
                        struct.pack_into('>ii', img, p3.exports[i3]['entry'] + 32, len(data), base)
                    print(f'{pkg3}: {len(targets)} mesh(es) {path3} <- {game}:{pkg2}: {path2}')
                    continue
                i3, i2 = find(p3, path3), find(p2, path2)
                t3, t2 = texture2d.Texture(p3, i3), texture2d.Texture(p2, i2)
                if (t3.format, t3.props.get('SizeX'), t3.props.get('SizeY')) != (t2.format, t2.props.get('SizeX'), t2.props.get('SizeY')):
                    raise ValueError(f'{path3} {t3.format} {t3.props.get("SizeX")}x{t3.props.get("SizeY")} vs '
                                     f'{path2} {t2.format} {t2.props.get("SizeX")}x{t2.props.get("SizeY")}: resizing is not done yet')
                img += bytes((-len(img)) % 16)
                base = len(img)
                data = texture_data(p3, i3, t3, t2, base, tfc_name, tfc)
                img += data
                struct.pack_into('>ii', img, p3.exports[i3]['entry'] + 32, len(data), base)
                print(f'{pkg3}: {path3} <- {pkg2}: {path2} ({len(data)} bytes at {base:#x})')
            open(os.path.join(cooked, pkg3), 'wb').write(img)
            written[pkg3] = img
    written[tfc_file] = open(os.path.join(cooked, tfc_file), 'rb').read()
    update_toc(mods, g3dir)


def update_toc(mods, g3dir):
    """mods\\GearGame\\Xbox360TOC.txt: the disc's TOC with the size and MD5 of every file in the mod's cooked
    folder (added when new)"""
    if os.path.isfile(os.path.join(mods, 'mod.ini')):  # a mod folder: the runtime merges the TOC (kernel/mods.cpp)
        stale = os.path.join(mods, 'GearGame', 'Xbox360TOC.txt')
        if os.path.isfile(stale): os.remove(stale)
        return
    cooked = os.path.join(mods, 'GearGame', 'CookedXbox360')
    written = {f: open(os.path.join(cooked, f), 'rb').read() for f in os.listdir(cooked)}
    toc_in = os.path.join(os.path.dirname(g3dir), 'Xbox360TOC.txt')
    lines, seen = [], set()
    for line in open(toc_in, encoding='latin-1').read().splitlines():
        parts = line.split(' ')
        name = parts[2].split('\\')[-1] if len(parts) >= 4 else ''
        if name in written:
            b = written[name]; seen.add(name)
            line = f'{len(b)} 0 {parts[2]} {hashlib.md5(b).hexdigest()}'
        lines.append(line)
    for name, b in written.items():
        if name not in seen:
            lines.insert(0, f'{len(b)} 0 ..\\GearGame\\CookedXbox360\\{name} {hashlib.md5(b).hexdigest()}')
    open(os.path.join(mods, 'GearGame', 'Xbox360TOC.txt'), 'w', encoding='latin-1', newline='\r\n').write('\n'.join(lines) + '\n')
    print(f'{len(written)} files in {cooked}, TOC updated')


if __name__ == '__main__':
    main()
