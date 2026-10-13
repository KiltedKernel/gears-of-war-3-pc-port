"""Decrypt + decompress an XEX2 (retail AES, basic compression) to its flat memory image, like XenonUtils/xex.cpp.
usage: xex_unpack.py default.xex out.bin   (analysis copy only: keep it in D:/gow3-decomp, never ship it)"""
import struct, sys
from Crypto.Cipher import AES
RETAIL_KEY = bytes([0x00  # (key removed from the history: read from XenonRecomp)])
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
    key = AES.new(RETAIL_KEY, AES.MODE_CBC, bytes(16)).decrypt(aes_key_enc)
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
