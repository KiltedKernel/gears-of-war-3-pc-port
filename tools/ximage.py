"""Sections of a flat Xbox 360 image (default.bin from xex_unpack.py), from its PE header. Shared by the tools so no
game's addresses are hard-coded: sections(img) -> {name: (start, size, flags)}, code_span(img) -> (start, end)."""
import struct

BASE = 0x82000000


def sections(img):
    pe = struct.unpack_from("<I", img, 0x3C)[0]
    n, opt = struct.unpack_from("<H", img, pe + 6)[0], struct.unpack_from("<H", img, pe + 20)[0]
    out = {}
    for k in range(n):
        h = pe + 24 + opt + 40 * k
        name = img[h:h + 8].split(b"\0")[0].decode("latin-1")
        vsize, va = struct.unpack_from("<II", img, h + 8)
        out[name] = (BASE + va, vsize, struct.unpack_from("<I", img, h + 36)[0])
    return out


def code_span(img):
    """first to last code section (.text, BINK, RADCODE ...): contiguous apart from padding"""
    code = [(s, s + z) for s, z, f in sections(img).values() if f & 0x20]
    return min(a for a, _ in code), max(b for _, b in code) & ~3


def pdata(img):
    s, z, _ = sections(img)[".pdata"]
    return s, z
