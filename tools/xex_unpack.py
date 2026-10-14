"""Decrypt + decompress an XEX2 (retail AES, basic compression) to its flat memory image, like XenonUtils/xex.cpp.
usage: xex_unpack.py default.xex out.bin   (analysis copy only: it stays with the player's game files, never shipped)
The retail key is not part of this repository: it is read from XenonRecomp's XenonUtils/xex.h (Xex2RetailKey), which
tools/setup_xenonrecomp.sh fetches into tools/XenonRecomp before this runs."""
import os, re, struct, sys
from Crypto.Cipher import AES

def retail_key():
    h = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'XenonRecomp', 'XenonUtils', 'xex.h')
    if not os.path.exists(h):
        sys.exit(f'{h} not found: run tools/setup_xenonrecomp.sh first (it fetches XenonRecomp)')
    m = re.search(r'Xex2RetailKey\s*\[\s*16\s*\]\s*=\s*\{([^}]*)\}', open(h, encoding='utf8', errors='replace').read())
    if not m:
        sys.exit(f'no Xex2RetailKey in {h}')
    key = bytes(int(v, 0) for v in m.group(1).split(','))
    assert len(key) == 16
    return key

d = open(sys.argv[1], "rb").read()
be = lambda f, o: struct.unpack_from(">" + f, d, o)
_, _, header_size, _, sec_off, nopt = be("4sIIIII", 0)
opts = dict(be("II", 24 + i * 8) for i in range(nopt))
image_size = be("I", sec_off + 4)[0]
aes_key_enc = d[sec_off + 0x150: sec_off + 0x160]          # Xex2SecurityInfo.aesKey
ff = opts[0x3FF]
info_size, enc, comp = be("IHH", ff)
src = d[header_size:]
if enc == 1:
    key = AES.new(retail_key(), AES.MODE_CBC, bytes(16)).decrypt(aes_key_enc)
    n = len(src) // 16 * 16
    src = AES.new(key, AES.MODE_CBC, bytes(16)).decrypt(src[:n])
assert comp == 1, f"compression {comp} not handled here"
nblocks = info_size // 8 - 1
out = bytearray(); p = 0
for i in range(nblocks):
    dsz, zsz = be("II", ff + 8 + i * 8)
    out += src[p:p + dsz]; p += dsz
    out += bytes(zsz)
print(f"image {len(out):#x} bytes (security image size {image_size:#x}); MZ={out[:2]}")
open(sys.argv[2], "wb").write(out)
