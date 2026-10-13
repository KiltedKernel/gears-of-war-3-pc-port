"""List an Xbox 360 XGD disc image (XDVDFS). usage: xdvdfs.py <iso> [extract <path-in-disc> <out>]"""
import struct, sys
SECTOR = 2048
MAGIC = b"MICROSOFT*XBOX*MEDIA"
f = open(sys.argv[1], "rb")


def find_base():
    for base in (0, 0xFD90000, 0x89D80000, 0x2080000, 0x18300000):  # plain / XGD2 / XGD3 / XGD1 / misc
        f.seek(base + 32 * SECTOR)
        if f.read(20) == MAGIC:
            return base
    raise SystemExit("no XDVDFS header found")


base = find_base()
f.seek(base + 32 * SECTOR + 20)
root_sector, root_size = struct.unpack("<II", f.read(8))


def read_dir(sector, size):
    f.seek(base + sector * SECTOR)
    data = f.read(size)
    out = []

    def walk(off):
        if off * 4 + 14 > len(data):
            return
        l, r, start, fsize, attr, nlen = struct.unpack_from("<HHIIBB", data, off * 4)
        if l == 0xFFFF and r == 0xFFFF:
            return
        name = data[off * 4 + 14: off * 4 + 14 + nlen].decode("latin-1")
        out.append((name, start, fsize, attr))
        if l: walk(l)
        if r: walk(r)
    walk(0)
    return out


def tree(sector, size, prefix=""):
    for name, start, fsize, attr in sorted(read_dir(sector, size)):
        path = prefix + name
        if attr & 0x10:
            yield path + "/", start, fsize, True
            if fsize:
                yield from tree(start, fsize, path + "/")
        else:
            yield path, start, fsize, False


if len(sys.argv) > 3 and sys.argv[2] == "extract-all":
    pass
elif len(sys.argv) > 3 and sys.argv[2] == "extract":
    for path, start, fsize, isdir in tree(root_sector, root_size):
        if path.lower() == sys.argv[3].lower():
            f.seek(base + start * SECTOR)
            with open(sys.argv[4], "wb") as o:
                left = fsize
                while left:
                    chunk = f.read(min(left, 1 << 22)); o.write(chunk); left -= len(chunk)
            print("extracted", path, fsize); break
else:
    print(f"partition base {base:#x}")
    total = 0
    for path, start, fsize, isdir in tree(root_sector, root_size):
        if not isdir:
            total += fsize
        if path.count("/") <= 2 or path.lower().endswith((".xex", ".ini")):
            print(f"{fsize:>12} {path}")
    print("total bytes", total)


def extract_all(out_root):
    import os
    n = 0
    for path, start, fsize, isdir in tree(root_sector, root_size):
        dst = os.path.join(out_root, path.replace("/", os.sep))
        if isdir:
            os.makedirs(dst, exist_ok=True); continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst) and os.path.getsize(dst) == fsize:
            continue
        f.seek(base + start * SECTOR)
        with open(dst, "wb") as o:
            left = fsize
            while left:
                chunk = f.read(min(left, 1 << 22)); o.write(chunk); left -= len(chunk)
        n += 1
    print("extracted", n, "files to", out_root)
if len(sys.argv) > 3 and sys.argv[2] == "extract-all":
    extract_all(sys.argv[3])
