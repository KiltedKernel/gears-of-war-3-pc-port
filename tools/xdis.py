"""Disassemble a flat Xbox 360 image (default.bin, base 0x82000000) with Capstone.
usage: xdis.py image.bin <address hex> [count=24] [before=0]"""
import struct, sys
import capstone

B = 0x82000000


def disasm(img, addr, count=24, before=0):
    md = capstone.Cs(capstone.CS_ARCH_PPC, capstone.CS_MODE_32 | capstone.CS_MODE_BIG_ENDIAN)
    out = []
    a = addr - 4 * before
    for k in range(count + before):
        o = a - B + 4 * k
        word = img[o:o + 4]
        ins = next(md.disasm(word, a + 4 * k), None)
        text = f"{ins.mnemonic} {ins.op_str}" if ins else f".long 0x{struct.unpack('>I', word)[0]:08X}"
        out.append(f"{'=>' if a + 4 * k == addr else '  '} {a + 4 * k:08X}  {struct.unpack('>I', word)[0]:08X}  {text}")
    return out


if __name__ == "__main__":
    img = open(sys.argv[1], "rb").read()
    args = [int(x, 16) if i == 0 else int(x) for i, x in enumerate(sys.argv[2:])]
    print("\n".join(disasm(img, *args)))
