"""Find the PPC register save/restore helpers XenonRecomp needs (byte patterns from its README) in the dumped image."""
import struct, sys
img = open(sys.argv[1], "rb").read()
BASE = 0x82000000
TEXT = (0x82220000, 0x82220000 + 0x00D2DBCC)
pats = {
    "restgprlr_14_address": "e9c1ff68", "savegprlr_14_address": "f9c1ff68",
    "restfpr_14_address": "c9ccff70", "savefpr_14_address": "d9ccff70",
    "restvmx_14_address": "3960fee07dcb60ce", "savevmx_14_address": "3960fee07dcb61ce",
    "restvmx_64_address": "3960fc00100b60cb", "savevmx_64_address": "3960fc00100b61cb",
}
for name, hexs in pats.items():
    p = bytes.fromhex(hexs)
    hits = []
    i = img.find(p, TEXT[0] - BASE)
    while i != -1 and i < TEXT[1] - BASE:
        if i % 4 == 0:
            hits.append(BASE + i)
        i = img.find(p, i + 4)
    print(f"{name} = {', '.join(hex(h) for h in hits[:4])}  ({len(hits)} hits)")
